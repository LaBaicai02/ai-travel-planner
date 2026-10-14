"""Internal classification, example isolation and unchanged public contract."""
from datetime import date
import unittest

from pydantic import ValidationError

from backend.agent import GeneratedTrip, _classifications, assemble_trip, _place_output_model, _bind_places, _place_catalog
from backend.planning_quality import meal_gaps, improve_schedule
from backend.prompts import EXAMPLES, GENERATION_EXAMPLES
from backend.providers import ProviderError
from backend.test_agent import REQUEST, output_for
from backend.test_itinerary_validation import evidence


class OutputConstraintsTests(unittest.TestCase):
    def test_three_short_examples_validate_and_ids_are_not_live_evidence(self):
        self.assertEqual(len(EXAMPLES), 3)
        self.assertLessEqual(len(GENERATION_EXAMPLES), 4000)
        for example in EXAMPLES:
            selection = _place_output_model(example['place_catalog']).model_validate(example['output'])
            parsed = _bind_places(selection, example['place_catalog'])
            with self.assertRaises(ValidationError):
                _place_output_model(_place_catalog(REQUEST, evidence())).model_validate(example['output'])
            request = REQUEST.model_copy(update={'departure_date': date(2030, 1, 1)})
            with self.assertRaises(ProviderError) as caught:
                assemble_trip(parsed, request, evidence(), 'example-check', [])
            self.assertEqual(caught.exception.validation_reason, 'source_unknown')

    def test_required_classification_and_illegal_combinations(self):
        for fields in [{'activity_type': 'invalid'}, {'meal_slot': 'breakfast'},
                       {'activity_type': 'shopping', 'meal_slot': 'lunch'}]:
            output = output_for('s1')
            output['days'][0]['activities'][0].update(fields)
            with self.assertRaises(ValidationError):
                GeneratedTrip.model_validate(output)
        for field in ('activity_type', 'meal_slot'):
            output = output_for('s1')
            del output['days'][0]['activities'][0][field]
            with self.assertRaises(ValidationError):
                GeneratedTrip.model_validate(output)

    def test_description_does_not_determine_meals_and_fields_stay_internal(self):
        output = output_for('s1')
        activity = output['days'][0]['activities'][0]
        activity.update(activity_type='dining', meal_slot='lunch', time='12:00 - 13:00', description='建议到访')
        parsed = GeneratedTrip.model_validate(output)
        trip = assemble_trip(parsed, REQUEST, evidence(), 'test', [])
        purposes = {'s1': {'restaurant'}}
        gap = meal_gaps(trip, purposes, _classifications(parsed))[0]
        self.assertFalse(gap['missing_lunch'])
        self.assertTrue(gap['missing_dinner'])
        self.assertTrue(meal_gaps(trip, {}, _classifications(parsed))[0]['missing_lunch'])
        public = trip.model_dump(mode='json')['days'][0]['activities'][0]
        self.assertNotIn('activity_type', public)
        self.assertNotIn('meal_slot', public)
        activity.update(activity_type='shopping', meal_slot=None, description='享用午餐，品尝美食')
        parsed = GeneratedTrip.model_validate(output)
        self.assertTrue(meal_gaps(trip, purposes, _classifications(parsed))[0]['missing_lunch'])
        activity.update(activity_type='dining', meal_slot='dinner')
        self.assertTrue(meal_gaps(trip, purposes, _classifications(GeneratedTrip.model_validate(output)))[0]['missing_lunch'])

    def test_classifications_follow_deduplication_indices(self):
        output = output_for('s1')
        activities = output['days'][0]['activities']
        activities.append(dict(activities[0], time='10:00 - 11:00'))
        activities.append(dict(activities[0], time='12:00 - 13:00', activity_type='dining', meal_slot='lunch', source_ids=['s2']))
        e = evidence()
        e['s2'] = e['s1'].model_copy(update={'id': 's2'})
        parsed = GeneratedTrip.model_validate(output)
        trip = assemble_trip(parsed, REQUEST, e, 'test', [])
        classifications = _classifications(parsed)
        purposes = {'s1': {'attraction'}, 's2': {'restaurant'}}
        trip = improve_schedule(trip, purposes, classifications)
        self.assertEqual(len(trip.days[0].activities), 2)
        self.assertEqual(classifications[(1, 1)], ('dining', 'lunch'))
        self.assertFalse(meal_gaps(trip, purposes, classifications)[0]['missing_lunch'])
