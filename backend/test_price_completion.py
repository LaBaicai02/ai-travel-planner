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

    async def test_same_day_attraction_deduplicated_before_price_search_and_total(self):
        trip,budget,searches=await self.run_fixture(repeated=True)
        self.assertEqual(trip.total_cost,Decimal('100.00'))
        self.assertEqual(len(trip.days[0].activities),1)
        self.assertEqual(searches,2)
        self.assertEqual(budget.counts['model_calls'],2)
        self.assertFalse(any('超过预算' in w for w in trip.warnings))
        self.assertTrue(any('重复安排已移除' in w for w in trip.warnings))

    async def test_missing_budget_missing_prices_or_search_failure_keep_unknown(self):
        for mode,limit in [('success',1),('missing',2),('timeout',2)]:
            with self.subTest(mode=mode,limit=limit):
                trip,budget,searches=await self.run_fixture(followup=mode,limit=limit)
                self.assertIsNone(trip.total_cost)
                self.assertEqual(trip.known_cost_subtotal,Decimal('0'))
                activity=trip.days[0].activities[0]
                self.assertIsNone(activity.estimated_cost)
                self.assertEqual(activity.cost_status,'unknown')
                self.assertEqual(activity.cost_basis,'')
                self.assertTrue(any('第1天第1项' in w for w in trip.warnings))
                self.assertTrue(any('未填默认金额' in w for w in trip.warnings))
                self.assertNotIn('每项金额均有',trip.notice)
                self.assertFalse(trip.demo)
                self.assertLessEqual(searches,limit)
                self.assertEqual(budget.counts['model_calls'],2)

    async def test_mixed_costs_preserve_known_subtotal_and_warn_when_over_budget(self):
        from copy import deepcopy
        output=output_for('s1')
        known=deepcopy(output['days'][0]['activities'][0])
        known.update(time='12:00 - 13:00',estimated_cost='160.00',cost_status='reference',
                     cost_basis='2人，每人80元',cost_evidence='导览80元/人')
        output['days'][0]['activities'].append(known)
        e={'s1':hit('s1','西湖','西湖 导览80元/人。')}
        trip=assemble_trip(GeneratedTrip.model_validate(output),REQUEST,e,'test',[])
        settings=SETTINGS.model_copy(update={'max_search_calls':1})
        budget=CallBudget(settings);budget.counts['search_calls']=1
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:self.fail('No search budget'))) as client:
            result=await complete_online_prices(trip,e,ServiceAdapters(settings,client,budget))
        self.assertIsNone(result.total_cost)
        self.assertIsNone(result.days[0].estimated_cost)
        self.assertEqual(result.known_cost_subtotal,Decimal('160.00'))
        self.assertEqual(result.days[0].activities[1].estimated_cost,Decimal('160.00'))
        self.assertTrue(any('超过预算' in w for w in result.warnings))
        self.assertIsNone(trip.days[0].activities[0].estimated_cost)

    async def test_http_endpoint_returns_live_200_without_price_evidence(self):
        from fastapi.testclient import TestClient
        from backend.main import create_app
        fixture=Fixture();original=fixture.handle
        def handle(request):
            if request.url.path.endswith('web-search'):
                body=search_response();body['data']['webPages']['value'][0]['summary']='西湖 风景介绍，无价格资料。'
                return httpx.Response(200,json=body)
            return original(request)
        settings=SETTINGS.model_copy(update={'max_search_calls':1,'max_search_rounds':1})
        with TestClient(create_app(settings,transport=httpx.MockTransport(handle))) as client:
            response=client.post('/api/itineraries',json=REQUEST.model_dump(mode='json'))
        self.assertEqual(response.status_code,200)
        data=response.json()
        self.assertEqual(data['mode'],'live')
        self.assertFalse(data['demo'])
        self.assertIsNone(data['total_cost'])
        self.assertEqual(data['known_cost_subtotal'],'0.00')
        self.assertIsNone(data['days'][0]['activities'][0]['estimated_cost'])
        self.assertNotIn('error',data)

    async def test_optional_search_retry_attempt_limit_keeps_unknown(self):
        trip=assemble_trip(GeneratedTrip.model_validate(output_for('s1')),REQUEST,{'s1':hit('s1','西湖','无报价')},'test',[])
        settings=SETTINGS.model_copy(update={'max_upstream_retries':1,'max_search_http_attempts':1})
        budget=CallBudget(settings)
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(500,json={}))) as client:
            result=await complete_online_prices(trip,{},ServiceAdapters(settings,client,budget))
        self.assertIsNone(result.total_cost)
        self.assertEqual(budget.counts['search_http_attempts'],1)
        self.assertTrue(any('价格定向搜索失败' in w for w in result.warnings))

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
