import asyncio
import copy
import json
import unittest
from datetime import timedelta
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from backend.agent import GeneratedTrip, assemble_trip, generate_live
from backend.config import Settings
from backend.main import create_app, run_live
from backend.providers import CallBudget, ProviderError, ServiceAdapters
from backend.schemas import TripRequest
from backend.test_support import check_local_socketpair

REQUEST = TripRequest(destination="杭州", departure_date="2026-10-10", days=1,
                      travelers=2, budget="100", currency="CNY", interests=["自然风光", "地道美食"])
SETTINGS = Settings(mode="live", dashscope_api_key="test-key", bocha_api_key="test-key",
                    llm_base_url="https://model.example/v1", max_upstream_retries=0)


def setUpModule():
    check_local_socketpair()


def tool_response(calls=None):
    calls = calls if calls is not None else [("call1", {"query": "杭州 西湖 餐厅 开放时间", "purpose": "attraction"})]
    return {"choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "content": None,
        "tool_calls": [{"id": cid, "type": "function", "function": {"name": "search_travel_info", "arguments": json.dumps(args)}} for cid, args in calls]}}]}


def text_response(content):
    return {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
            "usage": {"total_tokens": 10}}


def search_response(empty=False):
    return {"code": 200, "data": {"webPages": {"value": [] if empty else [
        {"name": "西湖 官方介绍", "url": "https://travel.example/west-lake", "summary": "西湖 全日开放。门票免费。导览参考价80元/人。"}]}}}


def output_for(source_id):
    return {"days": [{"day": 1, "date": "2026-10-10", "title": "西湖漫步", "activities": [{
        "time": "09:00 - 10:00", "location": "西湖", "description": "建议游览，出发前核实",
        "estimated_cost": None, "cost_status": "unknown", "cost_basis": "", "cost_evidence": None,
        "source_ids": [source_id], "opening_hours": None, "opening_hours_status": "unknown",
        "opening_hours_evidence": None}]}]}


class Fixture:
    def __init__(self, decisions=None, outputs=None, search_status=200, empty=False):
        self.decisions = list(decisions if decisions is not None else [tool_response(), text_response("资料足够")])
        self.outputs = list(outputs or [])
        self.search_status, self.empty = search_status, empty
        self.requests, self.source_id = [], None

    def handle(self, request):
        body = json.loads(request.content)
        self.requests.append((request.url.path, body))
        if request.url.path.endswith('web-search'):
            return httpx.Response(self.search_status, json=search_response(self.empty))
        if 'tools' in body:
            return httpx.Response(200, json=self.decisions.pop(0))
        for message in body['messages']:
            if message['role'] == 'tool':
                for hit in json.loads(message['content']).get('results', []):
                    self.source_id = hit['id']
        content = self.outputs.pop(0) if self.outputs else output_for(self.source_id)
        if callable(content):
            content = content(output_for(self.source_id))
        return httpx.Response(200, json=text_response(content if isinstance(content, str) else json.dumps(content)))


def mutate(field, value):
    def apply(output):
        output['days'][0]['activities'][0][field] = value
        return output
    return apply


