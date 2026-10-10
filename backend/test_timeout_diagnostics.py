"""Safe timeout diagnostics, with HTTP mock and no paid requests."""
import unittest
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.providers import ProviderError
from backend.test_agent import REQUEST, SETTINGS
from backend.test_support import check_local_socketpair


def setUpModule():
    check_local_socketpair()


class TimeoutDiagnosticsTests(unittest.TestCase):
    def test_model_timeout_names_service_and_logs_only_backend_metrics(self):
        calls=[]
        def handle(request):
            calls.append(request.url.path)
            raise httpx.ReadTimeout('PRIVATE-UPSTREAM-TEXT',request=request)
        with self.assertLogs('backend.main',level='WARNING') as captured:
            with TestClient(create_app(SETTINGS,transport=httpx.MockTransport(handle))) as client:
                response=client.post('/api/itineraries',json=REQUEST.model_dump(mode='json'))
        self.assertEqual(response.status_code,502)
        error=response.json()['error']
        self.assertEqual(error['code'],'UPSTREAM_TIMEOUT')
        self.assertTrue(error['retryable'])
        self.assertIn('千问模型',error['message'])
        self.assertIn('45',error['message'])
        self.assertIn('总期限',error['message'])
        self.assertEqual(len(calls),1)
        log=' '.join(captured.output)
        self.assertIn(error['request_id'],log)
        self.assertIn('provider=model',log)
        self.assertIn('elapsed_ms=',log)
        self.assertIn("'model_calls': 1",log)
        self.assertIn("'model_http_attempts': 1",log)
        for sensitive in ['PRIVATE-UPSTREAM-TEXT','Authorization','test-key','杭州']:
            self.assertNotIn(sensitive,log)
            self.assertNotIn(sensitive,response.text)

    def test_search_timeout_uses_search_limit_and_does_not_change_configuration(self):
        async def fail(*args,**kwargs):
            raise ProviderError('search','UPSTREAM_TIMEOUT','PRIVATE-UPSTREAM-TEXT',True)
        settings=SETTINGS.model_copy(update={'search_timeout_seconds':7.5})
        with patch('backend.main.run_live',fail),self.assertLogs('backend.main',level='WARNING') as logs:
            with TestClient(create_app(settings)) as client:
                response=client.post('/api/itineraries',json=REQUEST.model_dump(mode='json'))
        error=response.json()['error']
        self.assertEqual(response.status_code,502)
        self.assertIn('博查搜索',error['message'])
        self.assertIn('7.5',error['message'])
        self.assertEqual(settings.search_timeout_seconds,7.5)
        self.assertIn('provider=search',' '.join(logs.output))
        self.assertNotIn('PRIVATE-UPSTREAM-TEXT',response.text)

    def test_total_timeout_keeps_distinct_code_and_message(self):
        async def fail(*args,**kwargs):
            raise ProviderError('budget','TOTAL_TIMEOUT','生成请求已超过总时限。',True)
        with patch('backend.main.run_live',fail):
            with TestClient(create_app(SETTINGS)) as client:
                response=client.post('/api/itineraries',json=REQUEST.model_dump(mode='json'))
        self.assertEqual(response.status_code,504)
        self.assertEqual(response.json()['error']['code'],'TOTAL_TIMEOUT')
        self.assertEqual(response.json()['error']['message'],'生成请求已超过总时限。')

    def test_total_timeout_logs_phase_elapsed_and_counts_from_live_runner(self):
        async def fail(trip, adapters):
            adapters.budget.phase = "ITINERARY_REPAIR"
            adapters.budget.consume("model")
            raise ProviderError("budget", "TOTAL_TIMEOUT", "生成请求已超过总时限。", True)
        with patch('backend.main.generate_live', fail), self.assertLogs('backend.main', level='WARNING') as logs:
            with TestClient(create_app(SETTINGS)) as client:
                response = client.post('/api/itineraries', json=REQUEST.model_dump(mode='json'))
        self.assertEqual(response.status_code, 504)
        log = ' '.join(logs.output)
        self.assertIn(response.json()['error']['request_id'], log)
        self.assertIn('phase=ITINERARY_REPAIR', log)
        self.assertIn('elapsed_ms=', log)
        self.assertIn("'model_calls': 1", log)
        for secret in ['test-key', 'Authorization', '杭州']:
            self.assertNotIn(secret, log)


if __name__=='__main__':unittest.main()
