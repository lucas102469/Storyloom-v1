import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from app import create_app
from reference_quality import complete_reference_descriptions


class ReferenceResumeTests(unittest.TestCase):
    def test_incomplete_setup_reports_partial_and_preserves_confirmed_plan(self):
        plan = '제목: 신아\n로그라인: 도시를 여행한다.\n장르: 판타지\n\n## 시놉시스\n도시의 비밀을 찾는다.'
        def ai(prompt):
            if prompt.startswith('REFERENCE_BACKFILL'):
                return {'updates': [{'kind': 'world', 'content': '## 신아\n설명: 등급에 따라 거주 구역이 나뉜 도시.'}]}
            return {'reply': '기획안', 'updates': [{'kind': 'plan', 'content': plan}]}
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'test.sqlite3')
            app = create_app(path, ai)
            try:
                c = app.test_client()
                pid = c.post('/api/projects', json={'title': '완료 표시'}).json['id']
                base = '/api/projects/' + pid
                self.assertEqual(c.post(base + '/chat', json={'request_id': 'partial-plan-01', 'text': '기획해줘'}).status_code, 200)
                version = c.get(base).json['state']['drafts']['plan:0']['version']
                for action in ('review', 'approve'):
                    self.assertEqual(c.post(base + '/draft', json={'action': action, 'key': 'plan:0', 'version': version}).status_code, 200)
                response = c.post(base + '/chat', json={'request_id': 'partial-setup-01', 'text': '설정 초안 만들기', 'setup_plan_version': version})
                self.assertEqual(response.status_code, 200, response.json)
                state = c.get(base).json['state']
                self.assertEqual(state['official']['plan:0']['text'], plan)
                self.assertIn('일부만', state['messages'][-1]['text'])
                self.assertEqual(state['last_work_result']['status'], 'partial')
                self.assertTrue(state['initial_references_pending'])
                with closing(sqlite3.connect(path)) as con:
                    job = con.execute('select result,progress from jobs where id=?', ('partial-setup-01',)).fetchone()
                self.assertIn('일부만', json.loads(job[0])['reply'])
                self.assertEqual(job[1], '일부 완료')
            finally:
                app.extensions['storyloom_workers'].shutdown(wait=True)

    def test_same_name_in_two_documents_needs_explicit_kind(self):
        updates = [{'kind': 'world', 'content': '## 경계'}, {'kind': 'places', 'content': '## 경계'}]
        source = '경계는 두 세계를 구분하는 규칙이자 실제로 건널 수 있는 장소다.'
        def generate(prompt, task):
            return {'items': [{'name': '경계', 'description': source, 'evidence': source}]}
        with self.assertRaises(ValueError):
            complete_reference_descriptions(updates, {'official': {}, 'drafts': {}}, source, generate, lambda label: None)
        self.assertTrue(all('설명:' not in u['content'] for u in updates))

    def test_batch_completion_keeps_partial_success_and_retries_only_missing(self):
        updates = [{'kind': 'world', 'content': '## 신아'},
                   {'kind': 'places', 'content': '## 항구'}]
        source = '신아는 등급에 따라 거주 구역이 나뉜 도시다. 항구는 도시 남쪽의 교역 중심지다.'
        calls = []
        snapshots = []

        def generate(prompt, task):
            calls.append(prompt)
            if len(calls) == 1:
                return {'items': [{'kind': 'world', 'name': '신아', 'description': source[:25], 'evidence': source}]}
            return {'items': [{'kind': 'places', 'name': '항구', 'description': '도시 남쪽의 교역 중심지다.', 'evidence': source}]}

        def saved(label):
            snapshots.append(json.loads(json.dumps(updates)))

        state = {'official': {}, 'drafts': {}}
        with self.assertRaisesRegex(ValueError, '항구'):
            complete_reference_descriptions(updates, state, source, generate, saved)
        preserved = updates[0]['content']
        self.assertIn('설명:', preserved)
        self.assertEqual(snapshots[-1][0]['content'], preserved)
        complete_reference_descriptions(updates, state, source, generate, saved)
        requested = calls[-1].split('설명이 없는 항목:\n')[1].split('\n근거 자료:')[0]
        self.assertEqual(json.loads(requested), [{'kind': 'places', 'name': '항구'}])
        self.assertEqual(updates[0]['content'], preserved)

    def test_failed_followup_resumes_without_regenerating_or_recompleting(self):
        calls = []
        source = '신아는 등급에 따라 거주 구역이 나뉜 도시다. 항구는 도시 남쪽의 교역 중심지다.'

        def ai(prompt):
            calls.append(prompt)
            if prompt.startswith('설명 누락 보완'):
                return {'items': [{'kind': kind, 'name': name, 'description': source, 'evidence': source}
                                  for kind, name in [('world', '신아'), ('places', '항구')]]}
            if prompt.startswith('플롯 자동 정리'):
                if sum(p.startswith('플롯 자동 정리') for p in calls) == 1:
                    raise ValueError('temporary plot failure')
                return {'beginning': '도시에 도착한다.', 'middle': '갈등을 해결한다.', 'ending': '도시를 지킨다.',
                        'episodes': [{'number': 1, 'event': '도시에 도착한다.'}]}
            return {'reply': '정리했어요.', 'updates': [
                {'kind': 'world', 'content': '## 신아'}, {'kind': 'places', 'content': '## 항구'},
                {'kind': 'plot', 'content': '도시에 도착하는 이야기.'}]}

        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'test.sqlite3')
            app = create_app(path, ai)
            try:
                c = app.test_client()
                pid = c.post('/api/projects', json={'title': '이어 처리'}).json['id']
                url = '/api/projects/' + pid
                payload = {'request_id': 'resume-reference-test', 'text': '설정을 정리해줘. ' + source}
                first = c.post(url + '/chat', json=payload)
                self.assertEqual(first.status_code, 502, first.json)
                with closing(sqlite3.connect(path)) as con:
                    checkpoint = json.loads(con.execute('select checkpoint from jobs where id=?', (payload['request_id'],)).fetchone()[0])
                self.assertTrue(all('설명:' in u['content'] for u in checkpoint['reference_generation']['updates'] if u['kind'] != 'plot'))
                self.assertEqual(c.get(url).json['state']['official'], {})
                second = c.post(url + '/chat', json=payload)
                self.assertEqual(second.status_code, 200, second.json)
                self.assertEqual(sum(p.startswith('설명 누락 보완') for p in calls), 1)
                self.assertEqual(sum(not p.startswith(('설명 누락 보완', '플롯 자동 정리')) for p in calls), 1)
                self.assertIn('설명:', c.get(url).json['state']['drafts']['world:0']['text'])
            finally:
                app.extensions['storyloom_workers'].shutdown(wait=True)
