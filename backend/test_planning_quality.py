"""Daily quality and price-search contracts, with HTTP mocks only."""
import copy
import json
import unittest
from decimal import Decimal
from datetime import datetime, timezone
import httpx
from backend.agent import GeneratedTrip, SearchArguments, assemble_trip, generate_live
from backend.planning_quality import annotate_plan, price_clues
from backend.providers import CallBudget, SearchHit, ServiceAdapters, ProviderError
from backend.test_agent import Fixture, REQUEST, SETTINGS, output_for, tool_response, search_response
from backend.test_itinerary_validation import evidence


def hit(identifier,title,snippet):
    return SearchHit(id=identifier,title=title,snippet=snippet,url='https://example.com/'+identifier,
                     retrieved_at=datetime.now(timezone.utc))


class QualityTests(unittest.TestCase):
    def test_price_clues_are_exact_bounded_excerpts_not_invented_prices(self):
        hits=[hit('s'+str(i),'介绍','旅游介绍。'+('前'*80)+'人均消费80元，实际以菜单为准'+('后'*250)) for i in range(8)]
        clues=price_clues(hits)
        self.assertEqual(len(clues),5)
        for clue in clues:
            self.assertLessEqual(len(clue['quote']),200)
            self.assertIn(clue['quote'],hits[int(clue['source_id'][1:])].snippet)
            self.assertIn('80元',clue['quote'])
        self.assertEqual(price_clues([hit('s1','介绍','门票及人均价格未知')]),[])
        self.assertEqual(price_clues([hit('s1','介绍','门票免费。')])[0]['quote'],'门票免费')
        long_quote=price_clues([hit('s1','介绍','门票免费'+('说明'*160)+'导览80元')])[0]['quote']
        self.assertIn('80元',long_quote)

    def test_title_uses_actual_places_and_meal_only_day_warns(self):
        output=output_for('s1');output['days'][0]['title']='运河风情'
        a=output['days'][0]['activities'][0];a['location']='荣鲜馄饨'
        other=copy.deepcopy(a);other.update(location='新榆园',time='16:30 - 18:00')
        output['days'][0]['activities'].append(other)
        e={'s1':hit('s1','杭州餐厅','荣鲜馄饨。新榆园。')}
        trip=assemble_trip(GeneratedTrip.model_validate(output),REQUEST,e,'test',[])
        annotate_plan(trip,{'s1':{'restaurant'}})
        self.assertEqual(trip.days[0].title,'荣鲜馄饨、新榆园')
        self.assertNotIn('运河',trip.days[0].title)
        self.assertTrue(any('缺少已检索的观光安排' in w for w in trip.warnings))
        self.assertTrue(any('尚未核验网页正文' in w for w in trip.warnings))

    def test_food_only_preference_not_forced_to_add_sights(self):
        trip=assemble_trip(GeneratedTrip.model_validate(output_for('s1')),REQUEST,evidence(),'test',[])
        annotate_plan(trip,{'s1':{'restaurant'}},mixed_interests=False)
        self.assertFalse(any('缺少已检索的观光安排' in w for w in trip.warnings))
        self.assertFalse(any('安排较少' in w for w in trip.warnings))

    def test_unrelated_citation_is_not_displayed_as_venue_support(self):
        output=output_for('s1');output['days'][0]['activities'][0]['source_ids'].append('s2')
        e=evidence();e['s2']=hit('s2','另一家餐厅','其他地点的信息')
        trip=assemble_trip(GeneratedTrip.model_validate(output),REQUEST,e,'test',[])
        self.assertEqual([s.id for s in trip.days[0].activities[0].sources],['s1'])


class PriceRoundtripTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_price_tool_quotes_support_per_person_total(self):
        def known(output):
            tool=next(m for m in fixture.requests[-1][1]['messages'] if m['role']=='tool')
            payload=json.loads(tool['content'])
            self.assertTrue(payload['price_clues'])
            self.assertFalse(payload['price_follow_up_needed'])
            self.assertEqual(payload['remaining_search_calls'],0)
            self.assertTrue(any('80元' in q['quote'] for q in payload['price_clues']))
            output['days'][0]['activities'][0].update(estimated_cost='160.00',cost_status='reference',
                cost_basis='2人，每人导览参考价80元，共160元；以最新官方报价为准',cost_evidence='导览参考价80元/人')
            return output
        fixture=Fixture(decisions=[tool_response([('price1',{'query':'杭州 西湖 导览每人价格 官方','purpose':'price'})])],outputs=[known])
        settings=SETTINGS.model_copy(update={'max_search_rounds':1,'max_search_calls':1})
        budget=CallBudget(settings)
        async with httpx.AsyncClient(transport=httpx.MockTransport(fixture.handle)) as client:
            trip=await generate_live(REQUEST,ServiceAdapters(settings,client,budget))
        self.assertEqual(trip.total_cost,Decimal('160.00'))
        self.assertEqual(trip.days[0].activities[0].cost_status,'reference')
        self.assertEqual(budget.counts['model_calls'],2)
        self.assertEqual(budget.counts['search_calls'],1)
        self.assertEqual(SearchArguments(query='价格',purpose='price').purpose,'price')

    async def test_no_price_results_stay_unknown_without_more_calls(self):
        fixture=Fixture(decisions=[tool_response()])
        original=fixture.handle
        def handle(request):
            response=original(request)
            if request.url.path.endswith('web-search'):
                body=search_response();body['data']['webPages']['value'][0]['summary']='西湖 自然风景介绍。价格尚待核实。'
                return httpx.Response(200,json=body)
            return response
        fixture.handle=handle
        settings=SETTINGS.model_copy(update={'max_search_rounds':1,'max_search_calls':1})
        budget=CallBudget(settings)
        async with httpx.AsyncClient(transport=httpx.MockTransport(fixture.handle)) as client:
            with self.assertRaises(ProviderError) as caught:
                await generate_live(REQUEST,ServiceAdapters(settings,client,budget))
        self.assertEqual(caught.exception.code,'PRICE_EVIDENCE_MISSING')
        self.assertEqual(budget.counts['search_calls'],1)
        self.assertEqual(budget.counts['model_calls'],2)


if __name__ == '__main__':unittest.main()
