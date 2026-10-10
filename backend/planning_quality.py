"""Bounded presentation and search hints; no network or price invention."""
import re
from datetime import date

MONEY_PATTERN = re.compile(r"[¥￥]\s*\d+(?:\.\d+)?|\d+(?:\.\d+)?\s*元")
PRICE_PATTERN = re.compile(r"[¥￥]\s*\d+(?:\.\d+)?|\d+(?:\.\d+)?\s*元|免费|免票")


def price_clues(hits):
    """Exact source excerpts, not structured prices or proof of venue association."""
    clues = []
    for hit in hits:
        sentences = re.split(r"[。；;\n]", hit.snippet)
        # A generic free-admission mention must not hide a priced tour or meal.
        sentences.sort(key=lambda text: bool(MONEY_PATTERN.search(text)), reverse=True)
        for sentence in sentences:
            match = MONEY_PATTERN.search(sentence) or PRICE_PATTERN.search(sentence)
            if not match:
                continue
            start = max(0, match.start() - 70)
            quote = sentence[start:start + 200]
            clues.append({"source_id": hit.id, "quote": quote})
            break  # At most one excerpt per result, five results per search.
        if len(clues) >= 5:
            break
    return clues


def annotate_plan(trip, source_purposes, mixed_interests=True):
    """Titles describe actual places; quality gaps remain visible without fake stops."""
    for day in trip.days:
        names = list(dict.fromkeys(a.location for a in day.activities))
        title = '、'.join(names[:2])
        if len(names) > 2:
            title += f'等{len(names)}处安排'
        day.title = title
        restaurant_only = all(a.sources and all(source_purposes.get(s.id) == {'restaurant'} for s in a.sources)
                              for a in day.activities)
        if mixed_interests and restaurant_only:
            trip.warnings.append(f'第 {day.day} 天的地点均来自餐厅类搜索，缺少已检索的观光安排；未编造景点补齐，请调整后再确认。')
        if mixed_interests and len(day.activities) < 3:
            trip.warnings.append(f'第 {day.day} 天只有 {len(day.activities)} 项有来源活动，安排较少；请核对是否符合旅行节奏。')
    trip.warnings.append('来源仅经搜索标题／摘要匹配，尚未核验网页正文、官方身份及具体分店；请以最新官方公告或门店确认为准。')
    trip.warnings = list(dict.fromkeys(trip.warnings))
    return trip


def _daily_intervals(text):
    """Parse plain daytime intervals without inferring conditions."""
    if not text:
        return None
    text = text.strip()
    if text.startswith('周一至周日 '):
        text = text[len('周一至周日 '):]
    pattern = r'(\d{1,2}):(\d{2})\s*[-—–~～至]\s*(\d{1,2}):(\d{2})'
    matches = list(re.finditer(pattern, text))
    remainder = re.sub(pattern, '', text)
    if not matches or re.sub(r'[\s;,；，、]', '', remainder):
        return None
    intervals = []
    for match in matches:
        h1, m1, h2, m2 = map(int, match.groups())
        if h1 > 23 or h2 > 23 or m1 > 59 or m2 > 59:
            return None
        start, end = h1 * 60 + m1, h2 * 60 + m2
        if end <= start:  # Overnight hours are deliberately not guessed.
            return None
        intervals.append((start, end))
    return sorted(intervals)


_WEEKDAY = {name: index for index, name in enumerate('一二三四五六日')}
_WEEKDAY['天'] = 6
_SEPARATOR = r'[-—–~～至]'
_DATE_RANGE = re.compile(
    r'(?:(?:夏令时|冬令时)\s*[:：]?\s*)?'
    r'(\d{1,2})月(\d{1,2})日\s*' + _SEPARATOR + r'\s*(次年)?'
    r'(\d{1,2})月(\d{1,2})日\s*[:：,，]?\s*'
    r'(?:开闭园时间\s*[:：]\s*)?(.+)')
_WEEK_RANGE = re.compile(
    r'(?:周|星期)([一二三四五六日天])'
    r'(?:\s*' + _SEPARATOR + r'\s*(?:周|星期)?([一二三四五六日天]))?'
    r'\s*[:：]?\s*(.+)')


def _inside(value, start, end):
    return start <= value <= end if start <= end else value >= start or value <= end


