import copy
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from app import create_app
from review_cache import reusable_findings
from workflow import episode_continuity_stamp


class ReviewCacheTests(unittest.TestCase):
    def test_previous_approval_status_is_ignored_but_story_and_knowledge_changes_invalidate(self):
        state=self.state()
        state['official']['episode:1'].update(status='draft',summary='항구에 도착',facts='서린만 비밀을 안다.')
        state['official']['episode:2']={'text':'두 번째 이야기','status':'draft','summary':'지도 확인','facts':'아직 말하지 않았다.'}
        before=episode_continuity_stamp(state,3,'세 번째 이야기')
        confirmed=copy.deepcopy(state)
        confirmed['official']['episode:1']['status']='confirmed'
        confirmed['official']['episode:2']['status']='confirmed'
        self.assertEqual(before,episode_continuity_stamp(confirmed,3,'세 번째 이야기'))
        for mutate in (
            lambda s:s['official']['episode:2'].update(text='수정된 원고'),
            lambda s:s['official']['episode:2'].update(summary='변경된 사건 요약'),
            lambda s:s['official']['episode:2'].update(facts='동료에게 비밀을 말했다.'),
            lambda s:s['official']['world:0'].update(text='## 항구\n설명: 바뀐 세계 규칙'),
            lambda s:s['records'].update({'2':{'findings':[{'fact':'새로 확인된 정보'}]}})):
            changed=copy.deepcopy(confirmed);mutate(changed)
            self.assertNotEqual(before,episode_continuity_stamp(changed,3,'세 번째 이야기'))

    def state(self):
        body='서린은 오래된 항구에 도착해 지도를 펼쳤다.'
        findings=[{'kind':'plot','fact':'서린이 항구에 도착했다.','quote':body,'reason':'회차 설정 검토 대기'}]
        state={'selected':'episode','episode':1,'messages':[],'drafts':{},'settings':{'target_chars':1000,'total_episodes':5},
               'official':{'world:0':{'version':1,'text':'## 항구\n설명: 바닷가 도시.'},
                           'episode:1':{'version':2,'text':body,'findings':copy.deepcopy(findings),'findings_basis':{'world:0':1}}},
               'records':{'1':{'version':2,'findings':findings,'setting_review':'organize'}}}
        state['official']['episode:1']['continuity_stamp']=episode_continuity_stamp(state,1,body)
        return state

    def test_only_exact_manuscript_and_settings_reuse_results(self):
        state=self.state();self.assertEqual(reusable_findings(state,1)[0]['reason'],'')
        self.assertEqual(state['records']['1']['findings'][0]['reason'],'회차 설정 검토 대기')
        for mutate in (
            lambda s:s['official']['episode:1'].update(text='다른 원고'),
            lambda s:s['official']['world:0'].update(version=3),
            lambda s:s['drafts'].update({'episode:1':{'text':'새 초안'}}),
            lambda s:s['records']['1']['findings'][0].update(quote='원문에 없는 구절')):
            changed=copy.deepcopy(state);mutate(changed);self.assertIsNone(reusable_findings(changed,1))

    def test_verified_settings_review_does_not_call_ai_again(self):
        with tempfile.TemporaryDirectory() as folder:
            database=str(Path(folder)/'test.sqlite3')
            def ai(prompt):raise AssertionError('검증 결과가 있으면 AI를 재호출하면 안 됩니다.')
            app=create_app(database,ai)
            try:
                c=app.test_client();pid=c.post('/api/projects',json={'title':'검증 재사용'}).json['id']
                state=self.state();state['archived']=False
                with closing(sqlite3.connect(database)) as con, con:
                    con.execute('UPDATE projects SET state=? WHERE id=?',(json.dumps(state),pid))
                result=c.post('/api/projects/'+pid+'/episode-settings-review',json={'episode':1,'action':'organize','version':2})
                self.assertEqual(result.status_code,200,result.json);self.assertTrue(result.json['cached'])
                self.assertEqual(result.json['stage'],'done')
            finally:app.extensions['storyloom_workers'].shutdown(wait=True)
