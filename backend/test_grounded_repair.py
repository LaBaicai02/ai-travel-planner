"""Native place-ID output: candidate scope, binding and bounded repair."""
import copy
import json
import unittest
import httpx
from pydantic import ValidationError
from backend.agent import (_place_catalog, _place_output_model, _bind_places, assemble_trip, generate_live)
from backend.providers import CallBudget, ProviderError, ServiceAdapters
from backend.test_agent import REQUEST, SETTINGS, Fixture, output_for
from backend.test_itinerary_validation import evidence
from backend.test_planning_quality import hit


def selected(place_id='place_1'):
    output = output_for('s1')
    activity = output['days'][0]['activities'][0]
    activity.pop('location'); activity.pop('source_ids')
    activity['place_id'] = place_id
    return output


class CandidateTests(unittest.TestCase):
    def test_schema_exposes_only_place_id_and_backend_binds_name_sources(self):
        e = evidence(); catalog = _place_catalog(REQUEST, e)
        model = _place_output_model(catalog)
        activity = model.model_json_schema()['$defs']['SelectedActivity']
        self.assertEqual(activity['properties']['place_id']['enum'], ['place_1'])
        self.assertNotIn('const', activity['properties']['place_id'])
        self.assertNotIn('location', activity['properties'])
        self.assertNotIn('source_ids', activity['properties'])
        self.assertFalse(activity['additionalProperties'])
        bound = _bind_places(model.model_validate(selected()), catalog)
        trip = assemble_trip(bound, REQUEST, e, 'test', [])
        public = trip.model_dump(mode='json')['days'][0]['activities'][0]
        self.assertEqual(public['location'], '西湖')
        self.assertEqual(public['sources'][0]['id'], 's1')
        self.assertNotIn('place_id', public)
        for output in [selected('invented'), selected('example_view'), output_for('s1')]:
            with self.assertRaises(ValidationError): model.model_validate(output)
        for field, value in [('location', '伪造地点'), ('source_ids', ['other'])]:
            output = selected(); output['days'][0]['activities'][0][field] = value
            with self.assertRaises(ValidationError): model.model_validate(output)

    def test_branches_remain_distinct_and_titles_are_conservative(self):
        names = ['品味餐厅（庆春路店）', '品味餐厅（湖滨店）']
        e = {f's{i}': hit(f's{i}', name, name) for i, name in enumerate(names)}
        self.assertEqual([p['name'] for p in _place_catalog(REQUEST, e).values()], names)
        for title in ['杭州', '附近餐厅', '杭州6家地道杭帮菜推荐', '品味餐厅（暂停营业）', '景点攻略']:
            self.assertEqual(_place_catalog(REQUEST, {'s': hit('s', title, '没有明确名称的普通说明')}), {})
        e = {f's{i}': hit(f's{i}', f'测试{i}餐厅', f'测试{i}餐厅') for i in range(80)}
        self.assertEqual(len(_place_catalog(REQUEST, e)), 60)

    def test_other_place_quotes_cannot_supply_price_or_hours(self):
        e = {'s1': hit('s1', '梅园餐厅', '梅园餐厅提供用餐，价格未知'),
             's2': hit('s2', '湖畔饭店', '湖畔饭店人均80元，营业时间10:00-20:00')}
        catalog = _place_catalog(REQUEST, e); model = _place_output_model(catalog)
        for fields, reason in [
            ({'estimated_cost':'160.00','cost_status':'reference','cost_basis':'2人每人80元','cost_evidence':'湖畔饭店人均80元'}, 'cost_quote_missing'),
            ({'opening_hours':'10:00-20:00','opening_hours_status':'reported','opening_hours_evidence':'湖畔饭店人均80元，营业时间10:00-20:00'}, 'hours_quote_missing')]:
            output = selected(); output['days'][0]['activities'][0].update(fields)
            bound = _bind_places(model.model_validate(output), catalog)
            with self.assertRaises(ProviderError) as caught: assemble_trip(bound, REQUEST, e, 'test', [])
            self.assertEqual(caught.exception.validation_reason, reason)


class CandidateRoundtripTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_id_repaired_with_same_enum_without_more_search(self):
        fixture = Fixture(outputs=[selected('PRIVATE-ID'), selected()])
        settings = SETTINGS.model_copy(update={'max_search_rounds':1,'max_model_calls':3})
        budget = CallBudget(settings)
        async with httpx.AsyncClient(transport=httpx.MockTransport(fixture.handle)) as client:
            with self.assertLogs('backend.providers', level='WARNING') as logs:
                trip = await generate_live(REQUEST, ServiceAdapters(settings,client,budget))
        generated = [b for _,b in fixture.requests if 'response_format' in b]
        self.assertEqual(generated[0]['response_format'], generated[1]['response_format'])
        self.assertNotIn('PRIVATE-ID', ''.join(logs.output))
        self.assertNotIn('PRIVATE-ID', generated[1]['messages'][-1]['content'])
        self.assertEqual(trip.days[0].activities[0].location, '西湖')
        self.assertEqual(budget.counts['model_calls'], 3)
        self.assertEqual(budget.counts['search_calls'], 1)

    async def test_no_candidates_stops_before_any_generation(self):
        fixture = Fixture(); original = fixture.handle
        def handle(request):
            response = original(request)
            if request.url.path.endswith('web-search'):
                body=response.json(); body['data']['webPages']['value'][0].update(name='景点攻略与推荐',summary='没有可用的具体地点资料')
                return httpx.Response(200,json=body)
            return response
        settings = SETTINGS.model_copy(update={'max_search_rounds':1})
        budget = CallBudget(settings)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with self.assertRaises(ProviderError) as caught:
                await generate_live(REQUEST,ServiceAdapters(settings,client,budget))
        self.assertEqual(caught.exception.code,'INSUFFICIENT_EVIDENCE')
        self.assertEqual(budget.counts['model_calls'],1)
        self.assertFalse(any('response_format' in b for _,b in fixture.requests))
