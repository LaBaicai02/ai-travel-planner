"""Deterministic clock and HTTP mocks; no sleeps or external calls."""
import json
import unittest

import httpx

from backend.agent import GeneratedTrip, _generate_segment, assemble_trip, generate_live
from backend.price_completion import complete_online_prices
from backend.providers import CallBudget, ProviderError, ServiceAdapters
from backend.test_agent import Fixture, REQUEST, SETTINGS, output_for, text_response, tool_response
from backend.test_itinerary_validation import evidence
from backend.test_planning_quality import hit


class TimeReserveTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_search_stops_optional_calls_and_replies_to_every_tool_id(self):
        now = [0]
        calls = [(f'call{i}', {'query': '杭州景点', 'purpose': 'attraction'}) for i in range(3)]
        fixture = Fixture(decisions=[tool_response(calls)])
        budget = CallBudget(SETTINGS, clock=lambda: now[0])
        def handle(request):
            response = fixture.handle(request)
            if request.url.path.endswith('web-search'):
                now[0] = 40
            return response
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            trip = await generate_live(REQUEST, ServiceAdapters(SETTINGS, client, budget))
        self.assertFalse(trip.demo)
        self.assertEqual(budget.counts['model_calls'], 2)
        self.assertEqual(budget.counts['search_calls'], 1)
        messages = fixture.requests[-1][1]['messages']
        replies = [m for m in messages if m['role'] == 'tool']
        self.assertEqual([m['tool_call_id'] for m in replies], ['call0', 'call1', 'call2'])
        self.assertEqual([json.loads(m['content']).get('error') for m in replies],
                         [None, 'SEARCH_TIME_RESERVED', 'SEARCH_TIME_RESERVED'])
        self.assertTrue(any('预留时间' in w for w in trip.warnings))

    async def test_valid_draft_skips_meal_repair_when_time_is_low(self):
        now = [0]
        budget = CallBudget(SETTINGS, clock=lambda: now[0])
        def handle(request):
            now[0] = 90
            return httpx.Response(200, json=text_response(json.dumps(output_for('s1'))))
        e = evidence()
        e.update({'r1': hit('r1', '梅园餐厅', '梅园餐厅'), 'r2': hit('r2', '湖畔饭店', '湖畔饭店')})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            candidate, repaired, warnings = await _generate_segment(
                REQUEST, ServiceAdapters(SETTINGS, client, budget), e, [{'role': 'user', 'content': 'test'}], True,
                {'r1': {'restaurant'}, 'r2': {'restaurant'}})
        self.assertFalse(repaired)
        self.assertTrue(any('跳过可选餐次' in w for w in warnings))
        self.assertFalse(assemble_trip(candidate, REQUEST, e, 'test', []).demo)
        self.assertEqual(budget.counts['model_calls'], 1)

    async def test_invalid_core_output_is_not_returned_when_repair_time_is_low(self):
        now = [0]
        budget = CallBudget(SETTINGS, clock=lambda: now[0])
        output = output_for('invented')
        def handle(request):
            now[0] = 90
            return httpx.Response(200, json=text_response(json.dumps(output)))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with self.assertRaises(ProviderError) as caught:
                await _generate_segment(REQUEST, ServiceAdapters(SETTINGS, client, budget), evidence(), [{'role': 'user', 'content': 'test'}], True)
        self.assertEqual(caught.exception.code, 'INVALID_MODEL_OUTPUT')
        self.assertIn('停止修复', caught.exception.message)
        self.assertEqual(budget.counts['model_calls'], 1)

    async def test_optional_prices_skip_http_with_low_time_and_keep_unknown(self):
        now = [0]
        budget = CallBudget(SETTINGS, clock=lambda: now[0])
        now[0] = 112
        trip = assemble_trip(GeneratedTrip.model_validate(output_for('s1')), REQUEST, evidence(), 'test', [])
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: self.fail('No optional HTTP'))) as client:
            result = await complete_online_prices(trip, {}, ServiceAdapters(SETTINGS, client, budget))
        self.assertFalse(result.demo)
        self.assertIsNone(result.total_cost)
        self.assertEqual(budget.counts['search_calls'], 0)
        self.assertTrue(any('跳过可选价格' in w for w in result.warnings))
