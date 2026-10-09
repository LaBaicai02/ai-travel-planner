import asyncio
from uuid import uuid4

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from backend.agent import generate_live
from backend.config import Settings
from backend.demo import generate_demo
from backend.providers import CallBudget, ProviderError, ServiceAdapters
from backend.schemas import HealthResponse, TripRequest, TripResponse


async def wait_for_disconnect(request: Request):
    while True:
        message = await request.receive()
        if message["type"] == "http.disconnect":
            return


async def run_live(request: Request, trip: TripRequest, settings: Settings,
                   request_id: str, transport=None):
    budget = CallBudget(settings, request_id=request_id)
    async with httpx.AsyncClient(transport=transport) as client:
        generation = asyncio.create_task(generate_live(trip, ServiceAdapters(settings, client, budget)))
        disconnect = asyncio.create_task(wait_for_disconnect(request))
        try:
            done, _ = await asyncio.wait({generation, disconnect}, timeout=budget.remaining(),
                                         return_when=asyncio.FIRST_COMPLETED)
            if generation in done:
                return await generation
            if disconnect in done:
                raise ProviderError("client", "CLIENT_DISCONNECTED", "客户端已断开，生成已取消。")
            raise ProviderError("budget", "TOTAL_TIMEOUT", "生成请求已超过总时限。", True)
        finally:
            for task in (generation, disconnect):
                if not task.done():
                    task.cancel()
            await asyncio.gather(generation, disconnect, return_exceptions=True)


def create_app(settings: Settings | None = None, *, transport=None) -> FastAPI:
    settings = settings if settings is not None else Settings.from_env()
    app = FastAPI(title="漫游 · 旅行规划 API", version="0.3.0")
    app.state.settings = settings
    app.state.active_trips = 0

    def failure(code: str, message: str, request_id: str, status: int, retryable=False):
        return JSONResponse(status_code=status, content={"error": {
            "code": code, "message": message, "retryable": retryable, "request_id": request_id,
        }})

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request: Request, exc: RequestValidationError):
        return failure("INVALID_INPUT", "输入信息未通过校验，请检查日期、预算及其他字段。", uuid4().hex, 422)

    @app.get("/api/health", response_model=HealthResponse)
    async def health():
        demo = settings.mode == "demo"
        ready = demo or settings.configured
        message = "演示模式：使用模拟行程，不调用外部服务。" if demo else (
            "真实模式缺少或存在无效配置：" + "、".join(settings.configuration_issues)
            if not settings.configured else "真实模式已就绪：生成会调用模型与搜索服务；尚未由健康检查验证外部连通性。"
        )
        return HealthResponse(
            status="ok" if ready else "unavailable",
            mode=settings.mode, demo=demo, configured=settings.configured,
            ready=ready, live_integration_available=True,
            supported_currencies=["CNY", "USD", "EUR", "JPY"] if demo else ["CNY"],
            configuration_issues=settings.configuration_issues, message=message,
        )

    @app.post("/api/itineraries", response_model=TripResponse)
    async def generate_trip(trip: TripRequest, request: Request):
        request_id = uuid4().hex
        if settings.mode == "demo":
            return await generate_demo(trip, request_id)
        if trip.currency != "CNY":
            return failure("UNSUPPORTED_CURRENCY", "真实模式仅支持 CNY 人民币。", request_id, 422)
        if not settings.configured:
            return failure("LIVE_NOT_CONFIGURED", "真实模式缺少或存在无效配置：" + "、".join(settings.configuration_issues), request_id, 503)
        # Admission happens without an await; this is per worker, not a distributed limit.
        if app.state.active_trips >= settings.max_concurrent_trips:
            return failure("TRIP_BUSY", "正在生成的行程过多，请稍后重试。", request_id, 429, True)
        app.state.active_trips += 1
        try:
            return await run_live(request, trip, settings, request_id, transport)
        except ProviderError as exc:
            status = 504 if exc.code == "TOTAL_TIMEOUT" else (
                503 if exc.code in {"CONFIGURATION_ERROR", "UNSUPPORTED_MODEL"} else 502)
            return failure(exc.code, exc.message, request_id, status, exc.retryable)
        except Exception:
            # Never return raw upstream errors, credentials, or a demo fallback.
            return failure("GENERATION_FAILED", "行程生成失败，请稍后重试。", request_id, 502, True)
        finally:
            app.state.active_trips -= 1

    return app


app = create_app()
