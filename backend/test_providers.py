"""HTTP mocks only: no paid requests, no DNS/network provider connections."""
import asyncio
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import patch

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError
from backend.config import Settings
from backend.providers import CallBudget, ProviderError, ServiceAdapters, _retry_after
from backend.test_support import check_local_socketpair


def setUpModule():
    check_local_socketpair()


def settings(**overrides):
    return Settings(**{
        "mode": "live", "dashscope_api_key": "mock-llm-secret", "bocha_api_key": "mock-search-secret",
        "llm_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", **overrides,
    })


def model_body(content="完成", calls=None, finish=None, **message_fields):
    message = {"role": "assistant", "content": content, **message_fields}
    if calls is not None:
        message["tool_calls"] = calls
    return {"choices": [{"message": message, "finish_reason": finish or ("tool_calls" if calls else "stop")}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}}


def search_body(items=None):
    return {"code": 200, "data": {"webPages": {"value": items or []}}}


TOOLS = [{"type": "function", "function": {"name": "search_travel_info",
          "description": "搜索旅行信息", "parameters": {"type": "object", "properties": {
              "query": {"type": "string"}}, "required": ["query"], "additionalProperties": False}}}]
FORCED = {"type": "function", "function": {"name": "search_travel_info"}}
CALL = {"id": "call_1", "type": "function",
        "function": {"name": "search_travel_info", "arguments": '{"query":"杭州西湖 开放时间"}'}}


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str
    count: int


