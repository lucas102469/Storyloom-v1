import copy
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch
from google.genai import types
from app import create_app
from ai_client import gemini
import premise_guard


class PremiseGuardTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.database=str(Path(self.folder.name)/'premise.sqlite3')
        self.calls=[]
        self.answer={'premise_decision':'keep','reply':'수정안','updates':[]}
        def generate(prompt):
            self.calls.append(prompt)
            answer=copy.deepcopy(self.answer)
            if 'premise_decision' in answer:
                answer.setdefault('plan_conflict',False);answer.setdefault('plan_basis','')
            return answer
        self.app=create_app(self.database,generate,auth_required=False)
        self.client=self.app.test_client()
        self.pid=self.client.post('/api/projects',json={'title':'무림 작품'}).json['id']
        self.url='/api/projects/'+self.pid
        state=self.state()
        state['selected']='world'
        state['initial_references_pending']=False
        state['length_setup_pending']=False
        state['official']={
            'plan:0':{'text':'장르: 무협\n로그라인: 검객이 무림의 강자를 이긴다.','version':1},
            'world:0':{'text':'## 강호\n설명: 인간 무림인들이 무공으로 다투는 세상.','version':2},
            'characters:0':{'text':'## 강필두\n역할: 주인공\n설명: 검객.','version':3},
            'places:0':{'text':'## 산촌\n설명: 강호의 조용한 마을.','version':4},
            'plot:0':{'text':'## 회차 구성\n1화: 강필두가 무공을 수련한다.','version':6},
            'lore:0':{'text':'## 무공\n설명: 검으로 싸우는 기술.','version':5}}
        self.write(state)

    def tearDown(self):
        self.app.extensions['storyloom_workers'].shutdown(wait=True)
        self.folder.cleanup()

    def state(self):return self.client.get(self.url).json['state']
    def write(self,state):
        with closing(sqlite3.connect(self.database)) as con,con:
            con.execute('UPDATE projects SET state=? WHERE id=?',(json.dumps(state),self.pid))
    def request(self,text,**kwargs):
        return self.client.post(self.url+'/chat',json={'text':text,'request_id':'request-'+str(len(self.calls)),**kwargs})
    def documents(self):
        state=self.state()
        return {k:state.get(k,{}) for k in ('official','drafts','records','settings')}

    def test_major_replacement_creates_only_chat_not_documents_or_new_project(self):
        before=self.documents()
        self.answer={'premise_decision':'new_work','reply':'모두 바꿉니다','updates':[
            {'kind':'world','content':'강아지만 사는 평화 세계'}],
            'write_episode':1,'scene_goals':['시작','끝'],'settings':{'target_chars':5000}}
        response=self.request('강아지들만 있는 폭력 없는 세상으로 바꿔줘')
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(self.documents(),before)
        self.assertEqual(len(self.client.get('/api/projects').json),1)
        self.assertIn('새 작품',self.state()['messages'][-1]['text'])
        self.assertEqual(len(self.calls),1)
        for content in before['official'].values():self.assertIn(json.dumps(content['text'],ensure_ascii=False)[1:-1],self.calls[0])
        with closing(sqlite3.connect(self.database)) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM versions').fetchone()[0],0)

    def test_scoped_major_edit_cannot_bypass_guard(self):
        before=self.documents();source=before['official']['world:0'];quote='인간 무림인들이 무공으로 다투는 세상.'
        self.answer={'premise_decision':'new_work','replacement':'강아지만 사는 세상.'}
        response=self.request('강아지만 사는 폭력 없는 세계로 바꿔줘',edit_scope={
            'key':'world:0','version':2,'start':source['text'].index(quote),'quote':quote})
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(self.documents(),before)
        self.assertEqual(len(self.calls),1)
        self.assertIn('[작품 전제 변경 판단]',self.calls[0])
        self.assertIn('무협',self.calls[0])
        self.assertIn('강필두',self.calls[0])

    def test_small_edit_still_creates_reviewable_draft(self):
        self.answer={'premise_decision':'keep','reply':'마을 설정을 다듬었어요.','updates':[
            {'kind':'world','content':'## 강호\n설명: 인간 무림인들이 무공으로 다투는 세상.\n규칙: 강아지 등 동물은 보호한다.'}]}
        before=self.state()['official']
        response=self.request('세계관을 수정해줘. 강아지 등 동물을 보호한다는 공통 규칙을 추가해줘')
        self.assertEqual(response.status_code,200,response.json)
        state=self.state()
        self.assertEqual(state['official'],before)
        self.assertIn('강아지',state['drafts']['world:0']['text'])
        self.assertEqual(len(self.calls),1)

    def test_small_scoped_edit_preserves_surrounding_content(self):
        source=self.state()['official']['world:0'];quote='인간 무림인들이 무공으로 다투는 세상.'
        self.answer={'premise_decision':'keep','replacement':'인간 무림인들이 무공을 수련하며 다투는 세상.'}
        response=self.request('수련한다는 표현으로 수정해줘',edit_scope={
            'key':'world:0','version':2,'start':source['text'].index(quote),'quote':quote})
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(self.state()['drafts']['world:0']['text'],source['text'].replace(quote,self.answer['replacement']))

    def test_unclear_request_only_asks_without_changes(self):
        self.answer={'premise_decision':'unclear','reply':'바꿉니다','updates':[{'kind':'world','content':'다른 세계'}]}
        before=self.documents()
        self.assertEqual(self.request('동물 세계로 해줘').status_code,200)
        self.assertEqual(self.documents(),before)
        self.assertIn('확인해 주세요',self.state()['messages'][-1]['text'])

    def test_blocked_change_does_not_backfill_missing_references(self):
        state=self.state();state['official'].pop('plot:0');state['official'].pop('places:0');self.write(state)
        before=self.documents()
        self.answer={'premise_decision':'new_work','reply':'새 설정','updates':[]}
        response=self.request('세계관을 강아지 세계로 새로 만들어줘')
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(self.documents(),before)
        self.assertEqual(len(self.calls),1)
        duplicate=self.client.post(self.url+'/chat',json={'request_id':'request-0','text':'세계관을 강아지 세계로 새로 만들어줘'})
        self.assertEqual(duplicate.status_code,200)
        self.assertEqual(len(self.calls),1)

    def test_missing_decision_fails_closed(self):
        before=self.documents()
        self.answer={'reply':'변경','updates':[{'kind':'world','content':'다른 세계'}]}
        self.assertEqual(self.request('세계관을 바꿔줘').status_code,502)
        self.assertEqual(self.documents(),before)

    def test_initial_generation_and_episode_operations_are_outside_guard(self):
        self.assertFalse(premise_guard.applies({'selected':'world'},'강아지 세계로 해줘'))
        state=self.state()
        self.assertFalse(premise_guard.applies(state,'바꿔줘',{'key':'episode:1'}))
        self.assertFalse(premise_guard.applies(state,'바꿔줘',operation={'type':'extract_references'}))
        self.assertFalse(premise_guard.applies({**state,'selected':'episode'},'다음 회차 써줘'))

    def test_conflict_with_fixed_plan_blocks_even_if_model_says_keep(self):
        before=self.documents()
        self.answer={'premise_decision':'keep','plan_conflict':True,'plan_basis':'검객이 무림의 강자를 이긴다.',
                     'reply':'주먹으로 바꿉니다','updates':[{'kind':'world','content':'## 무림\n설명: 모든 무기를 금지하고 오직 맨손으로 싸운다.'}]}
        self.assertEqual(self.request('모두 주먹으로만 싸우는 세상으로 해줘').status_code,200)
        self.assertEqual(self.documents(),before)
        self.assertIn('검객이 무림의 강자를 이긴다.',self.state()['messages'][-1]['text'])

    def test_ai_cannot_append_to_fixed_plan_while_updating_a_setting(self):
        before=self.state()['official']['plan:0']
        self.answer={'premise_decision':'keep','reply':'변경안','updates':[
            {'kind':'plan','content':before['text']+'\n추가 사건: 새 사건'},
            {'kind':'lore','content':'## 무공\n설명: 검으로 싸우는 기술.\n제약: 훈련이 필요하다.'}]}
        self.assertEqual(self.request('주요 설정에 훈련 조건을 추가해줘').status_code,200)
        state=self.state();self.assertEqual(state['official']['plan:0'],before)
        self.assertNotIn('plan:0',state['drafts'])
        self.assertIn('lore:0',state['drafts'])

    def test_initial_draft_edit_first_approval_records_impact(self):
        from tests.test_setting_history import SettingHistoryTests
        # Populate actual version rows just like a completed initial setup.
        self.path=self.database;self.c=self.client;self.base=self.url
        SettingHistoryTests.seed(self,chapters=False)
        state=self.state();state['selected']='world'
        original=state['official'].pop('world:0');original['status']='draft';state['drafts']['world:0']=original
        self.write(state)
        self.answer={'premise_decision':'keep','reply':'수정안','updates':[
            {'kind':'world','content':original['text']+'\n규칙: 마을에서는 신분을 밝힌다.'}]}
        self.assertEqual(self.request('세계관을 수정해줘').status_code,200)
        draft=self.state()['drafts']['world:0']
        for action in ('review','approve'):
            r=self.client.post(self.url+'/draft',json={'key':'world:0','version':draft['version'],'action':action})
            self.assertEqual(r.status_code,200,r.json)
        impact=self.state()['revision_impact']
        self.assertEqual(impact['status'],'pending')
        self.assertEqual(impact['changes']['world:0']['before'],original['text'])
        self.assertIn('신분',impact['changes']['world:0']['after'])

    def test_schema_only_new_setting_tasks_require_decision(self):
        for task,answer in [('setting_change',{'premise_decision':'keep','reply':'응답','updates':[]}),
                            ('setting_partial_edit',{'premise_decision':'keep','replacement':'수정문'}),
                            ('chat',{'reply':'응답','updates':[]}),('partial_edit',{'replacement':'수정문'})]:
            response=types.GenerateContentResponse(candidates=[types.Candidate(finish_reason='STOP',
                content=types.Content(parts=[types.Part(text=json.dumps(answer))]))])
            client=Mock();client.models.generate_content.return_value=response
            with patch('ai_client.os.environ',{'GEMINI_API_KEY':'dummy'}),patch('ai_client.get_client',return_value=client):
                gemini('test',task)
            required=client.models.generate_content.call_args.kwargs['config'].response_json_schema['required']
            self.assertEqual('premise_decision' in required,task.startswith('setting_'))

if __name__=='__main__':unittest.main()
