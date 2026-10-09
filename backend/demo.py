"""Offline demo generation. Four activities per day are intentionally demo-only."""
import asyncio
import secrets
from datetime import timedelta
from decimal import Decimal, ROUND_DOWN
from backend.schemas import Activity, DayPlan, TripRequest, TripResponse

OPTIONS = {
    "自然风光": [("城市公园", "沿绿道散步，感受当地自然风光。"), ("滨水步道", "在水岸放慢脚步，寻找适合拍照的风景。")],
    "历史文化": [("历史街区", "漫步老街，观察建筑细节与城市记忆。"), ("文化博物馆", "了解当地历史与传统，预留自主参观时间。")],
    "地道美食": [("特色美食街", "尝试当地风味，按个人口味挑选小吃。"), ("传统市场", "探访市场摊位，体验当地日常生活与美食。")],
    "艺术展览": [("艺术展馆", "参观展览，欣赏本地艺术与创意作品。"), ("创意街区", "探索独立画廊与特色小店，寻找设计灵感。")],
    "城市漫步": [("城市中心步行街", "轻松步行，感受街头氛围与城市节奏。"), ("特色街巷", "在街巷中自由探索，记录旅途里的小发现。")],
    "休闲购物": [("特色商业街", "浏览本地品牌与纪念品，购物支出自行安排。"), ("生活方式街区", "逛逛特色店铺，在咖啡馆享受休息时间。")],
}


async def generate_demo(request: TripRequest, request_id: str) -> TripResponse:
    await asyncio.sleep(0.6)
    unit = (request.budget * Decimal("0.7") / (request.days * 4)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    basis = f"模拟预算分配：活动总预算约 70% 均分，覆盖 {request.travelers} 位旅客；不代表真实价格。"
    plans = []
    offset = secrets.randbelow(1000)
    for index in range(request.days):
        interest = request.interests[(index + offset) % len(request.interests)]
        second = request.interests[(index + offset + 1) % len(request.interests)]
        morning = OPTIONS[interest][secrets.randbelow(2)]
        afternoon = OPTIONS[second][secrets.randbelow(2)]
        activities = [
            Activity(time="09:00 — 11:30", location=f"{request.destination} · {morning[0]}（模拟地点）", description=morning[1], estimated_cost=unit, cost_status="demo", cost_basis=basis),
            Activity(time="12:00 — 13:30", location=f"{request.destination} · 本地餐馆（模拟地点）", description=f"享用当地风味午餐，为 {request.travelers} 位旅客安排用餐与休息。", estimated_cost=unit, cost_status="demo", cost_basis=basis),
            Activity(time="14:00 — 17:00", location=f"{request.destination} · {afternoon[0]}（模拟地点）", description=afternoon[1], estimated_cost=unit, cost_status="demo", cost_basis=basis),
            Activity(time="18:00 — 20:00", location=f"{request.destination} · 街区餐厅（模拟地点）", description="享用晚餐后自由散步，轻松结束一天的旅程。", estimated_cost=unit, cost_status="demo", cost_basis=basis),
        ]
        plans.append(DayPlan(day=index + 1, date=request.departure_date + timedelta(days=index), title=f"{interest} · 慢慢探索", activities=activities))
    return TripResponse(
        demo=True, mode="demo", request_id=request_id,
        destination=request.destination, travelers=request.travelers, currency=request.currency,
        budget=request.budget, days=plans,
        notice="演示数据：地点、时间与费用均为模拟，未接入 AI 或实时旅行信息。费用为所有旅客的活动合计，不含往返交通、住宿及额外购物；币种仅作为计价单位，不进行汇率换算。",
    )
