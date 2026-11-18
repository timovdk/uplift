"""Tests for scripts/build_lifts.py: run with `npm test` (or python3 -m unittest discover -s test)."""
import importlib.util
import os
import unittest
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location('build_lifts', os.path.join(ROOT, 'scripts', 'build_lifts.py'))
bl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bl)

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
KNOWN = {i: i for i in ('UT-LIF-007', 'UT-LIF-008', 'ASD-LIF-010', 'HT-LIF-003', 'DVNK-LIF-001', 'DVNK-LIF-002', 'ASD-LIF-013')}
KNOWN['NL:CHB:LiftEquipment:8400058_013'] = 'ASD-LIF-013'


def msg(conditions, at='2026-11-16T11:00:00Z'):
    """conditions: (ref, status, start, end) or (ref, status, start, end, description)."""
    def one(ref, s, start, end, desc=None):
        return (f'<FacilityCondition><FacilityRef>{ref}</FacilityRef>'
                f'<FacilityStatus><Status>{s}</Status>' + (f'<Description>{desc}</Description>' if desc else '')
                + '</FacilityStatus>'
                + (f'<ValidityPeriod><StartTime>{start}</StartTime>' + (f'<EndTime>{end}</EndTime>' if end else '') + '</ValidityPeriod>' if start else '')
                + '</FacilityCondition>')
    body = ''.join(one(*c) for c in conditions)
    return (f'<Siri xmlns="http://www.siri.org.uk/siri"><ServiceDelivery><FacilityMonitoringDelivery>'
            f'<ResponseTimestamp>{at}</ResponseTimestamp>{body}</FacilityMonitoringDelivery></ServiceDelivery></Siri>').encode()


