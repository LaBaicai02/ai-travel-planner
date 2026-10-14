"""Bounded native tool-calling workflow. Search text is evidence, never instructions."""
import json
import logging
import re
from datetime import timedelta
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model, model_validator

from backend.trip_coverage import supplement_coverage, require_candidates, coverage_counts, deficits, schedule_coverage_gaps, coverage_gap_message
from backend.place_candidates import explicit_names, screen_venue_results, venue_source_usable, restaurant_discovery_query, dining_context
from backend.prompts import SYSTEM, SEARCH_POLICY, OUTPUT_RULES, GENERATION_EXAMPLES
from backend.providers import ProviderError, ServiceAdapters
from backend.planning_quality import annotate_plan, price_clues, improve_schedule, meal_gaps
from backend.price_estimates import estimate_from_sources
from backend.price_completion import complete_online_prices
from backend.schemas import Activity, DayPlan, Source, TripRequest, TripResponse


logger = logging.getLogger(__name__)


class StrictOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchArguments(StrictOutput):
    query: str = Field(min_length=1, max_length=500)
    purpose: Literal["attraction", "restaurant", "opening_hours", "price"]


class ActivityFacts(StrictOutput):
    activity_type: Literal["sightseeing", "dining", "shopping", "other"]
    meal_slot: Literal["lunch", "dinner"] | None

    @model_validator(mode="after")
    def validate_meal_type(self):
        if self.meal_slot is not None and self.activity_type != "dining":
            raise ValueError("meal_slot requires dining")
        return self

    time: str = Field(pattern=r"^\d{2}:\d{2} - \d{2}:\d{2}$")
    description: str = Field(min_length=1, max_length=1000)
    estimated_cost: str | None = Field(pattern=r"^\d{1,8}\.\d{2}$")
    cost_status: Literal["reference", "estimated", "unknown"]
    cost_basis: str = Field(max_length=500)
    cost_evidence: str | None = Field(max_length=500)
    opening_hours: str | None = Field(max_length=200)
    opening_hours_status: Literal["reported", "unknown"]
    opening_hours_evidence: str | None = Field(max_length=500)


class GeneratedActivity(ActivityFacts):
    """Backend-bound activity, never the provider's free-form place selection."""
    location: str = Field(min_length=1, max_length=120)
    source_ids: list[str] = Field(min_length=1, max_length=5)


class GeneratedDay(StrictOutput):
    day: int = Field(ge=1, le=14)
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    title: str = Field(min_length=1, max_length=120)
    activities: list[GeneratedActivity] = Field(min_length=1, max_length=12)


class GeneratedTrip(StrictOutput):
    days: list[GeneratedDay] = Field(min_length=1, max_length=14)


SEARCH_TOOL = {"type": "function", "function": {
    "name": "search_travel_info",
    "description": "搜索目的地的景点、餐厅、营业时间与费用来源。优先官网和官方公告。",
    "parameters": SearchArguments.model_json_schema(),
}}


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


VALIDATION_HINTS = {
    "unexpected_output": "模型返回的数据类型或工具调用顺序无效。",
    "day_count": "行程天数必须与请求一致。",
    "day_number": "每日序号必须从 1 开始连续排列。",
    "day_date": "日期必须从请求出发日期开始连续排列。",
    "source_unknown": "仅可引用搜索结果中已经登记的来源 ID。",
    "location_generic": "地点必须是有来源支持的具体景点、街区或餐厅，不能用目的地城市或泛称代替；可减少无依据的活动。",
    "location_unsupported": "地点名称必须原样出现于引用来源的标题或摘要。",
    "time_invalid": "时间须为有效的 HH:MM - HH:MM，结束晚于开始且活动不可重叠。",
    "cost_status_mismatch": "未知费用必须为 null 且状态为 unknown；有金额须为 reference 或 estimated。",
    "cost_basis_missing": "有金额时必须说明全体旅客人数、计算方式及依据。",
    "cost_quote_missing": "费用依据须原样出现在该地点的引用来源中，无法证实则标未知。",
    "cost_zero_unsupported": "零费用必须有免费、免票或零元的来源依据。",
    "cost_price_missing": "有金额须有明确的元或人民币符号价格依据，无法证实则标未知。",
    "reference_price_mismatch": "来源参考价须匹配原文金额或按旅客人数计算，不能任意填写。",
    "cost_unknown_with_quote": "费用未知时 cost_evidence 应为 null。",
    "hours_status_mismatch": "营业时间未知须为 null 且状态 unknown；有时间须为 reported。",
    "hours_quote_missing": "营业时间和证据必须来自该地点的引用原文，无法证实则标未知。",
    "hours_unknown_with_quote": "营业时间未知时 opening_hours_evidence 应为 null。",
    "activity_contract": "活动费用、依据和营业时间状态须符合公开数据契约。",
}


