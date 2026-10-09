"""Explicit local browser-test server; all live upstream requests use HTTP mocks."""
import copy
import json
from datetime import date, timedelta

import httpx
import uvicorn

from backend.config import Settings
from backend.main import create_app
from backend.test_agent import Fixture, SETTINGS, text_response, tool_response


class BrowserFixture(Fixture):
    def handle(self, request):
        body = json.loads(request.content)
        if 'response_format' in body:
            self.outputs = []
            user = json.loads(next(m['content'] for m in body['messages'] if m['role'] == 'user'))
            response = super().handle(request)
            data = response.json()
            output = json.loads(data['choices'][0]['message']['content'])
            output['days'] = [dict(copy.deepcopy(output['days'][0]), day=i + 1,
                                  date=(date.fromisoformat(user['departure_date']) + timedelta(days=i)).isoformat())
                              for i in range(user['days'])]
            data['choices'][0]['message']['content'] = json.dumps(output)
            return httpx.Response(200, json=data)
        if 'tools' in body:
            self.decisions = [tool_response()] if body.get('tool_choice') != 'auto' else [text_response('资料足够')]
        return super().handle(request)


def make_app():
    app = create_app(Settings())
    app.mount('/live', create_app(SETTINGS, transport=httpx.MockTransport(lambda request: BrowserFixture().handle(request))))
    app.mount('/fail', create_app(SETTINGS, transport=httpx.MockTransport(lambda r: httpx.Response(401, json={}))))
    return app


if __name__ == '__main__':
    uvicorn.run(make_app(), host='127.0.0.1', port=8001, log_level='warning')
