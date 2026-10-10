"""Daily sourced dining coverage; HTTP mocks only, no paid calls."""
import copy
import json
import unittest
import httpx
from backend.agent import GeneratedTrip, assemble_trip, generate_live
from backend.planning_quality import annotate_plan, meal_gaps
from backend.providers import CallBudget, ProviderError, ServiceAdapters
from backend.test_agent import Fixture, REQUEST, SETTINGS, output_for, tool_response, search_response
from backend.test_planning_quality import hit


def meal_trip(lunch_name='梅园餐厅', dinner_name='湖畔饭店', lunch_time='12:00 - 13:00', dinner_time='18:00 - 19:00'):
    output=output_for('lunch')
    base=output['days'][0]['activities'][0]
    base.update(location=lunch_name, description='享用午餐', time=lunch_time)
    dinner=copy.deepcopy(base)
    dinner.update(location=dinner_name,description='品尝美食，享用晚餐',time=dinner_time,source_ids=['dinner'])
    output['days'][0]['activities'].append(dinner)
    sources={'lunch':hit('lunch',lunch_name,lunch_name+' 提供午餐，无明确价格。'),
             'dinner':hit('dinner',dinner_name,dinner_name+' 提供晚餐，无明确价格。')}
    return assemble_trip(GeneratedTrip.model_validate(output),REQUEST,sources,'test',[])


class MealCoverageTests(unittest.TestCase):
    def test_two_distinct_sourced_restaurants_cover_lunch_and_dinner(self):
        trip=meal_trip()
        self.assertEqual(meal_gaps(trip,{'lunch':{'restaurant'},'dinner':{'restaurant'}}),[])
        self.assertIsNone(trip.total_cost)

    def test_same_venue_twice_does_not_meet_two_restaurant_target(self):
        trip=meal_trip(dinner_name='梅园餐厅')
        gap=meal_gaps(trip,{'lunch':{'restaurant'},'dinner':{'restaurant'}})[0]
        self.assertEqual(gap['distinct_restaurants'],1)
        self.assertFalse(gap['missing_lunch'])
        self.assertFalse(gap['missing_dinner'])
        annotate_plan(trip,{'lunch':{'restaurant'},'dinner':{'restaurant'}})
        self.assertTrue(any('午晚餐未覆盖两家不同餐厅' in w for w in trip.warnings))

    def test_sights_shopping_wrong_mealtime_and_short_stop_do_not_count(self):
        for changes, purposes in [({},{}),
            ({'description':'参观餐厅，购买伴手礼'},{'lunch':{'restaurant'},'dinner':{'restaurant'}}),
            ({'time':'09:00 - 10:00'},{'lunch':{'restaurant'},'dinner':{'restaurant'}}),
            ({'time':'12:00 - 12:20'},{'lunch':{'restaurant'},'dinner':{'restaurant'}})]:
            with self.subTest(changes=changes,purposes=purposes):
                trip=meal_trip()
                trip.days[0].activities[0]=trip.days[0].activities[0].model_copy(update=changes)
                self.assertTrue(meal_gaps(trip,purposes)[0]['missing_lunch'])

    def test_closed_day_and_lunch_break_do_not_count_as_meals(self):
        for hours in ['周六闭馆，周日至周五09:00-21:00','09:00-11:00;17:00-21:00']:
            trip=meal_trip()
            trip.days[0].activities[0].opening_hours=hours
            trip.days[0].activities[0].opening_hours_status='reported'
            self.assertTrue(meal_gaps(trip,{'lunch':{'restaurant'},'dinner':{'restaurant'}})[0]['missing_lunch'])

    def test_each_day_has_its_own_coverage_no_cross_day_substitution(self):
        from datetime import timedelta
        trip=meal_trip()
        second=trip.days[0].model_copy(deep=True)
        second.day=2;second.date+=timedelta(days=1)
        second.activities=second.activities[:1]
        trip.days.append(second)
        gaps=meal_gaps(trip,{'lunch':{'restaurant'},'dinner':{'restaurant'}})
        self.assertEqual([g['day'] for g in gaps],[2])
        self.assertTrue(gaps[0]['missing_dinner'])


