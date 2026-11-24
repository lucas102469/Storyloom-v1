import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from app import create_app
from ai_client import RetryableAIError
import branch_edit
from tests import test_setting_history as fixtures

class BranchCurrentEditTests(unittest.TestCase):
    seed=fixtures.SettingHistoryTests.seed

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=str(Path(self.temp.name)/'branch-edit.sqlite3')
        self.calls=[];self.plan_calls=0;self.audit_calls=0
        self.response=self.normal_response
        def ai(prompt):
            self.calls.append(prompt);return self.response(prompt)
        self.app=create_app(self.path,ai,auth_required=False);self.c=self.app.test_client()
        self.pid=self.c.post('/api/projects',json={'title':'현재 회차 수정'}).json['id'];self.base='/api/projects/'+self.pid
        self.seed(chapters=True)
        self.opening='아서는 검투장에서 나왔다.'
        self.reaction='사람들은 그를 바라보았다.'
        self.original=self.opening+' '+self.reaction+'\n\n'+'그는 차가운 골목을 천천히 걸었다. '*65
        self.new_opening='아서는 출구에 버려진 검을 집었다. 칼날에는 다른 사람의 피가 말라붙어 있었다. 그는 그 검을 들고 검투장에서 나왔다.'
        self.new_reaction='사람들은 피 묻은 검을 보고 놀라 한 걸음 물러섰다.'
        with closing(sqlite3.connect(self.path)) as con,con:
            state=self.state()
            state['settings']={'target_chars':1000,'total_episodes':5}
            previous='아서는 작은 티켓을 쥔 채 검투장에 들어섰다. '+'그는 평범한 인간으로 마나와 특별한 힘이 없었다. '*50
            state['official']['episode:1']['text']=previous
            second=state['drafts'].pop('episode:2');second.update(text=self.original,status='confirmed')
            state['official']['episode:2']=second
            for n in (1,2):
                item=state['official'][f'episode:{n}']
                state['records'][str(n)]={'version':item['version'],'summary':'기존 요약','facts':'기존 사실','setting_review':'done','findings':[]}
                con.execute("UPDATE versions SET status='confirmed',content=? WHERE id=?",(json.dumps({k:v for k,v in item.items() if k not in ('version','status')}),item['version']))
            state['branch_origin']={'id':'original','title':'원작','author':'원작자','episode':2,'settings_source':'snapshot',
                'presentation_version':2,'setup_flow':1,'setup_complete':True,'body':hashlib.sha256(b'original chapter two').hexdigest(),
                'prefix':{'1':hashlib.sha256(previous.encode()).hexdigest()}}
            state['selected']='episode';state['episode']=2
            con.execute('UPDATE projects SET state=? WHERE id=?',(json.dumps(state),self.pid))
        self.before=self.state()

    def tearDown(self):
        self.app.extensions['storyloom_workers'].shutdown(wait=True);self.temp.cleanup()

    def state(self):
        return self.c.get(self.base).json['state']

    def normal_response(self,prompt):
        if prompt.startswith('BRANCH_CURRENT_CHAPTER_EDIT'):
            self.plan_calls+=1
            return {'needs_clarification':False,'reply':'검의 획득과 사람들의 반응을 연결했어요.','patches':[
                {'old':self.opening,'new':self.new_opening},{'old':self.reaction,'new':self.new_reaction}]}
        if prompt.startswith('BRANCH_CURRENT_CHAPTER_AUDIT'):
            self.audit_calls+=1
            return {'issues':[],'summary':'버려진 검을 들고 나온 아서를 사람들이 두려워했다.','facts':'검에는 다른 사람의 피가 묻었다.','findings':[]}
        raise AssertionError('Unexpected request: '+prompt[:80])

    def chat(self,text='피 묻은 검을 들고 나와 사람들이 놀라게 고쳐줘',rid='branch-current-edit',scope=None):
        return self.c.post(self.base+'/chat',json={'request_id':rid,'text':text,**({'edit_scope':scope} if scope else {})})

    def test_changes_requested_scene_and_required_connections_only(self):
        result=self.chat();self.assertEqual(result.status_code,200,result.json)
        state=self.state();draft=state['drafts']['episode:2']
        self.assertEqual(state['official'],self.before['official'])
        self.assertEqual(state['records'],self.before['records'])
        expected=self.original.replace(self.opening,self.new_opening).replace(self.reaction,self.new_reaction)
        self.assertEqual(draft['text'],expected)
        self.assertEqual(self.plan_calls,1);self.assertEqual(self.audit_calls,1)
        version=draft['version']
        for action in ('review','approve'):
            accepted=self.c.post(self.base+'/draft',json={'key':'episode:2','version':version,'action':action})
            self.assertEqual(accepted.status_code,200,accepted.json)
        state=self.state()
        self.assertEqual(state['official']['episode:1'],self.before['official']['episode:1'])
        self.assertEqual(state['official']['episode:2']['text'],expected)
        reviewed=self.c.post(self.base+'/episode-settings-review',json={'episode':2,'version':version,'action':'organize'})
        self.assertEqual(reviewed.status_code,200,reviewed.json)
        self.assertEqual(len(self.calls),2,'same verified manuscript reuses findings instead of requesting them again')

    def test_selected_scene_can_include_necessary_reaction_in_same_chapter(self):
        source=self.before['official']['episode:2']
        scope={'key':'episode:2','quote':self.opening,'start':0,'version':source['version']}
        result=self.chat(scope=scope)
        self.assertEqual(result.status_code,200,result.json)
        self.assertIn(self.new_reaction,self.state()['drafts']['episode:2']['text'])
        self.assertEqual(self.state()['official']['episode:1'],self.before['official']['episode:1'])

    def test_impossible_change_asks_in_chat_without_editing_or_auditing(self):
        self.response=lambda prompt:{'needs_clarification':True,'reply':'앞 회차에서 사망한 인물을 그대로 등장시킬 수 없어요. 현재 회차에서 의도한 상황을 더 알려주세요.','patches':[]}
        result=self.chat('사망한 인물이 살아서 등장하게 고쳐줘')
        self.assertEqual(result.status_code,200,result.json)
        state=self.state();self.assertEqual(state['official'],self.before['official']);self.assertEqual(state['drafts'],self.before['drafts'])
        self.assertIn('의도한 상황',state['messages'][-1]['text']);self.assertEqual(len(self.calls),1)

    def test_audit_conflict_preserves_every_original_document(self):
        def respond(prompt):
            result=self.normal_response(prompt)
            if prompt.startswith('BRANCH_CURRENT_CHAPTER_AUDIT'):
                result['issues']=['요청과 무관하게 새 능력을 추가하여 앞 회차와 모순됩니다.']
            return result
        self.response=respond
        result=self.chat();self.assertEqual(result.status_code,200,result.json)
        state=self.state();self.assertEqual(state['official'],self.before['official']);self.assertEqual(state['drafts'],self.before['drafts'])
        self.assertIn('앞 회차는 유지',state['messages'][-1]['text'])

    def test_unchanged_patch_does_not_start_another_audit(self):
        self.response=lambda prompt:{'needs_clarification':False,'reply':'현재 원고에 이미 반영되어 있어요.','patches':[{'old':self.opening,'new':self.opening}]}
        self.assertEqual(self.chat().status_code,200)
        self.assertEqual(len(self.calls),1);self.assertEqual(self.state()['drafts'],self.before['drafts'])

    def test_previous_chapter_is_never_edited(self):
        result=self.chat('1화를 수정해줘')
        self.assertEqual(result.status_code,200,result.json)
        self.assertEqual(self.calls,[])
        self.assertEqual(self.state()['official'],self.before['official'])
        self.assertIn('분기 이전 회차는 유지',self.state()['messages'][-1]['text'])

    def test_failed_audit_retries_from_saved_patch_plan(self):
        failed=[False]
        def respond(prompt):
            if prompt.startswith('BRANCH_CURRENT_CHAPTER_AUDIT') and not failed[0]:
                failed[0]=True;raise RetryableAIError('일시적인 검토 응답 실패')
            return self.normal_response(prompt)
        self.response=respond
        first=self.chat();self.assertEqual(first.status_code,502,first.json)
        self.assertNotIn('episode:2',self.state()['drafts'])
        second=self.chat();self.assertEqual(second.status_code,200,second.json)
        self.assertEqual(self.plan_calls,1)
        self.assertIn('episode:2',self.state()['drafts'])

    def test_normal_writing_and_regular_projects_are_not_intercepted(self):
        regular=copy.deepcopy(self.before);regular.pop('branch_origin')
        self.assertIsNone(branch_edit.target(regular,'수정해줘'))
        self.assertIsNone(branch_edit.target(self.before,'다음 회차를 이어 써줘'))
        self.assertIsNone(branch_edit.target(self.before,'새로운 회차를 써줘'))
        self.assertIsNone(branch_edit.target(self.before,'밀정 이름을 룬델로 바꿔줘'))
        self.assertEqual(branch_edit.target(self.before,'1화와 연결해서 수정해줘'),2)

    def test_unrequested_whole_chapter_rewrite_is_not_applied(self):
        self.response=lambda prompt:{'needs_clarification':False,'reply':'회차를 전부 바꿨습니다.',
            'patches':[{'old':self.original,'new':self.original.replace('차가운','따뜻한')}]}
        result=self.chat();self.assertEqual(result.status_code,200,result.json)
        self.assertEqual(self.state()['drafts'],self.before['drafts'])
        self.assertIn('범위가 요청한 장면보다 너무 넓어',self.state()['messages'][-1]['text'])
        self.assertEqual(len(self.calls),1)

    def test_response_cannot_include_another_chapter_or_setting_update(self):
        self.response=lambda prompt:{'needs_clarification':False,'reply':'고쳤어요.','patches':[],
                                     'updates':[{'kind':'episode','episode':1,'content':'다른 원고'}]}
        result=self.chat();self.assertEqual(result.status_code,502,result.json)
        self.assertEqual(self.state()['official'],self.before['official'])
        self.assertEqual(self.state()['drafts'],self.before['drafts'])

    def test_concurrent_user_edit_is_preserved_instead_of_overwritten(self):
        manually_changed=self.original+' 사용자가 직접 덧붙인 내용.'
        changed=[False]
        def respond(prompt):
            if prompt.startswith('BRANCH_CURRENT_CHAPTER_AUDIT') and not changed[0]:
                changed[0]=True
                edited=self.c.post(self.base+'/edit',json={'key':'episode:2',
                    'base_version':self.before['official']['episode:2']['version'],'text':manually_changed})
                self.assertEqual(edited.status_code,200,edited.json)
            return self.normal_response(prompt)
        self.response=respond
        result=self.chat();self.assertEqual(result.status_code,502,result.json)
        self.assertIn('수정하는 동안 원고나 설정이 바뀌었어요',result.json['error'])
        self.assertEqual(self.state()['drafts']['episode:2']['text'],manually_changed)
        self.assertEqual(self.state()['official'],self.before['official'])
