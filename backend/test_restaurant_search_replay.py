"""Exact excerpts from the user's 2026-10-14 Bocha diagnostic; synthetic URLs.

Not the original failed itinerary response or a real network test.
"""
import unittest
import json
import httpx
from backend.agent import _place_catalog
from backend.place_candidates import explicit_names, restaurant_discovery_query, screen_venue_results
from backend.trip_coverage import coverage_counts, require_candidates
from backend.providers import CallBudget, ServiceAdapters, ProviderError
from backend.test_agent import REQUEST, SETTINGS


EXCERPTS = [
    ('大众点评网餐饮行业系列报告之杭州_文档下载',
     '目前杭州市场较为热门的杭帮江浙餐厅主要有,以连锁经营的外婆家、江南驿、楼外楼等都是点评分数较高的餐厅。'),
    ('2008年度杭州餐饮报告 - 豆丁网', '2008年杭州市民下馆子人均消费60元。其中,粤菜最贵,人均消费129元;'),
    ('“和家人聚餐,菜品胜在量多,口味一般,18个...”-中国杭帮菜博物馆-杭州美食-大众点评网',
     '口味:一般 环境:一般 服务:一般 人均:50元 对于一次不算美好的体验,表示性价比很低,不想再来!! 其实一开始气氛还是可以的......除了上菜速度不算特别快'),
    ('夏日时光旅游美食之杭帮菜_聚会餐饮_什么值得买',
     '杭州之旅,落地到酒店,就在酒店附近找了一家杭帮菜,东大方延安路店,第一次吃杭帮菜,感觉南方的菜量没有北方的菜量大,先看看大众点评,然后在大众点评选了一份两人套餐,加了两个菜'),
    ('2010杭州餐厅调查报告.doc - 豆丁网',
     '评评数据依据:大众点评评高档餐定价位:人均消250元以上评评评评高档餐数量估数量名称菜系价格粤菜434')
]


class RestaurantReplayTests(unittest.IsolatedAsyncioTestCase):
    async def test_user_search_excerpts_recover_two_venues_and_exclude_reports(self):
        query = restaurant_discovery_query('杭州', '杭州 杭帮菜 餐厅 分店 人均消费 午餐 晚餐')
        self.assertNotIn('人均', query)
        self.assertNotIn('消费', query)
        self.assertIn('地址 电话 营业时间', query)
        calls = []
        def handle(request):
            calls.append(request)
            return httpx.Response(200, json={'code':200, 'data':{'webPages':{'value':[
                {'name': title, 'summary': snippet, 'url': f'https://replay.example/{i}'}
                for i,(title,snippet) in enumerate(EXCERPTS)]}}})
        budget = CallBudget(SETTINGS)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            adapter = ServiceAdapters(SETTINGS, client, budget)
            raw = await adapter.search(query, freshness="oneYear")
            with self.assertRaises(ProviderError):
                await adapter.search(query, freshness="invalid")
        self.assertEqual(len(calls),1)
        self.assertEqual(json.loads(calls[0].content)["freshness"],"oneYear")
        result = screen_venue_results(raw)
        self.assertEqual(len(result.results),2)
        self.assertTrue(any('3条' in warning for warning in result.warnings))
        evidence = {hit.id:hit for hit in result.results}
        purposes = {hit.id:{'restaurant'} for hit in result.results}
        catalog = _place_catalog(REQUEST,evidence)
        self.assertEqual({p['name'] for p in catalog.values()}, {'中国杭帮菜博物馆','东大方延安路店'})
        self.assertEqual(len(coverage_counts(catalog,purposes)[1]),2)
        require_candidates(REQUEST.model_copy(update={'days':3,'interests':['地道美食']}),catalog,purposes)
        self.assertEqual(explicit_names(*EXCERPTS[0]),[])
        self.assertEqual(explicit_names('评论', '外婆家、江南驿、楼外楼等都是点评分数较高的餐厅'),['楼外楼'])
        # A plain museum is still not a restaurant without the food-review context.
        plain = {'place': {'name':'浙江自然博物馆','source_ids':['plain']}}
        self.assertEqual(coverage_counts(plain,{'plain':{'restaurant'}})[1],set())
