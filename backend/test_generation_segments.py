"""Bounded long trips and evidence repair; HTTP mocks only."""
import copy
import json
import unittest
from datetime import timedelta
from decimal import Decimal
import httpx
from backend.agent import generate_live
from backend.providers import CallBudget, ProviderError, ServiceAdapters
from backend.test_agent import Fixture, REQUEST, SETTINGS


def segment(offset, count, known=False):
    def apply(output):
        day = output['days'][0]
        if known:
            day['activities'][0].update(estimated_cost='160.00',cost_status='reference',
                cost_basis='2人，每人80元',cost_evidence='导览参考价80元/人')
        output['days'] = [dict(copy.deepcopy(day),day=i+1,
            date=(REQUEST.departure_date+timedelta(days=offset+i)).isoformat()) for i in range(count)]
        return output
    return apply


class SegmentTests(unittest.IsolatedAsyncioTestCase):
    async def run_fixture(self, fixture, days=14, settings=SETTINGS, budget=None):
        budget = budget or CallBudget(settings)
        async with httpx.AsyncClient(transport=httpx.MockTransport(fixture.handle)) as client:
            trip = await generate_live(REQUEST.model_copy(update={'days':days}),ServiceAdapters(settings,client,budget))
        return trip,budget

    async def test_boundaries_dates_numbers_money_and_prior_locations(self):
        for count in (5,6,10,14):
            with self.subTest(days=count):
                fixture=Fixture(outputs=[segment(i,min(5,count-i),True) for i in range(0,count,5)])
                trip,budget=await self.run_fixture(fixture,days=count)
                self.assertEqual([d.day for d in trip.days],list(range(1,count+1)))
                self.assertEqual(trip.total_cost,Decimal(160*count))
                self.assertEqual(budget.counts['model_calls'],(count+4)//5+1 if count>10 else (count+4)//5+2)
                self.assertEqual(budget.counts['search_calls'],1)
                self.assertTrue(any('重复地点' in w for w in trip.warnings) if count>1 else True)
                generations=[b for _,b in fixture.requests if 'response_format' in b]
                for i,b in enumerate(generations):
                    instruction=json.loads(b['messages'][-1]['content'])
                    self.assertEqual(instruction['segment_request']['days'],min(5,count-i*5))
                    self.assertEqual(instruction['segment_request']['departure_date'],(REQUEST.departure_date+timedelta(days=i*5)).isoformat())
                    self.assertEqual(instruction['previous_locations'],[] if i==0 else ['西湖'])
                    self.assertNotIn('tools',b)

    async def test_small_budget_rejected_before_paid_search(self):
        for field in ('max_model_calls','max_model_http_attempts'):
            fixture=Fixture()
            settings=SETTINGS.model_copy(update={field:4})
            with self.subTest(field=field), self.assertRaises(ProviderError) as caught:
                await self.run_fixture(fixture,settings=settings)
            self.assertEqual(caught.exception.code,'CALL_LIMIT')
            self.assertEqual(fixture.requests,[])

    async def test_one_repair_shared_across_all_segments(self):
        fixture=Fixture(outputs=['bad JSON',segment(0,5),'bad JSON'])
        budget=CallBudget(SETTINGS)
        with self.assertRaises(ProviderError) as caught:
            await self.run_fixture(fixture,budget=budget)
        self.assertEqual(caught.exception.code,'INVALID_MODEL_OUTPUT')
        self.assertEqual(budget.counts['model_calls'],4)
        self.assertEqual(len([b for _,b in fixture.requests if 'response_format' in b]),3)

    async def test_timeout_never_returns_partial_or_retries(self):
        fixture=Fixture(outputs=[segment(0,5)])
        original=fixture.handle
        def handle(request):
            body=json.loads(request.content)
            if 'response_format' in body and any('response_format' in b for _,b in fixture.requests):
                raise httpx.ReadTimeout('mock',request=request)
            return original(request)
        fixture.handle=handle
        budget=CallBudget(SETTINGS)
        with self.assertRaises(ProviderError) as caught:
            await self.run_fixture(fixture,budget=budget)
        self.assertEqual(caught.exception.code,'UPSTREAM_TIMEOUT')
        self.assertEqual(budget.counts['model_http_attempts'],3)

    async def test_shared_deadline_stops_next_segment(self):
        now=[0.0]
        budget=CallBudget(SETTINGS,clock=lambda:now[0])
        def expired(output):
            now[0]=121
            return segment(0,5)(output)
        fixture=Fixture(outputs=[expired])
        with self.assertRaises(ProviderError) as caught:
            await self.run_fixture(fixture,budget=budget)
        self.assertEqual(caught.exception.code,'TOTAL_TIMEOUT')
        self.assertEqual(budget.counts['model_http_attempts'],2)

    async def test_unverified_price_becomes_unknown_without_paid_repair(self):
        def wrong(output):
            output['days'][0]['activities'][0].update(estimated_cost='80.00',cost_status='reference',
                cost_basis='来源参考价',cost_evidence='原文没有的价格80元')
            return output
        fixture=Fixture(outputs=[wrong])
        trip,budget=await self.run_fixture(fixture,days=1)
        self.assertIsNone(trip.total_cost)
        self.assertEqual(trip.days[0].activities[0].cost_status,'unknown')
        self.assertTrue(any('未通过来源证据校验' in w for w in trip.warnings))
        self.assertEqual(budget.counts['model_calls'],3)  # Two search decisions plus generation.
        self.assertEqual(len([b for _,b in fixture.requests if 'response_format' in b]),1)


if __name__ == '__main__': unittest.main()
