import json
import sqlite3
import unittest
from contextlib import closing
from app import create_app
from setting_edits import edit_source
from tests import test_setting_history as fixtures

class SettingEditTargetTests(unittest.TestCase):
    setUp=fixtures.SettingHistoryTests.setUp
    tearDown=fixtures.SettingHistoryTests.tearDown
    seed=fixtures.SettingHistoryTests.seed

    def prepare(self):
        self.seed(chapters=True)
        with closing(sqlite3.connect(self.path)) as con,con:
            state=json.loads(con.execute('SELECT state FROM projects WHERE id=?',(self.pid,)).fetchone()[0])
            state['selected']='characters'
            state['official']['characters:0']['text']='## 루칸\n역할: 주인공\n설명: 나뭇가지를 강력한 검으로 만드는 체질이다.'
            state['official']['episode:1']['text']='그 사내는 정보 조직에서 파견된 베테랑 요원이었다.'
            state['records']['1'].update(setting_review='done',findings=[{
                'kind':'characters','name':'밀정','role':'정보 요원','field':'관계',
                'fact':'정보 조직에서 파견된 베테랑 요원이다.',
                'quote':'그 사내는 정보 조직에서 파견된 베테랑 요원이었다.','reason':''}])
            for key in ('characters:0','episode:1'):
                document=state['official'][key]
                con.execute('UPDATE versions SET content=? WHERE id=?',(json.dumps({k:v for k,v in document.items() if k not in ('version','status')}),document['version']))
            con.execute('UPDATE projects SET state=? WHERE id=?',(json.dumps(state),self.pid))
        return self.c.get(self.base).json

    def chat(self,text,scope=None,rid='setting-edit-test'):
        return self.c.post(self.base+'/chat',json={'request_id':rid,'text':text,**({'edit_scope':scope} if scope else {})})

    def wrong_scope(self,project):
        source=project['setting_edit_source']
        quote='나뭇가지를 강력한 검으로 만드는 체질이다.'
        return {'key':'characters:0','quote':quote,'start':source['text'].index(quote),'version':source['version']}

    def test_chat_confirms_conflicting_target_then_creates_named_draft_without_ai(self):
        project=self.prepare()
        original=project['state']['official']['characters:0']
        scope=self.wrong_scope(project)
        first=self.chat('밀정 이름은 룬델로 바꿔줘',scope)
        self.assertEqual(first.status_code,200,first.json)
        state=self.c.get(self.base).json['state']
        self.assertNotIn('characters:0',state['drafts'])
        self.assertIn('밀정의 이름을 룬델로',state['messages'][-1]['text'])
        self.assertIn('루칸',state['messages'][-1]['text'])
        second=self.chat('ㅇㅇ',scope,'confirm-edit-test')
        self.assertEqual(second.status_code,200,second.json)
        state=self.c.get(self.base).json['state']
        self.assertEqual(state['official']['characters:0'],original)
        self.assertIn('## 룬델',state['drafts']['characters:0']['text'])
        self.assertIn('나뭇가지를 강력한 검으로 만드는 체질이다.',state['drafts']['characters:0']['text'])
        self.assertNotIn('## 밀정',state['drafts']['characters:0']['text'])
        self.assertEqual(self.calls,[])
        duplicate=self.chat('ㅇㅇ',scope,'confirm-edit-test')
        self.assertTrue(duplicate.json.get('duplicate'))
        version=state['drafts']['characters:0']['version']
        for action in ('review','approve'):
            result=self.c.post(self.base+'/draft',json={'key':'characters:0','version':version,'action':action})
            self.assertEqual(result.status_code,200,result.json)
        state=self.c.get(self.base).json['state']
        self.assertEqual(state['linked_names']['밀정'],'룬델')
        self.assertIn('characters:0',state['revision_impact']['changes'])
        self.assertIn('## 밀정',state['revision_impact']['changes']['characters:0']['before'])

    def test_direct_rename_of_reflected_character_does_not_require_selection(self):
        self.prepare()
        result=self.chat('밀정 이름을 룬델로 바꿔줘')
        self.assertEqual(result.status_code,200,result.json)
        self.assertIn('## 룬델',self.c.get(self.base).json['state']['drafts']['characters:0']['text'])
        self.assertFalse(self.calls)

    def test_declining_confirmation_changes_no_document(self):
        project=self.prepare()
        self.chat('밀정 이름은 룬델로 바꿔줘',self.wrong_scope(project))
        result=self.chat('아니',None,'decline-edit-test')
        self.assertEqual(result.status_code,200,result.json)
        state=self.c.get(self.base).json['state']
        self.assertNotIn('characters:0',state['drafts'])
        self.assertNotIn('setting_edit_confirmation',state)

    def test_reflected_fact_is_valid_scoped_source_and_uses_existing_partial_edit(self):
        project=self.prepare()
        self.app.extensions['storyloom_workers'].shutdown(wait=True)
        def ai(prompt):
            self.calls.append(prompt)
            return {'premise_decision':'keep','plan_conflict':False,'plan_basis':'','replacement':'왕실 정보 조직에서 파견된 베테랑 요원이다.'}
        self.app=create_app(self.path,ai,auth_required=False);self.c=self.app.test_client()
        source=project['setting_edit_source'];quote='정보 조직에서 파견된 베테랑 요원이다.'
        scope={'key':'characters:0','quote':quote,'start':source['text'].index(quote),'version':source['version']}
        result=self.chat('왕실 소속이라고 고쳐줘',scope)
        self.assertEqual(result.status_code,200,result.json)
        state=self.c.get(self.base).json['state']
        self.assertIn('왕실 정보 조직',state['drafts']['characters:0']['text'])
        self.assertIn('## 루칸',state['drafts']['characters:0']['text'])
        self.assertIn('## 밀정',state['drafts']['characters:0']['text'])
        self.assertEqual(len(self.calls),1)
        effective=self.c.get(self.base).json['setting_edit_source']['text']
        self.assertEqual(effective.count('관계:'),1)
        version=state['drafts']['characters:0']['version']
        for action in ('review','approve'):
            response=self.c.post(self.base+'/draft',json={'key':'characters:0','version':version,'action':action})
            self.assertEqual(response.status_code,200,response.json)
        effective=self.c.get(self.base).json['setting_edit_source']['text']
        self.assertEqual(effective.count('관계:'),1)
        package=self.c.get(self.base+'/export').json
        restored=self.c.post('/api/projects/import',json=package)
        self.assertEqual(restored.status_code,200,restored.json)
        restored_project=self.c.get('/api/projects/'+restored.json['id']).json
        self.assertEqual(restored_project['setting_edit_source']['text'].count('관계:'),1)
        current=self.c.get(self.base).json['state']['official']['characters:0']
        edited=self.c.post(self.base+'/edit',json={'key':'characters:0','base_version':current['version'],
            'text':current['text'].replace('왕실 정보 조직','왕실 비밀 조직')})
        self.assertEqual(edited.status_code,200,edited.json)
        self.assertEqual(self.c.get(self.base).json['setting_edit_source']['text'].count('관계:'),1)

    def test_confirmation_is_invalidated_by_new_findings(self):
        project=self.prepare()
        self.chat('밀정 이름은 룬델로 바꿔줘',self.wrong_scope(project))
        with closing(sqlite3.connect(self.path)) as con,con:
            state=json.loads(con.execute('SELECT state FROM projects WHERE id=?',(self.pid,)).fetchone()[0])
            state['records']['1']['findings'][0]['fact']='이미 다른 내용으로 바뀐 설정이다.'
            con.execute('UPDATE projects SET state=? WHERE id=?',(json.dumps(state),self.pid))
        result=self.chat('ㅇㅇ',None,'stale-confirm-test')
        self.assertEqual(result.status_code,200,result.json)
        state=self.c.get(self.base).json['state']
        self.assertNotIn('characters:0',state['drafts'])
        self.assertIn('설정이 바뀌었어요',state['messages'][-1]['text'])

    def test_edit_source_ignores_unaccepted_and_removed_findings(self):
        project=self.prepare();state=project['state']
        finding=state['records']['1']['findings'][0]
        finding['reason']='검토 필요'
        self.assertNotIn('## 밀정',edit_source(state,'characters:0')['text'])
        finding['reason']='';finding['removed']=True
        self.assertNotIn('## 밀정',edit_source(state,'characters:0')['text'])

    def test_general_edit_target_conflict_uses_chat_confirmation(self):
        project=self.prepare();scope=self.wrong_scope(project)
        result=self.chat('밀정의 소속을 왕실로 수정해줘',scope)
        self.assertEqual(result.status_code,200,result.json)
        state=self.c.get(self.base).json['state']
        self.assertNotIn('characters:0',state['drafts'])
        self.assertIn('밀정에 요청한 수정',state['messages'][-1]['text'])
        self.app.extensions['storyloom_workers'].shutdown(wait=True)
        def ai(prompt):
            self.calls.append(prompt)
            return {'premise_decision':'keep','plan_conflict':False,'plan_basis':'','replacement':'## 밀정\n역할: 정보 요원\n관계: 왕실 소속의 베테랑 요원이다.'}
        self.app=create_app(self.path,ai,auth_required=False);self.c=self.app.test_client()
        result=self.chat('네',scope,'general-confirm-test')
        self.assertEqual(result.status_code,200,result.json)
        state=self.c.get(self.base).json['state']
        self.assertIn('왕실 소속',state['drafts']['characters:0']['text'])
        self.assertIn('나뭇가지를 강력한 검으로 만드는 체질이다.',state['drafts']['characters:0']['text'])
        self.assertNotIn('setting_edit_confirmation',state)
        self.assertEqual(state['messages'][-2]['text'],'네')
        self.assertEqual(len(self.calls),1)

    def test_duplicate_name_does_not_merge_distinct_characters(self):
        self.prepare()
        result=self.chat('밀정 이름은 루칸으로 바꿔줘')
        self.assertEqual(result.status_code,200,result.json)
        state=self.c.get(self.base).json['state']
        self.assertNotIn('characters:0',state['drafts'])
        self.assertIn('같은 이름',state['messages'][-1]['text'])
