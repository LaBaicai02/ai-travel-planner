from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Literal
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl, field_serializer, field_validator, model_validator

INTERESTS = ["自然风光", "历史文化", "地道美食", "艺术展览", "城市漫步", "休闲购物"]
Currency = Literal["CNY", "USD", "EUR", "JPY"]
Mode = Literal["demo", "live"]
Money = Annotated[Decimal, Field(ge=0, decimal_places=2, allow_inf_nan=False)]


class TripRequest(BaseModel):
    destination: str = Field(min_length=1, max_length=60)
    departure_date: date
    days: int = Field(ge=1, le=14)
    travelers: int = Field(ge=1, le=20)
    budget: Decimal = Field(gt=0, le=10000000, max_digits=10, decimal_places=2)
    currency: Currency
    interests: list[str] = Field(min_length=1, max_length=6)

    @field_validator("destination")
    @classmethod
    def trim_destination(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("请输入目的地")
        return value

    @field_validator("interests")
    @classmethod
    def check_interests(cls, values):
        if any(value not in INTERESTS for value in values):
            raise ValueError("兴趣偏好无效")
        return list(dict.fromkeys(values))

    @model_validator(mode="after")
    def validate_dates(self):
        if not date(2000, 1, 1) <= self.departure_date <= date(2100, 12, 18):
            raise ValueError("日期须在 2000 年至 2100 年 12 月 18 日之间")
        return self


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    url: HttpUrl
    retrieved_at: AwareDatetime

    @field_validator("url")
    @classmethod
    def reject_credentials(cls, value):
        if value.username or value.password:
            raise ValueError("来源链接不能包含凭据")
        return value


class Activity(BaseModel):
    time: str = Field(min_length=1)
    location: str = Field(min_length=1)
    description: str = Field(min_length=1)
    estimated_cost: Money | None = None
    cost_status: Literal["reference", "estimated", "unknown", "demo"] = "unknown"
    cost_basis: str = ""
    sources: list[Source] = Field(default_factory=list)
    opening_hours: str | None = None
    opening_hours_status: Literal["reported", "unknown"] = "unknown"

    @model_validator(mode="after")
    def consistent_status(self):
        if (self.estimated_cost is None) != (self.cost_status == "unknown"):
            raise ValueError("未知费用必须为 null；有金额时必须声明费用依据类型")
        if self.cost_status != "unknown" and not self.cost_basis.strip():
            raise ValueError("已知或估算费用需要说明依据")
        if (self.opening_hours is None) != (self.opening_hours_status == "unknown"):
            raise ValueError("营业时间与状态不一致")
        if self.opening_hours is not None and not self.opening_hours.strip():
            raise ValueError("营业时间不能是空字符串")
        return self

    @field_serializer("estimated_cost", when_used="json")
    def serialize_cost(self, value):
        return None if value is None else format(value, ".2f")


class DayPlan(BaseModel):
    day: int = Field(ge=1, le=14)
    date: date
    title: str = Field(min_length=1)
    # Four activities are a demo convention, not a shared contract requirement.
    activities: list[Activity] = Field(min_length=1, max_length=12)
    estimated_cost: Money | None = None
    known_cost_subtotal: Money = Decimal("0")

    @model_validator(mode="after")
    def calculate_costs(self):
        self.known_cost_subtotal = sum((a.estimated_cost for a in self.activities if a.estimated_cost is not None), Decimal("0"))
        self.estimated_cost = self.known_cost_subtotal if all(a.estimated_cost is not None for a in self.activities) else None
        return self

    @field_serializer("estimated_cost", "known_cost_subtotal", when_used="json")
    def serialize_cost(self, value):
        return None if value is None else format(value, ".2f")


class TripResponse(BaseModel):
    demo: bool
    mode: Mode
    destination: str
    travelers: int = Field(ge=1, le=20)
    currency: Currency
    budget: Money
    total_cost: Money | None = None
    known_cost_subtotal: Money = Decimal("0")
    notice: str
    warnings: list[str] = Field(default_factory=list)
    request_id: str
    days: list[DayPlan] = Field(min_length=1, max_length=14)

    @model_validator(mode="after")
    def calculate_costs(self):
        if self.demo != (self.mode == "demo"):
            raise ValueError("演示标记与模式不一致")
        if self.mode == "live" and self.currency != "CNY":
            raise ValueError("真实模式仅支持 CNY")
        for index, day in enumerate(self.days):
            if day.day != index + 1 or day.date != self.days[0].date + timedelta(days=index):
                raise ValueError("行程序号和日期必须连续")
            if self.mode == "live" and any(a.cost_status == "demo" or not a.sources for a in day.activities):
                raise ValueError("真实活动需要来源且不能包含模拟费用")
        self.known_cost_subtotal = sum((day.known_cost_subtotal for day in self.days), Decimal("0"))
        self.total_cost = self.known_cost_subtotal if all(day.estimated_cost is not None for day in self.days) else None
        return self

    @field_serializer("budget", "total_cost", "known_cost_subtotal", when_used="json")
    def serialize_cost(self, value):
        return None if value is None else format(value, ".2f")


class HealthResponse(BaseModel):
    status: Literal["ok", "unavailable"]
    mode: Mode
    demo: bool
    configured: bool
    ready: bool
    live_integration_available: bool = False
    supported_currencies: list[Currency]
    configuration_issues: list[str]
    message: str
