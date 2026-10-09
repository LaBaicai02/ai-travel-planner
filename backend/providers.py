"""Independent HTTP adapters. No Agent loop, itinerary generation or demo fallback."""
import asyncio
import hashlib
import json
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field, ValidationError
from backend.config import Settings
from backend.schemas import Source

logger = logging.getLogger(__name__)

# Verified against Alibaba's model, Function Calling and JSON Schema documentation
# on 2026-10-09. Do not infer support from a model name prefix.
VERIFIED_MODELS = {"qwen3.7-plus", "qwen3.7-plus-2026-05-26"}


class ProviderError(Exception):
    """Public safe error: never embeds upstream bodies, queries or credentials."""
    def __init__(self, provider: str, code: str, message: str, retryable: bool = False):
        self.provider, self.code, self.message, self.retryable = provider, code, message, retryable
        super().__init__(message)


@dataclass
class CallBudget:
    """One shared instance per future itinerary request; retries also consume attempts."""
    settings: Settings
    clock: Callable[[], float] = time.monotonic
    counts: dict[str, int] = field(default_factory=lambda: {
        "model_calls": 0, "search_calls": 0, "model_http_attempts": 0, "search_http_attempts": 0,
    })
    request_id: str = field(default_factory=lambda: uuid4().hex)
    deadline: float = field(init=False)

    def __post_init__(self):
        self.deadline = self.clock() + self.settings.trip_timeout_seconds

    def remaining(self) -> float:
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise ProviderError("budget", "TOTAL_TIMEOUT", "生成请求已超过总时限。", True)
        return remaining

    def consume(self, provider: str, attempts: bool = False):
        self.remaining()
        key = provider + ("_http_attempts" if attempts else "_calls")
        limit = getattr(self.settings, "max_" + key)
        if self.counts[key] >= limit:
            raise ProviderError(provider, "CALL_LIMIT", "已达到本次请求的外部调用次数上限。")
        self.counts[key] += 1


class SearchHit(Source):
    snippet: str = Field(max_length=1500)
    # Preserve the source's reported publication text; do not invent a timezone.
    published_at: str | None = None


class SearchResult(BaseModel):
    query: str
    results: list[SearchHit]
    warnings: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ModelResult:
    message: dict[str, Any]
    tool_calls: list[ToolCall]
    parsed: BaseModel | None
    usage: dict[str, int]


def _reject_constant(value):
    raise ValueError("non-finite JSON number")


def _json(value: str | bytes):
    return json.loads(value, parse_constant=_reject_constant)


def _error(provider: str, status: int, body: Any = None) -> ProviderError:
    # Vendor quota codes are not transient, even when carried by HTTP 429/5xx.
    detail = body.get("error", body) if isinstance(body, dict) else {}
    code = detail.get("code", "") if isinstance(detail, dict) else ""
    if str(code).lower() in {
        "insufficient_quota", "quota_exceeded", "arrearage",
        "allocationquota.freetieronly", "isv.out_of_service",
    }:
        return ProviderError(provider, "QUOTA_EXHAUSTED", "服务额度已耗尽，请检查账户额度。")
    if status in (401, 403):
        return ProviderError(provider, "AUTH_FAILED", "服务鉴权失败，请检查后端密钥、地域和权限。")
    if status == 429:
        return ProviderError(provider, "RATE_LIMITED", "上游服务限流，请稍后重试。", True)
    if 500 <= status <= 599:
        return ProviderError(provider, "UPSTREAM_UNAVAILABLE", "上游服务暂时不可用。", True)
    return ProviderError(provider, "UPSTREAM_REJECTED", "上游服务拒绝请求，请检查模型权限或请求配置。")


