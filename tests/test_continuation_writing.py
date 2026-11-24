import json
import sqlite3
import unittest
import time
import threading
from contextlib import closing
from pathlib import Path
from ai_client import RetryableAIError
from tests import test_functional_upgrade as fixtures


class ContinuationWritingTests(unittest.TestCase):
    setUp=fixtures.FunctionalUpgradeTests.setUp
    tearDown=fixtures.FunctionalUpgradeTests.tearDown
    state=fixtures.FunctionalUpgradeTests.state
    edit=fixtures.FunctionalUpgradeTests.edit
    approve=fixtures.FunctionalUpgradeTests.approve

    def test_total_episode_limit_blocks_before_plan(self):
        self.prepare()
        with closing(sqlite3.connect(str(Path(self.temp.name)/'test.sqlite3'))) as con,con:
            state=self.state();state['settings']['total_episodes']=1
            con.execute('UPDATE projects SET state=? WHERE id=?',(json.dumps(state,ensure_ascii=False),self.pid))
        calls=[]
        def respond(prompt):calls.append(prompt);return {'items':[]}
        self.response=respond
        result=self.c.post(self.base+'/chat',json=self.payload)
        self.assertEqual(result.status_code,502,result.json)
        self.assertIn('총 회차수를 초과',result.json['error'])
        self.assertFalse(any(prompt.startswith(('2화 집필 계획','한국어 소설의 다음 장면')) for prompt in calls))

    def _exercise_stopped_batch_resume(self,changed=False):
        import re
        self.prepare()
        for kind in ('plan','characters','places','lore'):
            self.edit(kind+':0','## 정원 자료\n설명: 정원에서 이야기를 잇는다.');self.approve(kind+':0')
        entered=threading.Event();release=threading.Event();number=[2]
        counts={'plan2':0,'write2':0,'audit2':0}
        def respond(prompt):
            match=re.match(r'(\d+)화 집필 계획',prompt)
            if match:
                number[0]=int(match[1])
                if number[0]==2:counts['plan2']+=1
                return {'reply':'회차','scene_goals':['시작','끝']}
            if prompt.startswith('한국어 소설의 다음 장면'):
                if number[0]==2:counts['write2']+=1
                return {'text':str(number[0])+'화 '+chr(0xac00+number[0])*1100}
            if prompt.startswith('이번 원고와 이전에 확정한'):
                if number[0]==2:
                    counts['audit2']+=1
                    if counts['audit2']==1:entered.set();release.wait(10)
                return {'issues':[],'summary':'사건','facts':'사실','findings':[]}
            return {'items':[]}
        self.response=respond
        try:
            start=self.c.post(self.base+'/episode-batch',json={'action':'start','count':2})
            self.assertEqual(start.status_code,202,start.json);self.assertTrue(entered.wait(10))
            stop=self.c.post(self.base+'/episode-batch',json={'action':'stop'})
            self.assertEqual(stop.status_code,200,stop.json);release.set()
            for _ in range(500):
                state=self.state()
                if state['episode_batch']['status']=='paused':break
                time.sleep(.02)
            self.assertEqual(state['episode_batch']['status'],'paused')
            job=state['episode_batch']['request_id']
            database=str(Path(self.temp.name)/'test.sqlite3')
            with closing(sqlite3.connect(database)) as con,con:
                token=con.execute('SELECT run_token FROM jobs WHERE id=?',(job,)).fetchone()[0]
                if changed:
                    state['messages'].append({'role':'user','text':'중단 후 변경된 집필 요청'})
                    con.execute('UPDATE projects SET state=? WHERE id=?',(json.dumps(state,ensure_ascii=False),self.pid))
            resumed=self.c.post(self.base+'/episode-batch',json={'action':'resume'})
            self.assertEqual(resumed.status_code,202,resumed.json)
            for _ in range(500):
                state=self.state()
                if state['episode_batch']['status']!='running':break
                time.sleep(.02)
            self.assertEqual(state['episode_batch']['status'],'done',state['episode_batch'])
            expected=2 if changed else 1
            self.assertEqual(counts,{'plan2':expected,'write2':expected,'audit2':2})
            with closing(sqlite3.connect(database)) as con:
                saved=con.execute('SELECT status,run_token FROM jobs WHERE id=?',(job,)).fetchone()
            self.assertEqual(saved[0],'cancelled' if changed else 'done')
            if not changed:self.assertNotEqual(saved[1],token)
            self.assertIn('episode:2',state['drafts']);self.assertIn('episode:3',state['drafts'])
        finally:release.set()

    def test_stopped_batch_resumes_saved_prose_with_fresh_token_and_required_audit(self):
        self._exercise_stopped_batch_resume()

    def test_stopped_batch_does_not_reuse_checkpoint_after_source_changes(self):
        self._exercise_stopped_batch_resume(changed=True)

    def test_batch_chapters_keep_their_audit_when_predecessors_are_approved_unchanged(self):
        self.prepare()
        for kind in ('plan','characters','places','lore'):
            self.edit(kind+':0','## 정원 자료\n설명: 정원에서 수첩의 꿈을 찾는다.');self.approve(kind+':0')
        counts={'plans':0,'writes':0,'audits':0,'findings':0};current=[2]
        def respond(prompt):
            import re
            match=re.match(r'(\d+)화 집필 계획',prompt)
            if match:
                current[0]=int(match[1]);counts['plans']+=1
                return {'reply':'후속 사건','scene_goals':['사건의 후속','마무리']}
            if prompt.startswith('한국어 소설의 다음 장면'):
                counts['writes']+=1
                return {'text':str(current[0])+'화 사건. '+chr(0xac00+current[0])*1100,'title':'후속 사건'}
            if prompt.startswith('이번 원고와 이전에 확정한'):
                counts['audits']+=1
                return {'issues':[],'summary':'후속 사건','facts':'정원에서 사건이 이어졌다.','findings':[]}
            if prompt.startswith('REFERENCE_BACKFILL'):
                counts['findings']+=1;return {'findings':[]}
            return {'items':[],'issues':[]}
        self.response=respond
        started=self.c.post(self.base+'/episode-batch',json={'action':'start','count':4})
        self.assertEqual(started.status_code,202,started.json)
        for _ in range(500):
            state=self.state()
            if state['episode_batch']['status']!='running':break
            time.sleep(.02)
        self.assertEqual(state['episode_batch']['status'],'done',state['episode_batch'])
        self.assertEqual(counts,{'plans':4,'writes':4,'audits':4,'findings':0})
        for episode in range(2,6):
            self.approve(f'episode:{episode}')
            state=self.state();record=state['official'][f'episode:{episode}']
            self.assertEqual(state['records'][str(episode)]['setting_review'],'organize')
            response=self.c.post(self.base+'/episode-settings-review',json={
                'episode':episode,'version':record['version'],'action':'organize'})
            self.assertEqual(response.status_code,200,response.json)
            self.assertTrue(response.json['cached'])
            self.assertEqual(response.json['stage'],'done')
            self.assertTrue(self.c.get(self.base+f'/setting-history?episode={episode}').json['available'])
        self.assertEqual(counts,{'plans':4,'writes':4,'audits':4,'findings':0})

    def prepare(self,stage='done'):
        self.c.post(self.base+'/length',json={'target_chars':1000,'total_episodes':5})
        self.edit('world:0','## 정원\n설명: 시온은 독심술이 없고 연우의 과거를 모른다.');self.approve('world:0')
        self.edit('plot:0','## 회차 구성\n1화: 정원 입장\n2화: 수첩을 펼침\n3화: 씨앗을 받음');self.approve('plot:0')
        self.previous='연우는 해고 사실을 시온에게 말하지 않고 닫힌 수첩을 탁자에 놓았다. '+'가'*1050
        self.edit('episode:1',self.previous);self.approve('episode:1')
        with closing(sqlite3.connect(str(Path(self.temp.name)/'test.sqlite3'))) as con,con:
            s=self.state();s['records']['1']['setting_review']=stage
            con.execute('UPDATE projects SET state=? WHERE id=?',(json.dumps(s,ensure_ascii=False),self.pid))
        self.c.post(self.base+'/view',json={'selected':'episode','episode':2})
        self.payload={'request_id':'continuation-test','text':
            '2화를 이어 써줘. 직전 확정 회차의 마지막 장면과 미해결 사건, 인물들이 알고 있는 정보를 이어받고 확정 설정과 회차 계획을 지켜줘. 이번 화의 목표와 끝맺음을 먼저 계획하고 원고를 작성해줘.'}

    def test_single_full_chapter_uses_prior_knowledge_and_reuses_exact_audit_after_approval(self):
        self.prepare();self.edit('episode:3','미래초안전용표식 아직 만나지 않은 인물')
        self.c.post(self.base+'/view',json={'selected':'episode','episode':2})
        writes=[];audits=[];plans=[]
        def respond(prompt):
            if prompt.startswith('2화 집필 계획'):
                plans.append(prompt);return {'reply':'수첩','scene_goals':['수첩을 펼침','꿈 이야기']}
            if prompt.startswith('한국어 소설의 다음 장면'):
                writes.append(prompt)
                self.assertEqual(prompt.count(self.previous),1)
                self.assertIn('직전 회차의 마지막 상태',prompt)
                self.assertIn('2화 전체를 완성',prompt)
                self.assertNotIn('이야기의 첫 장면부터',prompt)
                self.assertNotIn('미래초안전용표식',prompt)
                self.assertIn('reserved_for_later',prompt)
                return {'text':'연우는 수첩을 펼쳤다. '+'나'*1100,'title':'수첩'}
            if prompt.startswith('이번 원고와 이전에 확정한'):
                audits.append(prompt)
                self.assertIn('아직 전달받지 않은 비밀',prompt)
                self.assertIn('회차 번호는 경과한 날짜나 밤의 횟수가 아닙니다',prompt)
                self.assertNotIn('이전 회차가 없는 1화',prompt)
                self.assertNotIn('미래초안전용표식',prompt)
                return {'issues':[],'summary':'수첩을 읽었다.','facts':'꿈을 말했다.','findings':[]}
            if prompt.startswith('REFERENCE_BACKFILL'):self.fail('검토한 동일 원고를 재추출함')
            return {'items':[]}
        self.response=respond
        result=self.c.post(self.base+'/chat',json=self.payload)
        self.assertEqual(result.status_code,200,result.json)
        self.assertEqual([len(plans),len(writes),len(audits)],[1,1,1])
        draft=self.state()['drafts']['episode:2'];self.approve('episode:2')
        approved=self.state()['official']['episode:2']
        self.assertEqual(approved['continuity_stamp'],draft['continuity_stamp'])
        count=len(self.calls)
        result=self.c.post(self.base+'/episode-settings-review',json={'episode':2,'version':approved['version'],'action':'organize'})
        self.assertEqual(result.status_code,200,result.json)
        self.assertTrue(result.json['cached']);self.assertEqual(len(self.calls),count)

    def test_blocked_previous_review_does_not_request_a_plan(self):
        self.prepare(stage='organize');calls=[]
        def respond(prompt):calls.append(prompt);return {'items':[]}
        self.response=respond
        result=self.c.post(self.base+'/chat',json=self.payload)
        self.assertEqual(result.status_code,502,result.json)
        self.assertFalse(any(prompt.startswith('2화 집필 계획') for prompt in calls))

    def test_pending_setting_draft_blocks_before_any_plan_request(self):
        self.prepare()
        self.edit('characters:0','## 시온\n설명: 아직 검토하지 않은 설정 수정안')
        self.c.post(self.base+'/view',json={'selected':'episode','episode':2})
        calls=[]
        def respond(prompt):calls.append(prompt);return {'items':[]}
        self.response=respond
        result=self.c.post(self.base+'/chat',json=self.payload)
        self.assertEqual(result.status_code,502,result.json)
        self.assertIn('기획과 설정 변경안',result.json['error'])
        self.assertFalse(any(prompt.startswith(('2화 집필 계획','한국어 소설의 다음 장면')) for prompt in calls))

    def test_missing_or_unapproved_previous_chapter_blocks_without_plan(self):
        self.c.post(self.base+'/length',json={'target_chars':1000,'total_episodes':5})
        self.c.post(self.base+'/view',json={'selected':'episode','episode':2})
        calls=[]
        def respond(prompt):calls.append(prompt);return {'items':[]}
        self.response=respond
        text='2화를 이어 써줘. 직전 확정 회차의 마지막 장면과 미해결 사건, 인물들이 알고 있는 정보를 이어받고 확정 설정과 회차 계획을 지켜줘. 이번 화의 목표와 끝맺음을 먼저 계획하고 원고를 작성해줘.'
        for has_draft in (False,True):
            with self.subTest(has_draft=has_draft):
                if has_draft:self.edit('episode:1','미승인 원고. '+'가'*1100)
                result=self.c.post(self.base+'/chat',json={'request_id':'missing-previous-'+str(has_draft),'text':text})
                self.assertEqual(result.status_code,502,result.json)
                self.assertIn('1화 원고를 확정',result.json['error'])
                self.assertNotIn('episode:2',self.state()['drafts'])
        self.assertFalse(any(prompt.startswith(('2화 집필 계획','한국어 소설의 다음 장면')) for prompt in calls))

    def test_freeform_writing_cannot_bypass_missing_previous_chapter(self):
        self.c.post(self.base+'/length',json={'target_chars':1000,'total_episodes':5})
        self.response={'reply':'2화','write_episode':2,'scene_goals':['시작','끝'],'updates':[]}
        result=self.c.post(self.base+'/chat',json={'request_id':'freeform-missing-first','text':'2화를 써줘'})
        self.assertEqual(result.status_code,502,result.json)
        self.assertIn('1화 원고를 확정',result.json['error'])
        self.assertFalse(any(prompt.startswith('한국어 소설의 다음 장면') for prompt in self.calls))

    def test_freeform_direct_manuscript_cannot_create_chapter_without_predecessor(self):
        self.c.post(self.base+'/length',json={'target_chars':1000,'total_episodes':5})
        self.response={'reply':'2화','updates':[{'kind':'episode','episode':2,'content':'나'*1100}]}
        result=self.c.post(self.base+'/chat',json={'request_id':'direct-missing-first','text':'2화를 써줘'})
        self.assertEqual(result.status_code,502,result.json)
        self.assertIn('1화 원고를 확정',result.json['error'])
        self.assertNotIn('episode:2',self.state()['drafts'])

    def test_fifth_chapter_requires_fourth_instead_of_first_or_second(self):
        self.prepare()
        self.c.post(self.base+'/view',json={'selected':'episode','episode':5})
        calls=[]
        def respond(prompt):calls.append(prompt);return {'items':[]}
        self.response=respond
        result=self.c.post(self.base+'/chat',json={'request_id':'fifth-needs-fourth',
            'text':self.payload['text'].replace('2화를','5화를')})
        self.assertEqual(result.status_code,502,result.json)
        self.assertIn('4화 원고를 확정',result.json['error'])
        self.assertFalse(any(prompt.startswith('5화 집필 계획') for prompt in calls))

    def test_invalid_full_manuscript_reuses_saved_plan(self):
        self.prepare();plans=[];writes=[]
        def respond(prompt):
            if prompt.startswith('2화 집필 계획'):
                plans.append(prompt);return {'reply':'수첩','scene_goals':['펼침','대화']}
            if prompt.startswith('한국어 소설의 다음 장면'):
                writes.append(prompt);return {'text':'' if len(writes)==1 else '나'*1100}
            return {'items':[],'issues':[],'summary':'수첩','facts':'꿈','findings':[]}
        self.response=respond
        self.assertEqual(self.c.post(self.base+'/chat',json=self.payload).status_code,502)
        result=self.c.post(self.base+'/chat',json=self.payload)
        self.assertEqual(result.status_code,200,result.json)
        self.assertEqual([len(plans),len(writes)],[1,2])

    def test_interrupted_repaired_chapter_resumes_audit_without_repeating_repair(self):
        self.prepare();repairs=[];audits=[];failed=[False]
        original='시온은 말하지 않은 해고 사실을 알고 있었다. '+'나'*1100
        repaired=original.replace('알고 있었다','모르고 있었다')
        def respond(prompt):
            if prompt.startswith('2화 집필 계획'):return {'reply':'수첩','scene_goals':['펼침','대화']}
            if prompt.startswith('한국어 소설의 다음 장면'):return {'text':original}
            if prompt.startswith('아래 원고의 일관성 충돌'):
                repairs.append(prompt);return {'text':repaired,'patches':[]}
            if prompt.startswith('이번 원고와 이전에 확정한'):
                audits.append(prompt)
                if repaired in prompt:
                    if not failed[0]:failed[0]=True;raise RetryableAIError('재검토 연결 중단')
                    return {'issues':[],'summary':'수첩','facts':'시온은 해고를 모름','findings':[]}
                return {'issues':['시온이 전달받지 않은 해고를 알고 있다.']}
            return {'items':[]}
        self.response=respond
        self.assertEqual(self.c.post(self.base+'/chat',json=self.payload).status_code,502)
        result=self.c.post(self.base+'/chat',json=self.payload)
        self.assertEqual(result.status_code,200,result.json)
        self.assertEqual(len(repairs),1);self.assertEqual(len(audits),3)
        self.assertEqual(self.state()['drafts']['episode:2']['text'],repaired)