class AgentTests(unittest.IsolatedAsyncioTestCase):
    async def execute(self, fixture, settings=SETTINGS, request=REQUEST):
        budget = CallBudget(settings)
        async with httpx.AsyncClient(transport=httpx.MockTransport(fixture.handle)) as client:
            trip = await generate_live(request, ServiceAdapters(settings, client, budget))
        return trip, budget

    async def test_native_roundtrip_and_unknown_cost(self):
        fixture = Fixture()
        trip, budget = await self.execute(fixture)
        self.assertFalse(trip.demo)
        self.assertIsNone(trip.total_cost)
        self.assertEqual(trip.days[0].activities[0].sources[0].id, fixture.source_id)
        self.assertEqual(budget.counts, dict(model_calls=3, search_calls=1, model_http_attempts=3, search_http_attempts=1))
        messages = fixture.requests[-1][1]['messages']
        self.assertEqual(next(m for m in messages if m['role'] == 'tool')['tool_call_id'], 'call1')
        self.assertNotIn('tools', fixture.requests[-1][1])
        self.assertEqual(fixture.requests[0][1]['tool_choice']['function']['name'], 'search_travel_info')
        schema = fixture.requests[-1][1]['response_format']['json_schema']['schema']
        self.assertFalse(schema['additionalProperties'])

    async def test_multiple_calls_all_have_tool_results_partial_failure(self):
        fixture = Fixture(decisions=[tool_response([('a', {'query': '杭州 西湖', 'purpose': 'attraction'}),
            ('b', {'query': '', 'purpose': 'invalid'})]), text_response('完成')])
        trip, budget = await self.execute(fixture)
        messages = fixture.requests[-1][1]['messages']
        tools = [m for m in messages if m['role'] == 'tool']
        self.assertEqual([m['tool_call_id'] for m in tools], ['a', 'b'])
        self.assertEqual(json.loads(tools[1]['content'])['error'], 'INVALID_ARGUMENT')
        self.assertEqual(budget.counts['search_calls'], 1)

    async def test_search_execution_cap_and_round_cap(self):
        calls = [('id' + str(i), {'query': '杭州 西湖', 'purpose': 'attraction'}) for i in range(4)]
        fixture = Fixture(decisions=[tool_response(calls)])
        settings = SETTINGS.model_copy(update={'max_search_calls': 1})
        trip, budget = await self.execute(fixture, settings)
        self.assertEqual(budget.counts['search_calls'], 1)
        tools = [m for m in fixture.requests[-1][1]['messages'] if m['role'] == 'tool']
        self.assertEqual(len(tools), 4)
        self.assertEqual(json.loads(tools[-1]['content'])['error'], 'SEARCH_CALL_LIMIT')
        fixture = Fixture(decisions=[tool_response([('a'+str(i), {'query':'杭州 西湖','purpose':'opening_hours'})]) for i in range(3)])
        trip, budget = await self.execute(fixture)
        self.assertEqual(budget.counts['model_calls'], 4)
        self.assertEqual(budget.counts['search_calls'], 3)

    async def test_one_repair_then_success(self):
        fixture = Fixture(outputs=['not json'])
        trip, budget = await self.execute(fixture)
        self.assertEqual(budget.counts['model_calls'], 4)
        self.assertFalse(trip.demo)
        self.assertNotIn('tools', fixture.requests[-1][1])

    async def test_business_invalid_outputs_rejected_after_one_repair(self):
        cases = [mutate('source_ids', ['fake']), mutate('location', '虚构景点'),
                 mutate('estimated_cost', '-1.00'), mutate('time', '25:00 - 26:00'),
                 mutate('opening_hours', '08:00-22:00')]
        def bad_day(output):
            output['days'][0]['date'] = '2027-01-01'
            return output
        cases.append(bad_day)
        for bad in cases:
            with self.subTest(bad=bad):
                fixture = Fixture(outputs=[bad, bad])
                with self.assertRaises(ProviderError) as caught:
                    await self.execute(fixture)
                self.assertEqual(caught.exception.code, 'INVALID_MODEL_OUTPUT')
                self.assertEqual(len([p for p,b in fixture.requests if 'response_format' in b]), 2)

    async def test_empty_and_all_failed_search(self):
        for fixture in [Fixture(empty=True), Fixture(search_status=500), Fixture(search_status=401)]:
            with self.subTest(status=fixture.search_status), self.assertRaises(ProviderError) as caught:
                await self.execute(fixture)
            self.assertIn(caught.exception.code, ['INSUFFICIENT_EVIDENCE', 'AUTH_FAILED'])
            self.assertFalse(any('response_format' in body for path,body in fixture.requests))

    async def test_partial_search_failure_warns(self):
        fixture = Fixture(decisions=[tool_response([('a',{'query':'杭州 西湖','purpose':'attraction'}),
            ('b',{'query':'杭州 餐厅','purpose':'restaurant'})]), text_response('完成')])
        count = 0
        def handle(request):
            nonlocal count
            if request.url.path.endswith('web-search'):
                count += 1
                if count == 2:
                    return httpx.Response(500,json={})
            return fixture.handle(request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            trip = await generate_live(REQUEST, ServiceAdapters(SETTINGS,client))
        self.assertTrue(any('部分搜索失败' in w for w in trip.warnings))

    async def test_reused_call_id_rejected(self):
        fixture = Fixture(decisions=[tool_response(), tool_response()])
        with self.assertRaises(ProviderError):
            await self.execute(fixture)

    async def test_refusal_truncation_not_repaired(self):
        for finish in ['length', 'content_filter']:
            fixture=Fixture(decisions=[{'choices':[{'finish_reason':finish,'message':{'role':'assistant','content':'x'}}]}])
            with self.subTest(finish=finish), self.assertRaises(ProviderError):
                await self.execute(fixture)
            self.assertEqual(len(fixture.requests),1)

    async def test_model_budget_too_small_no_requests(self):
        fixture=Fixture()
        with self.assertRaises(ProviderError):
            await self.execute(fixture,SETTINGS.model_copy(update={'max_model_calls':2}))
        self.assertEqual(fixture.requests,[])

    async def test_known_cost_opening_hours_and_over_budget(self):
        def known(output):
            a=output['days'][0]['activities'][0]
            a.update(estimated_cost='150.00',cost_status='estimated',cost_basis='2人活动费用估算，依据来源导览80元每人，2人约150元，实际以报价为准',
                     cost_evidence='导览参考价80元/人',opening_hours='全日开放',opening_hours_status='reported',opening_hours_evidence='全日开放')
            return output
        trip,_=await self.execute(Fixture(outputs=[known]))
        self.assertEqual(str(trip.total_cost),'150.00')
        self.assertTrue(any('超过预算' in w for w in trip.warnings))

    async def test_multiple_days_backend_dates_and_non_four_activities(self):
        def days(output):
            output['days'] += [copy.deepcopy(output['days'][0])]
            output['days'][1].update(day=2,date='2026-10-11')
            return output
        trip,_=await self.execute(Fixture(outputs=[days]),request=REQUEST.model_copy(update={'days':2}))
        self.assertEqual(len(trip.days),2)
        self.assertEqual(len(trip.days[0].activities),1)

    async def test_fourteen_days_complete_or_error_never_shortened(self):
        def days(output):
            output['days'] = [dict(copy.deepcopy(output['days'][0]), day=i+1,
                date=(REQUEST.departure_date+timedelta(days=i)).isoformat()) for i in range(14)]
            return output
        trip,_=await self.execute(Fixture(outputs=[days]),request=REQUEST.model_copy(update={'days':14}))
        self.assertEqual(len(trip.days),14)
        self.assertEqual(trip.days[-1].date,REQUEST.departure_date+timedelta(days=13))

    async def test_overlapping_times_and_fake_reference_prices_rejected(self):
        def overlap(output):
            output['days'][0]['activities'] *= 2
            return output
        def fake_price(output):
            a=output['days'][0]['activities'][0]
            a.update(estimated_cost='500.00',cost_status='reference',cost_basis='两人参考价',cost_evidence='导览参考价80元/人')
            return output
        def unrelated_price(output):
            a=output['days'][0]['activities'][0]
            a.update(estimated_cost='100.00',cost_status='estimated',cost_basis='估算',cost_evidence='门票免费')
            return output
        for invalid in [overlap,fake_price,unrelated_price]:
            with self.subTest(invalid=invalid),self.assertRaises(ProviderError) as caught:
                await self.execute(Fixture(outputs=[invalid,invalid]))
            self.assertEqual(caught.exception.code,'INVALID_MODEL_OUTPUT')

    async def test_outer_timeout_cancels_upstream(self):
        cancelled=asyncio.Event()
        async def handler(request):
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.set()
        class Connected:
            async def receive(self):
                await asyncio.sleep(10)
                return {"type": "http.disconnect"}
        settings=SETTINGS.model_copy(update={'trip_timeout_seconds':0.02})
        with self.assertRaises(ProviderError) as caught:
            await run_live(Connected(),REQUEST,settings,'test',httpx.MockTransport(handler))
        self.assertEqual(caught.exception.code,'TOTAL_TIMEOUT')
        self.assertTrue(cancelled.is_set())

    async def test_disconnect_cancels_generation(self):
        class Disconnected:
            async def receive(self): return {"type": "http.disconnect"}
        async def handler(request): await asyncio.sleep(10)
        with self.assertRaises(ProviderError) as caught:
            await run_live(Disconnected(),REQUEST,SETTINGS,'test',httpx.MockTransport(handler))
        self.assertEqual(caught.exception.code,'CLIENT_DISCONNECTED')


class LiveRouteTests(unittest.TestCase):
    def test_full_mock_http_itinerary_endpoint(self):
        fixture=Fixture()
        with TestClient(create_app(SETTINGS,transport=httpx.MockTransport(fixture.handle))) as client:
            self.assertTrue(client.get('/api/health').json()['ready'])
            response=client.post('/api/itineraries',json=REQUEST.model_dump(mode='json'))
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['mode'],'live')
        self.assertEqual(response.json()['days'][0]['activities'][0]['sources'][0]['url'],'https://travel.example/west-lake')
        self.assertIsNone(response.json()['total_cost'])

    def test_error_status_and_no_demo_fallback(self):
        for status in [401,429,500]:
            with self.subTest(status=status):
                fixture=Fixture(search_status=status)
                with TestClient(create_app(SETTINGS,transport=httpx.MockTransport(fixture.handle))) as client, patch('backend.main.generate_demo',side_effect=AssertionError('no demo')):
                    response=client.post('/api/itineraries',json=REQUEST.model_dump(mode='json'))
                    self.assertEqual(response.status_code,502)
                    self.assertTrue(response.json()['error']['request_id'])
                    self.assertNotIn('days',response.json())
                    self.assertEqual(client.app.state.active_trips,0)

    def test_concurrency_rejected_before_upstream(self):
        app=create_app(SETTINGS)
        with TestClient(app) as client:
            app.state.active_trips=SETTINGS.max_concurrent_trips
            response=client.post('/api/itineraries',json=REQUEST.model_dump(mode='json'))
        self.assertEqual(response.status_code,429)
        self.assertEqual(response.json()['error']['code'],'TRIP_BUSY')


if __name__=='__main__': unittest.main()
