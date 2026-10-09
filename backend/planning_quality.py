"""Bounded presentation and search hints; no network or price invention."""
import re

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
