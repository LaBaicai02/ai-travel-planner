"""Constrained repair must use observed places and registered source IDs."""
import json
import unittest
import httpx
from backend.agent import GeneratedTrip, _grounded_repair_model, _observed_candidates, generate_live
from backend.providers import CallBudget, ProviderError, ServiceAdapters
from backend.test_agent import REQUEST, SETTINGS, Fixture, output_for
from backend.test_itinerary_validation import evidence


class CandidateTests(unittest.TestCase):
    def test_observed_title_name_is_candidate_but_fake_alias_is_not(self):
        output=output_for('s1');output['days'][0]['activities'][0]['location']='PRIVATE-ALIAS'
        model,candidates=_grounded_repair_model(GeneratedTrip.model_validate(output),REQUEST,evidence())
        self.assertEqual(candidates,{'西湖':['s1']})
        schema=model.model_json_schema()
        activity=schema['$defs']['GroundedActivity']
        self.assertEqual(activity['properties']['location']['enum'],['西湖'])
        self.assertEqual(activity['properties']['source_ids']['items']['enum'],['s1'])
        self.assertFalse(activity['additionalProperties'])
        self.assertIsInstance(model.model_validate(output_for('s1')),GeneratedTrip)
        with self.assertRaises(ValueError):model.model_validate(output)

    def test_no_candidate_from_generic_article_or_city(self):
        e=evidence();e['s1']=e['s1'].model_copy(update={'title':'杭州6家地道杭帮菜推荐','snippet':'没有对应地点原文'})
        output=output_for('s1');output['days'][0]['activities'][0]['location']='PRIVATE-ALIAS'
        model,candidates=_grounded_repair_model(GeneratedTrip.model_validate(output),REQUEST,e)
        self.assertIsNone(model)
        self.assertEqual(candidates,{})

    def test_branch_suffix_keeps_exact_full_name_and_rejects_alias(self):
        e=evidence();e['s1']=e['s1'].model_copy(update={'title':'品味餐厅（庆春路店）',
            'snippet':'品味餐厅（庆春路店）提供午餐与晚餐。'})
        output=output_for('s1');output['days'][0]['activities'][0]['location']='品味餐厅庆春店'
        model,candidates=_grounded_repair_model(GeneratedTrip.model_validate(output),REQUEST,e)
        self.assertEqual(candidates,{'品味餐厅（庆春路店）':['s1']})
        self.assertEqual(_observed_candidates(None,REQUEST,e),candidates)
        with self.assertRaises(ValueError):model.model_validate(output)

    def test_parenthesized_recommendation_or_closed_titles_not_candidates(self):
        for title in ['杭州6家餐厅（庆春路店）','地道餐厅推荐（庆春路店）','品味餐厅（暂停营业）']:
            e=evidence();e['s1']=e['s1'].model_copy(update={'title':title,'snippet':'综合资料'})
            self.assertEqual(_observed_candidates(None,REQUEST,e),{})


class CandidateRoundtripTests(unittest.IsolatedAsyncioTestCase):
    async def test_wrong_place_repaired_with_enum_and_no_extra_search(self):
        def wrong(output):
            output['days'][0]['activities'][0]['location']='PRIVATE-ALIAS'
            return output
        fixture=Fixture(outputs=[wrong])
        settings=SETTINGS.model_copy(update={'max_search_rounds':1,'max_model_calls':3})
        budget=CallBudget(settings)
        async with httpx.AsyncClient(transport=httpx.MockTransport(fixture.handle)) as client:
            with self.assertLogs('backend.agent',level='WARNING') as logs:
                trip=await generate_live(REQUEST,ServiceAdapters(settings,client,budget))
        self.assertEqual(trip.days[0].activities[0].location,'西湖')
        repair=fixture.requests[-1][1]
        schema=repair['response_format']['json_schema']['schema']
        self.assertEqual(schema['$defs']['GroundedActivity']['properties']['location']['enum'],['西湖'])
        self.assertIn('西湖',repair['messages'][-1]['content'])
        self.assertNotIn('PRIVATE-ALIAS',repair['messages'][-1]['content'])
        self.assertNotIn('PRIVATE-ALIAS',''.join(logs.output))
        self.assertNotIn('tools',repair)
        self.assertEqual(budget.counts['model_calls'],3)
        self.assertEqual(budget.counts['search_calls'],1)

    async def test_wrong_branch_name_repaired_to_exact_observed_branch(self):
        def wrong(output):
            output['days'][0]['activities'][0]['location']='品味餐厅庆春店'
            return output
        def correct(output):
            output['days'][0]['activities'][0]['location']='品味餐厅（庆春路店）'
            return output
        fixture=Fixture(outputs=[wrong,correct]);original=fixture.handle
        def handle(request):
            response=original(request)
            if request.url.path.endswith('web-search'):
                body=response.json();row=body['data']['webPages']['value'][0]
                row.update(name='品味餐厅（庆春路店）',summary='品味餐厅（庆春路店）提供午餐和晚餐，价格未知。')
                return httpx.Response(200,json=body)
            return response
        settings=SETTINGS.model_copy(update={'max_search_rounds':1,'max_model_calls':3,'max_search_calls':1})
        budget=CallBudget(settings)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with self.assertLogs('backend.agent',level='WARNING'):
                trip=await generate_live(REQUEST,ServiceAdapters(settings,client,budget))
        self.assertEqual(trip.days[0].activities[0].location,'品味餐厅（庆春路店）')
        generated=[b for _,b in fixture.requests if 'response_format' in b]
        initial=json.loads(generated[0]['messages'][-1]['content'])
        self.assertEqual(list(initial['observed_place_candidates']),['品味餐厅（庆春路店）'])
        enum=generated[1]['response_format']['json_schema']['schema']['$defs']['GroundedActivity']['properties']['location']['enum']
        self.assertEqual(enum,['品味餐厅（庆春路店）'])
        self.assertEqual(budget.counts['model_calls'],3)
        self.assertEqual(budget.counts['search_calls'],1)

    async def test_no_candidates_stop_before_paid_repair(self):
        def wrong(output):
            output['days'][0]['activities'][0]['location']='PRIVATE-ALIAS'
            return output
        fixture=Fixture(outputs=[wrong]);original=fixture.handle
        def handle(request):
            response=original(request)
            if request.url.path.endswith('web-search'):
                body=response.json();hit=body['data']['webPages']['value'][0]
                hit.update(name='景点攻略与推荐',summary='没有可用的具体地点资料')
                return httpx.Response(200,json=body)
            return response
        settings=SETTINGS.model_copy(update={'max_search_rounds':1,'max_model_calls':3})
        budget=CallBudget(settings)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with self.assertLogs('backend.agent',level='WARNING'),self.assertRaises(ProviderError) as caught:
                await generate_live(REQUEST,ServiceAdapters(settings,client,budget))
        self.assertEqual(caught.exception.code,'INSUFFICIENT_EVIDENCE')
        self.assertEqual(budget.counts['model_calls'],2)
        self.assertEqual(budget.counts['search_calls'],1)
        self.assertNotIn('PRIVATE-ALIAS',caught.exception.message)


if __name__ == '__main__':unittest.main()
