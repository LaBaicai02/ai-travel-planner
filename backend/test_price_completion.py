"""Only online evidence may complete costs; never substitute product defaults."""
import unittest
from decimal import Decimal
import httpx
from backend.agent import GeneratedTrip, assemble_trip, generate_live
from backend.price_completion import complete_online_prices, searched_price
from backend.providers import CallBudget, ProviderError, ServiceAdapters
from backend.test_agent import Fixture, REQUEST, SETTINGS, output_for, search_response
from backend.test_planning_quality import hit


class PriceSelectionTests(unittest.TestCase):
    def test_free_requires_explicit_ordinary_admission_not_default_zero(self):
        activity={'location':'西湖','description':'游览'}
        self.assertIsNone(searched_price(activity,[hit('s1','西湖','景色优美')],2))
        self.assertEqual(searched_price(activity,[hit('s1','西湖','门票免费')],2)[0]['amount'],'0.00')
        for description,quote in [('乘船游览','门票免费'),('游览','学生门票免费'),('游览','部分区域免费开放'),('游览','不免费开放')]:
            with self.subTest(description=description,quote=quote):
                self.assertIsNone(searched_price(dict(activity,description=description),[hit('s1','西湖',quote)],2))

    def test_price_links_are_actual_samples_even_after_irrelevant_hits(self):
        activity={'location':'西湖','description':'游览'}
        hits=[hit(f's{i}','西湖','无价格资料') for i in range(6)]
        hits.append(hit('price','西湖','门票40元'))
        price,sources,status=searched_price(activity,hits,2)
        self.assertEqual(price['amount'],'80.00')
        self.assertEqual([h.id for h in sources],['price'])
        self.assertEqual(status,'estimated')


class CompletionTests(unittest.IsolatedAsyncioTestCase):
    async def run_fixture(self, *, followup='success', limit=2, repeated=False):
        def repeat(output):
            if repeated:
                from copy import deepcopy
                other=deepcopy(output['days'][0]['activities'][0]);other['time']='12:00 - 13:00'
                output['days'][0]['activities'].append(other)
            return output
        fixture=Fixture(outputs=[repeat]);original=fixture.handle;searches=0
        def handle(request):
            nonlocal searches
            if request.url.path.endswith('web-search'):
                searches+=1
                if searches>1 and followup=='timeout':raise httpx.ReadTimeout('mock',request=request)
                if searches>1 and followup=='auth':return httpx.Response(401,json={})
                body=search_response();row=body['data']['webPages']['value'][0]
                row.update(name='西湖 官方介绍',summary='西湖风景介绍，无明确报价。')
                if searches>1 and followup=='success':
                    row.update(url='https://travel.example/prices',summary='西湖门票40至60元。')
                return httpx.Response(200,json=body)
            return original(request)
        settings=SETTINGS.model_copy(update={'max_search_rounds':1,'max_search_calls':limit})
        budget=CallBudget(settings)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            trip=await generate_live(REQUEST,ServiceAdapters(settings,client,budget))
        return trip,budget,searches

    async def test_followup_price_search_completes_every_cost_and_total_without_model_retry(self):
        trip,budget,searches=await self.run_fixture()
        self.assertEqual(trip.total_cost,Decimal('100.00'))
        self.assertEqual(trip.days[0].estimated_cost,trip.total_cost)
        self.assertEqual(trip.days[0].activities[0].cost_status,'estimated')
        self.assertIn('https://travel.example/prices',[str(s.url) for s in trip.days[0].activities[0].sources])
        self.assertIn('https://travel.example/west-lake',[str(s.url) for s in trip.days[0].activities[0].sources])
        self.assertFalse(any('超过预算' in w for w in trip.warnings))
        self.assertFalse(any('完整总价不可计算' in w for w in trip.warnings))
        self.assertEqual(budget.counts['model_calls'],2)
        self.assertEqual(searches,2)
        self.assertEqual(budget.counts['search_calls'],2)

    async def test_same_place_reuses_price_search_and_sums_each_visit(self):
        trip,budget,searches=await self.run_fixture(repeated=True)
        self.assertEqual(trip.total_cost,Decimal('200.00'))
        self.assertEqual(searches,2)
        self.assertTrue(any('超过预算' in w for w in trip.warnings))

    async def test_missing_budget_missing_prices_or_search_failure_never_default(self):
        for mode,limit in [('success',1),('missing',2),('timeout',2)]:
            with self.subTest(mode=mode,limit=limit),self.assertRaises(ProviderError) as caught:
                await self.run_fixture(followup=mode,limit=limit)
            self.assertEqual(caught.exception.code,'PRICE_EVIDENCE_MISSING')
            self.assertIn('第1天第1项',caught.exception.message)
            self.assertIn('未填默认金额',caught.exception.message)

    async def test_auth_failure_remains_explicit(self):
        with self.assertRaises(ProviderError) as caught:await self.run_fixture(followup='auth')
        self.assertEqual(caught.exception.code,'AUTH_FAILED')

    async def test_deadline_applies_before_followup(self):
        trip=assemble_trip(GeneratedTrip.model_validate(output_for('s1')),REQUEST,{'s1':hit('s1','西湖','无报价')},'test',[])
        now=[0];budget=CallBudget(SETTINGS,clock=lambda:now[0]);now[0]=121
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: self.fail('No HTTP after deadline'))) as client:
            with self.assertRaises(ProviderError) as caught:
                await complete_online_prices(trip,{},ServiceAdapters(SETTINGS,client,budget))
        self.assertEqual(caught.exception.code,'TOTAL_TIMEOUT')
        self.assertEqual(budget.counts['search_calls'],0)


if __name__=='__main__':unittest.main()
