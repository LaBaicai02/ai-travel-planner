"""Business-rule diagnostics: preserve strict evidence checks, no paid requests."""
import unittest
from datetime import datetime, timezone

import httpx

from backend.agent import GeneratedTrip, assemble_trip, generate_live
from backend.providers import ProviderError, SearchHit, ServiceAdapters
from backend.test_agent import Fixture, REQUEST, SETTINGS, output_for
from backend.test_support import check_local_socketpair


def setUpModule():
    check_local_socketpair()


def evidence():
    return {'s1': SearchHit(id='s1',title='西湖 官方介绍',url='https://example.com/westlake',
        retrieved_at=datetime.now(timezone.utc),snippet='西湖 全日开放。门票免费。导览参考价80元/人。')}


class BusinessValidationTests(unittest.TestCase):
    def check_error(self, output, reason, field):
        with self.assertRaises(ProviderError) as caught:
            assemble_trip(GeneratedTrip.model_validate(output), REQUEST, evidence(), 'test', [])
        self.assertEqual(caught.exception.code,'INVALID_MODEL_OUTPUT')
        self.assertEqual(caught.exception.validation_reason,reason)
        self.assertEqual(caught.exception.validation_field,field)
        self.assertIn('ITINERARY_VALIDATION',caught.exception.message)
        self.assertNotIn('PRIVATE-VALUE',caught.exception.message)

    def test_generic_places_rejected_even_when_present_in_source(self):
        for location in (REQUEST.destination, "地道的美食", "附近餐厅", "午餐", "自由活动"):
            with self.subTest(location=location):
                output = output_for('s1')
                output['days'][0]['activities'][0]['location'] = location
                hit = evidence()['s1'].model_copy(update={'title': location, 'snippet': location})
                with self.assertRaises(ProviderError) as caught:
                    assemble_trip(GeneratedTrip.model_validate(output), REQUEST, {'s1': hit}, 'test', [])
                self.assertEqual(caught.exception.validation_reason, 'location_generic')
                self.assertEqual(caught.exception.validation_field, 'days.0.activities.0.location')

    def test_named_restaurant_and_district_are_allowed_with_evidence(self):
        for location in ("九门轩烤鸭店(前门店)", "宽窄巷子"):
            with self.subTest(location=location):
                output = output_for('s1')
                activity = output['days'][0]['activities'][0]
                activity.update(location=location, estimated_cost=None, cost_status='unknown', cost_basis='',
                                cost_evidence=None, opening_hours=None, opening_hours_status='unknown',
                                opening_hours_evidence=None)
                hit = evidence()['s1'].model_copy(update={'title': location, 'snippet': location})
                trip = assemble_trip(GeneratedTrip.model_validate(output), REQUEST, {'s1': hit}, 'test', [])
                self.assertEqual(trip.days[0].activities[0].location, location)

    def test_specific_destination_can_be_its_own_activity(self):
        request = REQUEST.model_copy(update={'destination': '西湖'})
        output = GeneratedTrip.model_validate(output_for('s1'))
        trip = assemble_trip(output, request, evidence(), 'test', [])
        self.assertEqual(trip.days[0].activities[0].location, '西湖')

    def test_day_count_number_date_separate(self):
        for field,value,reason in [('day',2,'day_number'),('date','2027-01-01','day_date')]:
            with self.subTest(field=field):
                output=output_for('s1');output['days'][0][field]=value
                self.check_error(output,reason,'days.0.'+field)
        output=output_for('s1');output['days'] *= 2
        self.check_error(output,'day_count','days')

    def test_source_and_location_are_distinct_and_values_redacted(self):
        for field,value,reason in [('source_ids',['PRIVATE-VALUE'],'source_unknown'),('location','PRIVATE-VALUE','location_unsupported')]:
            with self.subTest(field=field):
                output=output_for('s1');output['days'][0]['activities'][0][field]=value
                self.check_error(output,reason,'days.0.activities.0.'+field)

    def test_invalid_or_overlapping_time_has_exact_activity_index(self):
        output=output_for('s1');output['days'][0]['activities'][0]['time']='25:00 - 26:00'
        self.check_error(output,'time_invalid','days.0.activities.0.time')
        output=output_for('s1');output['days'][0]['activities'] *= 2
        self.check_error(output,'time_invalid','days.0.activities.1.time')

    def test_cost_status_basis_quote_and_reference_amount_separate(self):
        cases=[
            ({'cost_status':'estimated'},'cost_status_mismatch','cost_status'),
            ({'estimated_cost':'80.00','cost_status':'reference'},'cost_basis_missing','cost_basis'),
            ({'estimated_cost':'80.00','cost_status':'reference','cost_basis':'1人参考价'},'cost_quote_missing','cost_evidence'),
            ({'estimated_cost':'500.00','cost_status':'reference','cost_basis':'来源价','cost_evidence':'导览参考价80元/人'},'reference_price_mismatch','estimated_cost'),
            ({'estimated_cost':'0.00','cost_status':'reference','cost_basis':'零价','cost_evidence':'导览参考价80元/人'},'cost_zero_unsupported','estimated_cost'),
            ({'estimated_cost':'50.00','cost_status':'estimated','cost_basis':'估算','cost_evidence':'全日开放'},'cost_price_missing','cost_evidence'),
            ({'cost_evidence':'门票免费'},'cost_unknown_with_quote','cost_evidence'),
        ]
        for changes,reason,field in cases:
            with self.subTest(reason=reason):
                output=output_for('s1');output['days'][0]['activities'][0].update(changes)
                self.check_error(output,reason,'days.0.activities.0.'+field)

    def test_opening_status_and_source_evidence_separate(self):
        for changes,reason,field in [
            ({'opening_hours_status':'reported'},'hours_status_mismatch','opening_hours_status'),
            ({'opening_hours':'09:00-17:00','opening_hours_status':'reported','opening_hours_evidence':'全日开放'},'hours_quote_missing','opening_hours_evidence'),
            ({'opening_hours_evidence':'全日开放'},'hours_unknown_with_quote','opening_hours_evidence'),
        ]:
            with self.subTest(reason=reason):
                output=output_for('s1');output['days'][0]['activities'][0].update(changes)
                self.check_error(output,reason,'days.0.activities.0.'+field)