def _output_error(reason="unexpected_output", field="root"):
    error = ProviderError("model", "INVALID_MODEL_OUTPUT",
                          "行程校验失败（ITINERARY_VALIDATION；检查：" + reason + "；字段：" + field + "）。" + VALIDATION_HINTS[reason])
    # These diagnostics are created only from backend-owned labels and indices.
    error.validation_reason, error.validation_field = reason, field
    return error


def assemble_trip(output: GeneratedTrip, request: TripRequest, evidence: dict,
                  request_id: str, warnings: list[str]) -> TripResponse:
    if len(output.days) != request.days:
        raise _output_error("day_count", "days")
    days = []
    for index, item in enumerate(output.days):
        day_path = f"days.{index}"
        expected_date = request.departure_date + timedelta(days=index)
        if item.day != index + 1:
            raise _output_error("day_number", day_path + ".day")
        if item.date != expected_date.isoformat():
            raise _output_error("day_date", day_path + ".date")
        activities, previous_end = [], -1
        for activity_index, activity in enumerate(item.activities):
            path = day_path + f".activities.{activity_index}"
            if any(s not in evidence for s in activity.source_ids):
                raise _output_error("source_unknown", path + ".source_ids")
            hits = [evidence[s] for s in dict.fromkeys(activity.source_ids)]
            location = _compact(activity.location)
            # Reject known placeholders even when a travel article contains them.
            # This is a conservative guard, not named-entity or factual verification.
            generic_locations = {"地道的美食", "地道美食", "当地美食", "特色美食", "美食", "午餐", "晚餐", "早餐",
                                 "附近餐厅", "周边餐厅", "当地餐厅", "餐厅", "景点", "自由活动", "待定"}
            city_names = {"北京", "上海", "天津", "重庆", "杭州", "成都", "广州", "深圳", "南京", "苏州",
                          "西安", "武汉", "长沙", "昆明", "厦门", "青岛", "大连", "宁波", "郑州", "济南"}
            city_labels = city_names | {name + "市" for name in city_names}
            if location in city_labels or location in generic_locations:
                raise _output_error("location_generic", path + ".location")
            relevant = [h for h in hits if location and location in _compact(h.title + " " + h.snippet)]
            if not relevant:
                raise _output_error("location_unsupported", path + ".location")
            text = _compact(" ".join(h.title + " " + h.snippet for h in relevant))
            try:
                start, end = activity.time.split(" - ")
                def minutes(value):
                    hours, minute = map(int, value.split(":"))
                    if not 0 <= hours <= 23 or not 0 <= minute <= 59:
                        raise ValueError
                    return hours * 60 + minute
                start, end = minutes(start), minutes(end)
                if start < previous_end or end <= start:
                    raise ValueError
                previous_end = end
            except ValueError:
                raise _output_error("time_invalid", path + ".time") from None
            if (activity.estimated_cost is None) != (activity.cost_status == "unknown"):
                raise _output_error("cost_status_mismatch", path + ".cost_status")
            if activity.estimated_cost is not None:
                if not activity.cost_basis.strip():
                    raise _output_error("cost_basis_missing", path + ".cost_basis")
                if not activity.cost_evidence or _compact(activity.cost_evidence) not in text:
                    raise _output_error("cost_quote_missing", path + ".cost_evidence")
                amount = Decimal(activity.estimated_cost)
                quote = activity.cost_evidence
                prices = [Decimal(a or b) for a, b in re.findall(r"[¥￥]\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*元", quote)]
                free = any(word in quote for word in ("免费", "免票"))
                if amount == 0 and not free and Decimal("0") not in prices:
                    raise _output_error("cost_zero_unsupported", path + ".estimated_cost")
                if amount > 0 and not prices:
                    raise _output_error("cost_price_missing", path + ".cost_evidence")
                if activity.cost_status == "reference" and amount not in prices + [p * request.travelers for p in prices] and not (amount == 0 and free):
                    raise _output_error("reference_price_mismatch", path + ".estimated_cost")
            elif activity.cost_evidence is not None:
                raise _output_error("cost_unknown_with_quote", path + ".cost_evidence")
            if (activity.opening_hours is None) != (activity.opening_hours_status == "unknown"):
                raise _output_error("hours_status_mismatch", path + ".opening_hours_status")
            if activity.opening_hours is not None:
                quote = activity.opening_hours_evidence
                if not quote or _compact(quote) not in text or _compact(activity.opening_hours) not in _compact(quote):
                    raise _output_error("hours_quote_missing", path + ".opening_hours_evidence")
            elif activity.opening_hours_evidence is not None:
                raise _output_error("hours_unknown_with_quote", path + ".opening_hours_evidence")
            # Keep source validation strict; use deterministic averages for optional
            # unknown/estimated prices instead of asking the model to invent arithmetic.
            if activity.cost_status in {"unknown", "estimated", "reference"}:
                estimate = estimate_from_sources(activity.location, relevant, request.travelers)
                if estimate and (activity.cost_status != "reference" or estimate["count"] > 1):
                    activity = activity.model_copy(update={"estimated_cost": estimate["amount"],
                        "cost_status": "estimated", "cost_basis": estimate["basis"],
                        "cost_evidence": estimate["quote"]})
            try:
                activities.append(Activity(
                    **activity.model_dump(exclude={"source_ids", "cost_evidence", "opening_hours_evidence", "activity_type", "meal_slot"}),
                    sources=[Source.model_validate(h.model_dump(include={"id", "title", "url", "retrieved_at"})) for h in relevant],
                ))
            except ValidationError:
                raise _output_error("activity_contract", path) from None
        days.append(DayPlan(day=index + 1, date=expected_date, title=item.title, activities=activities))
    trip = TripResponse(
        demo=False, mode="live", destination=request.destination, travelers=request.travelers,
        currency=request.currency, budget=request.budget, days=days, request_id=request_id,
        notice="真实搜索辅助生成；来源仅供核查，建议安排时间不等于营业时间。费用为全体旅客活动费用，不含住宿、往返交通与购物。",
        warnings=list(dict.fromkeys(warnings + ["营业信息可能变动，出行前请向官方核实；来源可追溯不代表未来营业保证。"])),
    )
    if any(a.cost_status == "estimated" and "本次检索样本估算" in a.cost_basis for d in trip.days for a in d.activities):
        trip.warnings.append("部分费用按网上同地点价格样本估算，区间取中点、同类样本取平均并按人数计算；样本少或过时可能有偏差，不代表全网均价或最终报价。")
    if trip.total_cost is None:
        trip.warnings.append("部分费用未知，完整总价不可计算；已知费用小计不代表全部支出。")
    if trip.known_cost_subtotal > request.budget:
        trip.warnings.append("已知活动费用超过预算，请调整安排；未缩小费用以满足预算。")
    locations = [_compact(a.location) for d in trip.days for a in d.activities]
    if len(set(locations)) < len(locations):
        trip.warnings.append("行程包含重复地点，可能是再次游览或用餐；请核对是否符合偏好，搜索资料不足时无法保证每天都有不同地点。")
    return trip