class BuildLifts(unittest.TestCase):
    def test_example_fixture(self):
        with open(bl.EXAMPLE, 'rb') as f:
            conditions, _ = bl.parse_message(f.read())
        self.assertEqual(len(conditions), 7)
        state = {}
        unmatched = bl.apply(state, conditions, KNOWN, NOW)
        lifts = state['lifts']
        self.assertEqual(lifts['UT-LIF-007']['s'], 'notAvailable')
        self.assertEqual(lifts['ASD-LIF-010']['until'], '2026-11-27T16:00:00Z')
        self.assertEqual(lifts['HT-LIF-003']['desc'], 'No connection with object (temporally).')
        self.assertNotIn('s', lifts['DVNK-LIF-002'], 'future outage is only planned')
        self.assertEqual(lifts['DVNK-LIF-002']['planned']['s'], 'notAvailable')
        self.assertEqual(unmatched, {'NL:PRORAIL:XYZ-LIF-999'})

    def test_state_carries_over_and_newest_wins(self):
        state = {}
        c1, _ = bl.parse_message(msg([('UT-LIF-007', 'notAvailable', '2026-11-16T09:00:00Z', None)], at='2026-11-16T09:01:00Z'))
        bl.apply(state, c1, KNOWN, NOW)
        # A later message only about another lift leaves UT-LIF-007 alone.
        c2, _ = bl.parse_message(msg([('UT-LIF-008', 'available', None, None)], at='2026-11-16T10:00:00Z'))
        bl.apply(state, c2, KNOWN, NOW)
        self.assertEqual(state['lifts']['UT-LIF-007']['s'], 'notAvailable')
        # Back in service.
        c3, _ = bl.parse_message(msg([('UT-LIF-007', 'available', '2026-11-16T10:30:00Z', None)], at='2026-11-16T10:31:00Z'))
        bl.apply(state, c3, KNOWN, NOW)
        self.assertEqual(state['lifts']['UT-LIF-007']['s'], 'available')
        self.assertEqual(state['lifts']['UT-LIF-007']['since'], '2026-11-16T10:30:00Z')
        # An older message arriving late doesn't overwrite it.
        bl.apply(state, c1, KNOWN, NOW)
        self.assertEqual(state['lifts']['UT-LIF-007']['s'], 'available')

    def test_planned_outage_survives_status_update_and_expires(self):
        state = {}
        planned, _ = bl.parse_message(msg([('DVNK-LIF-002', 'notAvailable', '2026-12-01T06:00:00Z', '2026-12-02T18:00:00Z')], at='2026-11-16T09:00:00Z'))
        bl.apply(state, planned, KNOWN, NOW)
        daily, _ = bl.parse_message(msg([('DVNK-LIF-002', 'available', None, None)], at='2026-11-16T10:00:00Z'))
        bl.apply(state, daily, KNOWN, NOW)
        self.assertEqual(state['lifts']['DVNK-LIF-002']['s'], 'available')
        self.assertIn('planned', state['lifts']['DVNK-LIF-002'])
        bl.apply(state, [], KNOWN, datetime(2026, 12, 3, tzinfo=timezone.utc))
        self.assertNotIn('planned', state['lifts']['DVNK-LIF-002'])

    def test_lift_code_in_facility_element(self):
        # Shape of a real DOVA message (OpenOV forum, Jan 2024): code in Facility/FacilityCode.
        xml = (b'<Siri xmlns="http://www.siri.org.uk/siri"><ServiceDelivery><FacilityMonitoringDelivery>'
               b'<ResponseTimestamp>2026-11-16T11:00:00Z</ResponseTimestamp><FacilityCondition>'
               b'<FacilityRef>NL:CHB:fac:123456</FacilityRef>'
               b'<Facility><FacilityCode>DVNK-LIF-001</FacilityCode><FacilityClass>fixedEquipment</FacilityClass></Facility>'
               b'<FacilityStatus><Status>notAvailable</Status></FacilityStatus>'
               b'</FacilityCondition></FacilityMonitoringDelivery></ServiceDelivery></Siri>')
        conditions, _ = bl.parse_message(xml)
        state = {}
        self.assertEqual(bl.apply(state, conditions, KNOWN, NOW), set())
        self.assertEqual(state['lifts']['DVNK-LIF-001']['s'], 'notAvailable')

    def test_real_message_with_chb_ref(self):
        # Captured from OpenGeo's SIRI stream (envelope /DOVA/ServiceDelivery), 16 Nov 2026.
        with open(os.path.join(ROOT, 'test', 'fixtures', 'siri-fm-real-2026-11-16.xml'), 'rb') as f:
            conditions, _ = bl.parse_message(f.read())
        state = {}
        self.assertEqual(bl.apply(state, conditions, KNOWN, NOW), set())
        self.assertEqual(state['lifts']['ASD-LIF-013']['s'], 'notAvailable')
        self.assertEqual(state['lifts']['ASD-LIF-013']['since'], '2024-06-19T14:30:29Z')

    def test_inference_from_listen_window(self):
        window = {'from': '2026-11-16T11:00:00Z', 'to': '2026-11-16T11:11:00Z', 'messages': 3}
        lifts = {
            'UT-LIF-007': {'s': 'notAvailable', 'since': '2026-11-16T07:00:00Z'},      # not repeated -> back
            'ASD-LIF-010': {'s': 'notAvailable', 'until': '2026-11-27T16:00:00Z'},     # long outage -> keep
            'HT-LIF-003': {'s': 'unknown'},                                            # unknown -> keep
            'DVNK-LIF-001': {'s': 'notAvailable'},                                     # repeated -> keep
        }
        monitored = {'UT-LIF-007', 'ASD-LIF-010', 'HT-LIF-003', 'DVNK-LIF-001', 'UT-LIF-008'}
        n = bl.infer_from_window(lifts, {'DVNK-LIF-001'}, monitored, window, NOW)
        self.assertEqual(n, 2)
        self.assertEqual(lifts['UT-LIF-007']['s'], 'available')
        self.assertTrue(lifts['UT-LIF-007']['inferred'])
        self.assertEqual(lifts['ASD-LIF-010']['s'], 'notAvailable')
        self.assertEqual(lifts['HT-LIF-003']['s'], 'unknown')
        self.assertEqual(lifts['DVNK-LIF-001']['s'], 'notAvailable')
        self.assertEqual(lifts['UT-LIF-008'], {'s': 'available', 'inferred': True, 'upd': '2026-11-16T11:11:00Z'})

    def test_no_inference_when_window_short_or_silent(self):
        lifts = {'UT-LIF-007': {'s': 'notAvailable'}}
        short = {'from': '2026-11-16T11:00:00Z', 'to': '2026-11-16T11:04:00Z', 'messages': 3}
        silent = {'from': '2026-11-16T11:00:00Z', 'to': '2026-11-16T11:11:00Z', 'messages': 0}
        for w in (short, silent):
            self.assertEqual(bl.infer_from_window(lifts, set(), {'UT-LIF-007'}, w, NOW), 0)
        self.assertEqual(lifts['UT-LIF-007']['s'], 'notAvailable')

    def test_explicit_message_clears_inferred(self):
        state = {'lifts': {'UT-LIF-007': {'s': 'available', 'inferred': True, 'upd': '2026-11-16T09:00:00Z'}}}
        c, _ = bl.parse_message(msg([('UT-LIF-007', 'available', '2026-11-16T10:00:00Z', None)]))
        bl.apply(state, c, KNOWN, NOW)
        self.assertNotIn('inferred', state['lifts']['UT-LIF-007'])

    def test_heartbeat(self):
        xml = (b'<Siri xmlns="http://www.siri.org.uk/siri"><HeartbeatNotification>'
               b'<RequestTimestamp>2026-11-16T13:25:00+01:00</RequestTimestamp><Status>true</Status>'
               b'</HeartbeatNotification></Siri>')
        conditions, hb = bl.parse_message(xml)
        self.assertEqual(conditions, [])
        self.assertEqual(bl.iso(hb), '2026-11-16T12:25:00Z')



