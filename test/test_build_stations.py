"""Tests for the station list check (scripts/build_stations.py), which guards the unattended
monthly rebuild on the feed service."""
import copy
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import build_stations  # noqa: E402

with open(os.path.join(ROOT, 'data', 'stations.json'), encoding='utf-8') as f:
    CURRENT = json.load(f)


class CheckTest(unittest.TestCase):
    def test_current_list_passes(self):
        self.assertEqual(build_stations.check(CURRENT, CURRENT), [])

    def test_too_few_stations(self):
        small = dict(CURRENT, stations=CURRENT['stations'][:100])
        problems = build_stations.check(small, CURRENT)
        self.assertTrue(any('only 100 stations' in p for p in problems))
        self.assertTrue(any('down from' in p for p in problems))

    def test_station_outside_nl(self):
        moved = copy.deepcopy(CURRENT)
        moved['stations'][0]['lat'] = 48.1
        self.assertTrue(any('outside the Netherlands' in p for p in build_stations.check(moved)))

    def test_odd_lift_code(self):
        odd = copy.deepcopy(CURRENT)
        odd['stations'][0]['lifts'][0]['id'] = 'lift 1'
        self.assertTrue(any('odd lift code' in p for p in build_stations.check(odd)))

    def test_feed_links_lost(self):
        unlinked = copy.deepcopy(CURRENT)
        for st in unlinked['stations']:
            for l in st['lifts']:
                l.pop('ref', None)
        self.assertTrue(any('live feed' in p for p in build_stations.check(unlinked)))


if __name__ == '__main__':
    unittest.main()
