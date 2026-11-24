import copy
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from app import create_app
from tests import test_setting_history as fixtures
import story_additions


class StoryAdditionTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.path=str(Path(self.folder.name)/'additions.sqlite3')
        self.calls=[]
        self.reply={'reply':'소녀와 후반 계획 수정안','updates':[]}
        def generate(prompt):
            self.calls.append(prompt)
            if prompt.startswith('STORY_ADDITION_PATCH'):
                return {'patches':[{'old':'카이렌은 멸시받으며 광장을 걸었다.','new':'카이렌은 멸시받으며 광장을 걸었다. '+self.new}]}
            if prompt.startswith('REFERENCE_BACKFILL'):
                return {'findings':[{'kind':'characters','name':'소녀','role':'조연','field':'관계',
                    'fact':'소녀는 카이렌에게 사과를 건넨다.','quote':'한 소녀가 카이렌에게 사과를 건네며 말했다.','status':'clear'}]}
            return copy.deepcopy(self.reply)
        self.app=create_app(self.path,generate,auth_required=False);self.c=self.app.test_client()
        self.pid=self.c.post('/api/projects',json={'title':'추가 검사'}).json['id'];self.base='/api/projects/'+self.pid
        fixtures.SettingHistoryTests.seed(self,chapters=True)
        state=self.state();state['selected']='episode';state['episode']=1;state['drafts']={}
        state['official']['plot:0']['text']='## 전체 아웃라인\n시작: 검을 얻는다.\n중간: 추적을 피한다.\n결말: 적을 이긴다.\n## 회차 구성\n1화: 검을 얻는다.\n10화: 적을 이긴다.'
        state['official']['characters:0']['text']='## 카이렌\n역할: 주인공\n설명: 검을 쥔 소년.'
        state['official']['episode:1']['text']='카이렌은 멸시받으며 광장을 걸었다.\n\n카이렌은 창고에서 검을 얻었다.'
        state['settings']={'target_chars':4000,'total_episodes':10}
        with closing(sqlite3.connect(self.path)) as con,con:
            con.execute('UPDATE projects SET state=? WHERE id=?',(json.dumps(state),self.pid))
        self.request_text='카이렌에게 사과를 주며 스쳐 지나가는 소녀를 등장시켜줘. 나중에 강해지면 이 소녀를 구하는 전개로, 극후반 회차에.'
        self.new='한 소녀가 카이렌에게 사과를 건네며 말했다. "배고프지? 먹어." 그녀는 스쳐 지나갔다.'
        self.reply={'reply':'추가안','updates':[
            {'kind':'characters','content':'## 소녀\n역할: 조연\n설명: 카이렌에게 사과를 건네며 스쳐 지나간 소녀.'},
            {'kind':'episode','episode':1,'patches':[{'old':'카이렌은 멸시받으며 광장을 걸었다.','new':'카이렌은 멸시받으며 광장을 걸었다. '+self.new}]},
            {'kind':'memory','content':'세아를 반드시 구하기로 다짐했다.'}],
            'future_plan':{'timing':'훗날','note':'강해진 카이렌이 사과를 건넨 소녀를 구한다.'},
            'settings':state['settings']}

    def tearDown(self):
        self.app.extensions['storyloom_workers'].shutdown(wait=True);self.folder.cleanup()
    def state(self):return self.c.get(self.base).json['state']
    def chat(self):return self.c.post(self.base+'/chat',json={'request_id':'add-girl-test','text':self.request_text})

    def test_requested_character_and_future_plot_are_reviewable_without_other_changes(self):
        before=self.state();response=self.chat()
        self.assertEqual(response.status_code,200,response.json)
        state=self.state()
        self.assertEqual(state['official'],before['official'])
        self.assertNotIn('characters:0',state['drafts'])
        self.assertIn('## 카이렌',state['official']['characters:0']['text'])
        self.assertIn(''.join(self.new.split()),''.join(state['drafts']['episode:1']['text'].split()))
        self.assertTrue(state['drafts']['plot:0']['text'].startswith(before['official']['plot:0']['text']))
        self.assertIn('극후반',state['drafts']['plot:0']['text'])
        self.assertIn('強해진'.replace('強','강'),state['drafts']['plot:0']['text'])
        self.assertNotIn('memory:0',state['drafts'])
        self.assertNotIn('settings:0',state['drafts'])
        self.assertEqual(state['settings'],before['settings'])
        self.assertIn('현재의 다짐이 아닙니다',self.calls[0])
        self.assertEqual(len(self.calls),2)

    def test_unsupported_character_name_is_not_saved(self):
        self.reply['updates'][0]['content']=self.reply['updates'][0]['content'].replace('## 소녀','## 세아')
        before=self.state()['official'];response=self.chat()
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.state()['official'],before)
        self.assertNotIn('characters:0',self.state()['drafts'])

    def test_write_girl_request_preserves_character_and_future_review(self):
        self.request_text='나중에 마지막 회차에서 주인공이랑 결혼하게 될 소녀를 이번 1회차에서 스처가듯이 써줘 눈이 소녀와 눈이 마주쳤다 이런식으로 가볍게'
        self.assertTrue(story_additions.requested(self.state(),self.request_text))
        self.assertFalse(story_additions.requested(self.state(),self.request_text,operation='other'))
        self.assertEqual(self.chat().status_code,200)
        state=self.state()
        self.assertNotIn('characters:0',state['drafts'])
        self.assertIn('## 카이렌',state['official']['characters:0']['text'])
        self.assertIn('마지막 회차',state['drafts']['plot:0']['text'])
        self.assertIn('현재의 다짐이 아닙니다',self.calls[0])

    def test_identical_explicit_settings_update_is_not_saved(self):
        self.reply['updates']=[u for u in self.reply['updates'] if u['kind']!='memory']
        self.reply.pop('settings')
        self.reply['updates'].append({'kind':'settings','content':json.dumps(self.state()['settings'])})
        self.assertEqual(self.chat().status_code,200)
        self.assertNotIn('settings:0',self.state()['drafts'])

    def test_character_only_response_repairs_missing_scene_without_dropping_hero(self):
        self.reply['updates']=[u for u in self.reply['updates'] if u['kind']!='episode']
        self.request_text='소녀를 스쳐 지나가게 추가해줘. 중반에 구출하고 마지막화에 반려가 되게 하자.'
        response=self.chat();self.assertEqual(response.status_code,200,response.json)
        state=self.state()
        self.assertIn(''.join(self.new.split()),''.join(state['drafts']['episode:1']['text'].split()))
        self.assertNotIn('characters:0',state['drafts'])
        self.assertIn('## 카이렌',state['official']['characters:0']['text'])
        plot=state['drafts']['plot:0']['text']
        self.assertIn('중반',plot);self.assertIn('마지막화',plot);self.assertIn('반려',plot)
        self.assertEqual(sum(p.startswith('STORY_ADDITION_PATCH') for p in self.calls),1)

    def test_existing_incomplete_character_draft_does_not_erase_official_hero(self):
        state=self.state();state['drafts']['characters:0']={'version':100,'status':'draft','text':'## 소녀\n설명: 처음 만난 소녀.'}
        with closing(sqlite3.connect(self.path)) as con,con:
            con.execute('UPDATE projects SET state=? WHERE id=?',(json.dumps(state),self.pid))
        self.assertEqual(self.chat().status_code,200)
        self.assertEqual(self.state()['drafts']['characters:0'],state['drafts']['characters:0'])
        self.assertIn('## 카이렌',self.state()['official']['characters:0']['text'])

    def test_new_chapter_and_unrelated_edits_are_not_reclassified(self):
        state=self.state();original=copy.deepcopy(self.reply)
        response={**original,'write_episode':2,'scene_goals':['始','終']}
        prepared,allowed=story_additions.prepare(state,self.request_text,response)
        self.assertEqual(prepared,response);self.assertEqual(allowed,set())
        self.assertFalse(story_additions.requested(state,'文章を直す'))
        self.assertFalse(story_additions.requested(state,self.request_text,scope={'key':'episode:1'}))

if __name__=='__main__':unittest.main()
