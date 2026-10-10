"""Daily quality and price-search contracts, with HTTP mocks only."""
import copy
import json
import unittest
from decimal import Decimal
from datetime import date, datetime, timezone
import httpx
from backend.agent import GeneratedTrip, SearchArguments, assemble_trip, generate_live
from backend.planning_quality import annotate_plan, price_clues, improve_schedule, opening_intervals
from backend.providers import CallBudget, SearchHit, ServiceAdapters
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


class ScheduleTests(unittest.TestCase):
    def trip(self, times, hours=None, price='40.00'):
        output = output_for('s1')
        template = output['days'][0]['activities'][0]
        template.update(estimated_cost=price, cost_status='reference' if price else 'unknown',
                        cost_basis='2人参考价格' if price else '', cost_evidence='门票20元' if price else None,
                        opening_hours=hours, opening_hours_status='reported' if hours else 'unknown',
                        opening_hours_evidence=hours)
        output['days'][0]['activities'] = [dict(template, time=time) for time in times]
        sources = {'s1':hit('s1', '西湖', '西湖 ' + ('门票20元。' if price else '价格未知。') + (hours or ''))}
        return assemble_trip(GeneratedTrip.model_validate(output), REQUEST, sources, 'test', [])

    def test_duplicate_attraction_removed_and_totals_recomputed_without_mutation(self):
        trip = self.trip(['09:00 - 11:00', '14:00 - 16:00'])
        result = improve_schedule(trip, {'s1':{'attraction'}})
        self.assertEqual(len(result.days[0].activities), 1)
        self.assertEqual(result.total_cost, Decimal('40'))
        self.assertEqual(trip.total_cost, Decimal('80'))
        self.assertEqual(len(trip.days[0].activities), 2)
        self.assertTrue(any('重复安排已移除' in w for w in result.warnings))
        self.assertEqual(result.days[0].activities[0].sources, trip.days[0].activities[0].sources)

    def test_over_budget_warning_recalculated_after_duplicate_removal(self):
        trip = self.trip(['09:00 - 11:00', '14:00 - 16:00'])
        trip.budget = Decimal('60')
        trip.warnings.append('已知活动费用超过预算，请调整安排；未缩小费用以满足预算。')
        result = improve_schedule(trip, {'s1':{'attraction'}})
        self.assertFalse(any('超过预算' in w for w in result.warnings))
        trip.budget = Decimal('30')
        result = improve_schedule(trip, {'s1':{'attraction'}})
        self.assertTrue(any('超过预算' in w for w in result.warnings))

    def test_repeated_meals_and_mixed_purpose_sources_not_deleted(self):
        for purposes in ({'restaurant'}, {'attraction','restaurant'}):
            result = improve_schedule(self.trip(['09:00 - 11:00','14:00 - 16:00']), {'s1':purposes})
            self.assertEqual(len(result.days[0].activities), 2)
            self.assertEqual(result.total_cost, Decimal('80'))
            self.assertTrue(any('费用分别计算' in w for w in result.warnings))

    def test_lunch_shortened_before_break_without_changing_prices_or_hours(self):
        trip = self.trip(['13:00 - 16:00'], '周一至周日 10:30-14:30 16:30-20:30')
        result = improve_schedule(trip, {'s1':{'restaurant'}})
        activity = result.days[0].activities[0]
        self.assertEqual(activity.time, '13:00 - 14:30')
        self.assertEqual(activity.opening_hours, trip.days[0].activities[0].opening_hours)
        self.assertEqual(result.total_cost, trip.total_cost)
        self.assertTrue(any('已缩短' in w for w in result.warnings))
        self.assertEqual(trip.days[0].activities[0].time, '13:00 - 16:00')

    def test_closed_or_tiny_overlap_warns_without_guessing_a_new_time(self):
        for time in ['15:00 - 16:00','14:20 - 16:00']:
            result = improve_schedule(self.trip([time], '10:30-14:30;17:00-21:00'), {'s1':{'restaurant'}})
            self.assertEqual(result.days[0].activities[0].time, time)
            self.assertTrue(any('时间冲突' in w for w in result.warnings))

    def test_seasonal_weekday_overnight_and_invalid_hours_not_guessed(self):
        for hours in ['夏令时4月-10月 07:30-18:30;冬令时08:00-17:30',
                      '周一闭馆，周二至周日09:00-17:00（节假日例外）', '22:00-02:00', '25:00-26:00']:
            self.assertIsNone(opening_intervals(hours))
            result = improve_schedule(self.trip(['09:00 - 11:00'], hours), {'s1':{'attraction'}})
            self.assertEqual(result.days[0].activities[0].time,'09:00 - 11:00')
            self.assertTrue(any('人工确认' in w for w in result.warnings))

    def test_unknown_prices_remain_unknown_after_duplicate_removal(self):
        result = improve_schedule(self.trip(['09:00 - 11:00','14:00 - 16:00'], price=None), {'s1':{'attraction'}})
        self.assertIsNone(result.total_cost)
        self.assertEqual(result.known_cost_subtotal, Decimal('0'))
        self.assertEqual(result.days[0].activities[0].cost_status,'unknown')

    def test_plain_intervals_valid_and_in_range_schedule_unchanged(self):
        self.assertEqual(opening_intervals('8:00—12:00；13:30～17:00'),[(480,720),(810,1020)])
        trip = self.trip(['09:00 - 11:00','14:00 - 16:00'], '08:00-17:00')
        result = improve_schedule(trip, {'s1':{'restaurant'}})
        self.assertEqual([a.time for a in result.days[0].activities],['09:00 - 11:00','14:00 - 16:00'])
        self.assertFalse(any('已缩短' in w for w in result.warnings))


