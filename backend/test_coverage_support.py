"""Explicit multi-day fixtures with diverse places and two sourced meals."""
import copy
import json
from datetime import date
import httpx
from backend.test_agent import REQUEST


def with_multi_coverage(fixture):
    original = fixture.handle
    def handle(request):
        body = json.loads(request.content)
        if request.url.path.endswith('web-search') and '餐厅 门店 地址 电话' in body['query']:
            fixture.requests.append((request.url.path, body))
            return httpx.Response(200, json={'code':200, 'data':{'webPages':{'value':[
                {'name': name, 'url':f'https://food.example/{i}', 'summary':f'{name}人均消费10元，提供午餐和晚餐。'}
                for i, name in enumerate(['梅园餐厅', '湖畔饭店'])]}}})
        response = original(request)
        if request.url.path.endswith('web-search') and response.status_code == 200:
            data = response.json()
            rows = data.get('data', {}).get('webPages', {}).get('value', [])
            if rows:
                rows[0]['summary'] += ''.join(f'；景点名称：测试{i}公园。测试{i}公园导览参考价80元/人。' for i in range(1,14))
            return httpx.Response(200, json=data)
        if 'response_format' not in body:
            return response
        data = response.json()
        try:
            output = json.loads(data['choices'][0]['message']['content'])
        except ValueError:
            return response
        try: catalog = json.loads(body['messages'][-1]['content']).get('place_catalog')
        except (ValueError, AttributeError): catalog = None
        if catalog is None:
            # Repair messages are prose; the catalog is in the earlier instruction.
            for message in body['messages']:
                try: catalog = json.loads(message['content']).get('place_catalog') or catalog
                except (ValueError, AttributeError, TypeError): pass
        ids = {p['name']: pid for pid,p in catalog.items()}
        for day in output['days']:
            offset = (date.fromisoformat(day['date']) - REQUEST.departure_date).days
            base = day['activities'][0]
            base['place_id'] = ids['西湖' if offset == 0 else f'测试{offset}公园']
            for slot, time, name in [('lunch','12:00 - 13:00','梅园餐厅'),('dinner','18:00 - 19:00','湖畔饭店')]:
                meal = dict(copy.deepcopy(base), place_id=ids[name], time=time, activity_type='dining', meal_slot=slot,
                            estimated_cost='20.00', cost_status='reference', cost_basis='2人，每人10元',
                            cost_evidence=f'{name}人均消费10元', opening_hours=None, opening_hours_status='unknown', opening_hours_evidence=None)
                day['activities'].append(meal)
        data['choices'][0]['message']['content'] = json.dumps(output)
        return httpx.Response(200, json=data)
    fixture.handle = handle
    return fixture