# A snapshot needs SNAPSHOT_MIN lifts; pad it with lifts that are just working.
FILLER = [f'XX-LIF-{n:03d}' for n in range(bl.SNAPSHOT_MIN)]
KNOWN_ALL = dict(KNOWN, **{i: i for i in FILLER})


def ingest(state, conditions, at):
    bl.ingest(state, msg(conditions, at=at), KNOWN_ALL, NOW)


def snapshot(state, conditions, at='2026-11-16T03:02:00Z'):
    ingest(state, conditions + [(i, 'available', '2026-11-01T00:00:00Z', None) for i in FILLER], at)


class Snapshot(unittest.TestCase):
    def test_snapshot_is_the_new_baseline(self):
        state = bl.new_state(None, 'siri-fm')
        ingest(state, [('UT-LIF-007', 'notAvailable', '2026-11-15T20:00:00Z', None)], '2026-11-15T20:01:00Z')
        ingest(state, [('UT-LIF-008', 'notAvailable', '2026-11-15T21:00:00Z', None)], '2026-11-15T21:01:00Z')
        snapshot(state, [('UT-LIF-007', 'available', '2026-11-15T23:00:00Z', None),
                         ('ASD-LIF-010', 'notAvailable', '2026-11-10T22:00:00Z', '2026-11-30T23:00:00Z', 'Storing')])
        lifts = state['lifts']
        self.assertEqual(state['snapshot'], '2026-11-16T03:02:00Z')
        self.assertEqual(lifts['UT-LIF-007']['s'], 'available', 'the snapshot replaces what we had')
        self.assertTrue(lifts['UT-LIF-007']['snap'])
        self.assertEqual(lifts['ASD-LIF-010']['since'], '2026-11-10T22:00:00Z', 'start time from the snapshot')
        self.assertEqual(lifts['ASD-LIF-010']['desc'], 'Storing')
        self.assertNotIn('s', lifts['UT-LIF-008'], 'not in the snapshot: status forgotten')
        self.assertEqual(lifts['UT-LIF-008']['h'], [['2026-11-15T21:00:00Z', 'notAvailable']], '... history kept')

    def test_live_change_after_snapshot(self):
        state = bl.new_state(None, 'siri-fm')
        snapshot(state, [('UT-LIF-007', 'notAvailable', '2026-11-15T22:00:00Z', None)])
        ingest(state, [('UT-LIF-007', 'available', '2026-11-16T08:00:00Z', None)], '2026-11-16T08:01:00Z')
        e = state['lifts']['UT-LIF-007']
        self.assertEqual((e['s'], e['since']), ('available', '2026-11-16T08:00:00Z'))
        self.assertNotIn('snap', e, 'heard live now')
        self.assertEqual(e['h'], [['2026-11-15T22:00:00Z', 'notAvailable'], ['2026-11-16T08:00:00Z', 'available']])

    def test_repeat_keeps_what_the_snapshot_said(self):
        state = bl.new_state(None, 'siri-fm')
        snapshot(state, [('ASD-LIF-013', 'notAvailable', '2026-11-16T03:02:00Z', '2027-06-30T00:00:00Z',
                          'Lift wordt in juni 2027 in gebruik genomen')])
        # The 5-minute repeat: same status, old start, no end date or description.
        ingest(state, [('NL:CHB:LiftEquipment:8400058_013', 'notAvailable', '2024-06-19T14:30:29Z', None)], '2026-11-16T03:06:00Z')
        e = state['lifts']['ASD-LIF-013']
        self.assertEqual(e['desc'], 'Lift wordt in juni 2027 in gebruik genomen')
        self.assertEqual(e['until'], '2027-06-30T00:00:00Z')
        self.assertEqual(e['since'], '2026-11-16T03:02:00Z')
        self.assertNotIn('snap', e)

    def test_history_keeps_24_hours_of_changes(self):
        state = bl.new_state(None, 'siri-fm')
        for t, s in [('2026-11-15T09:00:00Z', 'notAvailable'), ('2026-11-15T09:05:00Z', 'available'),
                     ('2026-11-16T09:00:00Z', 'notAvailable'), ('2026-11-16T09:04:00Z', 'available')]:
            ingest(state, [('UT-LIF-007', s, t, None)], t)
        self.assertEqual(state['lifts']['UT-LIF-007']['h'], [['2026-11-16T09:00:00Z', 'notAvailable'],
                                                             ['2026-11-16T09:04:00Z', 'available']])

    def test_no_history_for_first_working_status(self):
        state = bl.new_state(None, 'siri-fm')
        snapshot(state, [('UT-LIF-007', 'available', '2026-11-16T01:00:00Z', None)])
        self.assertNotIn('h', state['lifts']['UT-LIF-007'])


if __name__ == '__main__':
    unittest.main()