async def generate_live(request: TripRequest, adapters: ServiceAdapters) -> TripResponse:
    settings, budget = adapters.settings, adapters.budget
    chunk_count = (request.days + 4) // 5
    minimum_calls = chunk_count + 2  # One search decision, each chunk, one shared repair.
    if min(settings.max_model_calls, settings.max_model_http_attempts) < minimum_calls:
        raise ProviderError("budget", "CALL_LIMIT", f"该行程分段生成至少需要 {minimum_calls} 次模型调用预算（含一次修复预留），请调整后端调用配置或减少天数。")
    messages = [{"role": "system", "content": SYSTEM + "\n" + SEARCH_POLICY},
                {"role": "user", "content": json.dumps(request.model_dump(mode="json"), ensure_ascii=False)}]
    evidence, warnings, seen_calls = {}, [], set()
    source_purposes = {}
    coverage_attempted = set()
    generation_reserve = min(settings.llm_timeout_seconds * (chunk_count + 1),
                             settings.trip_timeout_seconds * 0.6)
    for round_index in range(min(settings.max_search_rounds, settings.max_model_calls - chunk_count - 1,
                                 settings.max_model_http_attempts - chunk_count - 1)):
        # Subsequent decisions are optional; leave time for generation and repair.
        if evidence and budget.remaining() < generation_reserve + settings.llm_timeout_seconds + 1:
            warnings.append("为行程生成与校验预留时间，已停止追加搜索决策；资料可能不完整。")
            break
        budget.phase = "SEARCH_DECISION"
        choice = {"type": "function", "function": {"name": "search_travel_info"}} if round_index == 0 else "auto"
        result = await adapters.complete(messages, tools=[SEARCH_TOOL], tool_choice=choice)
        messages.append(result.message)
        if not result.tool_calls:
            break
        if len(result.tool_calls) > 20:
            raise _output_error()
        for call in result.tool_calls:
            if call.id in seen_calls:
                raise _output_error()
            seen_calls.add(call.id)
            try:
                arguments = SearchArguments.model_validate(call.arguments)
                if not arguments.query.strip():
                    raise ValueError
                if request.days > 1 and arguments.purpose == "price" and any(deficits(request, _place_catalog(request, evidence), source_purposes).values()):
                    payload = {"error": "COVERAGE_FIRST", "message": "先补齐景点及餐厅覆盖，再检索价格。"}
                elif budget.counts["search_calls"] >= settings.max_search_calls:
                    payload = {"error": "SEARCH_CALL_LIMIT", "message": "搜索次数已用完。"}
                elif evidence and budget.remaining() < generation_reserve + settings.search_timeout_seconds + 1:
                    payload = {"error": "SEARCH_TIME_RESERVED", "message": "剩余时间已预留给行程生成与校验，不再执行本次搜索。"}
                    warnings.append("为行程生成与校验预留时间，已跳过部分搜索；资料可能不完整。")
                else:
                    budget.phase = "TRAVEL_SEARCH"
                    query = restaurant_discovery_query(request.destination, arguments.query) if arguments.purpose == "restaurant" else arguments.query
                    freshness = "oneYear" if arguments.purpose in {"restaurant", "opening_hours", "price"} else "noLimit"
                    search = screen_venue_results(await adapters.search(query, freshness=freshness))
                    evidence.update({hit.id: hit for hit in search.results})
                    for hit in search.results:
                        source_purposes.setdefault(hit.id, set()).add(arguments.purpose)
                    warnings.extend(search.warnings)
                    payload = search.model_dump(mode="json")
                    payload["price_clues"] = price_clues(search.results)
                    payload["remaining_search_calls"] = max(0, settings.max_search_calls - budget.counts["search_calls"])
                    payload["price_follow_up_needed"] = not bool(payload["price_clues"])
                    payload["coverage_hint"] = {
                        "trip_days": request.days,
                        "restaurants_per_day": 2,
                        "meal_slots": ["lunch", "dinner"],
                        "attraction_source_count": sum('attraction' in p for p in source_purposes.values()),
                        "restaurant_source_count": sum('restaurant' in p for p in source_purposes.values()),
                        "note": "来源数量不等于独立地点数量。多天优先补不同景点，避免只反复查价格；缺价允许未知。"}
                    payload["source_notice"] = "网页标题和摘要仅作线索，未核验正文、官方身份、当前分店或价格有效期。"
            except (ValidationError, ValueError):
                payload = {"error": "INVALID_ARGUMENT", "message": "query 和 purpose 不符合工具参数要求。"}
            except ProviderError as exc:
                if exc.code in {"AUTH_FAILED", "QUOTA_EXHAUSTED", "CONFIGURATION_ERROR", "TOTAL_TIMEOUT", "CALL_LIMIT"}:
                    raise
                warnings.append("部分搜索失败或返回异常，行程仅依据成功检索的资料。")
                payload = {"error": exc.code, "message": exc.message}
            messages.append({"role": "tool", "tool_call_id": call.id,
                             "content": json.dumps(payload, ensure_ascii=False)})
        if request.days > 1:
            await supplement_coverage(request, adapters, evidence, source_purposes, messages, warnings, _place_catalog, coverage_attempted)
        if budget.counts["search_calls"] >= settings.max_search_calls:
            warnings.append("已达到搜索次数上限，行程仅依据本次已获取资料。")
            break
    if not evidence:
        raise ProviderError("search", "INSUFFICIENT_EVIDENCE", "没有获得可用旅行资料，请调整目的地或稍后重试。", True)
    catalog = _place_catalog(request, evidence)
    if not catalog:
        logger.warning("request_id=%s error_code=INSUFFICIENT_EVIDENCE phase=CANDIDATE_EXTRACTION evidence_count=%s candidate_count=0 counts=%s",
                       budget.request_id, len(evidence), budget.counts)
        raise ProviderError("search", "INSUFFICIENT_EVIDENCE", "搜索结果未形成可用的具体地点候选，已停止生成；请调整检索条件。", True)
    require_candidates(request, catalog, source_purposes, budget)
    combined, repair_used = [], False
    for offset in range(0, request.days, 5):
        part = request.model_copy(update={"days": min(5, request.days - offset),
                                         "departure_date": request.departure_date + timedelta(days=offset)})
        used_names = {a.location for d in combined for a in d.activities}
        previous_place_ids = [pid for pid, place in catalog.items() if place["name"] in used_names]
        instructions = {"segment_request": part.model_dump(mode="json"),
                        "previous_place_ids": previous_place_ids,
                        "place_catalog": catalog,
                        "source_rule": "只选择候选place_id，名称和来源由后端绑定；价格和营业引文仅取该候选对应来源。",
                        "instructions": "只生成 segment_request 日期范围，day 从 1 开始。优先避免重复已有地点。每天合理安排，描述简短，不为凑数量添加活动。"}
        segment_messages = messages + [{"role": "system", "content": OUTPUT_RULES + "\n" + GENERATION_EXAMPLES},
                                      {"role": "user", "content": json.dumps(instructions, ensure_ascii=False)}]
        parsed, repaired, fact_warnings = await _generate_segment(part, adapters, evidence, segment_messages, not repair_used, source_purposes, chunk_count - offset // 5 - 1, catalog, request.days > 1)
        warnings.extend((f"第 {offset + 1}–{offset + part.days} 天：" + warning) if chunk_count > 1 else warning for warning in fact_warnings)
        repair_used = repair_used or repaired
        combined.extend(day.model_copy(update={"day": offset + index + 1}) for index, day in enumerate(parsed.days))
    trip = assemble_trip(GeneratedTrip(days=combined), request, evidence, budget.request_id, warnings)
    if any(a.estimated_cost is None for d in trip.days for a in d.activities) and not price_clues(list(evidence.values())):
        trip.warnings.append("本次搜索摘要没有价格线索，无法可靠估算；可在调用预算允许时定向核实门票和餐厅人均消费，当前上限未自动提高。")
    classifications = _classifications(GeneratedTrip(days=combined))
    trip = improve_schedule(trip, source_purposes, classifications)
    trip = annotate_plan(trip, source_purposes, any(i != "地道美食" for i in request.interests), classifications)
    if request.days > 1:
        gaps = schedule_coverage_gaps(trip, source_purposes, classifications, catalog,
                                      any(i != "地道美食" for i in request.interests))
        if gaps:
            logger.warning("request_id=%s error_code=ITINERARY_COVERAGE_MISSING phase=FINAL_COVERAGE gaps=%s counts=%s", budget.request_id, gaps, budget.counts)
            raise ProviderError("model", "ITINERARY_COVERAGE_MISSING",
                "生成结果未达到多日行程最低覆盖：" + coverage_gap_message(gaps) +
                "。本次修复或时间预算内未补齐，未返回缺餐或重复景点凑成的行程。", True)
    trip = await complete_online_prices(trip, evidence, adapters)
    # Preserve internal activity intent through price checks, then render neutral text.
    for day in trip.days:
        for activity in day.activities:
            activity.description = f'建议前往{activity.location}；具体体验、开放及预约要求请以来源和现场确认为准。'
    return trip


def _repair_issues(output: GeneratedTrip, request: TripRequest, evidence: dict,
                   first_error: ProviderError) -> list[dict[str, str]]:
    """Bounded, value-free diagnostics; never changes the candidate or evidence."""
    issues, seen = [], set()

    def add(error, day_index=None, activity_index=None):
        reason = getattr(error, "validation_reason", None)
        field = getattr(error, "validation_field", None)
        if reason not in VALIDATION_HINTS or not field or len(issues) >= 20:
            return
        if activity_index is not None:
            field = field.replace("days.0.activities.0", f"days.{day_index}.activities.{activity_index}", 1)
        elif day_index is not None:
            field = field.replace("days.0", f"days.{day_index}", 1)
        key = (reason, field)
        if key not in seen:
            seen.add(key)
            issues.append({"check": reason, "field": field, "hint": VALIDATION_HINTS[reason]})

    add(first_error)
    for index, day in enumerate(output.days[:request.days]):
        if len(issues) >= 20:
            break
        if day.day != index + 1:
            add(_output_error("day_number", f"days.{index}.day"))
        part = request.model_copy(update={"days": 1, "departure_date": request.departure_date + timedelta(days=index)})
        probe_day = day.model_copy(update={"day": 1})
        try:
            assemble_trip(GeneratedTrip(days=[probe_day]), part, evidence, "repair-check", [])
        except ProviderError as error:
            add(error, index)
        for activity_index, activity in enumerate(day.activities):
            if len(issues) >= 20:
                break
            # Isolate each activity so an earlier price error cannot hide later bad sources.
            probe_day = day.model_copy(update={"day": 1, "date": part.departure_date.isoformat(),
                                              "activities": [activity]})
            try:
                assemble_trip(GeneratedTrip(days=[probe_day]), part, evidence, "repair-check", [])
            except ProviderError as error:
                add(error, index, activity_index)
    return issues


def _unknown_unverified_facts(output: GeneratedTrip, request: TripRequest,
                              evidence: dict) -> tuple[GeneratedTrip, list[str]]:
    """Project unsupported optional facts to explicit unknowns, never invent evidence.

    Structural, place and source errors remain fatal. The strict assembler is still
    applied to the complete candidate after this projection.
    """
    candidate = output.model_copy(deep=True)
    counts = {"cost": 0, "hours": 0, "estimated": 0}
    cost_checks = {"cost_quote_missing", "cost_zero_unsupported", "cost_price_missing", "reference_price_mismatch"}
    for index, day in enumerate(candidate.days):
        part = request.model_copy(update={"days": 1, "departure_date": request.departure_date + timedelta(days=index)})
        for activity in day.activities:
            for _ in range(3):  # Cost and hours may each need one projection.
                probe = day.model_copy(update={"day": 1, "date": part.departure_date.isoformat(), "activities": [activity]})
                try:
                    assemble_trip(GeneratedTrip(days=[probe]), part, evidence, "fact-check", [])
                    break
                except ProviderError as error:
                    reason = getattr(error, "validation_reason", "")
                    if reason in cost_checks:
                        relevant = [evidence[s] for s in dict.fromkeys(activity.source_ids) if s in evidence
                                    and _compact(activity.location) in _compact(evidence[s].title + " " + evidence[s].snippet)]
                        estimate = estimate_from_sources(activity.location, relevant, request.travelers)
                        activity.estimated_cost = estimate["amount"] if estimate else None
                        activity.cost_status = "estimated" if estimate else "unknown"
                        activity.cost_basis = estimate["basis"] if estimate else ""
                        activity.cost_evidence = estimate["quote"] if estimate else None
                        counts["estimated" if estimate else "cost"] += 1
                    elif reason == "hours_quote_missing":
                        activity.opening_hours = None
                        activity.opening_hours_status = "unknown"
                        activity.opening_hours_evidence = None
                        counts["hours"] += 1
                    else:
                        break
    warnings = []
    if counts["cost"]:
        warnings.append(f"有 {counts['cost']} 项模型费用未通过来源证据校验，已标为未知；未知不代表免费，请出行前核实。")
    if counts["estimated"]:
        warnings.append(f"有 {counts['estimated']} 项模型费用未通过证据校验，已改用网上同地点价格样本估算；请核对来源及实际收费。")
    if counts["hours"]:
        warnings.append(f"有 {counts['hours']} 项模型营业时间未通过来源证据校验，已标为未知，请出行前向官方核实。")
    return candidate, warnings


def _observed_candidates(request, evidence):
    """Keep exact observed spellings, including branch suffixes, never aliases."""
    evidence = {sid: hit for sid, hit in evidence.items() if venue_source_usable(hit.title)}
    names = []
    for hit in evidence.values():
        names.extend(explicit_names(hit.title, hit.snippet))
    candidates = {}
    for name in dict.fromkeys(names):
        if len(candidates) >= 60:
            break
        ids = [sid for sid, hit in evidence.items() if _compact(name) and _compact(name) in _compact(hit.title + " " + hit.snippet)][:5]
        if not ids:
            continue
        # Use the original place checks, including generic/city rejection.
        activity = GeneratedActivity(activity_type="other", meal_slot=None, time="09:00 - 10:00", location=name, description="候选地点",
            estimated_cost=None, cost_status="unknown", cost_basis="", cost_evidence=None,
            source_ids=ids, opening_hours=None, opening_hours_status="unknown", opening_hours_evidence=None)
        day = GeneratedDay(day=1, date=request.departure_date.isoformat(), title="候选地点", activities=[activity])
        try:
            assemble_trip(GeneratedTrip(days=[day]), request.model_copy(update={"days":1}), evidence, "candidate-check", [])
        except ProviderError:
            continue
        candidates[name] = ids
    return candidates


def _place_catalog(request, evidence):
    """Request-local IDs from observed titles; no model names or alias merging."""
    return {f"place_{i}": {"name": name, "source_ids": ids,
                             "dining_supported": any(dining_context(evidence[sid].title, evidence[sid].snippet) for sid in ids)}
            for i, (name, ids) in enumerate(_observed_candidates(request, evidence).items(), 1)}


def _place_output_model(catalog):
    def place_schema(schema):
        schema.pop("const", None)
        schema["enum"] = list(catalog)
    activity = create_model("SelectedActivity", __base__=ActivityFacts,
        place_id=(Literal[tuple(catalog)], Field(json_schema_extra=place_schema)))
    day = create_model("SelectedDay", __base__=GeneratedDay,
        activities=(list[activity], Field(min_length=1, max_length=12)))
    return create_model("SelectedTrip", __base__=StrictOutput,
        days=(list[day], Field(min_length=1, max_length=14)))


def _bind_places(selection, catalog):
    data = selection.model_dump()
    for day in data["days"]:
        for activity in day["activities"]:
            place_id = activity.pop("place_id")
            if place_id not in catalog:
                raise _output_error()
            place = catalog[place_id]
            activity.update(location=place["name"], source_ids=list(place["source_ids"]))
    return GeneratedTrip.model_validate(data)



def _classifications(output):
    """Request-local classification; deliberately excluded from public responses."""
    return {(day.day, index): (activity.activity_type, activity.meal_slot)
            for day in output.days for index, activity in enumerate(day.activities)}


async def _generate_segment(request, adapters, evidence, messages, allow_repair, source_purposes=None, remaining_chunks=0, catalog=None, coverage_required=False):
    """Validate each segment; one repair allowance is shared by the whole trip."""
    catalog = catalog if catalog is not None else _place_catalog(request, evidence)
    if not catalog:
        raise ProviderError("search", "INSUFFICIENT_EVIDENCE", "没有可用的具体地点候选，已停止生成。", True)
    output_model = _place_output_model(catalog)
    source_purposes = source_purposes or {}
    meal_fallback = None
    for attempt in range(2 if allow_repair else 1):
        result, bound = None, None
        try:
            adapters.budget.phase = "ITINERARY_GENERATION" if attempt == 0 else "ITINERARY_REPAIR"
            result = await adapters.complete(messages, output_model=output_model)
            if not isinstance(result.parsed, output_model):
                raise _output_error()
            bound = _bind_places(result.parsed, catalog)
            candidate, fact_warnings = _unknown_unverified_facts(bound, request, evidence)
            validated = assemble_trip(candidate, request, evidence, adapters.budget.request_id, [])
            classes = _classifications(candidate)
            checked = improve_schedule(validated, source_purposes, classes) if coverage_required else validated
            gaps = (schedule_coverage_gaps(checked, source_purposes, classes, catalog,
                    any(i != "地道美食" for i in request.interests)) if coverage_required else
                    meal_gaps(checked, source_purposes, classes))
            restaurant_sources = sum('restaurant' in purposes for purposes in source_purposes.values())
            # Use the existing global repair allowance, never another search loop.
            if gaps and (coverage_required or restaurant_sources >= 2) and attempt == 0 and allow_repair:
                if adapters.budget.remaining() < adapters.settings.llm_timeout_seconds * (remaining_chunks + 1) + 1:
                    return candidate, False, fact_warnings + (["剩余时间优先用于完成行程，已跳过覆盖修复；最终仍需通过覆盖检查。"] if coverage_required else ["剩余时间优先用于完成行程，已跳过可选餐次补充；餐次缺口请确认。"])
                meal_fallback = (candidate, fact_warnings)
                messages.append(result.message)
                messages.append({"role": "user", "content":
                    "仅选择已有候选place_id，使用后端绑定的名称与来源。行程覆盖／餐次检查：" + json.dumps(gaps, ensure_ascii=False) +
                    "。请只依据已有餐厅来源补齐每天两家不同门店的午餐和晚餐，观光行程必须为每天匹配一个不同景点，不能将同一景点用于多天凑数。"
                    "每餐至少30分钟，安排不能重叠或落在已知闭馆/午休时间。价格可未知，不新增无来源门店。"
                    "重新输出完整行程JSON，不能调用工具。"})
                continue
            return candidate, attempt == 1, fact_warnings
        except ProviderError as exc:
            if meal_fallback is not None and exc.code == "INVALID_MODEL_OUTPUT":
                # This is a validated live draft, never synthetic/demo fallback.
                saved, saved_warnings = meal_fallback
                return saved, True, saved_warnings + (["覆盖修复输出未通过校验，保留此前核心字段有效的草稿，最终仍需通过覆盖检查。"] if coverage_required else ["餐厅补充输出未通过校验，保留此前已验证的真实行程；餐次缺口仍需确认。"])
            if hasattr(exc, "validation_reason"):
                logger.warning("request_id=%s error_code=INVALID_MODEL_OUTPUT phase=ITINERARY_VALIDATION check=%s fields=%s counts=%s",
                               adapters.budget.request_id, exc.validation_reason, exc.validation_field, adapters.budget.counts)
            if exc.code != "INVALID_MODEL_OUTPUT" or attempt == 1 or not allow_repair:
                raise
            if adapters.budget.remaining() < adapters.settings.llm_timeout_seconds * (remaining_chunks + 1) + 1:
                # Invalid core output is never returned as a successful itinerary.
                exc.message += " 剩余时间不足以预留完整修复窗口，已停止修复，未返回无效行程。"
                raise
            if result is not None:
                # Retain only in request memory and the model conversation, never logs.
                messages.append(result.message)
            issues = _repair_issues(bound, request, evidence, exc) if bound is not None else []
            feedback = " 本轮逐项检查：" + json.dumps(issues, ensure_ascii=False) if issues else ""
            specific = ""
            if any(issue["check"] in {"cost_quote_missing", "cost_price_missing", "reference_price_mismatch", "cost_zero_unsupported"} for issue in issues):
                specific = " 对失败活动重新检查价格原文；无法在对应地点来源中确认时，将 estimated_cost、cost_evidence 设 null，cost_status 设 unknown，cost_basis 设空字符串。不要只改引文措辞或自行推算价格。"
            messages.append({"role": "user", "content": "后端校验反馈：" + exc.message + feedback + specific + " 请重新依据已有资料输出完整 JSON，必须提供 Schema 中所有必填字段。金额为两位小数字符串，未知必须显式填 null；cost_basis 未知时填空字符串。核对日期、天数、时间顺序、候选place_id、费用和营业时间证据。无法证实的费用及营业时间设为未知。逐项修复全部检查，只选候选表内place_id；删除无法获得具体地点证据的活动，仍需每天至少一项有证据的活动，否则不能生成成功行程。不得调用工具。"})
    raise AssertionError("unreachable")
