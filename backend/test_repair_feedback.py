"""Multi-issue repair feedback; synthetic fixtures, no external calls."""
import copy
import json
import unittest
from datetime import timedelta
import httpx
from backend.agent import GeneratedTrip, _repair_issues, assemble_trip, generate_live
from backend.providers import CallBudget, ProviderError, ServiceAdapters
from backend.test_agent import Fixture, REQUEST, SETTINGS, output_for
from backend.test_itinerary_validation import evidence


def broken(output):
    first=output['days'][0]['activities'][0]
    first.update(estimated_cost='0.00',cost_status='reference',cost_basis='无免费依据',
                 cost_evidence='导览参考价80元/人')
    second=copy.deepcopy(first)
    second.update(time='12:00 - 13:00',location='PRIVATE-UNSUPPORTED-PLACE')
    output['days'][0]['activities'].append(second)
    return output


def check_issues(output):
    candidate=GeneratedTrip.model_validate(output)
    try:
        assemble_trip(candidate,REQUEST,evidence(),'test',[])
    except ProviderError as error:
        return candidate,_repair_issues(candidate,REQUEST,evidence(),error)
    raise AssertionError('Invalid fixture expected')


class FeedbackTests(unittest.TestCase):
    def test_cost_error_does_not_hide_next_location_and_values_are_private(self):
        output=broken(output_for('s1'))
        candidate,issues=check_issues(output)
        checks={(issue['check'],issue['field']) for issue in issues}
        self.assertIn(('cost_zero_unsupported','days.0.activities.0.estimated_cost'),checks)
        self.assertIn(('location_unsupported','days.0.activities.1.location'),checks)
        self.assertNotIn('PRIVATE-UNSUPPORTED-PLACE',json.dumps(issues))
        self.assertEqual(candidate.model_dump(),GeneratedTrip.model_validate(output).model_dump())
        self.assertEqual(len(checks),len(issues))

    def test_field_indices_and_overlap_preserved_across_days(self):
        output=broken(output_for('s1'))
        output['days'][0]['activities'][0].update(estimated_cost=None,cost_status='unknown',cost_basis='',cost_evidence=None)
        output['days'][0]['activities'][1]['location']='西湖'
        output['days'][0]['activities'][1].update(estimated_cost=None,cost_status='unknown',cost_basis='',cost_evidence=None,time='09:30 - 10:30')
        other=copy.deepcopy(output['days'][0]);other.update(day=3,date='2026-10-11')
        other['activities'][1]['source_ids']=['PRIVATE-SOURCE-ID']
        output['days'].append(other)
        request=REQUEST.model_copy(update={'days':2})
        candidate=GeneratedTrip.model_validate(output)
        try:
            assemble_trip(candidate,request,evidence(),'test',[])
        except ProviderError as error:
            issues=_repair_issues(candidate,request,evidence(),error)
        checks={(i['check'],i['field']) for i in issues}
        self.assertIn(('time_invalid','days.0.activities.1.time'),checks)
        self.assertIn(('day_number','days.1.day'),checks)
        self.assertIn(('source_unknown','days.1.activities.1.source_ids'),checks)
        self.assertNotIn('PRIVATE-SOURCE-ID',json.dumps(issues))

    def test_report_bounded_and_candidate_unchanged(self):
        output=output_for('s1')
        activity=output['days'][0]['activities'][0]
        activity['source_ids']=['PRIVATE-SOURCE-ID']
        day=output['days'][0]
        day['activities']=[copy.deepcopy(activity) for _ in range(12)]
        output['days']=[dict(copy.deepcopy(day),day=i+1,date=(REQUEST.departure_date+timedelta(days=i)).isoformat()) for i in range(5)]
        candidate=GeneratedTrip.model_validate(output);before=candidate.model_dump()
        request=REQUEST.model_copy(update={'days':5})
        try:
            assemble_trip(candidate,request,evidence(),'test',[])
        except ProviderError as error:
            issues=_repair_issues(candidate,request,evidence(),error)
        self.assertEqual(len(issues),20)
        self.assertEqual(candidate.model_dump(),before)
        self.assertNotIn('PRIVATE-SOURCE-ID',json.dumps(issues))


class FeedbackRoundtripTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_repair_gets_both_errors_no_additional_tools(self):
        def invalid_schedule(output):
            output['days'][0]['date']='2026-10-11'
            output['days'][0]['activities'][0]['time']='99:00 - 99:30'
            return output
        fixture=Fixture(outputs=[invalid_schedule])
        settings=SETTINGS.model_copy(update={'max_search_rounds':1,'max_model_calls':3})
        budget=CallBudget(settings)
        async with httpx.AsyncClient(transport=httpx.MockTransport(fixture.handle)) as client:
            with self.assertLogs('backend.agent',level='WARNING') as logs:
                trip=await generate_live(REQUEST,ServiceAdapters(settings,client,budget))
        repair=fixture.requests[-1][1]
        feedback=repair['messages'][-1]['content']
        self.assertIn('day_date',feedback)
        self.assertIn('time_invalid',feedback)
        self.assertIn('days.0.activities.0.time',feedback)
        self.assertIn('place_id',feedback)
        self.assertNotIn('PRIVATE-UNSUPPORTED-PLACE',feedback)
        self.assertNotIn('PRIVATE-UNSUPPORTED-PLACE',''.join(logs.output))
        self.assertNotIn('tools',repair)
        self.assertEqual(budget.counts['model_calls'],3)
        self.assertEqual(budget.counts['search_calls'],1)
        self.assertFalse(trip.demo)
        self.assertEqual(str(trip.total_cost),'0.00')

    async def test_new_bad_location_after_repair_still_fails_at_three_calls(self):
        def bad_location(output):
            output['days'][0]['activities'][0]['location']='PRIVATE-UNSUPPORTED-PLACE'
            return output
        fixture=Fixture(outputs=[broken,bad_location])
        settings=SETTINGS.model_copy(update={'max_search_rounds':1,'max_model_calls':3})
        budget=CallBudget(settings)
        async with httpx.AsyncClient(transport=httpx.MockTransport(fixture.handle)) as client:
            with self.assertLogs('backend.providers',level='WARNING'),self.assertRaises(ProviderError) as caught:
                await generate_live(REQUEST,ServiceAdapters(settings,client,budget))
        self.assertEqual(caught.exception.code,'INVALID_MODEL_OUTPUT')
        self.assertIn('OUTPUT_SCHEMA',caught.exception.message)
        self.assertIn('place_id',caught.exception.message)
        self.assertEqual(budget.counts['model_calls'],3)
        self.assertNotIn('PRIVATE-UNSUPPORTED-PLACE',caught.exception.message)


if __name__ == '__main__':unittest.main()
