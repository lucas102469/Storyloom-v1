import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from google.genai import types

from app import create_app
from ai_client import gemini
from reference_quality import empty_reference_cards, needs_reference_classification
from reference_setup import render_reference_setup


def example():
    return {
        'characters': [{'name': '연우', 'description': '지친 일상에서 정원을 발견한 사람.',
                        'role': '주인공', 'personality': '섬세함', 'goal': '평온을 찾는다.',
                        'relationships': '시온에게 지혜를 배운다.',
                        'attributes': [{'field': '과거', 'value': '오래된 꿈을 접었다.'}]}],
        'world': [{'name': '시간의 틈', 'description': '정원에 들어가면 현실의 시간이 멈춘다.',
                   'attributes': [{'field': '규칙', 'value': '지친 사람이 골목 끝 문을 열면 들어올 수 있다.'}]}],
        'places': [{'name': '달빛 정원', 'description': '연우와 시온이 만나는 안식처.', 'attributes': []}],
        'lore': [{'name': '꿈의 씨앗', 'description': '잊었던 꿈에 반응해 싹이 난다.',
                  'attributes': [{'field': '제약', 'value': '스스로 심은 씨앗만 자란다.'}]}],
        'world_summary': '현실과 시간이 다르게 흐르는 정원에서 쉬며 꿈을 되찾는다.',
        'plot': {'beginning': '정원을 발견한다.', 'middle': '꿈을 되찾는다.', 'ending': '현실에서 꿈을 시작한다.',
                 'episodes': [{'number': 1, 'event': '정원에 들어가 시온을 만난다.'},
                              {'number': 2, 'event': '씨앗을 심는다.'}]},
    }


class ReferenceSetupTests(unittest.TestCase):
    def test_additional_attributes_are_optional_not_a_slot_to_fill(self):
        from reference_setup import SETUP_SCHEMA
        value=example()
        for kind in ('characters','world','places','lore'):
            self.assertNotIn('attributes',SETUP_SCHEMA['properties'][kind]['items']['required'])
            for card in value[kind]:card.pop('attributes')
        docs={u['kind']:u['content'] for u in render_reference_setup(value)['updates']}
        self.assertIn('설명: 연우와 시온이 만나는 안식처.',docs['places'])
        self.assertNotIn('과거:',docs['characters'])

    def test_sdk_contract_renders_all_reference_fields_without_another_ai_call(self):
        client = Mock()
        client.models.generate_content.return_value = types.GenerateContentResponse(candidates=[types.Candidate(
            finish_reason='STOP', content=types.Content(parts=[types.Part(text=json.dumps(example(), ensure_ascii=False))]))])
        with patch('ai_client.os.environ', {'GEMINI_API_KEY': 'dummy-key'}), \
                patch('ai_client.get_client', return_value=client), self.assertLogs('storyloom.ai'):
            result = gemini('synthetic initial setup', task='reference_setup')
        self.assertEqual(client.models.generate_content.call_count, 1)
        schema = client.models.generate_content.call_args.kwargs['config'].response_json_schema
        self.assertEqual(set(schema['required']), set(example()))
        docs = {u['kind']: u for u in result['updates']}
        self.assertEqual(set(docs), {'characters', 'world', 'places', 'lore', 'plot'})
        self.assertIn('과거: 오래된 꿈을 접었다.', docs['characters']['content'])
        self.assertIn('제약: 스스로 심은 씨앗만 자란다.', docs['lore']['content'])
        self.assertEqual(docs['world']['summary'], example()['world_summary'])
        for kind in ('characters', 'world', 'places', 'lore'):
            self.assertEqual(empty_reference_cards(docs[kind]['content']), [])
        self.assertFalse(needs_reference_classification(docs['world']['content']))

    def test_initial_setup_uses_one_generation_and_keeps_the_confirmed_plan(self):
        plan = '제목: 달빛 정원\n로그라인: 연우가 정원에서 쉬며 꿈을 되찾는다.\n장르: 힐링 판타지\n\n## 시놉시스\n연우가 정원을 발견하고 시온과 교류한다.'
        calls = []
        def ai(prompt):
            calls.append(prompt)
            if prompt.startswith('REFERENCE_BACKFILL'):
                self.assertEqual(prompt.count('연우가 정원을 발견하고 시온과 교류한다.'),1)
                self.assertNotIn('확정하지 않은 해석은 reply',prompt)
                return render_reference_setup(example())
            if len(calls) == 1:
                return {'reply': '기획안', 'updates': [{'kind': 'plan', 'content': plan}]}
            raise AssertionError('정상 초안에 추가 생성·재분류·설명 보완 호출이 발생했습니다.')
        with tempfile.TemporaryDirectory() as folder:
            app = create_app(str(Path(folder)/'test.sqlite3'), ai)
            try:
                c = app.test_client()
                pid = c.post('/api/projects', json={'title': '속도 검사'}).json['id']
                base = '/api/projects/'+pid
                self.assertEqual(c.post(base+'/chat', json={'request_id': 'setup-plan-01', 'text': '기획해줘'}).status_code, 200)
                v = c.get(base).json['state']['drafts']['plan:0']['version']
                for action in ('review', 'approve'):
                    self.assertEqual(c.post(base+'/draft', json={'key': 'plan:0', 'version': v, 'action': action}).status_code, 200)
                response = c.post(base+'/chat', json={'request_id': 'setup-speed-01', 'text': '설정 초안 만들기', 'setup_plan_version': v})
                self.assertEqual(response.status_code, 200, response.json)
                state = c.get(base).json['state']
                self.assertEqual(state['last_work_result']['calls_this_run'], 1)
                self.assertEqual(state['official']['plan:0']['text'], plan)
                self.assertEqual(set(state['drafts']), {'characters:0', 'world:0', 'places:0', 'lore:0', 'plot:0'})
                self.assertNotIn('initial_references_pending', state)
                self.assertIn('과거: 오래된 꿈을 접었다.', state['drafts']['characters:0']['text'])
            finally:
                app.extensions['storyloom_workers'].shutdown(wait=True)

    def test_invalid_bundle_and_duplicate_episode_numbers_are_rejected(self):
        for mutation in ('description', 'missing', 'duplicate'):
            value = copy.deepcopy(example())
            if mutation == 'description':
                value['world'][0]['description'] = ''
            elif mutation == 'missing':
                del value['places']
            else:
                value['plot']['episodes'].append(value['plot']['episodes'][0])
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                render_reference_setup(value)

    def test_ambiguous_rooms_still_require_classification(self):
        self.assertTrue(needs_reference_classification('## 회사\n설명: 긴 책상이 늘어선 사무실이다.'))
        self.assertTrue(needs_reference_classification('## 골목\n규칙: 밤에는 통행하지 않는다.'))
        self.assertFalse(needs_reference_classification('## 시간의 틈\n규칙: 골목 끝 문을 열면 현실 시간이 멈춘다.'))
