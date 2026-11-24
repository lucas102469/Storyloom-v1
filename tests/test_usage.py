"""App-wide daily call caps; no real Gemini or user data."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from app import create_app


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = str(Path(self.folder.name) / 'usage.sqlite3')
        self.calls = 0
        self.result = {'reply': '테스트', 'updates': []}
        self.started, self.release = None, None
        def fake(prompt):
            self.calls += 1
            if self.started:
                self.started.set()
                if not self.release.wait(5):
                    raise ValueError('테스트 대기 시간 초과')
            if isinstance(self.result, Exception):
                raise self.result
            return self.result
        self.app = create_app(self.path, fake)
        self.app.config['TOTAL_DAILY_LIMIT'] = 1
        self.c = self.app.test_client()
        self.pid = self.c.post('/api/projects', json={'title': '사용량 시험'}).json['id']

    def tearDown(self):
        self.app.extensions['storyloom_workers'].shutdown(wait=True)
        self.folder.cleanup()

    def chat(self, rid='usage-request-001'):
        return self.c.post('/api/projects/' + self.pid + '/chat', json={'request_id': rid, 'text': '테스트 요청'})

    def test_normal_usage_counts_below_configured_caps(self):
        self.app.config['TOTAL_DAILY_LIMIT'] = 400
        for i in range(35):
            self.assertEqual(self.chat('unlimited-request-'+str(i)).status_code,200)
        usage=self.c.get('/api/usage').json
        self.assertFalse(usage['unlimited'])
        self.assertEqual(usage['remaining'],25)
        self.assertEqual(usage['used'],35)
        self.assertEqual(self.calls,35)

    def test_cap_duplicate_and_persistence(self):
        self.assertEqual(self.chat().status_code, 200)
        self.assertEqual(self.chat().status_code, 200)  # cached duplicate: no extra call
        self.assertEqual(self.chat('usage-request-002').status_code, 429)
        usage = self.c.get('/api/usage').json
        self.assertEqual((usage['used'], usage['remaining'], self.calls), (1, 0, 1))
        self.assertFalse(usage['provider_quota'])
        reopened = create_app(self.path, lambda _: {})
        try:
            self.assertEqual(reopened.test_client().get('/api/usage').json['used'], 1)
        finally:
            reopened.extensions['storyloom_workers'].shutdown(wait=True)

    def test_failed_attempt_is_counted_and_previous_day_resets(self):
        self.result = ValueError('가짜 시간 초과')
        self.assertEqual(self.chat().status_code, 502)
        self.assertEqual(self.c.get('/api/usage').json['remaining'], 0)
        with closing(sqlite3.connect(self.path)) as con, con:
            con.execute("UPDATE api_usage SET day='2000-01-01'")
        self.assertEqual(self.c.get('/api/usage').json['remaining'], 1)

    def test_each_scene_call_reserves_capacity_before_provider(self):
        self.result = {'reply': '집필', 'updates': [], 'write_episode': 1, 'scene_goals': ['도입', '결말']}
        # Planning consumes the only call, so scene generation must not call AI.
        self.assertEqual(self.chat().status_code, 429)
        self.assertEqual(self.calls, 1)
        self.assertFalse(self.c.get('/api/projects/' + self.pid).json['state']['official'])

    def test_parallel_projects_share_one_atomic_cap(self):
        self.started, self.release = threading.Event(), threading.Event()
        other = self.c.post('/api/projects', json={'title': '다른 작품'}).json['id']
        try:
            response = self.c.post('/api/projects/' + self.pid + '/chat', json={
                'request_id': 'parallel-request-001', 'text': '첫 요청', 'background': True})
            self.assertEqual(response.status_code, 202)
            self.assertTrue(self.started.wait(2))
            response = self.c.post('/api/projects/' + other + '/chat', json={
                'request_id': 'parallel-request-002', 'text': '둘째 요청'})
            self.assertEqual(response.status_code, 429)
            self.assertEqual(self.calls, 1)
        finally:
            self.release.set()
