"""Regression tests for value-free model diagnostics and bounded format repair."""
import json
import unittest

import httpx
from pydantic import BaseModel, ConfigDict

from backend.agent import generate_live
from backend.providers import ProviderError, ServiceAdapters
from backend.test_agent import Fixture, REQUEST, SETTINGS
from backend.test_support import check_local_socketpair


def setUpModule():
    check_local_socketpair()


class Output(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str


def response(content=None, **changes):
    body = {'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': content}}]}
    body.update(changes)
    return body


class DiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def fail(self, body, **kwargs):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))) as client:
            adapter = ServiceAdapters(SETTINGS, client)
            with self.assertLogs('backend.providers', level='WARNING') as log:
                with self.assertRaises(ProviderError) as caught:
                    await adapter.complete([{'role':'user','content':'test'}], **kwargs)
            return caught.exception, '\n'.join(log.output)

    async def test_invalid_json_classified_without_body(self):
        exc, log = await self.fail(response('PRIVATE-BODY invalid json'), output_model=Output)
        self.assertEqual(exc.code, 'INVALID_MODEL_OUTPUT')
        self.assertIn('OUTPUT_JSON', exc.message)
        self.assertIn('phase=OUTPUT_JSON', log)
        self.assertNotIn('PRIVATE-BODY', log + exc.message)

    async def test_wrong_field_type_classified_and_value_redacted(self):
        exc, log = await self.fail(response(json.dumps({'title': {'secret':'PRIVATE-VALUE'}})), output_model=Output)
        self.assertIn('OUTPUT_SCHEMA', exc.message)
        self.assertIn('title', exc.message)
        self.assertNotIn('PRIVATE-VALUE', log + exc.message)
        self.assertNotIn('test-key', log + exc.message)

    async def test_unknown_field_names_are_never_reported(self):
        exc, log = await self.fail(response(json.dumps({'title':'ok','PRIVATE-FIELD-NAME':'PRIVATE-VALUE'})), output_model=Output)
        self.assertIn('unknown_field', exc.message)
        self.assertNotIn('PRIVATE-FIELD-NAME', log + exc.message)
        self.assertNotIn('PRIVATE-VALUE', log + exc.message)

    async def test_missing_fields_and_malformed_protocol_separate(self):
        exc, _ = await self.fail(response('{}'), output_model=Output)
        self.assertIn('OUTPUT_SCHEMA', exc.message)
        self.assertIn('title', exc.message)
        exc, log = await self.fail({'choices': []})
        self.assertIn('MODEL_PROTOCOL', exc.message)
        self.assertNotIn('title', exc.message)

    async def test_malformed_tool_argument_json_separate(self):
        tools=[{'type':'function','function':{'name':'search_travel_info','parameters':{'type':'object'}}}]
        body={'choices':[{'finish_reason':'tool_calls','message':{'role':'assistant','content':None,
            'tool_calls':[{'id':'call1','type':'function','function':{'name':'search_travel_info','arguments':'PRIVATE-BODY not json'}}]}}]}
        exc, log = await self.fail(body, tools=tools)
        self.assertIn('TOOL_ARGUMENT_JSON', exc.message)
        self.assertNotIn('PRIVATE-BODY', log + exc.message)

    async def test_invalid_usage_is_distinct(self):
        exc, log = await self.fail(response('ok',usage={'total_tokens':'PRIVATE-VALUE'}))
        self.assertIn('MODEL_USAGE', exc.message)
        self.assertNotIn('PRIVATE-VALUE', log + exc.message)

    async def test_stop_with_native_tools_passes_forced_call_and_agent_search(self):
        from backend.test_agent import tool_response, text_response
        body=tool_response()
        body['choices'][0]['finish_reason']='stop'
        fixture=Fixture(decisions=[body,text_response('资料足够')])
        async with httpx.AsyncClient(transport=httpx.MockTransport(fixture.handle)) as client:
            adapter=ServiceAdapters(SETTINGS,client)
            trip=await generate_live(REQUEST,adapter)
        self.assertFalse(trip.demo)
        self.assertEqual(adapter.budget.counts['search_calls'],1)
        tools=[m for m in fixture.requests[-1][1]['messages'] if m['role']=='tool']
        self.assertEqual(tools[0]['tool_call_id'],'call1')

    async def test_stop_does_not_bypass_tool_security_checks(self):
        from backend.test_agent import tool_response
        tools=[{'type':'function','function':{'name':'search_travel_info','parameters':{'type':'object'}}}]
        for invalid in ('name','id','arguments','duplicate','none'):
            body=tool_response()
            choice=body['choices'][0]
            choice['finish_reason']='stop'
            call=choice['message']['tool_calls'][0]
            options={'tools':tools}
            if invalid=='name': call['function']['name']='unregistered'
            elif invalid=='id': call['id']=''
            elif invalid=='arguments': call['function']['arguments']='not json'
            elif invalid=='duplicate': choice['message']['tool_calls'] *= 2
            elif invalid=='none': options['tool_choice']='none'
            with self.subTest(invalid=invalid):
                exc,_=await self.fail(body,**options)
                self.assertEqual(exc.code,'INVALID_MODEL_OUTPUT')

    async def test_tool_marker_without_payload_still_rejected(self):
        body=response('ok')
        body['choices'][0]['finish_reason']='tool_calls'
        exc,_=await self.fail(body)
        self.assertIn('tool_calls_missing',exc.message)

    async def test_repair_uses_safe_specific_feedback_and_same_budget(self):
        fixture=Fixture(outputs=['PRIVATE-BODY not json'])
        async with httpx.AsyncClient(transport=httpx.MockTransport(fixture.handle)) as client:
            adapter=ServiceAdapters(SETTINGS,client)
            with self.assertLogs('backend.providers',level='WARNING'):
                trip=await generate_live(REQUEST,adapter)
        self.assertFalse(trip.demo)
        repair=fixture.requests[-1][1]
        self.assertNotIn('tools',repair)
        feedback=repair['messages'][-1]['content']
        self.assertIn('OUTPUT_JSON',feedback)
        self.assertNotIn('PRIVATE-BODY',feedback)
        self.assertEqual(adapter.budget.counts['model_calls'],4)
        self.assertEqual(adapter.budget.counts['search_calls'],1)


if __name__ == '__main__': unittest.main()
