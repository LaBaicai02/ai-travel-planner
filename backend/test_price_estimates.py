"""Price arithmetic and source association without external requests."""
import unittest
from decimal import Decimal
import httpx
from backend.price_estimates import estimate_from_sources
from backend.agent import GeneratedTrip, assemble_trip, generate_live, _unknown_unverified_facts
from backend.test_planning_quality import hit
from backend.test_agent import Fixture, REQUEST, SETTINGS, output_for
from backend.providers import CallBudget, ServiceAdapters


class EstimateTests(unittest.TestCase):
    def test_average_same_venue_meal_samples_and_range_for_all_travelers(self):
        hits=[hit('s1','九门轩','人均消费80元。'),hit('s2','探店','九门轩人均100至140元。')]
        result=estimate_from_sources('九门轩',hits,3)
        self.assertEqual(result['amount'],'300.00')
        self.assertIn('2条',result['basis'])
        self.assertIn('s1、s2',result['basis'])
        self.assertIn('区间取中点',result['basis'])
        self.assertIn(result['quote'],hits[0].snippet)

    def test_single_ticket_sample_is_estimate_not_market_average(self):
        result=estimate_from_sources('雷峰塔',[hit('s1','雷峰塔 官网','门票40元。')],2)
        self.assertEqual(result['amount'],'80.00')
        self.assertIn('1条',result['basis'])
        self.assertIn('并非全网平均价',result['basis'])

    def test_currency_symbol_and_multi_sample_reference_becomes_average(self):
        hits=[hit('s1','雷峰塔','门票￥40'),hit('s2','雷峰塔','成人票60元')]
        self.assertEqual(estimate_from_sources('雷峰塔',hits,2)['amount'],'100.00')
        output=GeneratedTrip.model_validate(output_for('s1'))
        a=output.days[0].activities[0]
        a.location='雷峰塔';a.source_ids=['s1','s2'];a.estimated_cost='80.00'
        a.cost_status='reference';a.cost_basis='2人门票40元';a.cost_evidence='门票￥40'
        trip=assemble_trip(output,REQUEST,{h.id:h for h in hits},'test',[])
        self.assertEqual(trip.total_cost,Decimal('100.00'))
        self.assertEqual(trip.days[0].activities[0].cost_status,'estimated')

    def test_wrong_venue_branch_and_ambiguous_article_do_not_supply_price(self):
        for title,snippet in [('楼外楼(甲店)','人均80元。'),('推荐','楼外楼(乙店)。另一家餐厅人均80元。')]:
            self.assertIsNone(estimate_from_sources('楼外楼(乙店)',[hit('s1',title,snippet)],2))

    def test_exclude_packages_discounts_addons_units_and_unknown_free(self):
        for text in ['门票免费','门票未知','双人套餐人均80元','学生门票20元','门票40元起','门票40元，不含登塔费',
                     '单品人均30元','每桌人均80元','门票40元，成人票50元','门票50至40元','门票40美元','门票40','每人40元']:
            with self.subTest(text=text):
                self.assertIsNone(estimate_from_sources('雷峰塔',[hit('s1','雷峰塔',text)],2))

    def test_url_dedup_rounding_and_mixed_units(self):
        a=hit('s1','雷峰塔','门票10元');b=hit('s2','雷峰塔','门票20元');c=hit('s3','雷峰塔','门票20元')
        self.assertEqual(estimate_from_sources('雷峰塔',[a,b,c],2)['amount'],'33.34')
        self.assertIn('1条',estimate_from_sources('雷峰塔',[a,a],2)['basis'])
        self.assertIsNone(estimate_from_sources('雷峰塔',[a,hit('s4','雷峰塔','人均消费30元')],2))

    def test_unknown_enriched_without_input_mutation_and_reference_preserved(self):
        output=GeneratedTrip.model_validate(output_for('s1'))
        output.days[0].activities[0].location='雷峰塔'
        e={'s1':hit('s1','雷峰塔','门票40元')}
        trip=assemble_trip(output,REQUEST,e,'test',[])
        self.assertEqual(trip.total_cost,Decimal('80.00'))
        self.assertEqual(trip.days[0].activities[0].cost_status,'estimated')
        self.assertIsNone(output.days[0].activities[0].estimated_cost)
        self.assertTrue(any('不代表全网均价' in w for w in trip.warnings))
        output.days[0].activities[0].estimated_cost='80.00'
        output.days[0].activities[0].cost_status='reference'
        output.days[0].activities[0].cost_basis='2人门票每人40元'
        output.days[0].activities[0].cost_evidence='门票40元'
        self.assertEqual(assemble_trip(output,REQUEST,e,'test',[]).days[0].activities[0].cost_status,'reference')


class RecoveryTests(unittest.TestCase):
    def test_bad_model_quote_is_replaced_by_source_average_with_correct_warning(self):
        output=GeneratedTrip.model_validate(output_for('s1'));a=output.days[0].activities[0]
        a.location='雷峰塔';a.estimated_cost='999.00';a.cost_status='estimated'
        a.cost_basis='模型随意价格';a.cost_evidence='伪造价格999元'
        e={'s1':hit('s1','雷峰塔','门票40元')}
        candidate,warnings=_unknown_unverified_facts(output,REQUEST,e)
        self.assertEqual(candidate.days[0].activities[0].estimated_cost,'80.00')
        self.assertTrue(any('改用网上同地点价格样本估算' in w for w in warnings))
        self.assertFalse(any('已标为未知' in w for w in warnings))
        self.assertEqual(output.days[0].activities[0].estimated_cost,'999.00')


class EstimateRoundtripTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_search_unknown_model_cost_becomes_estimate_without_extra_calls(self):
        fixture=Fixture();original=fixture.handle
        def handle(request):
            response=original(request)
            if request.url.path.endswith('web-search'):
                body=response.json();body['data']['webPages']['value'][0].update(name='西湖',summary='西湖 门票40至60元。')
                return httpx.Response(200,json=body)
            return response
        settings=SETTINGS.model_copy(update={'max_search_rounds':1})
        budget=CallBudget(settings)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            trip=await generate_live(REQUEST,ServiceAdapters(settings,client,budget))
        self.assertEqual(trip.total_cost,Decimal('100.00'))
        self.assertEqual(trip.days[0].activities[0].cost_status,'estimated')
        self.assertEqual(budget.counts['model_calls'],2)
        self.assertEqual(budget.counts['search_calls'],1)


if __name__=='__main__':unittest.main()