def opening_intervals(text, on_date=None):
    """Resolve explicit recurring date/weekday clauses; unknown stays None.

    [] means a stated closure on this date. None also covers gaps, conflicts,
    exceptions, mixed conditions and overnight schedules; no holidays inferred.
    """
    daily = _daily_intervals(text)
    if daily is not None or not text or on_date is None:
        return daily
    if not isinstance(on_date, date):
        return None
    # This exact label occurs in the supplied seasonal source excerpt.
    normalized = re.sub(r'[;；]\s*开闭园时间\s*[:：]', ':', text.strip())
    clauses = [part.strip() for part in re.split(r'[。;；]|[,，](?=(?:周|星期))', normalized) if part.strip()]
    selected, family = [], None
    for clause in clauses:
        dates = _DATE_RANGE.fullmatch(clause)
        weekdays = _WEEK_RANGE.fullmatch(clause)
        if dates:
            if family not in (None, 'date'):
                return None
            family = 'date'
            m1, d1, next_year, m2, d2, hours = dates.groups()
            try:
                # A leap year validates month/day syntax, including February 29.
                start, end = date(2000, int(m1), int(d1)), date(2000, int(m2), int(d2))
            except ValueError:
                return None
            if next_year and end >= start:
                return None  # Ambiguous multi-year span, not a recurring winter.
            applicable = _inside((on_date.month, on_date.day), (start.month, start.day), (end.month, end.day))
        elif weekdays:
            if family not in (None, 'weekday'):
                return None
            family = 'weekday'
            first, last, hours = weekdays.groups()
            applicable = _inside(on_date.weekday(), _WEEKDAY[first], _WEEKDAY[last or first])
        else:
            return None  # Do not ignore an unrecognized exception clause.
        intervals = [] if hours in {'闭馆', '闭园', '休息', '不开放'} else _daily_intervals(hours)
        if intervals is None:
            return None
        if applicable:
            selected.append(intervals)
    return selected[0] if len(selected) == 1 else None


def improve_schedule(trip, source_purposes):
    """Conservative, source-preserving corrections; no invented stops or charges."""
    from backend.schemas import TripResponse
    data = trip.model_dump()
    warnings = data['warnings']
    for day in data['days']:
        kept, seen_attractions = [], set()
        for activity in day['activities']:
            name = re.sub(r'\s+', '', activity['location']).casefold()
            attraction = any(source_purposes.get(s['id']) == {'attraction'} for s in activity['sources'])
            restaurant = any('restaurant' in source_purposes.get(s['id'], set()) for s in activity['sources'])
            if attraction and not restaurant:
                if name in seen_attractions:
                    warnings.append(f"第 {day['day']} 天同一景点的重复安排已移除，保留首次游览及其费用，未假定重复入场免费。")
                    continue
                seen_attractions.add(name)
            kept.append(activity)
        day['activities'] = kept
        previous_end = 0
        for index, activity in enumerate(kept):
            def minute(value):
                h, m = map(int, value.split(':'))
                return h * 60 + m
            start, end = map(minute, activity['time'].split(' - '))
            intervals = opening_intervals(activity['opening_hours'], day['date'])
            if intervals == []:
                warnings.append(f"第 {day['day']} 天第 {index + 1} 项来源记载当日闭馆或不开放，未擅自改期，请更换活动或向官方核实。")
            elif intervals is not None and not any(a <= start and end <= b for a, b in intervals):
                # Shorten only to a usable overlap with the original suggestion.
                overlaps = [(max(start, a, previous_end), min(end, b)) for a, b in intervals]
                options = [(a, b) for a, b in overlaps if b - a >= 30]
                if options:
                    start, end = max(options, key=lambda pair: pair[1] - pair[0])
                    activity['time'] = f'{start // 60:02d}:{start % 60:02d} - {end // 60:02d}:{end % 60:02d}'
                    warnings.append(f"第 {day['day']} 天第 {index + 1} 项安排已缩短至来源记载的营业时段，请核对游览或用餐时长。")
                else:
                    warnings.append(f"第 {day['day']} 天第 {index + 1} 项安排与来源营业时间冲突，无法安全自动调整，请改期或更换活动。")
            elif activity['opening_hours'] and intervals is None:
                warnings.append(f"第 {day['day']} 天第 {index + 1} 项营业时间条件不完整、相互冲突或格式暂不支持，无法确认当日时段，请人工确认。")
            previous_end = end
    data['warnings'] = [w for w in dict.fromkeys(warnings) if not w.startswith('行程包含重复地点')]
    locations = [re.sub(r'\s+', '', a['location']).casefold() for d in data['days'] for a in d['activities']]
    if len(set(locations)) < len(locations):
        data['warnings'].append('行程仍包含跨天或再次用餐的重复地点；每次活动费用分别计算，不能假定原门票可重复使用，请核对安排。')
    # Rebuild nested models to recalculate day totals and the trip subtotal.
    result = TripResponse.model_validate(data)
    result.warnings = [w for w in result.warnings if not w.startswith('已知活动费用超过预算')]
    if result.known_cost_subtotal > result.budget:
        result.warnings.append('已知活动费用超过预算，请调整安排；未缩小费用以满足预算。')
    return result