class RepairTests(unittest.IsolatedAsyncioTestCase):
    async def test_generic_place_repairs_once_with_specific_feedback(self):
        def wrong(output):
            output['days'][0]['activities'][0]['location'] = REQUEST.destination
            return output
        fixture = Fixture(outputs=[wrong])
        async with httpx.AsyncClient(transport=httpx.MockTransport(fixture.handle)) as client:
            adapter = ServiceAdapters(SETTINGS, client)
            with self.assertLogs('backend.agent', level='WARNING'):
                trip = await generate_live(REQUEST, adapter)
        self.assertNotEqual(trip.days[0].activities[0].location, REQUEST.destination)
        self.assertIn('location_generic', fixture.requests[-1][1]['messages'][-1]['content'])
        self.assertNotIn('tools', fixture.requests[-1][1])
        self.assertEqual(adapter.budget.counts['model_calls'], 4)
        self.assertEqual(adapter.budget.counts['search_calls'], 1)

    async def test_business_repair_sees_previous_output_and_specific_reason(self):
        def wrong(output):
            output['days'][0]['activities'][0]['cost_evidence']='PRIVATE-VALUE'
            return output
        fixture=Fixture(outputs=[wrong])
        async with httpx.AsyncClient(transport=httpx.MockTransport(fixture.handle)) as client:
            adapter=ServiceAdapters(SETTINGS,client)
            with self.assertLogs('backend.agent',level='WARNING') as log:
                trip=await generate_live(REQUEST,adapter)
        self.assertFalse(trip.demo)
        repair=fixture.requests[-1][1]
        self.assertEqual(repair['messages'][-2]['role'],'assistant')
        self.assertIn('cost_unknown_with_quote',repair['messages'][-1]['content'])
        self.assertIn('days.0.activities.0.cost_evidence',repair['messages'][-1]['content'])
        self.assertNotIn('tools',repair)
        self.assertNotIn('PRIVATE-VALUE','\n'.join(log.output))
        self.assertIn('phase=ITINERARY_VALIDATION','\n'.join(log.output))
        self.assertEqual(adapter.budget.counts['model_calls'],4)
        self.assertEqual(adapter.budget.counts['search_calls'],1)

    async def test_small_live_acceptance_budget_allows_exactly_one_repair(self):
        def wrong(output):
            output['days'][0]['date']='2027-01-01'
            return output
        settings=SETTINGS.model_copy(update={'max_search_rounds':1,'max_model_calls':3,'max_search_calls':2})
        fixture=Fixture(outputs=[wrong])
        async with httpx.AsyncClient(transport=httpx.MockTransport(fixture.handle)) as client:
            adapter=ServiceAdapters(settings,client)
            with self.assertLogs('backend.agent',level='WARNING'):
                trip=await generate_live(REQUEST,adapter)
        self.assertEqual(trip.days[0].date,REQUEST.departure_date)
        self.assertEqual(adapter.budget.counts['model_calls'],3)
        self.assertEqual(adapter.budget.counts['search_calls'],1)

    async def test_still_bad_after_repair_stops_and_preserves_diagnostic(self):
        def wrong(output):
            output['days'][0]['activities'][0]['source_ids']=['PRIVATE-VALUE']
            return output
        fixture=Fixture(outputs=[wrong,wrong])
        async with httpx.AsyncClient(transport=httpx.MockTransport(fixture.handle)) as client:
            adapter=ServiceAdapters(SETTINGS,client)
            with self.assertLogs('backend.agent',level='WARNING'),self.assertRaises(ProviderError) as caught:
                await generate_live(REQUEST,adapter)
        self.assertIn('source_unknown',caught.exception.message)
        self.assertNotIn('PRIVATE-VALUE',caught.exception.message)
        self.assertEqual(adapter.budget.counts['model_calls'],4)


if __name__ == '__main__': unittest.main()