def _retry_after(value: str | None, attempt: int) -> float:
    fallback = 0.25 * (2 ** attempt)
    if not value:
        return fallback
    try:
        delay = float(value)
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                return fallback
            delay = (date - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return fallback
    return max(0.0, delay) if math.isfinite(delay) else fallback


class ServiceAdapters:
    """Caller owns AsyncClient lifetime. Inject MockTransport for offline tests.

    A new adapter/budget is required for each top-level generation request.
    Messages/tools/schema are backend-owned, never forwarded from a public route.
    """
    def __init__(self, settings: Settings, client: httpx.AsyncClient,
                 budget: CallBudget | None = None, *, sleep=asyncio.sleep):
        self.settings, self.client = settings, client
        self.budget = budget if budget is not None else CallBudget(settings)
        if self.budget.settings != settings:
            raise ValueError("Budget and adapters must share Settings")
        self.sleep = sleep

    def _check_configuration(self, provider: str):
        if self.settings.mode != "live":
            raise ProviderError(provider, "DEMO_MODE", "演示模式禁止调用真实服务。")
        # Both credentials are required for the planned live workflow.
        if not self.settings.configured:
            raise ProviderError(provider, "CONFIGURATION_ERROR", "真实服务配置缺失或无效。")

    async def _post(self, provider: str, url: str, secret: str, payload: dict) -> dict:
        self._check_configuration(provider)
        self.budget.consume(provider)
        timeout = self.settings.llm_timeout_seconds if provider == "model" else self.settings.search_timeout_seconds
        for attempt in range(self.settings.max_upstream_retries + 1):
            self.budget.consume(provider, attempts=True)
            duration = min(timeout, self.budget.remaining())
            retry_header = None
            started = time.monotonic()
            try:
                # HTTPX's phase timeouts alone do not bound an entire request.
                response = await asyncio.wait_for(
                    self.client.post(
                        url, json=payload,
                        headers={"Authorization": "Bearer " + secret, "Content-Type": "application/json"},
                        timeout=httpx.Timeout(duration), follow_redirects=False,
                    ),
                    timeout=duration,
                )
                self.budget.remaining()
                try:
                    body = _json(response.content)
                except (ValueError, UnicodeDecodeError):
                    body = None
                if not 200 <= response.status_code < 300:
                    failure = _error(provider, response.status_code, body)
                    retry_header = response.headers.get("Retry-After")
                elif not isinstance(body, dict):
                    failure = ProviderError(provider, "INVALID_RESPONSE", "上游返回了异常响应格式。")
                elif provider == "search" and body.get("code") != 200:
                    business_code = body.get("code")
                    if type(business_code) is not int:
                        failure = ProviderError(provider, "INVALID_RESPONSE", "搜索响应缺少有效状态码。")
                    else:
                        failure = _error(provider, business_code, body)
                        retry_header = response.headers.get("Retry-After")
                elif "error" in body:
                    failure = _error(provider, 400, body)
                else:
                    return body
            except (asyncio.TimeoutError, httpx.TimeoutException):
                self.budget.remaining()
                failure = ProviderError(provider, "UPSTREAM_TIMEOUT", "上游服务请求超时。", True)
            except httpx.TransportError:
                failure = ProviderError(provider, "NETWORK_ERROR", "无法连接上游服务。", True)
            finally:
                logger.info("request_id=%s provider=%s elapsed_ms=%d counts=%s",
                            self.budget.request_id, provider, int((time.monotonic() - started) * 1000), self.budget.counts)
            logger.info("request_id=%s provider=%s error_code=%s", self.budget.request_id, provider, failure.code)
            if not failure.retryable or attempt == self.settings.max_upstream_retries:
                raise failure
            # Do not sleep if the next attempt is already prohibited.
            key = provider + "_http_attempts"
            if self.budget.counts[key] >= getattr(self.settings, "max_" + key):
                raise ProviderError(provider, "CALL_LIMIT", "已达到本次请求的外部调用次数上限。")
            delay = _retry_after(retry_header, attempt)
            if delay >= self.budget.remaining():
                raise ProviderError(provider, "TOTAL_TIMEOUT", "剩余时限不足以执行服务重试。", True)
            try:
                await asyncio.wait_for(self.sleep(delay), timeout=self.budget.remaining())
            except asyncio.TimeoutError:
                raise ProviderError(provider, "TOTAL_TIMEOUT", "重试等待超过总时限。", True) from None
        raise AssertionError("unreachable")

    async def search(self, query: str) -> SearchResult:
        if not isinstance(query, str) or not query.strip() or len(query.strip()) > 500:
            raise ProviderError("search", "INVALID_ARGUMENT", "搜索文本必须为 1–500 个字符。")
        query = query.strip()
        body = await self._post(
            "search", self.settings.bocha_base_url.rstrip("/") + "/v1/web-search",
            self.settings.bocha_api_key.get_secret_value(),
            {"query": query, "freshness": "noLimit", "summary": True, "count": 5},
        )
        try:
            items = body["data"]["webPages"]["value"]
            if not isinstance(items, list):
                raise TypeError
        except (KeyError, TypeError):
            raise ProviderError("search", "INVALID_RESPONSE", "搜索响应缺少网页结果列表。") from None
        results, seen, invalid, duplicates = [], set(), 0, 0
        retrieved_at = datetime.now(timezone.utc)
        for item in items:
            try:
                if not isinstance(item, dict):
                    raise ValueError
                title, url = item["name"], item["url"]
                snippet = item.get("summary") or item.get("snippet") or ""
                if not isinstance(title, str) or not title.strip() or not isinstance(url, str) or not isinstance(snippet, str):
                    raise ValueError
                hit = SearchHit(
                    id="pending", title=title.strip()[:200], url=url.strip(),
                    snippet=snippet.strip()[:1500], retrieved_at=retrieved_at,
                    published_at=item.get("datePublished") if isinstance(item.get("datePublished"), str) else None,
                )
                # Fragment-only differences are the same source.
                normalized = str(hit.url).split("#", 1)[0]
                hit.url = type(hit.url)(normalized)
                hit.id = "s_" + hashlib.sha256(normalized.encode()).hexdigest()[:16]
            except (KeyError, TypeError, ValueError, ValidationError):
                invalid += 1
                continue
            if normalized in seen:
                duplicates += 1
                continue
            seen.add(normalized)
            if len(results) < 5:
                results.append(hit)
        if items and not results:
            raise ProviderError("search", "INVALID_RESPONSE", "搜索返回的网页结果均无效。")
        warnings = []
        if invalid:
            warnings.append(f"已丢弃 {invalid} 条无效网页结果。")
        if duplicates:
            warnings.append(f"已去除 {duplicates} 条重复网页结果。")
        if not results:
            warnings.append("搜索未找到结果；这不代表地点不存在或免费。")
        return SearchResult(query=query, results=results, warnings=warnings)

    async def complete(self, messages: list[dict], *, tools: list[dict] | None = None,
                       tool_choice: str | dict = "auto",
                       output_model: type[BaseModel] | None = None) -> ModelResult:
        if self.settings.llm_model not in VERIFIED_MODELS:
            raise ProviderError("model", "UNSUPPORTED_MODEL", "该模型尚未核实工具调用和 JSON Schema 能力。")
        if not messages or not isinstance(messages, list) or any(not isinstance(m, dict) for m in messages):
            raise ProviderError("model", "INVALID_ARGUMENT", "模型消息列表无效。")
        allowed = set()
        try:
            for tool in tools or []:
                if tool["type"] != "function" or not isinstance(tool["function"]["parameters"], dict):
                    raise ValueError
                name = tool["function"]["name"]
                if not isinstance(name, str) or not name.strip() or name in allowed:
                    raise ValueError
                allowed.add(name)
        except (KeyError, TypeError, ValueError):
            raise ProviderError("model", "INVALID_ARGUMENT", "模型工具定义无效。") from None
        if tool_choice not in ("auto", "none"):
            # Qwen documentation says 'required' does not guarantee a call.
            if not isinstance(tool_choice, dict) or tool_choice.get("type") != "function" or not isinstance(tool_choice.get("function"), dict) or not isinstance(tool_choice["function"].get("name"), str) or tool_choice["function"]["name"] not in allowed:
                raise ProviderError("model", "INVALID_ARGUMENT", "强制调用须指定已注册的函数名，不能使用 required。")
        if output_model is not None and tools:
            raise ProviderError("model", "INVALID_ARGUMENT", "结构化生成与工具决策须使用独立模型请求。")
        payload = {"model": self.settings.llm_model, "messages": messages,
                   "enable_thinking": False, "stream": False}
        if tools:
            payload.update(tools=tools, tool_choice=tool_choice)
        elif tool_choice != "auto":
            raise ProviderError("model", "INVALID_ARGUMENT", "没有工具时不能指定工具策略。")
        if output_model is not None:
            if not isinstance(output_model, type) or not issubclass(output_model, BaseModel):
                raise ProviderError("model", "INVALID_ARGUMENT", "结构化输出必须指定 Pydantic 模型。")
            payload["response_format"] = {
                "type": "json_schema", "json_schema": {
                    "name": output_model.__name__, "strict": True, "schema": output_model.model_json_schema(),
                },
            }
        body = await self._post(
            "model", self.settings.llm_base_url.rstrip("/") + "/chat/completions",
            self.settings.dashscope_api_key.get_secret_value(), payload,
        )
        try:
            choices = body["choices"]
            if not isinstance(choices, list) or len(choices) != 1:
                raise ValueError
            choice = choices[0]
            message = choice["message"]
            finish = choice["finish_reason"]
            if not isinstance(message, dict) or message.get("role") != "assistant":
                raise ValueError
            if message.get("refusal") or finish == "content_filter":
                raise ProviderError("model", "MODEL_REFUSED", "模型拒绝生成此内容。")
            if finish == "length":
                raise ProviderError("model", "MODEL_TRUNCATED", "模型输出被截断。")
            if finish not in ("stop", "tool_calls"):
                raise ValueError
            content = message.get("content")
            raw_calls = message.get("tool_calls")
            if raw_calls is None:
                raw_calls = []
            if not isinstance(raw_calls, list) or (content is not None and not isinstance(content, str)):
                raise ValueError
            calls, ids = [], set()
            for call in raw_calls:
                call_id, fn = call["id"], call["function"]
                if call["type"] != "function" or not isinstance(call_id, str) or not call_id or call_id in ids or fn["name"] not in allowed or tool_choice == "none":
                    raise ValueError
                if isinstance(tool_choice, dict) and fn["name"] != tool_choice["function"]["name"]:
                    raise ValueError
                arguments = _json(fn["arguments"])
                if not isinstance(arguments, dict):
                    raise ValueError
                calls.append(ToolCall(call_id, fn["name"], arguments))
                ids.add(call_id)
            if finish == "tool_calls" and not calls:
                raise ValueError
            if isinstance(tool_choice, dict) and not calls:
                raise ValueError
            if not calls and (content is None or not content.strip()):
                raise ProviderError("model", "MODEL_EMPTY", "模型返回空内容。")
            parsed = None
            if output_model is not None:
                if calls:
                    raise ValueError
                _json(content)
                parsed = output_model.model_validate_json(content)
            usage = body.get("usage")
            if usage is None:
                usage = {}
            if not isinstance(usage, dict):
                raise ValueError
            usage = {k: v for k, v in usage.items()
                     if k in ("prompt_tokens", "completion_tokens", "total_tokens") and type(v) is int and v >= 0}
            safe_message = {"role": "assistant", "content": content}
            if calls:
                safe_message["tool_calls"] = raw_calls
            logger.info("request_id=%s provider=model usage=%s", self.budget.request_id, usage)
            return ModelResult(safe_message, calls, parsed, usage)
        except (KeyError, TypeError, ValueError, ValidationError):
            raise ProviderError("model", "INVALID_MODEL_OUTPUT", "模型输出不符合协议或指定的数据格式。") from None