SEASONAL_HOURS = ('夏令时:4月1日-10月7日;开闭园时间:7:30-18:30。 '
                  '冬令时:10月8日-次年3月31日;开闭园时间:8:00-17:30')


class DateHoursTests(unittest.TestCase):
    def test_seasonal_boundaries_and_cross_year(self):
        for day, expected in [('2026-04-01',[(450,1110)]),('2026-10-07',[(450,1110)]),
                              ('2026-10-08',[(480,1050)]),('2026-12-31',[(480,1050)]),
                              ('2027-01-01',[(480,1050)]),('2027-03-31',[(480,1050)])]:
            with self.subTest(day=day):
                self.assertEqual(opening_intervals(SEASONAL_HOURS,date.fromisoformat(day)), expected)
        self.assertIsNone(opening_intervals(SEASONAL_HOURS))

    def test_weekday_closure_and_open_days(self):
        hours = '周一闭馆，周二至周日09:00-17:00'
        self.assertEqual(opening_intervals(hours,date(2026,10,12)),[])
        for day in [13,18]:
            self.assertEqual(opening_intervals(hours,date(2026,10,day)),[(540,1020)])
        self.assertEqual(opening_intervals('星期五至星期一10:00-18:00',date(2026,10,12)),[(600,1080)])
        self.assertIsNone(opening_intervals('星期五至星期一10:00-18:00',date(2026,10,13)))
        self.assertEqual(opening_intervals('周天09:00-12:00',date(2026,10,18)),[(540,720)])

    def test_split_meal_intervals_follow_weekday(self):
        self.assertEqual(opening_intervals('周一至周五10:30-14:30,17:00-21:00；周六至周日11:00-20:00',
                                          date(2026,10,12)),[(630,870),(1020,1260)])
        self.assertEqual(opening_intervals('周一至周五10:30-14:30,17:00-21:00；周六至周日11:00-20:00',
                                          date(2026,10,10)),[(660,1200)])

    def test_ambiguous_overlapping_missing_and_invalid_clauses_stay_unknown(self):
        for hours in ['4月1日-10月10日08:00-18:00；10月8日-次年3月31日09:00-17:00',
                      '周一至周日09:00-17:00；周六10:00-16:00',
                      '4月31日-10月7日09:00-17:00',
                      '4月1日-次年10月7日09:00-17:00',
                      '4月1日-10月7日09:00-17:00',
                      '周一至周日09:00-17:00；节假日另行通知',
                      '周一至周日09:00-17:00（法定节假日除外）',
                      '周一至周日09:00-17:00；10月1日-10月7日08:00-18:00',
                      '周一至周五09:00-17:00；周六25:00-26:00',
                      '夏令时09:00-18:00；冬令时08:00-17:00']:
            with self.subTest(hours=hours):
                self.assertIsNone(opening_intervals(hours,date(2026,10,10)))

    def test_leap_date_and_yearless_cross_year(self):
        self.assertEqual(opening_intervals('2月29日-3月1日09:00-17:00',date(2028,2,29)),[(540,1020)])
        self.assertIsNone(opening_intervals('2月30日-3月1日09:00-17:00',date(2028,2,29)))
        self.assertEqual(opening_intervals('10月8日-3月31日08:00-17:30',date(2027,1,1)),[(480,1050)])

    def test_trip_shortens_winter_hours_using_activity_day_and_preserves_evidence(self):
        trip = ScheduleTests().trip(['07:30 - 18:00'],SEASONAL_HOURS)
        result = improve_schedule(trip, {'s1':{'attraction'}})
        self.assertEqual(result.days[0].date,date(2026,10,10))
        self.assertEqual(result.days[0].activities[0].time,'08:00 - 17:30')
        self.assertEqual(result.days[0].activities[0].opening_hours,SEASONAL_HOURS)
        self.assertEqual(result.days[0].activities[0].sources,trip.days[0].activities[0].sources)
        self.assertEqual(result.total_cost,trip.total_cost)
        self.assertEqual(trip.days[0].activities[0].time,'07:30 - 18:00')
        self.assertFalse(any('人工确认' in w for w in result.warnings))

    def test_multiple_days_select_their_own_hours_and_closure_does_not_invent_a_time(self):
        from backend.schemas import TripResponse
        hours='周一闭馆，周二至周日09:00-17:00'
        trip=ScheduleTests().trip(['08:00 - 18:00'],hours)
        data=trip.model_dump()
        data['days'][0]['date']=date(2026,10,12)
        other=copy.deepcopy(data['days'][0]);other.update(day=2,date=date(2026,10,13))
        data['days'].append(other)
        trip=TripResponse.model_validate(data)
        result=improve_schedule(trip,{'s1':{'attraction'}})
        self.assertEqual(result.days[0].activities[0].time,'08:00 - 18:00')
        self.assertEqual(result.days[1].activities[0].time,'09:00 - 17:00')
        self.assertTrue(any('第 1 天第 1 项' in w and '当日闭馆' in w for w in result.warnings))
        self.assertFalse(any('第 2 天' in w and '当日闭馆' in w for w in result.warnings))
        self.assertEqual(result.total_cost,Decimal('80.00'))


class PriceRoundtripTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_uses_sourced_seasonal_hours_without_more_calls(self):
        def seasonal(output):
            output['days'][0]['activities'][0].update(time='07:30 - 18:00',
                opening_hours=SEASONAL_HOURS,opening_hours_status='reported',
                opening_hours_evidence=SEASONAL_HOURS)
            return output
        fixture=Fixture(decisions=[tool_response()],outputs=[seasonal])
        original=fixture.handle
        def handle(request):
            if request.url.path.endswith('web-search'):
                body=search_response()
                body['data']['webPages']['value'][0]['summary']='西湖 '+SEASONAL_HOURS
                return httpx.Response(200,json=body)
            return original(request)
        settings=SETTINGS.model_copy(update={'max_search_rounds':1,'max_search_calls':1})
        budget=CallBudget(settings)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            trip=await generate_live(REQUEST,ServiceAdapters(settings,client,budget))
        self.assertEqual(trip.days[0].activities[0].time,'08:00 - 17:30')
        self.assertEqual(trip.days[0].activities[0].opening_hours,SEASONAL_HOURS)
        self.assertFalse(trip.demo)
        self.assertIsNone(trip.total_cost)
        self.assertEqual(budget.counts['model_calls'],2)
        self.assertEqual(budget.counts['search_calls'],1)

    async def test_native_price_tool_quotes_support_per_person_total(self):
        def known(output):
            tool=next(m for m in fixture.requests[-1][1]['messages'] if m['role']=='tool')
            payload=json.loads(tool['content'])
            self.assertTrue(payload['price_clues'])
            self.assertFalse(payload['price_follow_up_needed'])
            self.assertEqual(payload['remaining_search_calls'],0)
            self.assertEqual(payload['coverage_hint']['trip_days'],REQUEST.days)
            self.assertEqual(payload['coverage_hint']['attraction_source_count'],0)
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
            trip=await generate_live(REQUEST,ServiceAdapters(settings,client,budget))
        self.assertIsNone(trip.total_cost)
        self.assertTrue(any('未取得可用价格依据' in w for w in trip.warnings))
        self.assertEqual(budget.counts['search_calls'],1)
        self.assertEqual(budget.counts['model_calls'],2)


if __name__ == '__main__':unittest.main()
