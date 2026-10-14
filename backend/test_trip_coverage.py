"""Reproduce sparse multi-day plans and validate bounded coverage recovery."""
import json
import unittest
import httpx
from backend.trip_coverage import distinct_daily_choices
from backend.agent import generate_live
from backend.providers import CallBudget, ProviderError, ServiceAdapters
from backend.test_agent import REQUEST, SETTINGS, Fixture, tool_response, search_response
from backend.test_coverage_support import with_multi_coverage
from backend.test_generation_segments import segment


class TripCoverageTests(unittest.IsolatedAsyncioTestCase):
    async def execute(self, fixture, settings=None, days=3, clock=None):
        settings = settings or SETTINGS.model_copy(update={'max_search_rounds':1})
        budget = CallBudget(settings, **({'clock':clock} if clock else {}))
        async with httpx.AsyncClient(transport=httpx.MockTransport(fixture.handle)) as client:
            result = await generate_live(REQUEST.model_copy(update={'days':days}), ServiceAdapters(settings,client,budget))
        return result, budget

    def test_each_day_needs_an_independent_sight_not_just_enough_names_in_total(self):
        self.assertFalse(distinct_daily_choices([{'西湖'}, {'西湖'}, {'灵隐寺','雷峰塔'}]))
        self.assertTrue(distinct_daily_choices([{'西湖','灵隐寺'}, {'西湖'}, {'雷峰塔'}]))

    async def test_one_museum_for_three_days_rejected_before_generation_with_bounded_search(self):
        fixture = Fixture(); original = fixture.handle
        def handle(request):
            if request.url.path.endswith('web-search'):
                fixture.requests.append((request.url.path,json.loads(request.content)))
                body = search_response()
                body['data']['webPages']['value'][0].update(name='杭州浙江自然博物馆', summary='杭州浙江自然博物馆，价格未知。')
                return httpx.Response(200,json=body)
            return original(request)
        fixture.handle = handle
        with self.assertRaises(ProviderError) as caught:
            await self.execute(fixture)
        self.assertEqual(caught.exception.code,'INSUFFICIENT_EVIDENCE')
        self.assertIn('候选覆盖不足',caught.exception.message)
        self.assertEqual(len([b for _,b in fixture.requests if 'response_format' in b]),0)
        self.assertEqual(len([b for p,b in fixture.requests if p.endswith('web-search')]),3)

    async def test_supplemental_restaurant_evidence_reaches_model_and_complete_multi_plan(self):
        fixture = with_multi_coverage(Fixture(outputs=[segment(0,3,True)]))
        trip,budget = await self.execute(fixture)
        self.assertEqual(len({d.activities[0].location for d in trip.days}),3)
        self.assertTrue(all(len(d.activities)==3 for d in trip.days))
        self.assertFalse(any('尚未达到每天至少两家餐厅' in w for w in trip.warnings))
        self.assertEqual(budget.counts['model_calls'],2)
        self.assertEqual(budget.counts['search_calls'],2)
        messages = fixture.requests[-1][1]['messages']
        extra = [json.loads(m['content']) for m in messages if m['role']=='user' and m['content'].startswith('{')]
        self.assertTrue(any('backend_coverage_evidence' in x for x in extra))
        self.assertTrue(all(a.location in a.description for d in trip.days for a in d.activities))
        self.assertNotIn('西湖',trip.days[1].activities[0].description)

    async def test_sufficient_candidates_do_not_allow_repeated_sights_or_missing_meals(self):
        for mode in ('repeat','missing_meals'):
            fixture = with_multi_coverage(Fixture(outputs=[segment(0,3),segment(0,3)]))
            original = fixture.handle
            def handle(request):
                response=original(request)
                body=json.loads(request.content)
                if 'response_format' in body:
                    data=response.json(); output=json.loads(data['choices'][0]['message']['content'])
                    for day in output['days']:
                        if mode=='repeat': day['activities'][0]['place_id']='place_1'
                        else: day['activities']=day['activities'][:1]
                    data['choices'][0]['message']['content']=json.dumps(output)
                    return httpx.Response(200,json=data)
                return response
            fixture.handle=handle
            with self.subTest(mode=mode),self.assertRaises(ProviderError) as caught:
                await self.execute(fixture)
            self.assertEqual(caught.exception.code,'ITINERARY_COVERAGE_MISSING')

    async def test_search_budget_and_low_time_stop_before_paid_generation(self):
        for limit,low_time in [(1,False),(6,True)]:
            now=[0.0]; fixture=Fixture(); original=fixture.handle
            def handle(request):
                response=original(request)
                if low_time and request.url.path.endswith('web-search'): now[0]=80
                return response
            fixture.handle=handle
            settings=SETTINGS.model_copy(update={'max_search_rounds':1,'max_search_calls':limit})
            with self.subTest(limit=limit),self.assertRaises(ProviderError) as caught:
                await self.execute(fixture,settings,clock=lambda:now[0])
            self.assertEqual(caught.exception.code,'INSUFFICIENT_EVIDENCE')
            self.assertEqual(len(fixture.requests),2)

    async def test_auth_failure_in_coverage_search_is_never_hidden(self):
        fixture=Fixture(); original=fixture.handle
        def handle(request):
            if request.url.path.endswith('web-search') and '旅行覆盖' in json.loads(request.content)['query']:
                return httpx.Response(401,json={})
            return original(request)
        fixture.handle=handle
        with self.assertRaises(ProviderError) as caught: await self.execute(fixture)
        self.assertEqual(caught.exception.code,'AUTH_FAILED')

    async def test_price_tool_waits_for_place_and_restaurant_coverage(self):
        decisions=[tool_response([('price',{'query':'杭州门票价格','purpose':'price'}),
                                 ('view',{'query':'杭州不同景点','purpose':'attraction'})])]
        fixture=with_multi_coverage(Fixture(decisions=decisions,outputs=[segment(0,3,True)]))
        trip,budget=await self.execute(fixture)
        generated=next(b for _,b in fixture.requests if 'response_format' in b)
        reply=next(m for m in generated['messages'] if m.get('tool_call_id')=='price')
        self.assertEqual(json.loads(reply['content'])['error'],'COVERAGE_FIRST')
        self.assertEqual(budget.counts['search_calls'],2)
        self.assertFalse(trip.demo)

    async def test_rendering_neutral_text_does_not_turn_paid_experience_into_free_entry(self):
        def intent(output):
            output['days'][0]['activities'][0]['description']='乘船游览西湖'
            return output
        fixture=Fixture(outputs=[intent])
        settings=SETTINGS.model_copy(update={'max_search_rounds':1,'max_search_calls':1})
        trip,_=await self.execute(fixture,settings,days=1)
        self.assertIsNone(trip.days[0].activities[0].estimated_cost)
        self.assertIn('西湖',trip.days[0].activities[0].description)
