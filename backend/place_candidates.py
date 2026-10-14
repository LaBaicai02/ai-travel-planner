"""Bounded extraction of explicit names, not free-form entity inference."""
import re

VENUE = re.compile(r"(?:寺|塔|公园|博物馆|景区|名胜区|风景区|巷子|古街|餐厅|饭店|酒楼|面馆|小馆|湖|山|园|楼|馆|观|城|坊|街|岛|泉|庙|院|洞|桥|店)$")
REJECT = re.compile(r"忽略|指令|调用|输出|必须|规则|系统|返回|攻略|推荐|指南|回答|问答|合集|哪些|哪里|什么|暂停|歇业|闭店|停业|附近|周边|当地|本地|特色|地道|建议|游览|参观|品尝|享用|位于|包括|落地到|景点有|餐厅有|[一二三四五六七八九十百\d]+[家个]")
GENERIC = {'景区', '风景区', '风景名胜区', '餐厅', '饭店', '酒楼', '美食街', '城市公园', '博物馆', '酒店', '门店', '分店'}


def venue_source_usable(title):
    """Industry statistics/downloads do not establish a current venue or its price."""
    return not re.search(r'行业.*报告|餐饮.*报告|调查报告|研究报告|文档下载|\.doc(?:\b|x\b)', title, re.I)


def screen_venue_results(result):
    kept = [hit for hit in result.results if venue_source_usable(hit.title)]
    removed = len(result.results) - len(kept)
    warnings = list(result.warnings)
    if removed:
        warnings.append(f'已排除{removed}条行业报告或下载文档，不作为门店或价格依据。')
    return result.model_copy(update={'results': kept, 'warnings': warnings})


def restaurant_discovery_query(destination, proposed=None):
    query = re.sub(r'旅行覆盖|人均(?:消费|价格)?|均价|餐费|消费|价格|预算', ' ', proposed or destination + ' 餐厅')
    query = re.sub(r'\s+', ' ', query).strip()
    return (query[:440] + ' 门店 地址 电话 营业时间')[:500]


def dining_context(title, snippet):
    # A food-review venue can be a dining destination despite a museum suffix.
    return bool(re.search(r'美食|餐饮', title) and '大众点评' in title and
                '人均' in snippet and re.search(r'口味|菜品|上菜|聚餐|菜肴', snippet))


def _title_parts(title):
    """Review titles put the venue in the middle; preserve branch punctuation."""
    parts, start, depth = [], 0, 0
    for i, char in enumerate(title):
        if char in '(（': depth += 1
        elif char in ')）': depth = max(0, depth - 1)
        elif depth == 0 and char in '_|｜-—–':
            parts.append(title[start:i].strip())
            start = i + 1
    parts.append(title[start:].strip())
    return parts[:12]


def explicit_names(title, snippet):
    """Use title prefixes and labelled/bracketed names; never unbounded prose."""
    if not venue_source_usable(title):
        return []
    parts = _title_parts(title)
    raw = list(parts)
    # A platform suffix is not part of the venue name. Preserve branch brackets.
    head = parts[0]
    raw.append(head)
    raw.append(re.split(r'门票|票价|开放时间|营业时间|地址|电话|人均|点评|游玩攻略', head, maxsplit=1)[0])
    # Only explicit labels or name brackets in snippets; no guessed prose entities.
    raw.extend(re.findall(r'(?:^|[。；;\n])\s*(?:景点名称|景区名称|餐厅名称|门店名称|名称)\s*[:：]\s*([^。；;\n，,]{2,40})', snippet)[:20])
    raw.extend(re.findall(r'[【「]([^】」\n]{2,40})[】」]', title + '\n' + snippet)[:20])
    # Only delimited lists and direct name/price pairs, not arbitrary prose NER.
    for clause in re.split(r'[。；;\n]', snippet)[:30]:
        if '、' in clause:
            for item in clause.split('、')[:12]:
                raw.append(re.split(r'等(?:都是|是|均|都)|是热门|是著名|都不错|人均|门票|开放时间|营业时间', item, maxsplit=1)[0].strip())
        # Explicit comma-bounded branch names, e.g. 东大方延安路店.
        for item in re.split(r'[,，]', clause)[:20]:
            if item.strip().endswith('店'):
                raw.append(item.strip())
        match = re.match(r'\s*([\w·•（）()\- ]{2,40}?)\s*(?:(?:人均(?:消费)?|门票(?:价格)?)\s*[:：]?\s*[¥￥]?\s*\d|(?:营业时间|开放时间)\s*[:：]?\s*\d)', clause)
        if match:
            raw.append(match.group(1).strip())
    result = []
    for value in raw[:100]:
        name = re.sub(r'\s*(?:官方介绍|官方网站|官网|简介|介绍)$', '', value).strip(' \t\r\n-—–:：')
        stem = re.sub(r'[（(][^()（）]{1,30}[）)]$', '', name).strip()
        if not 2 <= len(name) <= 40 or REJECT.search(name) or name in GENERIC:
            continue
        if not re.fullmatch(r'[\w·•（）()\-\s]+', name) or not VENUE.search(stem):
            continue
        # Exact source spelling is retained; no alias/branch normalization.
        if name not in result:
            result.append(name)
    return result
