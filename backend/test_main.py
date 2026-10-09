import os
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch
from fastapi.testclient import TestClient
from pydantic import ValidationError
from backend.config import Settings
from backend.main import create_app
from backend.schemas import Activity, DayPlan, Source, TripResponse
from backend.test_support import check_local_socketpair

VALID = dict(destination=" 杭州 ", departure_date="2026-10-08", days=3, travelers=2, budget="3000.00", currency="CNY", interests=["自然风光", "地道美食"])


def setUpModule():
    check_local_socketpair()


class TripTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(create_app(Settings()))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def test_health(self):
        health = self.client.get("/api/health").json()
        self.assertEqual(health["status"], "ok")
        self.assertTrue(health["demo"] and health["configured"] and health["ready"])
        self.assertEqual(health["mode"], "demo")
        self.assertEqual(health["supported_currencies"], ["CNY", "USD", "EUR", "JPY"])
        self.assertFalse(health["live_integration_available"])

    def test_plan_and_cost_totals(self):
        response = self.client.post("/api/itineraries", json=VALID)
        self.assertEqual(response.status_code, 200)
        plan = response.json()
        self.assertTrue(plan["demo"])
        self.assertEqual(plan["mode"], "demo")
        self.assertEqual(plan["destination"], "杭州")
        self.assertEqual([day["date"] for day in plan["days"]], ["2026-10-08", "2026-10-09", "2026-10-10"])
        self.assertEqual(len(plan["days"]), 3)
        self.assertTrue(plan["request_id"])
        total = Decimal("0")
        for day in plan["days"]:
            self.assertEqual(len(day["activities"]), 4)
            for a in day["activities"]:
                self.assertTrue(a["time"] and a["location"] and a["description"])
                self.assertEqual(a["cost_status"], "demo")
                self.assertIn("模拟地点", a["location"])
                self.assertEqual(a["sources"], [])
                self.assertIsNone(a["opening_hours"])
                self.assertRegex(a["estimated_cost"], r"^\d+\.\d{2}$")
            subtotal = sum(Decimal(a["estimated_cost"]) for a in day["activities"])
            self.assertEqual(subtotal, Decimal(day["estimated_cost"]))
            total += subtotal
        self.assertEqual(total, Decimal(plan["total_cost"]))
        self.assertEqual(plan["known_cost_subtotal"], plan["total_cost"])
        self.assertLessEqual(total, Decimal(VALID["budget"]))

    def test_invalid_requests(self):
        for field, value in [("destination", "  "), ("days", 0), ("days", 15), ("days", 1.5), ("travelers", 0), ("travelers", 21), ("budget", "-1"), ("budget", "1.001"), ("currency", "XXX"), ("interests", []), ("interests", ["未知"]), ("departure_date", "invalid"), ("departure_date", "9999-12-31")]:
            with self.subTest(field=field, value=value):
                response = self.client.post("/api/itineraries", json={**VALID, field: value})
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["error"]["code"], "INVALID_INPUT")

    def test_boundaries_and_currencies(self):
        for currency in ["CNY", "USD", "EUR", "JPY"]:
            response = self.client.post("/api/itineraries", json={**VALID, "days": 14, "travelers": 20, "budget": "0.01", "currency": currency, "departure_date": "2100-12-18"})
            self.assertEqual(response.status_code, 200)
            plan = response.json()
            self.assertEqual(plan["currency"], currency)
            self.assertEqual(plan["days"][-1]["date"], "2100-12-31")
            self.assertLessEqual(Decimal(plan["total_cost"]), Decimal("0.01"))

    def test_regenerate_uses_unique_request_ids(self):
        one = self.client.post("/api/itineraries", json={**VALID, "days": 1}).json()
        two = self.client.post("/api/itineraries", json={**VALID, "days": 1}).json()
        self.assertNotEqual(one["request_id"], two["request_id"])