class MockTests(unittest.IsolatedAsyncioTestCase):
    async def adapter(self, handler, config=None, **kwargs):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
        self.addAsyncCleanup(client.aclose)
        return ServiceAdapters(config or settings(), client, **kwargs)

    async def assert_error(self, adapter, provider, code, **kwargs):
        with self.assertRaises(ProviderError) as caught:
            if provider == "model":
                await adapter.complete([{"role": "user", "content": "测试"}], **kwargs)
            else:
                await adapter.search("杭州景点")
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn("mock-llm-secret", str(caught.exception))
        self.assertNotIn("mock-search-secret", str(caught.exception))
        return caught.exception

    async def test_model_protocol_and_native_tool_calls_without_execution(self):
        requests = []
        def handler(request):
            requests.append(request)
            self.assertEqual(str(request.url), "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions")
            self.assertEqual(request.headers["Authorization"], "Bearer mock-llm-secret")
            body = json.loads(request.content)
            self.assertEqual(body["model"], "qwen3.7-plus")
            self.assertFalse(body["enable_thinking"] or body["stream"])
            self.assertEqual(body["tools"], TOOLS)
            self.assertEqual(body["tool_choice"], FORCED)
            self.assertNotIn("max_tokens", body)
            self.assertLessEqual(request.extensions["timeout"]["read"], 45)
            return httpx.Response(200, json=model_body(None, [CALL]))
        adapter = await self.adapter(handler)
        result = await adapter.complete([{"role": "user", "content": "杭州旅行"}], tools=TOOLS, tool_choice=FORCED)
        self.assertEqual(result.tool_calls[0].id, "call_1")
        self.assertEqual(result.tool_calls[0].arguments["query"], "杭州西湖 开放时间")
        self.assertEqual(result.message["tool_calls"], [CALL])
        self.assertEqual(result.usage["total_tokens"], 7)
        self.assertEqual(len(requests), 1)
        self.assertEqual(adapter.budget.counts["search_http_attempts"], 0)

    async def test_workspace_endpoint_and_tool_result_messages_preserved(self):
        messages = [{"role": "assistant", "content": None, "tool_calls": [CALL]},
                    {"role": "tool", "tool_call_id": "call_1", "content": '{"results":[]}'}]
        def handler(request):
            self.assertEqual(str(request.url), "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions")
            self.assertEqual(json.loads(request.content)["messages"], messages)
            return httpx.Response(200, json=model_body())
        adapter = await self.adapter(handler, settings(llm_base_url="https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/"))
        await adapter.complete(messages)

    async def test_json_schema_request_and_local_validation(self):
        def handler(request):
            body = json.loads(request.content)
            fmt = body["response_format"]
            self.assertEqual(fmt["type"], "json_schema")
            self.assertTrue(fmt["json_schema"]["strict"])
            self.assertEqual(fmt["json_schema"]["schema"], Output.model_json_schema())
            self.assertNotIn("tools", body)
            self.assertNotIn("max_tokens", body)
            return httpx.Response(200, json=model_body('{"title":"杭州","count":2}'))
        adapter = await self.adapter(handler)
        result = await adapter.complete([{"role": "user", "content": "JSON 行程"}], output_model=Output)
        self.assertEqual(result.parsed, Output(title="杭州", count=2))

    async def test_invalid_json_and_schema_are_not_retried(self):
        for content in ['not JSON', '{"title":"杭州","count":"2"}', '{"title":"杭州"}',
                        '{"title":"杭州","count":2,"extra":1}', '{"title":"杭州","count":NaN}']:
            with self.subTest(content=content):
                adapter = await self.adapter(lambda r: httpx.Response(200, json=model_body(content)))
                await self.assert_error(adapter, "model", "INVALID_MODEL_OUTPUT", output_model=Output)
                self.assertEqual(adapter.budget.counts["model_http_attempts"], 1)

    async def test_model_empty_truncated_refused_and_malformed(self):
        cases = [(model_body(""), "MODEL_EMPTY"), (model_body(None), "MODEL_EMPTY"),
                 (model_body("part", finish="length"), "MODEL_TRUNCATED"),
                 (model_body(None, finish="content_filter"), "MODEL_REFUSED"),
                 (model_body(None, refusal="policy"), "MODEL_REFUSED"),
                 ({"choices": []}, "INVALID_MODEL_OUTPUT"),
                 (model_body("ok", finish="unexpected"), "INVALID_MODEL_OUTPUT"),
                 (model_body(None, calls=[{**CALL, "function": {"name": "unknown", "arguments": "{}"}}]), "INVALID_MODEL_OUTPUT"),
                 (model_body(None, calls=[CALL, CALL]), "INVALID_MODEL_OUTPUT"),
                 (model_body(None, calls=[{**CALL, "function": {"name": "search_travel_info", "arguments": "[]"}}]), "INVALID_MODEL_OUTPUT")]
        for body, code in cases:
            with self.subTest(code=code, body=body):
                adapter = await self.adapter(lambda r: httpx.Response(200, json=body))
                await self.assert_error(adapter, "model", code, tools=TOOLS)
                self.assertEqual(adapter.budget.counts["model_http_attempts"], 1)

    async def test_forced_function_must_be_returned(self):
        adapter = await self.adapter(lambda r: httpx.Response(200, json=model_body("natural text")))
        await self.assert_error(adapter, "model", "INVALID_MODEL_OUTPUT", tools=TOOLS, tool_choice=FORCED)

    async def test_search_protocol_and_normalization(self):
        items = [{"name": "西湖官网", "url": "https://example.com/lake#hours", "summary": "开园信息" + "字" * 1600, "datePublished": "2026-10-08"},
                 {"name": "重复", "url": "https://example.com/lake", "snippet": "重复"},
                 {"name": "餐厅", "url": "https://example.com/food", "summary": "", "snippet": "餐厅摘要"},
                 {"name": "危险", "url": "javascript:alert(1)"},
                 {"name": "凭据", "url": "https://user:pass@example.com"},
                 {"url": "https://example.com/no-name"}, 3]
        def handler(request):
            self.assertEqual(str(request.url), "https://api.bocha.cn/v1/web-search")
            self.assertEqual(request.headers["Authorization"], "Bearer mock-search-secret")
            self.assertEqual(json.loads(request.content), {"query": "杭州景点", "freshness": "noLimit", "summary": True, "count": 5})
            self.assertLessEqual(request.extensions["timeout"]["read"], 10)
            return httpx.Response(200, json=search_body(items))
        adapter = await self.adapter(handler)
        one = await adapter.search("  杭州景点 ")
        two = await adapter.search("杭州景点")
        self.assertEqual(one.query, "杭州景点")
        self.assertEqual(len(one.results), 2)
        self.assertEqual(str(one.results[0].url), "https://example.com/lake")
        self.assertEqual(one.results[0].id, two.results[0].id)
        self.assertEqual(len(one.results[0].snippet), 1500)
        self.assertEqual(one.results[0].published_at, "2026-10-08")
        self.assertIsNotNone(one.results[0].retrieved_at.tzinfo)
        self.assertEqual(one.results[1].snippet, "餐厅摘要")
        self.assertEqual(len(one.warnings), 2)

    async def test_search_empty_is_explicit_and_not_synthetic(self):
        adapter = await self.adapter(lambda r: httpx.Response(200, json=search_body()))
        result = await adapter.search("不存在的查询")
        self.assertEqual(result.results, [])
        self.assertTrue(result.warnings)
        self.assertEqual(adapter.budget.counts["search_http_attempts"], 1)

    async def test_search_result_cap(self):
        adapter = await self.adapter(lambda r: httpx.Response(200, json=search_body([
            {"name": str(i), "url": f"https://example.com/{i}"} for i in range(20)])))
        self.assertEqual(len((await adapter.search("杭州")).results), 5)

    async def test_search_malformed_not_empty(self):
        for body in [{}, {"code": 200}, {"code": 200, "data": None}, {"code": "200", "data": {}},
                     {"code": 200, "data": {"webPages": {"value": {}}}},
                     search_body([{"name": "无URL"}])]:
            with self.subTest(body=body):
                adapter = await self.adapter(lambda r: httpx.Response(200, json=body))
                await self.assert_error(adapter, "search", "INVALID_RESPONSE")
                self.assertEqual(adapter.budget.counts["search_http_attempts"], 1)

    async def test_auth_failures_do_not_retry_both_providers(self):
        for provider in ["model", "search"]:
            for status in [401, 403]:
                with self.subTest(provider=provider, status=status):
                    adapter = await self.adapter(lambda r: httpx.Response(status, json={"error": {"message": "mock-llm-secret"}}))
                    error = await self.assert_error(adapter, provider, "AUTH_FAILED")
                    self.assertFalse(error.retryable)
                    self.assertEqual(adapter.budget.counts[provider + "_http_attempts"], 1)

    async def test_transient_retry_success_both_providers(self):
        for provider in ["model", "search"]:
            for status in [429, 500, 503]:
                with self.subTest(provider=provider, status=status):
                    attempts, delays = [], []
                    def handler(request):
                        attempts.append(request)
                        return httpx.Response(status, headers={"Retry-After": "0"}, json={}) if len(attempts) == 1 else httpx.Response(200, json=model_body() if provider == "model" else search_body())
                    async def sleep(delay):
                        delays.append(delay)
                    adapter = await self.adapter(handler, sleep=sleep)
                    if provider == "model":
                        await adapter.complete([{"role": "user", "content": "test"}])
                    else:
                        await adapter.search("杭州")
                    self.assertEqual(len(attempts), 2)
                    self.assertEqual(delays, [0])
                    self.assertEqual(adapter.budget.counts[provider + "_calls"], 1)
                    self.assertEqual(adapter.budget.counts[provider + "_http_attempts"], 2)

    async def test_retry_limit_and_disabled_retries(self):
        for provider in ["model", "search"]:
            for retries in [0, 1, 3]:
                with self.subTest(provider=provider, retries=retries):
                    adapter = await self.adapter(lambda r: httpx.Response(503, headers={"Retry-After": "0"}, json={}), settings(max_upstream_retries=retries))
                    await self.assert_error(adapter, provider, "UPSTREAM_UNAVAILABLE")
                    self.assertEqual(adapter.budget.counts[provider + "_http_attempts"], retries + 1)

    async def test_timeouts_and_network_failures_retry_with_limit(self):
        for provider in ["model", "search"]:
            for exception, code in [(httpx.ReadTimeout, "UPSTREAM_TIMEOUT"), (httpx.ConnectError, "NETWORK_ERROR")]:
                with self.subTest(provider=provider, code=code):
                    def handler(request):
                        raise exception("secret upstream detail", request=request)
                    async def sleep(delay):
                        pass
                    adapter = await self.adapter(handler, sleep=sleep)
                    error = await self.assert_error(adapter, provider, code)
                    self.assertNotIn("secret upstream detail", str(error))
                    self.assertEqual(adapter.budget.counts[provider + "_http_attempts"], 2)

    async def test_entire_attempt_timeout_enforced_for_both_providers(self):
        async def slow(request):
            await asyncio.sleep(1)
            raise AssertionError("attempt should be cancelled")
        for provider in ["model", "search"]:
            adapter = await self.adapter(slow, settings(llm_timeout_seconds=0.01, search_timeout_seconds=0.01, max_upstream_retries=0))
            await self.assert_error(adapter, provider, "UPSTREAM_TIMEOUT")

    async def test_total_deadline_cancels_request(self):
        async def slow(request):
            await asyncio.sleep(1)
        adapter = await self.adapter(slow, settings(trip_timeout_seconds=0.01))
        await self.assert_error(adapter, "model", "TOTAL_TIMEOUT")
        self.assertEqual(adapter.budget.counts["model_http_attempts"], 1)

    async def test_retry_after_cannot_exceed_total_deadline(self):
        adapter = await self.adapter(lambda r: httpx.Response(429, headers={"Retry-After": "999"}, json={}))
        await self.assert_error(adapter, "search", "TOTAL_TIMEOUT")
        self.assertEqual(adapter.budget.counts["search_http_attempts"], 1)

    async def test_shared_logical_and_http_attempt_budgets(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, json=search_body() if "web-search" in str(request.url) else model_body())
        cfg = settings(max_model_calls=1, max_search_calls=1)
        budget = CallBudget(cfg)
        one = await self.adapter(handler, cfg, budget=budget)
        two = await self.adapter(handler, cfg, budget=budget)
        await one.search("杭州")
        await two.complete([{"role": "user", "content": "test"}])
        await self.assert_error(two, "model", "CALL_LIMIT")
        await self.assert_error(one, "search", "CALL_LIMIT")
        self.assertEqual(len(requests), 2)
        adapter = await self.adapter(lambda r: httpx.Response(503, json={}), settings(max_search_http_attempts=1))
        await self.assert_error(adapter, "search", "CALL_LIMIT")
        self.assertEqual(adapter.budget.counts["search_http_attempts"], 1)

    async def test_deadline_and_clipped_timeout(self):
        now = [0.]
        cfg = settings(trip_timeout_seconds=2)
        budget = CallBudget(cfg, clock=lambda: now[0])
        def handler(request):
            self.assertEqual(request.extensions["timeout"]["read"], 2)
            return httpx.Response(200, json=model_body())
        adapter = await self.adapter(handler, cfg, budget=budget)
        await adapter.complete([{"role": "user", "content": "test"}])
        now[0] = 2.
        await self.assert_error(adapter, "search", "TOTAL_TIMEOUT")
        self.assertEqual(budget.counts["search_http_attempts"], 0)

    async def test_bocha_business_error_and_quota_classification(self):
        for status, code in [(401, "AUTH_FAILED"), (403, "AUTH_FAILED"), (429, "RATE_LIMITED"), (500, "UPSTREAM_UNAVAILABLE")]:
            adapter = await self.adapter(lambda r: httpx.Response(200, json={"code": status}), settings(max_upstream_retries=0))
            await self.assert_error(adapter, "search", code)
        for status, code in [(429, "insufficient_quota"), (403, "AllocationQuota.FreeTierOnly"),
                             (400, "Arrearage"), (400, "isv.OUT_OF_SERVICE")]:
            adapter = await self.adapter(lambda r: httpx.Response(status, json={"error": {"code": code}}))
            await self.assert_error(adapter, "model", "QUOTA_EXHAUSTED")
            self.assertEqual(adapter.budget.counts["model_http_attempts"], 1)

    async def test_non_json_and_redirect_are_not_retried_or_followed(self):
        for provider in ["model", "search"]:
            adapter = await self.adapter(lambda r: httpx.Response(200, content=b"<html>bad</html>"))
            await self.assert_error(adapter, provider, "INVALID_RESPONSE")
            adapter = await self.adapter(lambda r: httpx.Response(302, headers={"Location": "https://other.example/steal"}))
            await self.assert_error(adapter, provider, "UPSTREAM_REJECTED")
            self.assertEqual(adapter.budget.counts[provider + "_http_attempts"], 1)

    async def test_configuration_demo_and_invalid_arguments_no_http(self):
        def prohibited(request):
            raise AssertionError("HTTP prohibited")
        for cfg, code in [(Settings(), "DEMO_MODE"), (Settings(mode="live"), "CONFIGURATION_ERROR")]:
            adapter = await self.adapter(prohibited, cfg)
            await self.assert_error(adapter, "search", code)
            await self.assert_error(adapter, "model", code)
            self.assertEqual(sum(adapter.budget.counts.values()), 0)
        adapter = await self.adapter(prohibited, settings(llm_model="unverified-model"))
        await self.assert_error(adapter, "model", "UNSUPPORTED_MODEL")
        adapter = await self.adapter(prohibited)
        for kwargs in [dict(tools=TOOLS, tool_choice="required"), dict(tools=TOOLS, output_model=Output), dict(tools=[{}]), dict(tool_choice=FORCED), dict(tools=TOOLS, tool_choice={"type": "function", "function": {"name": []}})]:
            await self.assert_error(adapter, "model", "INVALID_ARGUMENT", **kwargs)
        for query in ["", " ", "x" * 501, None]:
            with self.assertRaises(ProviderError) as caught:
                await adapter.search(query)
            self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        self.assertEqual(sum(adapter.budget.counts.values()), 0)

    async def test_cancellation_propagates_without_retry(self):
        async def cancelled(request):
            raise asyncio.CancelledError()
        adapter = await self.adapter(cancelled)
        with self.assertRaises(asyncio.CancelledError):
            await adapter.search("杭州")
        self.assertEqual(adapter.budget.counts["search_http_attempts"], 1)

    async def test_retry_wait_timeout_is_classified_and_stops(self):
        async def stuck(delay):
            await asyncio.sleep(1)
        adapter = await self.adapter(
            lambda r: httpx.Response(429, headers={"Retry-After": "0"}, json={}),
            settings(trip_timeout_seconds=0.02), sleep=stuck,
        )
        await self.assert_error(adapter, "model", "TOTAL_TIMEOUT")
        self.assertEqual(adapter.budget.counts["model_http_attempts"], 1)

    async def test_falsy_malformed_tool_calls_and_usage_are_rejected(self):
        for body in [model_body("ok", tool_calls={}), {**model_body(), "usage": []}]:
            adapter = await self.adapter(lambda r: httpx.Response(200, json=body))
            await self.assert_error(adapter, "model", "INVALID_MODEL_OUTPUT")

    async def test_retry_after_backoff_and_http_date_are_respected(self):
        requests, delays = [], []
        def handler(request):
            requests.append(request)
            return httpx.Response(503, json={}) if len(requests) == 1 else httpx.Response(200, json=search_body())
        async def sleep(delay):
            delays.append(delay)
        adapter = await self.adapter(handler, sleep=sleep)
        await adapter.search("杭州")
        self.assertEqual(delays, [0.25])
        requests.clear()
        delays.clear()
        future = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=10))
        def dated(request):
            requests.append(request)
            return httpx.Response(429, headers={"Retry-After": future}, json={}) if len(requests) == 1 else httpx.Response(200, json=search_body())
        adapter = await self.adapter(dated, sleep=sleep)
        await adapter.search("杭州")
        self.assertGreater(delays[0], 8)

    async def test_safe_logs_do_not_include_payloads_keys_or_bodies(self):
        adapter = await self.adapter(lambda r: httpx.Response(401, json={"message": "private-body"}))
        with self.assertLogs("backend.providers", level="INFO") as logs:
            await self.assert_error(adapter, "search", "AUTH_FAILED")
        joined = " ".join(logs.output)
        for value in ["mock-llm-secret", "mock-search-secret", "杭州景点", "private-body"]:
            self.assertNotIn(value, joined)
        self.assertIn("AUTH_FAILED", joined)


