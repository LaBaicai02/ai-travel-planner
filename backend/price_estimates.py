"""Conservative source-sample averages, never a citywide market-price claim."""
import re
from decimal import Decimal, ROUND_HALF_UP


def compact(text):
    return re.sub(r"\s+", "", text).casefold()


# Accept only explicit per-person meals or adult/ordinary admission.
PRICE = re.compile(r"(?P<kind>人均(?:消费)?|每人消费|成人(?:门)?票|门票)(?:价格|参考价|均价|约|为|价|[:：]|\s)*[¥￥]?(?P<low>\d{1,5}(?:\.\d{1,2})?)(?:\s*[-—~～至]\s*[¥￥]?(?P<high>\d{1,5}(?:\.\d{1,2})?))?\s*(?P<currency>元|人民币)?(?![\d.])")
EXCLUDED = re.compile(r"套餐|团购|优惠|儿童|学生|老人|老年|起价|起售|元起|不含|另付|另购|单品|半成品|外卖|每桌|双人|两人|家庭|套票|联票|美元|港币|日元|欧元")


def estimate_from_sources(location, hits, travelers):
    """One unambiguous sample per URL; range midpoints and same unit only.

    A price clause must name the exact place, or belong to a source whose
    complete title is the exact place. This deliberately misses ambiguous
    multi-venue snippets rather than assigning another restaurant's price.
    """
    samples, urls = [], set()
    for hit in hits:
        url = str(hit.url)
        if url in urls:
            continue
        title = re.sub(r"\s*(?:官方介绍|官方网站|官网|简介|介绍)$", "", hit.title).strip()
        matches = []
        for clause in re.split(r"[。；;\n]", hit.snippet):
            if not clause.strip() or len(clause) > 500 or EXCLUDED.search(clause):
                continue
            if compact(location) not in compact(clause) and compact(title) != compact(location):
                continue
            # More than one explicit price is ambiguous (different services/tiers).
            prices = list(PRICE.finditer(clause))
            if len(prices) != 1:
                continue
            match = prices[0]
            if not match['currency'] and not any(symbol in match[0] for symbol in ('¥', '￥')):
                continue
            low = Decimal(match['low'])
            high = Decimal(match['high']) if match['high'] else low
            if not Decimal('0') < low <= high:
                continue
            unit = '人均餐费' if match['kind'].startswith(('人均', '每人')) else '普通成人门票'
            matches.append((unit, (low + high) / 2, clause.strip(), hit.id))
        if len(matches) == 1:
            samples.append(matches[0])
            urls.add(url)
        if len(samples) >= 5:
            break
    if not samples or len({s[0] for s in samples}) != 1:
        return None
    per_person = (sum((s[1] for s in samples), Decimal('0')) / len(samples)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    total = per_person * travelers
    ids = '、'.join(s[3] for s in samples)
    basis = (f'{travelers}人；依据{len(samples)}条网上{samples[0][0]}价格样本（来源{ids}），'
             f'区间取中点，再取算术平均：每人约{per_person:.2f}元 × {travelers} = {total:.2f}元。'
             '仅为本次检索样本估算，并非全网平均价；不含额外项目，实际以菜单或售票处为准。')
    return {'amount': format(total, '.2f'), 'basis': basis[:500], 'quote': samples[0][2], 'count': len(samples)}
