"""Regression scenarios from the agreed Storyloom workflow; no real API or files."""
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from app import create_app
from workflow import build_context, validate_ai_result


class DetailedAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=str(Path(self.tmp.name)/'audit.sqlite3')
        self.ai=lambda p: {'reply':'대화','updates':[]}
        self.app=create_app(self.path,lambda p:({**self.ai(p),'premise_decision':'keep','plan_conflict':False,'plan_basis':''} if '[작품 전제 변경 판단]' in p else self.ai(p)))
        self.c=self.app.test_client()
        self.pid=self.c.post('/api/projects',json={'title':'검증용'}).json['id']
        self.base='/api/projects/'+self.pid

    def tearDown(self):
        self.app.extensions['storyloom_workers'].shutdown(wait=True)
        self.tmp.cleanup()

    def state(self):
        return self.c.get(self.base).json['state']

    def edit(self,key,text,base=None):
        return self.c.post(self.base+'/edit',json={'key':key,'text':text,'base_version':base})

    def approve(self,key):
        vid=self.state()['drafts'][key]['version']
        for action in ('review','approve'):
            self.assertEqual(self.c.post(self.base+'/draft',json={'key':key,'version':vid,'action':action}).status_code,200)

    def chat(self,rid='audit-request-001',**extra):
        return self.c.post(self.base+'/chat',json={'request_id':rid,'text':'검증 요청',**extra})

    def test_malformed_ai_response_can_be_retried_without_cached_bad_plan(self):
        self.ai=lambda p:{'reply':'잘못된 자료','updates':[None]}
        self.assertEqual(self.chat().status_code,502)
        self.assertFalse(self.state()['drafts'])
        self.ai=lambda p:{'reply':'정상 복구','updates':[]}
        self.assertEqual(self.chat().status_code,200)
        self.assertEqual([m['role'] for m in self.state()['messages']],['assistant','user','assistant'])

    def test_partial_selection_blocks_full_content_changes_outside_quote(self):
        self.edit('episode:1','앞문장\n고칠문장\n뒷문장')
        self.approve('episode:1')
        original=self.state()['official']['episode:1'].copy()
        self.ai=lambda p:{'reply':'수정','updates':[{'kind':'episode','episode':1,'content':'바뀐앞문장\n수정문장\n뒷문장'}]}
        scope={'key':'episode:1','quote':'고칠문장'}
        self.assertEqual(self.chat(edit_scope=scope).status_code,502)
        self.assertEqual(self.state()['official']['episode:1'],original)
        self.assertNotIn('episode:1',self.state()['drafts'])
        self.ai=lambda p:{'reply':'수정','updates':[{'kind':'episode','episode':1,'patches':[{'old':'고칠문장','new':'수정문장'}]}]}
        self.assertEqual(self.chat(edit_scope=scope).status_code,200)
        self.assertEqual(self.state()['drafts']['episode:1']['text'],'앞문장\n수정문장\n뒷문장')
        self.assertEqual(self.state()['official']['episode:1'],original)

    def test_scoped_confirmed_plan_is_locked(self):
        self.edit('plan:0', '앞 문장\n선택 문장\n뒤 문장')
        self.approve('plan:0')
        self.ai = lambda prompt: self.fail('Confirmed plans must be locked before calling AI')
        result = self.chat(edit_scope={'key': 'plan:0', 'quote': '선택 문장'})
        self.assertEqual(result.status_code, 409)
        state = self.state()
        self.assertNotIn('plan:0', state['drafts'])
        self.assertEqual(state['official']['plan:0']['text'], '앞 문장\n선택 문장\n뒤 문장')

    def test_duplicate_quote_fails_before_ai_and_scope_is_part_of_request_id(self):
        self.edit('episode:1','반복 반복')
        self.ai=lambda p:self.fail('AI must not be called for ambiguous selection')
        self.assertEqual(self.chat(edit_scope={'key':'episode:1','quote':'반복'}).status_code,400)
        self.assertEqual(len(self.state()['messages']),1)

    def test_preview_edit_targets_selected_occurrence_and_rejects_stale_version(self):
        text = '📚 같은 문장.\n\n같은 문장.'
        self.edit('episode:1', text)
        self.approve('episode:1')
        original = self.state()['official']['episode:1']
        scope = {'key': 'episode:1', 'quote': '같은 문장.', 'start': text.rindex('같은 문장.'),
                 'version': original['version']}
        self.ai = lambda prompt: self.fail('Stale selection must not call AI')
        stale = self.chat(edit_scope={**scope, 'version': original['version'] + 1})
        self.assertEqual(stale.status_code, 409)
        self.ai = lambda prompt: {'replacement': '바뀐 문장.'}
        self.assertEqual(self.chat(edit_scope=scope).status_code, 200)
        state = self.state()
        self.assertEqual(state['official']['episode:1']['text'], text)
        self.assertEqual(state['drafts']['episode:1']['text'], '📚 같은 문장.\n\n바뀐 문장.')

    def test_preview_rename_keeps_one_reference_heading_marker(self):
        self.edit('characters:0', '## ## 루카스\n역할: 검의 주인')
        original = self.state()['drafts']['characters:0']
        self.assertEqual(original['text'], '## 루카스\n역할: 검의 주인')
        self.ai = lambda prompt: {'replacement': '## 루루비스'}
        response = self.chat(edit_scope={
            'key': 'characters:0', 'quote': '루카스', 'start': 3, 'version': original['version'],
        })
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(self.state()['drafts']['characters:0']['text'], '## 루루비스\n역할: 검의 주인')

    def test_retry_after_source_edit_refuses_old_checkpoint_and_does_not_leave_running(self):
        def ai(prompt):
            if prompt.startswith('당신은'):
                return {'reply':'계획','updates':[],'write_episode':1,'scene_goals':['도입','결말']}
            if '목표: 도입' in prompt:
                return {'text':'저장된 첫 장면'}
            raise ValueError('중단')
        self.ai=ai
        self.assertEqual(self.chat().status_code,502)
        self.edit('world:0','중간에 바꾼 세계관')
        self.ai=lambda p:self.fail('Stale checkpoint must not call AI')
        self.assertEqual(self.chat().status_code,409)
        self.assertEqual(self.c.get(self.base+'/jobs/audit-request-001').json['status'],'failed')

    def test_approval_during_generation_keeps_new_official_and_saves_conflict_version(self):
        self.edit('characters:0','## 서윤\n성격: 신중함')
        self.approve('characters:0')
        def ai(prompt):
            official=self.state()['official']['characters:0']
            self.edit('characters:0','## 서윤\n성격: 대담함',official['version'])
            self.approve('characters:0')
            return {'reply':'이전 내용에 기반한 제안','updates':[{'kind':'characters','content':'## 서윤\n성격: 침착함'}]}
        self.ai=ai
        response = self.c.post(self.base+'/chat', json={
            'request_id': 'audit-request-001', 'text': '등장인물을 수정해줘'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.state()['official']['characters:0']['text'],'## 서윤\n성격: 대담함')
        self.assertNotIn('characters:0',self.state()['drafts'])
        self.assertEqual(len(self.state()['conflicts']),1)
        versions=self.c.get(self.base+'/versions').json
        self.assertTrue(any(json.loads(v['content'])['text']=='## 서윤\n성격: 침착함' for v in versions))

    def test_new_app_instance_does_not_cancel_existing_live_worker(self):
        entered,release=threading.Event(),threading.Event()
        def ai(prompt):
            entered.set()
            if not release.wait(5):raise ValueError('test timeout')
            return {'reply':'완료','updates':[]}
        self.ai=ai
        try:
            self.assertEqual(self.chat(background=True).status_code,202)
            self.assertTrue(entered.wait(2))
            other=create_app(self.path,lambda p:{'reply':'다른 앱','updates':[]})
            other.extensions['storyloom_workers'].shutdown(wait=True)
            self.assertEqual(self.c.get(self.base+'/jobs/audit-request-001').json['status'],'running')
        finally:
            release.set()
            self.app.extensions['storyloom_workers'].shutdown(wait=True)
        self.assertEqual(self.c.get(self.base+'/jobs/audit-request-001').json['status'],'done')

    def test_stale_worker_lease_recovers_without_deleting_checkpoint(self):
        with sqlite3.connect(self.path) as con:
            con.execute('INSERT INTO jobs(id,project,payload,status,created,lease,checkpoint) VALUES(?,?,?,?,?,?,?)',
                        ('stale-request-001',self.pid,'{"text":"요청"}','running',time.time()-200,time.time()-200,'{"scenes":["보존"]}'))
        con.close()
        self.assertEqual(self.c.get(self.base+'/jobs/stale-request-001').json['status'],'failed')
        with sqlite3.connect(self.path) as con:
            checkpoint=con.execute('SELECT checkpoint FROM jobs WHERE id=?',('stale-request-001',)).fetchone()[0]
        con.close()
        self.assertIn('보존',checkpoint)

    def test_restore_checks_current_version_and_preserves_all_versions(self):
        old=self.edit('plan:0','옛 초안').json['version']
        current=self.edit('plan:0','새 초안',old).json['version']
        count=len(self.c.get(self.base+'/versions').json)
        response=self.c.post(self.base+'/draft',json={'action':'restore','key':'plan:0','version':old,'base_version':old})
        self.assertEqual(response.status_code,409)
        self.assertEqual(len(self.c.get(self.base+'/versions').json),count)
        self.assertEqual(self.state()['drafts']['plan:0']['version'],current)

    def test_ai_settings_proposal_is_not_official_until_approved(self):
        self.ai=lambda p:{'reply':'설정 제안','updates':[],'settings':{'target_chars':4800,'total_episodes':40}}
        self.assertEqual(self.chat().status_code,200)
        self.assertEqual(self.state()['settings']['target_chars'],4000)
        self.approve('settings:0')
        self.assertEqual(self.state()['settings']['target_chars'],4800)

    def test_scene_total_limit_rejects_before_scene_call(self):
        calls=[]
        def ai(prompt):
            calls.append(prompt)
            return {'reply':'계획','updates':[],'write_episode':31,'scene_goals':['하나','둘']}
        self.ai=ai
        self.assertEqual(self.chat().status_code,502)
        self.assertEqual(len(calls),1)
        self.assertFalse(self.state()['drafts'])

    def test_approved_target_is_not_overridden_by_model_default(self):
        self.edit('settings:0',json.dumps({'target_chars':4800,'total_episodes':40}))
        self.approve('settings:0')
        prompts=[]
        def ai(prompt):
            prompts.append(prompt)
            if prompt.startswith('REFERENCE_BACKFILL'):return {'updates':[]}
            if prompt.startswith('이번 원고와 이전에 확정한'):return {'issues':[]}
            if prompt.startswith('당신은'):
                return {'reply':'집필','updates':[],'write_episode':1,'target_chars':4000,'scene_goals':['도입','끝']}
            return {'text':'장면'*2450,'summary':'전체 요약','facts':'새 사실'}
        self.ai=ai
        self.assertEqual(self.chat().status_code,200)
        scenes=[p for p in prompts if p.startswith('한국어 소설의 다음 장면')]
        self.assertEqual(len(scenes),1)
        self.assertTrue(all('회차 전체 목표 4800자' in p for p in scenes))

    def test_corrupt_backup_history_rolls_back_new_project(self):
        original=len(self.c.get('/api/projects').json)
        for history in ([None],['bad'],[{'id':True}]):
            response=self.c.post('/api/projects/import',json={'schema_version':1,'state':{'messages':[]},'versions':history})
            self.assertEqual(response.status_code,400)
            self.assertEqual(len(self.c.get('/api/projects').json),original)

    def test_fractional_and_boolean_episode_numbers_are_rejected(self):
        for number in (True,1.5,'2'):
            self.assertEqual(self.c.post(self.base+'/view',json={'selected':'episode','episode':number}).status_code,400)

    def test_invalid_json_objects_are_client_errors(self):
        for payload in ([],None,42,'text'):
            response=self.c.post('/api/projects',data=json.dumps(payload),content_type='application/json')
            self.assertEqual(response.status_code,400)

    def test_context_preserves_requested_old_chapter_and_all_official_facts(self):
        state={'messages':[{'role':'user','text':'1화 부분 수정'}], 'selected':'episode','episode':5,'drafts':{},'official':{},'settings':{'target_chars':4000,'total_episodes':30}}
        for n in range(1,7):
            state['official'][f'episode:{n}']={'text':f'{n}화 전문','summary':f'{n}화 요약','facts':f'{n}화 사실','status':'confirmed'}
        context=json.loads(build_context(state))
        self.assertEqual(context['official']['episode:1']['text'],'1화 전문')
        self.assertNotEqual(context['official']['episode:2']['text'],'2화 전문')
        self.assertEqual(context['official']['episode:2']['facts'],'2화 사실')

    def test_bad_model_shapes_and_duplicate_updates_are_rejected(self):
        for result in [None,{'reply':'x','updates':[{'kind':[]} ]},
                       {'reply':'x','updates':[],'write_episode':True},
                       {'reply':'x','updates':[],'write_episode':1,'scene_goals':'ab'},
                       {'reply':'x','updates':[{'kind':'plan','content':'a'},{'kind':'plan','content':'b'}]}]:
            with self.subTest(result=result),self.assertRaises(ValueError):
                validate_ai_result(result)

    def test_real_client_wrapper_uses_standard_mode_by_default(self):
        with patch('app.load_dotenv'), patch('app.gemini', return_value={'reply':'검증','updates':[]}) as mocked:
            app=create_app(self.path,auth_required=False)
            try:
                client=app.test_client()
                response=client.post(self.base+'/chat',json={'request_id':'mode-regression-001','text':'짧은 대화'})
                self.assertEqual(response.status_code,200)
                self.assertIsNone(mocked.call_args.kwargs['on_progress'])
                self.assertFalse(app.config['AI_STREAMING'])
            finally:
                app.extensions['storyloom_workers'].shutdown(wait=True)

    def test_plain_conversation_does_not_backfill_old_missing_sheets(self):
        self.edit('plan:0','기존 기획')
        calls=[]
        def ai(prompt):
            calls.append(prompt)
            return {'reply':'짧은 대화','updates':[]}
        self.ai=ai
        self.assertEqual(self.chat().status_code,200)
        self.assertEqual(len(calls),1)
        self.assertNotIn('일부 자료집',self.state()['messages'][-1]['text'])
