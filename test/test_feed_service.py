"""Tests for the feed service (nas/uplift_feed.py). Skipped when pyzmq/boto3 aren't installed
(e.g. plain `npm test`); `.venv/bin/python -m unittest discover -s test` runs them."""
import importlib.util
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HAVE_DEPS = all(importlib.util.find_spec(m) for m in ('zmq', 'boto3'))


@unittest.skipUnless(HAVE_DEPS, 'needs pyzmq and boto3 (nas/requirements.txt)')
class PublishTest(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, os.path.join(ROOT, 'nas'))
        import uplift_feed
        import boto3
        from botocore.stub import Stubber
        self.feed = uplift_feed
        self.client = boto3.client('s3', endpoint_url='https://acc.r2.cloudflarestorage.com',
                                   aws_access_key_id='k', aws_secret_access_key='s', region_name='auto')
        self.stub = Stubber(self.client)

    def test_uploads_json_with_short_cache(self):
        data = {'generated': '2026-11-17T10:00:00Z', 'source': 'siri-fm', 'lifts': {'ASD-LIF-013': {'s': 'notAvailable'}}}
        self.stub.add_response('put_object', {}, {
            'Bucket': 'uplift', 'Key': 'lifts.json', 'ContentType': 'application/json',
            'CacheControl': 'public, max-age=60',
            'Body': (json.dumps(data, separators=(',', ':')) + '\n').encode()})
        with self.stub:
            self.assertTrue(self.feed.publish('lifts.json', data, 'public, max-age=60', self.client))
        self.stub.assert_no_pending_responses()

    def test_failure_is_reported_not_raised(self):
        self.stub.add_client_error('put_object', 'AccessDenied', http_status_code=403)
        with self.stub:
            self.assertFalse(self.feed.publish('lifts.json', {'lifts': {}}, 'public, max-age=60', self.client))

    def test_no_settings_means_nothing_to_publish(self):
        self.assertTrue(self.feed.publish('lifts.json', {'lifts': {}}, 'public, max-age=60'))  # R2_* not set in the test environment


@unittest.skipUnless(HAVE_DEPS, 'needs pyzmq and boto3 (nas/requirements.txt)')
class StationRefreshTest(unittest.TestCase):
    def test_bad_list_is_not_used(self):
        import tempfile
        sys.path.insert(0, os.path.join(ROOT, 'nas'))
        import uplift_feed
        with tempfile.TemporaryDirectory() as d:
            old_data, old_build = uplift_feed.DATA, uplift_feed.build_stations.build
            uplift_feed.DATA = d
            uplift_feed.build_stations.build = lambda cache: {'generated': 'x', 'stations': []}
            try:
                r = uplift_feed.StationRefresh()
                r.run()
                self.assertIsNone(r.known)
                self.assertFalse(os.path.exists(os.path.join(d, 'stations.json')))
                self.assertEqual(uplift_feed.stations_age_s(), float('inf'))
            finally:
                uplift_feed.DATA, uplift_feed.build_stations.build = old_data, old_build


if __name__ == '__main__':
    unittest.main()
