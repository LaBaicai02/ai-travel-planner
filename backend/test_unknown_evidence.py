"""Unknown optional facts are explicit; invalid core itinerary still fails."""
import copy
import unittest
import httpx
from backend.agent import GeneratedTrip, _unknown_unverified_facts, assemble_trip, generate_live
from backend.providers import CallBudget, ProviderError, ServiceAdapters
from backend.test_agent import Fixture, REQUEST, SETTINGS, output_for
from backend.test_itinerary_validation import evidence


def unsupported(output):
    a=output['days'][0]['activities'][0]
    a.update(estimated_cost='0.00',cost_status='reference',cost_basis='无免费依据',
             cost_evidence='导览参考价80元/人',opening_hours='09:00-17:00',
             opening_hours_status='reported',opening_hours_evidence='PRIVATE-INVENTED-HOURS')
    return output


class UnknownFactsTests(unittest.TestCase):
    def test_both_unverified_fields_unknown_with_warning_without_mutation(self):
        original=GeneratedTrip.model_validate(unsupported(output_for('s1')))
        before=original.model_dump()
        with self.assertRaises(ProviderError):assemble_trip(original,REQUEST,evidence(),'test',[])
        candidate,warnings=_unknown_unverified_facts(original,REQUEST,evidence())
        trip=assemble_trip(candidate,REQUEST,evidence(),'test',warnings)
        a=trip.days[0].activities[0]
        self.assertIsNone(a.estimated_cost)
        self.assertEqual(a.cost_status,'unknown')
        self.assertEqual(a.cost_basis,'')
        self.assertIsNone(a.opening_hours)
        self.assertEqual(a.opening_hours_status,'unknown')
        self.assertIsNone(trip.total_cost)
        self.assertEqual(str(trip.known_cost_subtotal),'0')
        self.assertTrue(any('费用未通过' in w for w in trip.warnings))
        self.assertTrue(any('营业时间未通过' in w for w in trip.warnings))
        self.assertNotIn('PRIVATE-INVENTED-HOURS',''.join(trip.warnings))
        self.assertEqual(before,original.model_dump())

    def test_verified_free_price_and_hours_remain_known(self):
        output=output_for('s1');a=output['days'][0]['activities'][0]
        a.update(estimated_cost='0.00',cost_status='reference',cost_basis='来源门票免费',cost_evidence='门票免费',
                 opening_hours='全日开放',opening_hours_status='reported',opening_hours_evidence='全日开放')
        original=GeneratedTrip.model_validate(output)
        candidate,warnings=_unknown_unverified_facts(original,REQUEST,evidence())
        self.assertEqual(candidate,original)
        self.assertEqual(warnings,[])
        self.assertIsNotNone(assemble_trip(candidate,REQUEST,evidence(),'test',[]).total_cost)

    def test_invalid_source_place_date_and_time_remain_fatal(self):
        for field,value in [('source_ids',['fake']),('location','fake'),('time','25:00 - 26:00')]:
            with self.subTest(field=field):
                output=unsupported(output_for('s1'));output['days'][0]['activities'][0][field]=value
                candidate,warnings=_unknown_unverified_facts(GeneratedTrip.model_validate(output),REQUEST,evidence())
                with self.assertRaises(ProviderError):assemble_trip(candidate,REQUEST,evidence(),'test',warnings)
        output=unsupported(output_for('s1'));output['days'][0]['date']='2027-01-01'
        candidate,warnings=_unknown_unverified_facts(GeneratedTrip.model_validate(output),REQUEST,evidence())
        with self.assertRaises(ProviderError):assemble_trip(candidate,REQUEST,evidence(),'test',warnings)

    def test_known_subtotal_excludes_unverified_cost(self):
        output=unsupported(output_for('s1'))
        other=copy.deepcopy(output['days'][0]['activities'][0])
        other.update(time='12:00 - 13:00',estimated_cost='160.00',cost_status='reference',cost_basis='2人每人80元',
                     cost_evidence='导览参考价80元/人',opening_hours=None,opening_hours_status='unknown',opening_hours_evidence=None)
        output['days'][0]['activities'].append(other)
        candidate,warnings=_unknown_unverified_facts(GeneratedTrip.model_validate(output),REQUEST,evidence())
        trip=assemble_trip(candidate,REQUEST,evidence(),'test',warnings)
        self.assertIsNone(trip.total_cost)
        self.assertEqual(str(trip.known_cost_subtotal),'160.00')


class UnknownRoundtripTests(unittest.IsolatedAsyncioTestCase):
    async def test_user_error_pair_succeeds_with_unknowns_without_repair(self):
        fixture=Fixture(outputs=[unsupported])
        settings=SETTINGS.model_copy(update={'max_search_rounds':1,'max_model_calls':3})
        budget=CallBudget(settings)
        async with httpx.AsyncClient(transport=httpx.MockTransport(fixture.handle)) as client:
            trip=await generate_live(REQUEST,ServiceAdapters(settings,client,budget))
        self.assertFalse(trip.demo)
        self.assertEqual(budget.counts['model_calls'],2)
        self.assertEqual(budget.counts['search_calls'],1)
        self.assertEqual(str(trip.total_cost),'0.00')
        self.assertIsNone(trip.days[0].activities[0].opening_hours)
        self.assertTrue(any('营业时间未通过' in w for w in trip.warnings))


if __name__ == '__main__':unittest.main()
