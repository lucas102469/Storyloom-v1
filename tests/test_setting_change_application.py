import json
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from app import create_app
from tests import test_setting_history as fixtures


class SettingChangeApplicationTests(unittest.TestCase):
    seed = fixtures.SettingHistoryTests.seed

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=str(Path(self.temp.name)/'application.sqlite3')
        self.calls=[]
        self.response=lambda prompt: {'updates': [], 'issues': [], 'findings': []}
        def ai(prompt):
            self.calls.append(prompt)
            return self.response(prompt)
        self.app=create_app(self.path, ai, auth_required=False)
        self.c=self.app.test_client()
        self.pid=self.c.post('/api/projects',json={'title':'변경 반영 검사'}).json['id']
        self.base='/api/projects/'+self.pid
        self.seed(chapters=True)

    def tearDown(self):
        self.app.extensions['storyloom_workers'].shutdown(wait=True)
        self.temp.cleanup()

    def state(self):
        return self.c.get(self.base).json['state']

    def write(self, state):
        with closing(sqlite3.connect(self.path)) as con, con:
            con.execute('UPDATE projects SET state=? WHERE id=?',(json.dumps(state),self.pid))

    def start(self, mode='manual'):
        response=self.c.post(self.base+'/revision-impact',json={'id':'probe','mode':mode})
        self.assertEqual(response.status_code,202,response.json)
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            state=self.state()
            if state['revision_impact']['status']!='running':
                return state
            time.sleep(.01)
        self.fail('변경 반영 작업이 끝나지 않음')

    def prepare_settings(self):
        state=self.state()
        state['official']['world:0']['text']='## 문의 규칙\n설명: 문은 푸른색이다.'
        state['drafts']['lore:0']={**state['official']['lore:0'], 'text':'## 문\n설명: 문은 붉은색이다.', 'status':'draft'}
        state['revision_impact']={'id':'probe','status':'pending','changes':{
            'world:0':{'before':'문은 붉은색이다.','after':'문은 푸른색이다.'}}}
        self.write(state)
        return state

    def patch(self, old='문은 붉은색이다.', new='문은 푸른색이다.'):
        return {'kind':'lore','content':'','patches':[{'old':old,'new':new}], 'impact':'색깔 반영','conflict':''}

    def test_invalid_patch_never_counts_as_completed_and_valid_retry_works(self):
        before=self.prepare_settings()
        self.response=lambda prompt: {'updates':[self.patch(old='없는 구절')]}
        state=self.start()
        impact=state['revision_impact']
        self.assertEqual(impact['status'],'failed')
        self.assertFalse(impact.get('settings_checked'))
        self.assertIn('적용하지 못했어요',impact['error'])
        self.assertEqual(state['drafts'],before['drafts'])
        self.assertEqual(state['official'],before['official'])
        self.assertEqual(len(self.calls),1)
        self.response=lambda prompt: {'updates':[self.patch()]}
        state=self.start()
        self.assertEqual(state['revision_impact']['status'],'awaiting_settings')
        self.assertIn('푸른색',state['drafts']['lore:0']['text'])
        self.assertNotIn('error',state['revision_impact'])
        self.assertEqual(len(self.calls),2)

    def test_draft_changed_through_edit_api_invalidates_old_result(self):
        before=self.prepare_settings()
        def respond(prompt):
            result=self.c.post(self.base+'/edit',json={'key':'lore:0',
                'base_version':before['drafts']['lore:0']['version'],
                'text':'## 문\n설명: 사용자가 새로 수정한 붉은 문이다.'})
            self.assertEqual(result.status_code,200,result.json)
            return {'updates':[self.patch()]}
        self.response=respond
        state=self.start()
        self.assertEqual(state['revision_impact']['status'],'failed')
        self.assertFalse(state['revision_impact'].get('settings_checked'))
        self.assertIn('초안이 바뀌었어요',state['revision_impact']['error'])
        self.assertIn('새로 수정한',state['drafts']['lore:0']['text'])
        self.assertEqual(state['official'],before['official'])

    def test_missing_or_duplicate_updates_never_clear_settings_check(self):
        for response in ({}, {'updates':[self.patch(),self.patch()]}):
            with self.subTest(response=response):
                before=self.prepare_settings()
                self.response=lambda prompt: response
                state=self.start()
                self.assertEqual(state['revision_impact']['status'],'failed')
                self.assertFalse(state['revision_impact'].get('settings_checked'))
                self.assertEqual(state['drafts'],before['drafts'])

    def prepare_accept(self, rename=False, unchanged=False):
        state=self.state()
        old='문지기는 정원의 문을 지키고 있다.'
        quote=old
        text=old if unchanged else old+' 은빛 문은 한 번만 통과할 수 있다.'
        changes={'lore:0':{'before':'옛 규칙','after':'새 규칙'}}
        if rename:
            text=old.replace('문지기','박문수')
            changes={'characters:0':{'before':'## 문지기\n설명: 정원을 지킨다.',
                                     'after':'## 박문수\n설명: 정원을 지킨다.'}}
        finding={'kind':'characters','name':'문지기','fact':'문지기는 정원을 지킨다.', 'quote':quote,'reason':''}
        state['official']['episode:1']['text']=old
        state['records']['1'].update(setting_review='done',findings=[finding])
        content={'text':text,'summary':'문 규칙','facts':'실제 사건',
                 'findings':[{**finding,'name':'박문수','quote':text}] if rename else [],
                 'metadata_stale':False,'base_official_version':state['official']['episode:1']['version']}
        with closing(sqlite3.connect(self.path)) as con, con:
            vid=con.execute('INSERT INTO versions(project,kind,episode,content,status,created) VALUES(?,?,?,?,?,?)',
                (self.pid,'episode',1,json.dumps(content),'draft',time.time())).lastrowid
        state['drafts']['episode:1']={**content,'version':vid,'status':'draft'}
        basis={'official':{key:item['version'] for key,item in state['official'].items()},
               'drafts':{key:item['version'] for key,item in state['drafts'].items() if key.startswith('episode:')},
               'settings':state['settings']}
        state['revision_impact']={'id':'probe','status':'ready','changes':changes,'basis':basis,
            'results':[{'episode':1,'version':vid,'reason':'설정 반영','blocked':False}]}
        if rename: state['revision_impact']['linked_renames']={'문지기':'박문수'}
        self.write(state)
        result=self.c.post(self.base+'/revision-impact',json={'id':'probe','mode':'accept','episode':1})
        self.assertEqual(result.status_code,200,result.json)
        return self.state()

    def test_new_rule_requires_settings_review_even_when_old_evidence_survives(self):
        state=self.prepare_accept()
        record=state['records']['1']
        self.assertEqual(record['setting_review'],'organize')
        self.assertNotIn('findings_checked',record)
        self.assertEqual(len(record['findings']),1)
        self.assertIn('은빛 문',state['official']['episode:1']['text'])
        self.assertEqual(self.calls,[])
        quote='은빛 문은 한 번만 통과할 수 있다.'
        self.response=lambda prompt: {'findings':[{'kind':'lore','name':'은빛 문','fact':quote,
                                                  'quote':quote,'reason':'','status':'clear'}]}
        response=self.c.post(self.base+'/episode-settings-review',json={
            'episode':1,'version':record['version'],'action':'organize'})
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(self.state()['records']['1']['setting_review'],'done')
        self.assertEqual(self.state()['records']['1']['findings'][0]['name'],'은빛 문')
        self.assertEqual(len(self.calls),1)

    def test_exact_rename_preserves_fast_path_and_existing_review(self):
        state=self.prepare_accept(rename=True)
        self.assertEqual(state['records']['1']['setting_review'],'done')
        self.assertEqual(state['records']['1']['findings_checked'],state['official']['episode:1']['version'])
        self.assertEqual(self.calls,[])

    def test_unchanged_body_does_not_queue_another_review(self):
        state=self.prepare_accept(unchanged=True)
        self.assertEqual(state['records']['1']['setting_review'],'done')
        self.assertEqual(self.calls,[])

    def test_identity_patch_does_not_trigger_rewriting_or_auditing(self):
        state=self.state()
        state['revision_impact']={'id':'probe','status':'pending','settings_checked':True,'changes':{
            'world:0':{'before':'문은 붉은색이다.','after':'문은 푸른색이다.'}}}
        self.write(state)
        def respond(prompt):
            self.assertIn('직접 수정해야 할 구절',prompt)
            original=state['official']['episode:1']['text']
            return {'blocked':False,'impact':'변경 없음','patches':[{'old':original,'new':original}]}
        self.response=respond
        state=self.start()
        self.assertEqual(state['revision_impact']['status'],'done')
        self.assertNotIn('episode:1',state['drafts'])
        self.assertEqual(len(self.calls),1)

    def test_proposal_evidence_cleanup_does_not_mutate_accepted_record(self):
        state=self.state()
        original='문지기는 정원의 문을 지키고 있다.'
        revised='문지기는 정원의  문을 지키고 있다.'
        state['official']['episode:1']['text']=original
        finding={'kind':'characters','name':'문지기','fact':'정원을 지킨다.','quote':original,'reason':''}
        state['records']['1'].update(setting_review='done',findings=[finding])
        state['revision_impact']={'id':'probe','status':'pending','settings_checked':True,
            'changes':{'world:0':{'before':'옛 규칙','after':'새 규칙'}}}
        self.write(state)
        def respond(prompt):
            if '직접 수정해야 할 구절' in prompt:
                return {'blocked':False,'impact':'문 구절 정리','patches':[{'old':original,'new':revised}]}
            return {'issues':[],'summary':'정원을 지킴','facts':'문지기가 있다.'}
        self.response=respond
        state=self.start()
        self.assertEqual(state['revision_impact']['status'],'ready')
        self.assertEqual(state['official']['episode:1']['text'],original)
        self.assertEqual(state['records']['1']['findings'][0]['quote'],original)
        self.assertEqual(state['drafts']['episode:1']['findings'][0]['quote'],revised)
        result=self.c.post(self.base+'/revision-impact',json={'id':'probe','mode':'keep','episode':1})
        self.assertEqual(result.status_code,200,result.json)
        self.assertEqual(self.state()['records']['1']['findings'][0]['quote'],original)

    def test_auto_review_keeps_changed_chapter_pending_until_explicit_acceptance(self):
        before=self.state()
        original=before['official']['episode:1']
        revised=original['text']+' 은빛 문이 닫혔다.'
        before['revision_impact']={'id':'probe','status':'pending','settings_checked':True,
            'changes':{'world:0':{'before':'옛 규칙','after':'새 규칙'}}}
        self.write(before)
        def respond(prompt):
            if '직접 수정해야 할 구절' in prompt:
                return {'blocked':False,'impact':'새 규칙 반영','patches':[{'old':original['text'],'new':revised}]}
            return {'issues':[],'summary':'문이 닫힘','facts':'문이 닫혔다.'}
        self.response=respond
        state=self.start(mode='auto')
        self.assertEqual(state['revision_impact']['status'],'ready')
        self.assertEqual(state['official']['episode:1'],original)
        self.assertEqual(state['drafts']['episode:1']['text'],revised)
        self.assertNotIn('decision',state['revision_impact']['results'][0])
        calls=len(self.calls)
        accepted=self.c.post(self.base+'/revision-impact',json={'id':'probe','mode':'accept','episode':1})
        self.assertEqual(accepted.status_code,200,accepted.json)
        state=self.state()
        self.assertEqual(state['official']['episode:1']['text'],revised)
        self.assertEqual(state['revision_impact']['results'][0]['decision'],'accepted')
        self.assertEqual(len(self.calls),calls)

    def test_name_only_auto_review_also_waits_for_individual_acceptance(self):
        before=self.state()
        before['official']['characters:0']['text']='## 박문수\n설명: 문을 지킨다.'
        before['official']['episode:1']['text']='문지기는 정원의 문을 지킨다.'
        original=dict(before['official']['episode:1'])
        before['revision_impact']={'id':'probe','status':'pending','settings_checked':True,
            'changes':{'characters:0':{'before':'## 문지기\n설명: 문을 지킨다.',
                                      'after':'## 박문수\n설명: 문을 지킨다.'}},
            'linked_renames':{'문지기':'박문수'}}
        self.write(before)
        state=self.start(mode='auto')
        self.assertEqual(state['revision_impact']['status'],'ready')
        self.assertEqual(state['official']['episode:1'],original)
        self.assertIn('박문수는',state['drafts']['episode:1']['text'])
        self.assertNotIn('decision',state['revision_impact']['results'][0])
        self.assertEqual(self.calls,[])
        rejected=self.c.post(self.base+'/revision-impact',json={'id':'probe','mode':'keep','episode':1})
        self.assertEqual(rejected.status_code,200,rejected.json)
        self.assertEqual(self.state()['official']['episode:1'],original)
        self.assertNotIn('episode:1',self.state()['drafts'])
