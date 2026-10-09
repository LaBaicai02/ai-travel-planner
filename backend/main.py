from uuid import uuid4
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from backend.config import Settings
from backend.demo import generate_demo
from backend.schemas import HealthResponse, TripRequest, TripResponse


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings.from_env()
    app = FastAPI(title="漫游 · 旅行规划 API", version="0.2.0")
    app.state.settings = settings

    def failure(code: str, message: str, request_id: str, status: int):
        return JSONResponse(status_code=status, content={"error": {
            "code": code, "message": message, "retryable": False, "request_id": request_id,
        }})

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request: Request, exc: RequestValidationError):
        return failure("INVALID_INPUT", "输入信息未通过校验，请检查日期、预算及其他字段。", uuid4().hex, 422)

    @app.get("/api/health", response_model=HealthResponse)
    async def health():
        demo = settings.mode == "demo"
        message = "演示模式：使用模拟行程，不调用外部服务。" if demo else (
            "真实模式缺少或存在无效配置：" + "、".join(settings.configuration_issues)
            if not settings.configured else "真实模式已配置，服务适配层已实现；Agent 尚未接入行程生成。"
        )
        return HealthResponse(
            status="ok" if demo else "unavailable",
            mode=settings.mode, demo=demo, configured=settings.configured,
            ready=demo, supported_currencies=["CNY", "USD", "EUR", "JPY"] if demo else ["CNY"],
            configuration_issues=settings.configuration_issues, message=message,
        )

    @app.post("/api/itineraries", response_model=TripResponse)
    async def generate_trip(request: TripRequest):
        request_id = uuid4().hex
        if settings.mode == "live":
            if request.currency != "CNY":
                return failure("UNSUPPORTED_CURRENCY", "真实模式仅支持 CNY 人民币。", request_id, 422)
            if not settings.configured:
                return failure("LIVE_NOT_CONFIGURED", "真实模式缺少或存在无效配置：" + "、".join(settings.configuration_issues), request_id, 503)
            return failure("LIVE_NOT_IMPLEMENTED", "真实行程 Agent 尚未接入；服务适配层不会由本接口调用。", request_id, 503)
        return await generate_demo(request, request_id)

    return app


app = create_app()