class ConfigurationTests(unittest.TestCase):
    def test_environment_is_explicit_and_secrets_are_redacted(self):
        with patch.dict(os.environ, {"TRAVEL_MODE": "live", "DASHSCOPE_API_KEY": "test-secret-one", "BOCHA_API_KEY": "test-secret-two", "LLM_BASE_URL": "https://example.com/v1"}, clear=True):
            settings = Settings.from_env()
            self.assertTrue(settings.configured)
            self.assertEqual(settings.mode, "live")
            self.assertNotIn("test-secret", repr(settings))
            with TestClient(create_app(settings)) as client:
                response = client.get("/api/health")
                self.assertNotIn("test-secret", response.text)

    def test_invalid_mode_does_not_fall_back(self):
        with patch.dict(os.environ, {"TRAVEL_MODE": "typo"}, clear=True):
            with self.assertRaises(ValidationError):
                Settings.from_env()

    def test_invalid_urls_are_configuration_issues(self):
        for url in ["", "http://example.com", "https://user:pass@example.com", "https://example.com:bad"]:
            with self.subTest(url=url):
                settings = Settings(mode="live", dashscope_api_key="test", bocha_api_key="test", llm_base_url=url)
                self.assertIn("LLM_BASE_URL", settings.configuration_issues)

    def test_live_missing_configuration_never_calls_demo(self):
        with TestClient(create_app(Settings(mode="live"))) as client, patch("backend.main.generate_demo", side_effect=AssertionError("must not use demo")):
            health = client.get("/api/health").json()
            self.assertEqual(health["mode"], "live")
            self.assertFalse(health["demo"] or health["ready"] or health["configured"])
            self.assertEqual(health["supported_currencies"], ["CNY"])
            response = client.post("/api/itineraries", json=VALID)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["error"]["code"], "LIVE_NOT_CONFIGURED")
            self.assertNotIn("days", response.json())

    def test_live_configured_still_unimplemented_and_no_external_http(self):
        settings = Settings(mode="live", dashscope_api_key="test", bocha_api_key="test", llm_base_url="https://example.com/v1")
        with TestClient(create_app(settings)) as client, patch("httpx.HTTPTransport.handle_request", side_effect=AssertionError("external HTTP prohibited")), patch("httpx.AsyncHTTPTransport.handle_async_request", side_effect=AssertionError("external HTTP prohibited")), patch("backend.main.generate_demo", side_effect=AssertionError("must not use demo")):
            health = client.get("/api/health").json()
            self.assertTrue(health["configured"])
            self.assertFalse(health["ready"])
            response = client.post("/api/itineraries", json=VALID)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["error"]["code"], "LIVE_NOT_IMPLEMENTED")
            self.assertEqual(client.post("/api/itineraries", json={**VALID, "currency": "USD"}).status_code, 422)

    def test_demo_no_external_http(self):
        with TestClient(create_app(Settings())) as client, patch("httpx.HTTPTransport.handle_request", side_effect=AssertionError("external HTTP prohibited")), patch("httpx.AsyncHTTPTransport.handle_async_request", side_effect=AssertionError("external HTTP prohibited")):
            self.assertEqual(client.get("/api/health").status_code, 200)
            self.assertEqual(client.post("/api/itineraries", json={**VALID, "days": 1}).status_code, 200)

    def test_configuration_is_frozen_at_app_creation(self):
        with TestClient(create_app(Settings())) as client, patch.dict(os.environ, {"TRAVEL_MODE": "live"}):
            self.assertEqual(client.get("/api/health").json()["mode"], "demo")


class ContractTests(unittest.TestCase):
    def activity(self, **overrides):
        return Activity(time="09:00 — 10:00", location="地点", description="建议安排", **overrides)

    def test_unknown_cost_propagates_and_zero_remains_known(self):
        known = self.activity(estimated_cost="0.00", cost_status="reference", cost_basis="来源注明免费")
        day = DayPlan(day=1, date="2026-10-08", title="行程", activities=[known, self.activity()])
        trip = TripResponse(demo=True, mode="demo", destination="杭州", travelers=2, currency="CNY", budget="100", notice="测试", request_id="test", days=[day])
        payload = trip.model_dump(mode="json")
        self.assertIsNone(payload["total_cost"])
        self.assertIsNone(payload["days"][0]["estimated_cost"])
        self.assertEqual(payload["known_cost_subtotal"], "0.00")
        self.assertEqual(payload["days"][0]["activities"][0]["estimated_cost"], "0.00")

    def test_live_contract_accepts_two_or_six_activities(self):
        source = Source(id="s1", title="来源", url="https://example.com", retrieved_at=datetime.now(timezone.utc))
        for count in [2, 6]:
            with self.subTest(count=count):
                day = DayPlan(day=1, date="2026-10-08", title="行程", activities=[self.activity(sources=[source]) for _ in range(count)])
                trip = TripResponse(demo=False, mode="live", destination="杭州", travelers=2, currency="CNY", budget="100", notice="待核实", request_id="test", days=[day])
                self.assertEqual(len(trip.days[0].activities), count)
                self.assertIsNone(trip.total_cost)

    def test_negative_invalid_or_inconsistent_costs_rejected(self):
        for amount in ["-1", "NaN", "Infinity", "1.001"]:
            with self.subTest(amount=amount), self.assertRaises(ValidationError):
                self.activity(estimated_cost=amount, cost_status="estimated", cost_basis="估算依据")
        with self.assertRaises(ValidationError):
            self.activity(estimated_cost="1.00")
        with self.assertRaises(ValidationError):
            self.activity(cost_status="estimated", cost_basis="依据")
        with self.assertRaises(ValidationError):
            self.activity(estimated_cost="1.00", cost_status="estimated")

    def test_source_safety_and_timezone(self):
        for url in ["javascript:alert(1)", "file:///secret", "https://user:pass@example.com"]:
            with self.subTest(url=url), self.assertRaises(ValidationError):
                Source(id="s1", title="来源", url=url, retrieved_at=datetime.now(timezone.utc))
        with self.assertRaises(ValidationError):
            Source(id="s1", title="来源", url="https://example.com", retrieved_at=datetime.now())

    def test_opening_hours_status(self):
        with self.assertRaises(ValidationError):
            self.activity(opening_hours="09:00-17:00")
        with self.assertRaises(ValidationError):
            self.activity(opening_hours_status="reported")
        self.assertEqual(self.activity(opening_hours="09:00-17:00", opening_hours_status="reported").opening_hours_status, "reported")

    def test_totals_are_computed_from_activities(self):
        known = self.activity(estimated_cost="1.25", cost_status="estimated", cost_basis="估算")
        day = DayPlan(day=1, date="2026-10-08", title="行程", estimated_cost="999", activities=[known, known])
        self.assertEqual(day.estimated_cost, Decimal("2.50"))


if __name__ == "__main__":
    unittest.main()