class SettingsTests(unittest.TestCase):
    def test_limits_from_environment_and_validation(self):
        with patch.dict(os.environ, {"TRIP_TIMEOUT_SECONDS": "23", "MAX_SEARCH_CALLS": "2", "MAX_UPSTREAM_RETRIES": "0"}, clear=True):
            cfg = Settings.from_env()
            self.assertEqual(cfg.trip_timeout_seconds, 23)
            self.assertEqual(cfg.max_search_calls, 2)
            self.assertEqual(cfg.max_upstream_retries, 0)
        for name, value in [("trip_timeout_seconds", 0), ("llm_timeout_seconds", float("nan")),
                            ("search_timeout_seconds", float("inf")), ("max_search_calls", 0),
                            ("max_model_http_attempts", 0), ("max_upstream_retries", 4),
                            ("max_model_calls", 1.5)]:
            with self.subTest(name=name), self.assertRaises(ValidationError):
                settings(**{name: value})

    def test_retry_after_seconds_http_date_and_malformed(self):
        self.assertEqual(_retry_after("2", 0), 2)
        self.assertEqual(_retry_after("-2", 0), 0)
        self.assertEqual(_retry_after("not a date", 1), 0.5)
        self.assertEqual(_retry_after("NaN", 0), 0.25)
        future = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=10))
        self.assertGreater(_retry_after(future, 0), 8)


if __name__ == "__main__":
    unittest.main()