class MealAgentTests(unittest.IsolatedAsyncioTestCase):
    async def run_plan(self, mode='repair', empty_restaurants=False):
        def meals(output):
            ids={}
            for _,body in fixture.requests:
                for message in body.get('messages',[]):
                    if message['role']=='tool':
                        ids.update({h['title']:h['id'] for h in json.loads(message['content']).get('results',[])})
            base=copy.deepcopy(output['days'][0]['activities'][0])
            lunch=dict(base,time='12:00 - 13:00',location='梅园餐厅',description='享用午餐',source_ids=[ids['梅园餐厅']])
            dinner=dict(base,time='18:00 - 19:00',location='湖畔饭店',description='享用晚餐',source_ids=[ids['湖畔饭店']])
            output['days'][0]['activities'] += [lunch,dinner]
            return output
        def invalid(output):
            output['days'][0]['activities'][0]['source_ids']=['invented']
            return output
        def invalid_first(output):
            output['days'][0]['activities'][0]['time']='99:00 - 99:30'
            return output
        outputs={'complete':[meals], 'repair':[lambda x:x,meals],
                 'invalid_repair':[lambda x:x,invalid], 'still_missing':[lambda x:x,lambda x:x],
                 'core_repair':[invalid_first,lambda x:x], 'auth_repair':[lambda x:x]}
        decisions=[tool_response([('food',{'query':'杭州 午餐 晚餐 两家餐厅 分店','purpose':'restaurant'}),
                                  ('sights',{'query':'杭州 西湖 景点','purpose':'attraction'})])]
        fixture=Fixture(decisions=decisions,outputs=outputs[mode]);original=fixture.handle
        def handle(request):
            if mode=='auth_repair' and not request.url.path.endswith('web-search') and budget.counts['model_calls']==3:
                return httpx.Response(401,json={})
            if request.url.path.endswith('web-search') and '餐厅' in json.loads(request.content)['query']:
                rows=[] if empty_restaurants else [
                    {'name':name,'url':'https://food.example/'+str(i),'summary':name+' 提供午餐和晚餐，无明确价格。'}
                    for i,name in enumerate(['梅园餐厅','湖畔饭店'])]
                body=search_response();body['data']['webPages']['value']=rows
                return httpx.Response(200,json=body)
            return original(request)
        settings=SETTINGS.model_copy(update={'max_search_rounds':1,'max_search_calls':2,
                                            'max_model_calls':3,'max_model_http_attempts':3})
        budget=CallBudget(settings)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            trip=await generate_live(REQUEST,ServiceAdapters(settings,client,budget))
        return trip,budget,fixture

    async def test_complete_meals_need_no_quality_retry(self):
        trip,budget,fixture=await self.run_plan('complete')
        self.assertEqual(len(trip.days[0].activities),3)
        self.assertFalse(any('尚未达到每天至少两家餐厅' in w for w in trip.warnings))
        self.assertEqual(budget.counts['model_calls'],2)
        self.assertEqual(budget.counts['search_calls'],2)

    async def test_missing_meals_use_one_existing_repair_and_keep_unknown_prices(self):
        trip,budget,fixture=await self.run_plan()
        self.assertEqual([a.location for a in trip.days[0].activities],['西湖','梅园餐厅','湖畔饭店'])
        self.assertIsNone(trip.total_cost)
        self.assertFalse(any('尚未达到每天至少两家餐厅' in w for w in trip.warnings))
        self.assertEqual(budget.counts['model_calls'],3)
        self.assertEqual(budget.counts['search_calls'],2)
        self.assertIn('餐次检查',json.dumps(fixture.requests[-1][1],ensure_ascii=False))

    async def test_no_restaurant_evidence_warns_without_inventing_or_retrying(self):
        trip,budget,_=await self.run_plan('still_missing',empty_restaurants=True)
        self.assertEqual(len(trip.days[0].activities),1)
        self.assertTrue(any('午餐、晚餐' in w and '尚未达到' in w for w in trip.warnings))
        self.assertEqual(budget.counts['model_calls'],2)
        self.assertFalse(trip.demo)

    async def test_bad_quality_repair_retains_only_original_valid_live_draft(self):
        trip,budget,_=await self.run_plan('invalid_repair')
        self.assertEqual([a.location for a in trip.days[0].activities],['西湖'])
        self.assertTrue(any('餐厅补充输出未通过校验' in w for w in trip.warnings))
        self.assertTrue(any('尚未达到' in w for w in trip.warnings))
        self.assertEqual(budget.counts['model_calls'],3)
        self.assertFalse(trip.demo)

    async def test_quality_repair_auth_failure_is_not_hidden_by_saved_draft(self):
        with self.assertRaises(ProviderError) as caught:
            await self.run_plan('auth_repair')
        self.assertEqual(caught.exception.code,'AUTH_FAILED')

    async def test_still_missing_or_used_core_repair_never_causes_another_retry(self):
        for mode in ['still_missing','core_repair']:
            with self.subTest(mode=mode):
                trip,budget,_=await self.run_plan(mode)
                self.assertTrue(any('尚未达到' in w for w in trip.warnings))
                self.assertEqual(budget.counts['model_calls'],3)
                self.assertEqual(budget.counts['search_calls'],2)


if __name__=='__main__':unittest.main()
