import json
import sqlite3
import unittest
from contextlib import closing
from character_identity import explicit_identity, confirmed_character_aliases
from workflow import episode_findings, build_episode_context
from tests import test_setting_history as fixtures


class CharacterIdentityTests(unittest.TestCase):
    setUp = fixtures.SettingHistoryTests.setUp
    tearDown = fixtures.SettingHistoryTests.tearDown
    seed = fixtures.SettingHistoryTests.seed

    def test_missing_name_keeps_source_designation(self):
        quote = '문지기는 연우에게 정원의 낡은 문을 열어 주었다.'
        item = {'kind':'characters','fact':'문지기가 연우에게 문을 열어 주었다.',
                'name':'','quote':quote,'status':'clear'}
        self.assertEqual(episode_findings([item], quote)[0]['name'], '문지기')

    def test_only_explicit_identity_is_linked(self):
        self.assertTrue(explicit_identity('문지기의 이름은 박문수였다.', '문지기', '박문수'))
        self.assertFalse(explicit_identity('문지기와 박문수가 함께 문을 열었다.', '문지기', '박문수'))
        self.assertFalse(explicit_identity('문지기', [], '박문수'))
        item = {'kind':'characters','fact':'박문수가 문을 열었다.', 'name':'박문수',
                'previous_name':'문지기','quote':'문지기와 박문수가 함께 문을 열었다.','status':'clear'}
        finding = episode_findings([item],item['quote'])[0]
        self.assertNotIn('previous_name',finding)
        self.assertTrue(finding['reason'])

    def test_current_card_updates_without_rewriting_history_or_manuscript(self):
        self.seed()
        with closing(sqlite3.connect(self.path)) as con, con:
            state=json.loads(con.execute('SELECT state FROM projects WHERE id=?',(self.pid,)).fetchone()[0])
            state['official']['characters:0']['text']='## 문지기\n역할: 조력자\n설명: 정원을 지킨다.'
            con.execute('UPDATE projects SET state=? WHERE id=?',(json.dumps(state),self.pid))
        self.c.post(self.base+'/view',json={'selected':'characters'})
        old=self.c.get(self.base+'/setting-history?episode=0').json
        with closing(sqlite3.connect(self.path)) as con, con:
            state=json.loads(con.execute('SELECT state FROM projects WHERE id=?',(self.pid,)).fetchone()[0])
            quote='문지기의 이름은 박문수였다.'
            finding=episode_findings([{'kind':'characters','name':'박문수','previous_name':'문지기',
                'fact':'문지기의 실명은 박문수다.','quote':quote,'status':'clear'}],quote)[0]
            state['records']={'1':{'setting_review':'done','findings':[finding]}}
            state['official']['episode:1']={'text':quote,'version':999,'status':'confirmed'}
            con.execute('UPDATE projects SET state=? WHERE id=?',(json.dumps(state),self.pid))
        response=self.c.post(self.base+'/view',json={'selected':'characters'})
        self.assertEqual(response.status_code,200,response.json)
        current=self.c.get(self.base).json['state']
        self.assertIn('## 박문수',current['official']['characters:0']['text'])
        self.assertIn('정원을 지킨다.',current['official']['characters:0']['text'])
        self.assertEqual(current['official']['episode:1']['text'],quote)
        self.assertEqual(self.c.get(self.base+'/setting-history?episode=0').json['official'],old['official'])
        self.assertIn('문지기',old['official']['characters:0']['text'])
        self.assertEqual(json.loads(build_episode_context(current,2))['character_aliases'],{'문지기':'박문수'})
        self.assertFalse(self.calls)

    def test_conflicting_reveals_and_distinct_cards_are_not_merged(self):
        def item(name):
            return {'kind':'characters','name':name,'previous_name':'문지기','quote':f'문지기의 이름은 {name}였다.'}
        state={'records':{'1':{'findings':[item('박문수'),item('김철수')]}},'official':{}}
        self.assertEqual(confirmed_character_aliases(state),{})
        state['records']['1']['findings']=[item('박문수')]
        state['official']={'characters:0':{'text':'## 문지기\n설명: 왼쪽 문\n## 박문수\n설명: 오른쪽 문'}}
        self.assertEqual(confirmed_character_aliases(state),{})

    def test_organize_uses_source_label_without_name_generation_request(self):
        from app import create_app
        self.app.extensions['storyloom_workers'].shutdown(wait=True)
        quote='문지기는 연우에게 정원의 낡은 문을 열어 주었다.'
        def respond(prompt):
            self.calls.append(prompt)
            return {'findings':[{'kind':'characters','name':'','fact':'문지기가 연우에게 문을 열어 주었다.',
                'quote':quote,'status':'clear','reason':''}], 'issues':[],'items':[]}
        self.app=create_app(self.path,respond,auth_required=False)
        self.c=self.app.test_client()
        self.seed(chapters=True)
        with closing(sqlite3.connect(self.path)) as con, con:
            state=json.loads(con.execute('SELECT state FROM projects WHERE id=?',(self.pid,)).fetchone()[0])
            state['official']['episode:1']['text']=quote
            con.execute('UPDATE projects SET state=? WHERE id=?',(json.dumps(state),self.pid))
        result=self.c.post(self.base+'/episode-settings-review',json={
            'episode':1,'version':state['official']['episode:1']['version'],'action':'organize'})
        self.assertEqual(result.status_code,200,result.json)
        current=self.c.get(self.base).json['state']
        self.assertEqual(current['records']['1']['findings'][0]['name'],'문지기')
        self.assertEqual(len(self.calls),1)
        self.assertFalse(any('아래 설정 항목마다 name' in prompt for prompt in self.calls))

    def test_pending_character_edit_is_preserved_and_identity_waits_for_review(self):
        self.seed()
        with closing(sqlite3.connect(self.path)) as con, con:
            state=json.loads(con.execute('SELECT state FROM projects WHERE id=?',(self.pid,)).fetchone()[0])
            state['official']['characters:0']['text']='## 문지기\n설명: 정원을 지킨다.'
            state['drafts']['characters:0']={**state['official']['characters:0'],'text':'## 문지기\n설명: 사용자가 수정 중이다.'}
            finding={'kind':'characters','name':'박문수','previous_name':'문지기',
                'fact':'실명이 밝혀졌다.','quote':'문지기의 이름은 박문수였다.'}
            state['records']={'1':{'setting_review':'done','findings':[finding]}}
            con.execute('UPDATE projects SET state=? WHERE id=?',(json.dumps(state),self.pid))
        self.c.post(self.base+'/view',json={'selected':'characters'})
        current=self.c.get(self.base).json['state']
        self.assertEqual(current['drafts']['characters:0'],state['drafts']['characters:0'])
        self.assertEqual(current['official']['characters:0'],state['official']['characters:0'])
        self.assertTrue(current['records']['1']['findings'][0]['reason'])
