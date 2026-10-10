"""Complete live prices from searched evidence only; no default budgets."""
import re
from backend.price_estimates import compact, estimate_from_sources, EXCLUDED
from backend.providers import ProviderError
from backend.schemas import Source, TripResponse


def searched_price(activity, hits, travelers):
    relevant = [h for h in hits if compact(activity['location']) in compact(h.title + ' ' + h.snippet)]
    estimate = estimate_from_sources(activity['location'], relevant, travelers)
    if estimate:
        return estimate, [h for h in relevant if h.id in estimate['source_ids']], 'estimated'
    # Explicit ordinary free admission is a sourced price, not a zero fallback.
    if not re.search(r'导览|登塔|乘船|游船|缆车|演出|体验|购物', activity['description']):
        for hit in relevant:
            title = re.sub(r'\s*(?:官方介绍|官方网站|官网|简介|介绍)$', '', hit.title).strip()
            for clause in re.split(r'[。；;\n]', hit.snippet):
                if EXCLUDED.search(clause) or re.search(r'部分|不免费|并非免费|预约|特定|限时|节假日', clause):
                    continue
                if compact(activity['location']) not in compact(clause) and compact(title) != compact(activity['location']):
                    continue
                if re.search(r'门票免费|免费开放|免门票', clause):
                    return ({'amount':'0.00', 'basis':f'{travelers}人，来源记载普通入场免费：{clause.strip()[:180]}。不包含额外收费项目；出行前核实。'}, [hit], 'reference')
    return None


async def complete_online_prices(trip, evidence, adapters):
    """Enrich live prices when possible; optional missing prices remain unknown.

    Follow-up search shares the original deadline and HTTP/logical budgets.
    No model retry or arbitrary default can manufacture price coverage.
    """
    data = trip.model_dump()
    cache = {}
    missing = []
    for di, day in enumerate(data['days']):
        for ai, activity in enumerate(day['activities']):
            if activity['estimated_cost'] is not None:
                continue
            adapters.budget.remaining()
            name = activity['location']
            result = searched_price(activity, evidence.values(), trip.travelers)
            if result is None and name not in cache:
                cache[name] = True
                budget, settings = adapters.budget, adapters.settings
                if budget.remaining() < settings.search_timeout_seconds + 1:
                    data['warnings'].append('剩余时间不足，已跳过可选价格搜索；未核实费用保留未知。')
                elif budget.counts['search_calls'] < settings.max_search_calls and budget.counts['search_http_attempts'] < settings.max_search_http_attempts:
                    query = f'{trip.destination} {name} 门票价格 成人票 人均消费 人民币 官方 最新'
                    try:
                        budget.phase = 'PRICE_SEARCH'
                        search = await adapters.search(query[:500])
                        evidence.update({h.id:h for h in search.results})
                        data['warnings'].extend(search.warnings)
                    except ProviderError as exc:
                        if exc.code in {'AUTH_FAILED','QUOTA_EXHAUSTED','CONFIGURATION_ERROR','TOTAL_TIMEOUT'}:
                            raise
                        data['warnings'].append('部分价格定向搜索失败，没有使用默认价格替代。')
                    result = searched_price(activity, evidence.values(), trip.travelers)
            if result is None:
                missing.append(f'第{di+1}天第{ai+1}项')
                continue
            price, hits, status = result
            activity.update(estimated_cost=price['amount'],cost_status=status,cost_basis=price['basis'])
            price_sources = [Source.model_validate(h.model_dump(include={'id','title','url','retrieved_at'})).model_dump() for h in hits[:5]]
            activity['sources'] = list({s['id']:s for s in activity['sources'] + price_sources}.values())
    # Price coverage is optional; core itinerary/source validation remains strict.
    data['warnings'] = [w for w in data['warnings']
        if not w.startswith(('部分费用未知，完整总价不可计算','本次搜索摘要没有价格线索'))]
    if missing:
        data['warnings'].append('以下活动未取得可用价格依据：' + '、'.join(missing[:8]) +
            ('等' if len(missing)>8 else '') + '；费用保留未知，未填默认金额。')
        data['warnings'].append('部分费用未知，完整总价不可计算；已知费用小计不代表全部支出。')
        data['notice'] += ' 有依据的费用参与已知小计；未知费用不等于免费，完整总价待价格补齐后才能计算。'
    else:
        data['warnings'] = [w.replace('已标为未知', '原价格暂未确认，现已依据检索价格补齐')
            if '费用未通过来源证据校验' in w else w for w in data['warnings']]
        data['notice'] += ' 每项金额均有本次网上检索价格依据；总价包含估算，实际以售票处或菜单为准。'
    data['warnings'] = list(dict.fromkeys(data['warnings']))
    result = TripResponse.model_validate(data)
    if result.known_cost_subtotal > result.budget and not any('超过预算' in w for w in result.warnings):
        result.warnings.append(('已知活动费用小计' if missing else '预计活动总费用') + '超过预算，请调整安排；未按预算缩小金额。')
    return result
