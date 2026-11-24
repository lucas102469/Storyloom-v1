import json
import tempfile
import unittest
import io
import sqlite3
from contextlib import closing
from unittest.mock import patch
from pathlib import Path
from app import create_app


class StoryloomTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.result = {'reply': '기획 제안입니다.', 'updates': [{'kind': 'plan', 'content': '총 20화, 회차당 4000자'}]}
        self.calls = 0
        def ai(prompt):
            if prompt.startswith('REFERENCE_BACKFILL'):
                return {'updates': []}
            self.calls += 1
            if isinstance(self.result, Exception):
                raise self.result
            return {**self.result,'premise_decision':'keep','plan_conflict':False,'plan_basis':''} if '[작품 전제 변경 판단]' in prompt else self.result
        self.path = str(Path(self.tmp.name) / 'test.sqlite3')
        self.ai = ai
        self.client = create_app(self.path, ai).test_client()
        self.pid = self.client.post('/api/projects', json={'title': '시험 작품'}).json['id']
        self.base = '/api/projects/' + self.pid

    def tearDown(self):
        self.client.application.extensions['storyloom_workers'].shutdown(wait=True)
        self.tmp.cleanup()

    def state(self):
        return self.client.get(self.base).json['state']

    def test_apply_guidelines_saves_without_ai_and_can_clear_all(self):
        route = self.base + '/guidelines'
        data = {'items': ['1인칭으로 쓴다.'], 'base_version': None, 'draft_version': None}
        self.assertEqual(self.client.post(route, json=data).status_code, 200)
        record = self.state()['official']['guidelines:0']
        self.assertEqual(record['text'], '1인칭으로 쓴다.')
        self.assertEqual(self.client.post(route, json=data).status_code, 409)
        self.assertEqual(self.client.post(route, json={
            **data, 'items': [], 'base_version': record['version'],
        }).status_code, 200)
        self.assertEqual(self.state()['official']['guidelines:0']['text'], '')
        self.assertEqual(self.calls, 0)

    def chat(self, rid='request-0001', text='기획해줘'):
        return self.client.post(self.base + '/chat', json={'request_id': rid, 'text': text})

    def action(self, action, **kwargs):
        return self.client.post(self.base+'/draft', json={'key': 'plan:0', 'action': action, **kwargs})

    def test_reopen_is_quiet_and_persistent(self):
        self.assertEqual(len(self.state()['messages']), 1)
        self.client.post(self.base+'/view', json={'selected': 'world', 'episode': 7})
        reopened = create_app(self.path, self.ai).test_client().get(self.base).json['state']
        self.assertEqual(reopened['selected'], 'world')
        self.assertEqual(reopened['episode'], 7)
        self.assertEqual(len(reopened['messages']), 1)
        self.assertEqual(self.calls, 0)

    def test_ai_does_not_create_plot_draft_for_unchanged_text(self):
        key = 'plot:0'
        plot = '## 전체 플롯\n시작: 등대에서 첫 단서를 발견한다.\n중간: 편지를 조사한다.\n결말: 편지의 주인을 만난다.\n## 회차 구성\n1화: 등대에 도착한다.'
        version = self.client.post(self.base+'/edit', json={
            'key': key, 'text': plot, 'base_version': None,
        }).json['version']
        for action in ('review', 'approve'):
            self.assertEqual(self.client.post(self.base+'/draft', json={
                'key': key, 'action': action, 'version': version,
            }).status_code, 200)
        self.result = {'reply': '플롯을 확인했어요.', 'updates': [
            {'kind': 'plot', 'content': plot, 'summary': '새 요약'},
        ]}
        self.assertEqual(self.chat().status_code, 200)
        self.assertNotIn(key, self.state()['drafts'])
        self.assertEqual(self.state()['official'][key]['version'], version)
        self.result['updates'][0]['content'] = '제목: 코드의 종말. 로그라인: 창작자가 저항한다. 장르: SF. ## 시놉시스: 인공지능이 일상을 통제한다.'
        self.assertEqual(self.chat('request-0002').status_code, 200)
        self.assertNotIn(key, self.state()['drafts'])
        self.result['updates'][0]['content'] = plot.replace('등대에 도착한다', '등대에서 길을 잃는다')
        self.assertEqual(self.chat('request-0003').status_code, 200)
        self.assertIn('길을 잃는다', self.state()['drafts'][key]['text'])

    def test_ai_names_character_with_placeholder_heading(self):
        self.result = {'reply': '등장인물을 정리했어요.', 'updates': [{
            'kind': 'characters', 'content': '## 미정(주인공)\n역할: 창작자\n목표: 제니스에 맞선다.\n\n##\n역할: 조력자',
        }]}
        def ai(prompt):
            if prompt.startswith('다음 등장인물 자료'):
                return {'names': ['한서윤', '이도현']}
            return self.ai(prompt)
        self.client = create_app(self.path, ai).test_client()
        self.assertEqual(self.chat().status_code, 200)
        self.assertEqual(self.state()['drafts']['characters:0']['text'],
                         '## 한서윤\n역할: 창작자\n목표: 제니스에 맞선다.\n\n## 이도현\n역할: 조력자')

    def test_ai_skips_cosmetic_reference_changes_in_all_tabs(self):
        for kind, title in [('characters', '연우'), ('world', '서라벌'), ('lore', '석빙고')]:
            with self.subTest(kind=kind):
                key = kind + ':0'
                version = self.client.post(self.base+'/edit', json={
                    'key': key, 'text': f'## {title}\n설명: 기존 이야기.', 'base_version': None,
                }).json['version']
                for action in ('review', 'approve'):
                    self.assertEqual(self.client.post(self.base+'/draft', json={
                        'key': key, 'action': action, 'version': version,
                    }).status_code, 200)
                self.result = {'reply': '정리했어요.', 'updates': [
                    {'kind': kind, 'content': f'### **{title}**\n\n- 설명： 기존  이야기.'},
                ]}
                label = {'characters': '등장인물', 'world': '세계관', 'lore': '주요 설정'}[kind]
                self.assertEqual(self.chat('reference-same-'+kind, label+' 정리해줘').status_code, 200)
                self.assertNotIn(key, self.state()['drafts'])
                self.result['updates'][0]['content'] = f'## {title}\n설명: 새로운 이야기.'
                self.assertEqual(self.chat('reference-change-'+kind, label+' 정리해줘').status_code, 200)
                self.assertIn('새로운 이야기', self.state()['drafts'][key]['text'])

    def test_ai_names_lore_from_existing_description(self):
        description = '석빙고는 강에서 채취한 얼음을 보관하는 시설이다.'
        self.result = {'reply': '설정을 정리했어요.', 'updates': [
            {'kind': 'lore', 'content': '설명: ' + description},
        ]}

        def ai(prompt):
            if prompt.startswith('다음 주요 설정 자료'):
                self.assertIn(description, prompt)
                return {'names': ['석빙고']}
            return self.ai(prompt)

        self.client = create_app(self.path, ai).test_client()
        self.assertEqual(self.chat().status_code, 200)
        self.assertEqual(self.state()['drafts']['lore:0']['text'], '## 석빙고\n설명: ' + description)

    def test_ai_repairs_mixed_plot_before_saving(self):
        self.result = {'reply': '플롯을 만들었어요.', 'updates': [{
            'kind': 'plot',
            'content': '장르: 미스터리\n## 전체 플롯\n시작: 편지를 발견한다.\n중간: 미정.\n결말: 미정.\n## 회차 구성\n1화: 편지를 발견한다.',
        }]}

        def ai(prompt):
            if prompt.startswith('플롯 자동 정리'):
                return {'beginning': '편지를 발견한다.', 'middle': '등대를 조사한다.',
                        'ending': '편지의 주인을 만난다.',
                        'episodes': [{'number': 1, 'event': '편지를 발견한다.'}]}
            return self.ai(prompt)

        self.client = create_app(self.path, ai).test_client()
        self.assertEqual(self.chat().status_code, 200)
        self.assertEqual(self.state()['drafts']['plot:0']['text'],
                         '## 전체 플롯\n시작: 편지를 발견한다.\n중간: 등대를 조사한다.\n결말: 편지의 주인을 만난다.\n\n## 회차 구성\n1화: 편지를 발견한다.')

    def test_ai_automatically_structures_incomplete_plan(self):
        self.result = {'reply': '기획했어요.', 'updates': [{
            'kind': 'plan', 'content': '제목: 미정\n로그라인: 등대를 찾는다.\n장르: 미정',
        }]}

        def ai(prompt):
            if prompt.startswith('기획안 자동 정리'):
                return {'title': '마지막 등대', 'logline': '등대를 찾는다.',
                        'genre': '미스터리', 'synopsis': '등대에서 오래된 편지를 발견한다.'}
            return self.ai(prompt)

        self.client = create_app(self.path, ai).test_client()
        self.assertEqual(self.chat().status_code, 200)
        self.assertEqual(self.state()['drafts']['plan:0']['text'],
                         '제목: 마지막 등대\n로그라인: 등대를 찾는다.\n장르: 미스터리\n\n## 시놉시스\n등대에서 오래된 편지를 발견한다.')

    def test_dotenv_loading_uses_explicit_project_path(self):
        # Mock the loader: never open the developer's real secrets in tests.
        with patch('app.load_dotenv') as loader:
            create_app(self.path)
            loader.assert_called_once_with(
                dotenv_path=Path(__file__).resolve().parents[1] / '.env', override=False)
        with patch('app.load_dotenv') as loader:
            create_app(self.path, self.ai)
            loader.assert_not_called()

    def test_approval_and_non_destructive_restore(self):
        def action(action, **fields):
            return self.client.post(self.base+'/draft', json={'key': 'world:0', 'action': action, **fields})
        first = self.client.post(self.base+'/edit', json={'key': 'world:0', 'text': '처음 세계관'})
        self.assertEqual(first.status_code, 200)
        s = self.state()
        self.assertFalse(s['official'])
        vid = s['drafts']['world:0']['version']
        self.assertEqual(action('approve', version=vid).status_code, 400)
        action('review', version=vid)
        history = self.client.get(self.base+'/versions').json
        self.assertEqual(action('approve', version=vid).status_code, 200)
        approved_history = self.client.get(self.base+'/versions').json
        self.assertEqual(len(approved_history), len(history))
        self.assertEqual(approved_history[0]['id'], vid)
        self.assertEqual(approved_history[0]['status'], 'confirmed')
        self.assertGreater(approved_history[0]['created'], history[0]['created'])
        edit = self.client.post(self.base+'/edit', json={
            'key': 'world:0', 'text': '다른 세계관', 'base_version': vid})
        newer = edit.json['version']
        action('review', version=newer)
        self.assertEqual(action('approve', version=newer).status_code, 200)
        original = self.state()['official']['world:0']
        self.assertEqual(action('restore', version=original['version']).status_code, 409)
        self.assertEqual(action('restore', version=vid).status_code, 200)
        s = self.state()
        self.assertEqual(s['official']['world:0'], original)
        self.assertEqual(s['drafts']['world:0']['version'], vid)
        self.assertEqual(s['drafts']['world:0']['status'], 'draft')
        self.assertTrue(action('restore', version=vid).json['unchanged'])
        self.assertEqual(len(self.client.get(self.base+'/versions').json), 2)

    def test_idempotency_and_payload_conflict(self):
        self.chat()
        self.assertTrue(self.chat().json['duplicate'])
        self.assertEqual(self.calls, 1)
        self.assertEqual(self.chat(text='다른 요청').status_code, 400)
        self.assertEqual(len(self.state()['messages']), 3)

    def test_error_retry_keeps_one_user_message(self):
        self.result = ValueError('일시 실패')
        self.assertEqual(self.chat().status_code, 502)
        self.result = {'reply': '복구', 'updates': []}
        self.assertEqual(self.chat().status_code, 200)
        self.assertEqual(len(self.state()['messages']), 3)

    def test_stale_approval_rejected(self):
        self.chat()
        vid = self.state()['drafts']['plan:0']['version']
        self.chat('request-0002')
        self.assertEqual(self.action('review', version=vid).status_code, 409)

    def test_recovery_records_all_setting_kinds_once(self):
        quote = '서윤은 등대에서 다섯 번 울리는 종소리를 들었다.'
        calls = []
        def ai(prompt):
            calls.append(prompt)
            return {'findings': [
                {'kind': kind, 'name': name, 'role': '등대지기' if kind == 'characters' else '',
                 'fact': kind, 'quote': quote, 'status': 'clear'}
                for kind, name in [('characters', '서윤'), ('world', '해안 마을'),
                                   ('places', '마을 등대'), ('lore', '등대의 종'), ('plot', '')]
            ] + [
                {'kind': 'lore', 'name': '종의 의미', 'fact': '종소리가 경고일 수 있다.',
                 'quote': quote, 'status': 'review', 'reason': '의미는 명시되지 않았다.'},
                {'kind': 'lore', 'name': '비밀 조직', 'fact': '비밀 조직이 종을 울렸다.',
                 'quote': '원고에 없는 구절', 'status': 'clear'},
            ]}
        self.client = create_app(self.path, ai).test_client()
        key = 'episode:1'
        version = self.client.post(self.base+'/edit', json={
            'key': key, 'text': quote, 'base_version': None,
        }).json['version']
        for action in ('review', 'approve'):
            self.assertEqual(self.client.post(self.base+'/draft', json={
                'key': key, 'version': version, 'action': action,
            }).status_code, 200)
        self.assertEqual(self.client.post(self.base+'/recover-episode-findings', json={}).status_code, 200)
        self.assertEqual(len(calls), 0)
        self.assertEqual(self.client.post(self.base+'/episode-settings-review', json={
            'episode': 1, 'version': version, 'action': 'organize',
        }).status_code, 200)
        state = self.state()
        self.assertEqual(state['records']['1']['setting_review'], 'done')
        self.assertEqual(len(calls), 1)
        self.assertEqual(state['official'][key]['text'], quote)
        self.assertEqual([entry['kind'] for entry in state['records']['1']['findings']],
                         ['characters', 'world', 'places', 'lore', 'plot', 'lore'])
        self.assertEqual([entry.get('name') for entry in state['records']['1']['findings']],
                         ['서윤', '해안 마을', '마을 등대', '등대의 종', None, '종의 의미'])
        self.assertEqual(state['records']['1']['findings'][0]['role'], '등대지기')
        self.assertTrue(all(not item['reason'] for item in state['records']['1']['findings'][:5]))
        self.assertTrue(state['records']['1']['findings'][5]['reason'])

    def test_legacy_episode_review_reopens_without_blocking_next_episode(self):
        quote = '서윤은 등대에서 다섯 번 울리는 종소리를 들었다.'
        key = 'episode:1'
        version = self.client.post(self.base+'/edit', json={
            'key': key, 'text': quote, 'base_version': None,
        }).json['version']
        for action in ('review', 'approve'):
            self.assertEqual(self.client.post(self.base+'/draft', json={
                'key': key, 'version': version, 'action': action,
            }).status_code, 200)
        with closing(sqlite3.connect(self.path)) as con, con:
            state = json.loads(con.execute('SELECT state FROM projects WHERE id=?', (self.pid,)).fetchone()[0])
            state['records']['1'].update(setting_review='review', findings=[
                {'kind': 'plot', 'fact': '서윤이 종소리를 들었다.', 'quote': quote, 'reason': ''},
                {'kind': 'lore', 'name': '등대의 종', 'fact': '종이 경고일 수 있다.',
                 'quote': quote, 'reason': '해석이 필요하다.'},
                {'kind': 'plot', 'fact': '근거 없는 사건', 'quote': '', 'reason': '근거 부족'},
            ])
            con.execute('UPDATE projects SET state=? WHERE id=?', (json.dumps(state), self.pid))
        record = self.state()['records']['1']
        self.assertEqual(record['setting_review'], 'done')
        self.assertEqual(len(record['findings']), 2)
        self.assertEqual(record['findings'][0]['reason'], '')
        self.assertTrue(record['findings'][1]['reason'])
        self.assertEqual(self.client.post(self.base+'/episode-settings-review', json={
            'episode': 1, 'version': version, 'action': 'approve',
        }).status_code, 200)
        self.assertEqual(self.calls, 0)

    def test_episode_review_can_recover_after_invalid_result_and_stops_on_conflict(self):
        quote = '서윤은 등대에서 다섯 번 울리는 종소리를 들었다.'
        replies = iter([
            {'issues': ['이전 회차에서는 종이 세 번 울렸다고 했어요. 근거: ' + quote]},
            {'updates': []},
            {'findings': [{'kind': 'characters', 'fact': '서윤이 등대를 방문했다.',
                           'quote': quote, 'status': 'clear', 'reason': ''}]},
            {'names': ['서윤']},
        ])
        self.client = create_app(self.path, lambda prompt: next(replies)).test_client()
        key = 'episode:2'
        version = self.client.post(self.base+'/edit', json={
            'key': key, 'text': quote, 'base_version': None,
        }).json['version']
        for action in ('review', 'approve'):
            self.assertEqual(self.client.post(self.base+'/draft', json={
                'key': key, 'version': version, 'action': action,
            }).status_code, 200)

        def review(action, **extra):
            return self.client.post(self.base+'/episode-settings-review', json={
                'episode': 2, 'version': version, 'action': action, **extra,
            })

        self.assertEqual(review('continuity').status_code, 200)
        self.assertEqual(review('organize').status_code, 409)
        self.assertIn(quote, self.state()['records']['2']['continuity_issues'][0])
        self.assertEqual(review('continue', note='종소리의 이유는 4화에서 공개').status_code, 200)
        self.assertEqual(review('organize').status_code, 400)
        self.assertEqual(self.state()['records']['2']['setting_review'], 'organize')
        self.assertEqual(self.state()['official'][key]['text'], quote)
        self.assertEqual(review('organize').status_code, 200)
        self.assertEqual(self.state()['records']['2']['setting_review'], 'done')
        self.assertEqual(self.state()['records']['2']['findings'][0]['name'], '서윤')
        from workflow import build_episode_context
        memory = json.loads(build_episode_context(self.state(), 3))
        decision = memory['author_intentional_developments']['2']
        self.assertEqual(decision['note'], '종소리의 이유는 4화에서 공개')
        self.assertIn(quote, decision['issues'][0])

    def test_continuity_revision_keeps_official_until_approval(self):
        key = 'episode:2'
        original = '등대의 종이 다섯 번 울렸다.'
        revised = ('등대의 종이 세 번 울렸다. ' * 400)[:4200].strip()
        issue = '앞 회차에서 종은 세 번 울렸다.'
        repair_calls = []

        def ai(prompt):
            if prompt.startswith('이번 원고와 이전에 확정한'):
                return {'issues': [issue] if original in prompt else []}
            self.assertIn(issue, prompt)
            repair_calls.append(prompt)
            if len(repair_calls) == 1:
                return {'text': '', 'patches': [{'old': '원고에 없는 문장', 'new': '수정 문장'}]}
            self.assertIn('이전 수정 적용 실패', prompt)
            self.assertIn('전체 원고를 반환', prompt)
            return {'text': revised}

        self.client = create_app(self.path, ai).test_client()
        version = self.client.post(self.base+'/edit', json={
            'key': key, 'text': original, 'base_version': None,
        }).json['version']

        def approve(version):
            for action in ('review', 'approve'):
                self.assertEqual(self.client.post(self.base+'/draft', json={
                    'key': key, 'version': version, 'action': action,
                }).status_code, 200)

        def continuity(version):
            return self.client.post(self.base+'/episode-settings-review', json={
                'episode': 2, 'version': version, 'action': 'continuity',
            })

        approve(version)
        self.assertEqual(continuity(version).status_code, 200)
        response = self.client.post(self.base+'/chat', json={
            'request_id': 'fix-continuity-2', 'text': '2화 설정 충돌 수정',
            'operation': {'type': 'resolve_continuity', 'key': key, 'base_version': version},
        })
        self.assertEqual(response.status_code, 200, response.json)
        state = self.state()
        self.assertEqual(state['official'][key]['text'], original)
        draft = state['drafts'][key]
        self.assertEqual(draft['text'].strip(), revised.strip())
        self.assertEqual(len(repair_calls), 2)
        approve(draft['version'])
        self.assertEqual(self.state()['records']['2']['setting_review'], 'organize')

    def test_episode_approval_records_supported_facts_and_keeps_review_items(self):
        quote = '서린이 연회장의 열린 문을 지나 홀에 들어섰다.'
        manuscript = (quote * 210)[:4500]
        def ai(prompt):
            if prompt.startswith('REFERENCE_BACKFILL'):
                return {'findings': [
                    {'kind': 'characters', 'fact': '서린이 연회장에 들어왔다.', 'quote': quote, 'status': 'clear'},
                    {'kind': 'lore', 'fact': '서린은 비밀 조직 소속이다.', 'quote': '없는 구절', 'status': 'clear'},
                ]}
            return {'reply': '원고', 'updates': [{'kind': 'episode', 'episode': 1, 'content': manuscript}]}
        self.client = create_app(self.path, ai).test_client()
        self.assertEqual(self.chat().status_code, 200)
        state = self.state()
        draft = state['drafts']['episode:1']
        self.assertEqual(len(draft['findings']), 2)
        self.assertFalse(state.get('records'))
        for action in ('review', 'approve'):
            response = self.client.post(self.base+'/draft', json={'key': 'episode:1', 'version': draft['version'], 'action': action})
            self.assertEqual(response.status_code, 200)
        findings = self.state()['records']['1']['findings']
        self.assertTrue(findings[0]['reason'])
        self.assertTrue(findings[1]['reason'])
        response = self.client.post(self.base+'/episode-finding', json={
            'episode': 1, 'version': draft['version'], 'index': 0, 'action': 'remove',
        })
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.state()['official']['episode:1']['text'], manuscript)

    def test_short_full_revision_is_expanded_or_rejected(self):
        expanded = [False]
        def ai(prompt):
            if prompt.startswith('다음 한국어 소설 원고 전체'):
                return {'text': '본문' * (2000 if expanded[0] else 100)}
            if prompt.startswith('REFERENCE_BACKFILL'):
                return {'updates': []}
            return {'reply': '수정안', 'updates': [{'kind': 'episode', 'episode': 1, 'content': '짧은 원고'}]}
        self.client = create_app(self.path, ai).test_client()
        self.assertEqual(self.chat().status_code, 502)
        self.assertNotIn('episode:1', self.state()['drafts'])
        expanded[0] = True
        self.assertEqual(self.chat().status_code, 200)
        saved = self.state()['drafts']['episode:1']['text']
        self.assertGreaterEqual(len(saved), 4000)
        self.assertIn('짧은 원고', saved)

    def test_scene_generation_and_official_facts(self):
        first, last = '첫 장면.' * 400, '마지막 장면.' * 350
        results = iter([
            {'reply': '1화 초안입니다', 'updates': [], 'write_episode': 1, 'scene_goals': ['도입', '결말']},
            {'text': first + '\n\n' + last, 'title': '첫 모험'}, {'summary': '요약', 'facts': '새 인물: 유진'}])
        self.client = create_app(self.path, lambda p: {'updates': []} if p.startswith('REFERENCE_BACKFILL') else next(results)).test_client()
        self.assertEqual(self.chat().status_code, 200)
        s = self.state()
        self.assertEqual(s['drafts']['episode:1']['text'], first + '\n\n' + last)
        self.assertFalse(s['official'])
        vid = s['drafts']['episode:1']['version']
        for action in ('review', 'approve'):
            self.client.post(self.base+'/draft', json={'key': 'episode:1', 'version': vid, 'action': action})
        self.assertEqual(self.state()['official']['episode:1']['facts'], '새 인물: 유진')
        official = self.state()['official']['episode:1']
        self.assertEqual(official['title'], '첫 모험')
        renamed = self.client.post(self.base+'/edit', json={
            'key': 'episode:1', 'text': official['text'],
            'title': '사용자 제목', 'base_version': official['version']})
        self.assertEqual(renamed.status_code, 200)
        renamed_state = self.state()
        self.assertNotIn('episode:1', renamed_state['drafts'])
        self.assertEqual(renamed_state['official']['episode:1']['title'], '사용자 제목')
        self.assertEqual(renamed_state['official']['episode:1']['facts'], official['facts'])
        self.assertEqual(renamed_state['official']['episode:1']['version'], official['version'])


    def test_cancel_discards_late_result_and_blocks_concurrent(self):
        def ai(prompt):
            other = self.chat('concurrent-01')
            self.assertEqual(other.status_code, 409)
            self.client.post(self.base+'/cancel/request-0001', json={})
            return {'reply': '늦은 응답', 'updates': []}
        self.client = create_app(self.path, ai).test_client()
        self.assertEqual(self.chat().status_code, 409)
        self.assertEqual(len(self.state()['messages']), 2)

    def test_initial_plan_does_not_request_discarded_references_or_manuscript_work(self):
        prompts = []
        def ai(prompt):
            prompts.append(prompt)
            return {'reply': '기획안', 'updates': [{'kind': 'plan', 'content':
                '제목: 닫힌 수첩\n로그라인: 지진라가 서태준의 제안을 거절한다.\n장르: 드라마\n\n## 시놉시스\n지진라는 서태준을 싫어하지만 그의 제안을 고민한다.'}]}
        app = create_app(self.path, ai)
        try:
            client = app.test_client()
            response = client.post(self.base+'/chat', json={
                'request_id': 'focused-initial-plan', 'text': '지진라는 서태준이 싫어. 3화로 기획해줘.'})
            self.assertEqual(response.status_code, 200, response.json)
            self.assertEqual(len(prompts), 1)
            prompt = prompts[0]
            self.assertIn('누가 누구를 좋아하거나 싫어하는지', prompt)
            self.assertIn('사용자가 정한 이름은 반복 글자까지 정확히 유지', prompt)
            self.assertIn('3화로 기획해줘', prompt)
            self.assertIn('이 자료들은 기획안 승인 후 따로 생성', prompt)
            self.assertNotIn('초안을 모두 반환', prompt)
            self.assertNotIn('집필 계획 기준', prompt)
            self.assertNotIn('scene_goals', prompt)
            self.assertEqual(set(client.get(self.base).json['state']['drafts']), {'plan:0'})
        finally:
            app.extensions['storyloom_workers'].shutdown(wait=True)

    def test_missing_reference_sheets_backfill_as_drafts(self):
        calls_to_references = []
        def ai(prompt):
            if prompt.startswith('PLAN_RECONCILIATION'):
                return {'patches': []}
            if prompt.startswith('REFERENCE_BACKFILL'):
                calls_to_references.append(prompt)
                if '다음 종류만 반환: places,lore' in prompt:
                    return {'updates': [
                        {'kind': 'places', 'content': '## 마지막 등대\n설명: 서윤이 편지를 발견하는 곳.'},
                        {'kind': 'lore', 'content': '## 봉인된 편지\n설명: 편지에는 실종의 단서가 담겨 있다.'},
                    ]}
                return {'updates': [{'kind': 'characters', 'content': '## 서윤\n역할: 주인공'},
                                    {'kind': 'world', 'content': '## 해안 마을\n배경: 등대가 있는 마을'},
                                    {'kind': 'plot', 'content': '## 전체 플롯\n시작: 서윤이 등대로 간다.\n중간: 편지를 찾는다.\n결말: 편지의 주인을 만난다.\n\n## 회차 구성\n1화: 서윤이 등대로 간다.'}]}
            return {'reply': '기획안', 'updates': [{'kind': 'plan', 'content': '제목: 마지막 등대\n로그라인: 서윤이 등대를 찾는다.\n장르: 미스터리\n\n## 시놉시스\n서윤이 등대에서 편지를 발견한다.'}]}
        self.client = create_app(self.path, ai).test_client()
        self.assertEqual(self.chat().status_code, 200)
        state = self.state()
        self.assertEqual(set(state['drafts']), {'plan:0'})
        self.assertFalse(calls_to_references)
        self.assertFalse(state['official'])
        plan = state['drafts']['plan:0']
        edited = self.client.post(self.base+'/edit', json={
            'key': 'plan:0', 'base_version': plan['version'], 'text': plan['text']+'\n주인공은 서윤이다.'})
        self.assertEqual(edited.status_code, 200)
        version = edited.json['version']
        backup = self.client.get(self.base+'/export').json
        restored = self.client.post('/api/projects/import', json=backup)
        self.assertEqual(restored.status_code, 200, restored.json)
        restored_base = '/api/projects/'+restored.json['id']
        copy = self.client.get(restored_base).json['state']
        self.assertTrue(copy['initial_references_pending'])
        restored_plan = copy['drafts']['plan:0']
        self.assertEqual(self.client.post(restored_base+'/draft', json={'action': 'review', 'key': 'plan:0',
            'version': restored_plan['version']}).status_code, 409)
        restored_checked = self.client.post(restored_base+'/reconcile-plan', json={'version': restored_plan['version']})
        self.assertEqual(restored_checked.status_code, 200)
        self.assertEqual(self.client.post(restored_base+'/draft', json={'action': 'approve', 'key': 'plan:0',
            'version': restored_checked.json['version']}).status_code, 200)
        self.assertEqual(self.client.post(restored_base+'/chat', json={'request_id': 'restored-reference-setup',
            'text': '설정 초안 만들기', 'setup_plan_version': restored_checked.json['version']}).status_code, 200)
        self.assertFalse(self.client.get(restored_base).json['state'].get('initial_references_pending'))
        setup = {'request_id': 'reference-setup-01', 'text': '설정 초안 만들기', 'setup_plan_version': version}
        self.assertEqual(self.client.post(self.base+'/chat', json=setup).status_code, 409)
        self.assertEqual(self.action('review', version=version).status_code, 409)
        checked = self.client.post(self.base+'/reconcile-plan', json={'version': version})
        self.assertEqual(checked.status_code, 200)
        version = checked.json['version']
        setup['setup_plan_version'] = version
        self.assertEqual(self.action('approve', version=version).status_code, 200)
        self.assertEqual(self.client.post(self.base+'/edit', json={
            'key': 'plan:0', 'base_version': version, 'text': '확정 후 변경'}).status_code, 409)
        self.assertEqual(self.client.post(self.base+'/chat', json=setup).status_code, 200)
        state = self.state()
        self.assertEqual(set(state['drafts']), {'characters:0', 'world:0', 'places:0', 'lore:0', 'plot:0'})
        self.assertEqual(set(state['official']), {'plan:0'})
        self.assertNotIn('initial_references_pending', state)
        self.assertEqual(state['selected'], 'characters')
        self.assertIn('주인공은 서윤이다.', calls_to_references[0])
        calls = len(self.client.get(self.base+'/versions').json)
        self.assertTrue(self.chat().json['duplicate'])
        self.assertTrue(self.client.post(self.base+'/chat', json=setup).json['duplicate'])
        self.assertEqual(len(self.client.get(self.base+'/versions').json), calls)

    def test_initial_manual_plan_restore_and_reconcile(self):
        replies = iter([
            {'patches': [{'old': '서준', 'new': '서윤'}]},
            {'patches': [{'old': '서준은 등대로 간다.', 'new': '서윤은 등대로 간다.'},
                         {'old': '서준의 여행은', 'new': '서윤의 여행은'}]},
            {'patches': [{'old': '시윤이 길을 잃는다.', 'new': '하린이 길을 잃는다.'}]},
            {'patches': [{'old': '시윤이 길을 잃는다.', 'new': '하린이 길을 잃는다.'}]},
        ])
        self.client = create_app(self.path, lambda p: next(replies)).test_client()
        original = '제목: 등대\n로그라인: 서준이 길을 잃는다.\n## 시놉시스\n서준은 등대로 간다. 서준의 여행은 계속된다.'
        def edit(text, base=None):
            reply = self.client.post(self.base+'/edit', json={'key':'plan:0', 'text':text, 'base_version':base})
            self.assertEqual(reply.status_code, 200)
            return reply.json['version']
        base = edit(original)
        text = original.replace('서준이', '서윤이')
        edited = edit(text, base)
        self.assertEqual(self.action('approve', version=edited).status_code, 409)
        self.assertEqual(self.action('restore_setup', version=edited).status_code, 200)
        self.assertEqual(self.state()['drafts']['plan:0']['text'], original)
        self.assertNotIn('setup_edit_base', self.state()['drafts']['plan:0'])
        edited = edit(text, base)
        checked = self.client.post(self.base+'/reconcile-plan', json={'version':edited})
        self.assertEqual(checked.status_code, 200)
        draft = self.state()['drafts']['plan:0']
        self.assertEqual(draft['text'], text.replace('서준', '서윤'))
        self.assertEqual(draft['status'], 'review')
        self.assertFalse(self.state()['official'])
        revised = draft['text'].replace('서윤이', '시윤이')
        edited = edit(revised, draft['version'])
        self.assertEqual(self.client.post(self.base+'/reconcile-plan', json={'version':edited}).status_code, 502)
        draft = self.state()['drafts']['plan:0']
        self.assertEqual(draft['text'], revised)
        self.assertIn('setup_edit_base', draft)

    def test_initial_plan_preserves_repeated_name_when_ai_repeats_its_replacement(self):
        self.client = create_app(self.path, lambda prompt: {'patches': [
            {'old': '진혁', 'new': '진지지진혁'},
        ]}).test_client()
        original = '제목: 등대\n로그라인: 강진혁이 길을 잃는다.\n## 시놉시스\n강진혁은 등대로 간다. 진혁은 문을 연다.'
        first = self.client.post(self.base+'/edit', json={'key': 'plan:0', 'text': original})
        edited = self.client.post(self.base+'/edit', json={
            'key': 'plan:0', 'text': original.replace('강진혁은', '강진지지진혁은'),
            'base_version': first.json['version'],
        })
        result = self.client.post(self.base+'/reconcile-plan', json={'version': edited.json['version']})
        self.assertEqual(result.status_code, 200)
        draft = self.state()['drafts']['plan:0']
        self.assertEqual(draft['status'], 'review')
        self.assertEqual(draft['text'], '제목: 등대\n로그라인: 강진지지진혁이 길을 잃는다.\n'
                        '## 시놉시스\n강진지지진혁은 등대로 간다. 진지지진혁은 문을 연다.')

    def test_reference_failure_keeps_primary_output(self):
        def ai(prompt):
            if prompt.startswith('REFERENCE_BACKFILL'):
                raise ValueError('unavailable')
            return {'reply': '기획안', 'updates': [{'kind': 'plan', 'content': '보존할 기획'}]}
        self.client = create_app(self.path, ai).test_client()
        self.assertEqual(self.chat().status_code, 200)
        plan = self.state()['drafts']['plan:0']
        for action in ('review', 'approve'):
            response = self.client.post(self.base+'/draft', json={
                'action': action, 'key': 'plan:0', 'version': plan['version']})
            self.assertEqual(response.status_code, 200)
        setup = {'request_id': 'reference-setup-01', 'text': '설정 초안 만들기', 'setup_plan_version': plan['version']}
        for _ in range(2):
            self.assertEqual(self.client.post(self.base+'/chat', json=setup).status_code, 502)
            self.assertEqual(self.state()['official']['plan:0']['text'], '보존할 기획')
            self.assertTrue(self.state()['initial_references_pending'])

    def test_html_and_origin_protection(self):
        self.assertEqual(self.client.get('/').status_code, 200)
        with self.client.get('/static/app.js') as response:
            self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.post('/api/projects', json={'title': 'x'}, headers={'Origin': 'https://example.com'}).status_code, 403)


if __name__ == '__main__':
    unittest.main()
