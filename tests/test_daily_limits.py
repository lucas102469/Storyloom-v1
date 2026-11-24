"""Quota integration tests: temporary SQLite and fake provider only."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch, Mock
from app import create_app, AIUsageLimit
from ai_client import gemini, GeminiUnavailable
from google.genai import types


class DailyLimitsTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{'USER_DAILY_LIMIT':'60','TOTAL_DAILY_LIMIT':'400'})
        self.env.start()
        self.temp=tempfile.TemporaryDirectory()
        self.path=str(Path(self.temp.name)/'quota.sqlite3')
        self.calls=[]
        self.response={'reply':'확인','updates':[]}
        def fake(prompt):
            self.calls.append(prompt)
            if isinstance(self.response,Exception):raise self.response
            return self.response(prompt) if callable(self.response) else self.response
        self.fake=fake
        self.app=create_app(self.path,fake)
        self.a,self.pa=self.client('A')
        self.b,self.pb=self.client('B')

    def tearDown(self):
        self.app.extensions['storyloom_workers'].shutdown(wait=True)
        self.temp.cleanup()
        self.env.stop()

    def client(self,owner):
        c=self.app.test_client()
        with c.session_transaction() as session:session['uid']=owner
        pid=c.post('/api/projects',json={'title':'quota '+owner}).json['id']
        return c,pid

    def send(self,c,pid,rid='quota-request-0001'):
        return c.post('/api/projects/'+pid+'/chat',json={'request_id':rid,'text':'테스트'})

    def seed(self,count,owner='A',day=None):
        if day is None:
            from datetime import timedelta
            reset=datetime.fromisoformat(self.a.get('/api/usage').json['resets_at'])
            day=(reset-timedelta(days=1)).date().isoformat()
        with closing(sqlite3.connect(self.path)) as con,con:
            con.executemany('INSERT INTO api_usage(day,owner,task,status,created) VALUES(?,?,?,?,?)',
                            [(day,owner,'chat','succeeded',0)]*count)

    def test_personal_boundary_and_other_user_independence(self):
        self.seed(59)
        self.assertEqual(self.send(self.a,self.pa).status_code,200)
        denied=self.send(self.a,self.pa,'quota-request-0002')
        self.assertEqual(denied.status_code,429)
        self.assertIn('오늘 사용할 수 있는',denied.json['error'])
        self.assertEqual(self.send(self.b,self.pb,'quota-request-0003').status_code,200)
        ua,ub=self.a.get('/api/usage').json,self.b.get('/api/usage').json
        self.assertEqual((ua['personal_used'],ub['personal_used'],ua['used']),(60,1,61))
        self.assertEqual(len(self.calls),2)

    def test_pre_limit_history_of_180_is_shown_without_clamping_and_blocks_new_calls(self):
        self.seed(180,'A')
        usage=self.a.get('/api/usage').json
        self.assertEqual((usage['personal_used'],usage['personal_limit'],usage['personal_remaining']),(180,60,0))
        self.assertEqual((usage['total_used'],usage['total_limit'],usage['total_remaining']),(180,400,220))
        self.assertEqual(usage['remaining'],0)
        before=self.a.get('/api/projects/'+self.pa).json['state']
        for n in range(3):
            response=self.send(self.a,self.pa,'history-over-limit-'+str(n))
            self.assertEqual(response.status_code,429)
        self.assertEqual(len(self.calls),0)
        self.assertEqual(self.a.get('/api/usage').json['personal_used'],180)
        self.assertEqual(self.a.get('/api/projects/'+self.pa).json['state'],before)
        with closing(sqlite3.connect(self.path)) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM api_usage').fetchone()[0],180)

    def test_split_history_of_40_and_30_stays_personal_with_total_70(self):
        self.seed(40,'A');self.seed(30,'B')
        a,b=self.a.get('/api/usage').json,self.b.get('/api/usage').json
        self.assertEqual((a['personal_used'],b['personal_used']),(40,30))
        self.assertEqual((a['personal_remaining'],b['personal_remaining']),(20,30))
        self.assertEqual((a['total_used'],b['total_used']),(70,70))
        self.assertEqual((a['total_remaining'],b['total_remaining']),(330,330))

    def test_global_boundary_and_blocked_request_not_recorded(self):
        self.seed(399,'other')
        self.assertEqual(self.send(self.a,self.pa).status_code,200)
        denied=self.send(self.b,self.pb,'quota-request-0002')
        self.assertEqual(denied.status_code,429)
        self.assertIn('오늘 서비스의',denied.json['error'])
        self.assertEqual(self.a.get('/api/usage').json['used'],400)
        self.assertEqual(len(self.calls),1)

    def test_atomic_global_reservation_across_simultaneous_users(self):
        self.seed(399,'other')
        barrier=threading.Barrier(2)
        def send(args):
            barrier.wait(timeout=5)
            return self.send(*args).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(send,[(self.a,self.pa,'parallel-quota-0001'),(self.b,self.pb,'parallel-quota-0002')]))
        self.assertEqual(sorted(results),[200,429])
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.a.get('/api/usage').json['used'],400)

    def test_atomic_personal_reservation_across_browsers(self):
        self.seed(59)
        c,pid=self.client('A')
        barrier=threading.Barrier(2)
        def send(args):
            barrier.wait(timeout=5)
            return self.send(*args).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(send,[(self.a,self.pa,'personal-race-0001'),(c,pid,'personal-race-0002')]))
        self.assertEqual(sorted(results),[200,429])
        self.assertEqual(c.get('/api/usage').json['personal_used'],60)

    def test_usage_persists_on_reopen_and_same_identity_new_session(self):
        self.send(self.a,self.pa)
        reopened=create_app(self.path,self.fake)
        try:
            c=reopened.test_client()
            with c.session_transaction() as session:session['uid']='A'
            self.assertEqual(c.get('/api/usage').json['personal_used'],1)
            with c.session_transaction() as session:session.clear();session['uid']='B'
            self.assertEqual(c.get('/api/usage').json['personal_used'],0)
        finally:reopened.extensions['storyloom_workers'].shutdown(wait=True)

    def test_kst_midnight_changes_day_without_deleting_history(self):
        class Clock(datetime):
            instant=datetime(2026,11,24,14,59,59,tzinfo=timezone.utc)
            @classmethod
            def now(cls,tz=None):return cls.instant.astimezone(tz)
        with patch('app.datetime',Clock):
            self.send(self.a,self.pa)
            self.assertEqual(self.a.get('/api/usage').json['personal_used'],1)
            Clock.instant=datetime(2026,11,24,15,0,0,tzinfo=timezone.utc)
            self.assertEqual(self.a.get('/api/usage').json['personal_used'],0)
            self.send(self.a,self.pa,'new-day-quota-0002')
        with closing(sqlite3.connect(self.path)) as con:
            self.assertEqual(con.execute('SELECT day FROM api_usage ORDER BY id').fetchall(),[('2026-11-24',),('2026-11-25',)])

    def test_non_ai_edit_and_blocked_chat_preserve_project(self):
        self.seed(60)
        base='/api/projects/'+self.pa
        before=self.a.get(base).json['state']
        self.assertEqual(self.send(self.a,self.pa).status_code,429)
        self.assertEqual(self.a.get(base).json['state'],before)
        self.assertEqual(self.a.post(base+'/edit',json={'key':'characters:0','text':'## 주인공\n설명: 수동 설정'}).status_code,200)
        self.assertEqual(self.a.get('/api/usage').json['used'],60)
        self.assertEqual(len(self.calls),0)

    def test_logout_and_login_keep_same_user_usage(self):
        c=self.app.test_client()
        credentials={'username':'quota_writer','nickname':'검사','password':'Testing123!','password_confirm':'Testing123!'}
        self.assertEqual(c.post('/api/auth/register',json=credentials).status_code,200)
        pid=c.post('/api/projects',json={'title':'로그인 기록'}).json['id']
        self.assertEqual(self.send(c,pid).status_code,200)
        self.assertEqual(c.post('/api/auth/logout',json={}).status_code,200)
        self.assertEqual(c.post('/api/auth/login',json={'username':'quota_writer','password':'Testing123!'}).status_code,200)
        self.assertEqual(c.get('/api/usage').json['personal_used'],1)

    def test_real_retry_is_counted_twice(self):
        count=[0]
        def respond(prompt):
            count[0]+=1
            if count[0]==1:raise GeminiUnavailable('fake 503')
            return {'reply':'재시도 성공','updates':[]}
        self.response=respond
        with patch('app.time.sleep'):
            self.assertEqual(self.send(self.a,self.pa).status_code,200)
        self.assertEqual(self.a.get('/api/usage').json['personal_used'],2)
        self.assertEqual(len(self.calls),2)

    def test_failed_sent_request_remains_counted(self):
        self.response=ValueError('가짜 응답 오류')
        self.assertEqual(self.send(self.a,self.pa).status_code,502)
        self.assertEqual(self.a.get('/api/usage').json['personal_used'],1)
        with closing(sqlite3.connect(self.path)) as con:
            self.assertEqual(con.execute('SELECT owner,status FROM api_usage').fetchall(),[('A','failed')])

    def test_mid_job_limit_keeps_checkpoint_and_resumes_next_day(self):
        base='/api/projects/'+self.pa
        self.a.post(base+'/length',json={'target_chars':1000,'total_episodes':3})
        self.app.config['USER_DAILY_LIMIT']=1
        plan={'reply':'계획','updates':[],'write_episode':1,'scene_goals':['도입','결말']}
        self.response=plan
        self.assertEqual(self.send(self.a,self.pa).status_code,429)
        with closing(sqlite3.connect(self.path)) as con:
            status,checkpoint=con.execute('SELECT status,checkpoint FROM jobs').fetchone()
        self.assertEqual(status,'failed')
        self.assertEqual(json.loads(checkpoint)['plan']['write_episode'],1)
        self.assertEqual(len(self.calls),1)
        with closing(sqlite3.connect(self.path)) as con,con:con.execute("UPDATE api_usage SET day='2000-01-01'")
        self.app.config['USER_DAILY_LIMIT']=60
        self.response={'text':'조용한 밤이었다. '*110,'reply':'완료','summary':'밤','facts':'밤이었다.','findings':[],'issues':[],'updates':[]}
        resumed=self.send(self.a,self.pa)
        self.assertEqual(resumed.status_code,200,resumed.json)
        self.assertIn('episode:1',self.a.get(base).json['state']['drafts'])
        self.assertNotIn('1화 집필 계획',''.join(self.calls[1:]))
        self.assertEqual(self.a.get('/api/usage').json['personal_used'],len(self.calls)-1)

    def test_limit_after_saved_scene_does_not_complete_job(self):
        base='/api/projects/'+self.pa
        self.a.post(base+'/length',json={'target_chars':1000,'total_episodes':3})
        self.app.config['USER_DAILY_LIMIT']=2
        count=[0]
        def respond(prompt):
            count[0]+=1
            if count[0]==1:
                return {'reply':'계획','updates':[],'write_episode':1,'scene_goals':['도입','결말']}
            return {'text':'조용한 밤이었다. '*110,'title':'밤'}
        self.response=respond
        result=self.send(self.a,self.pa)
        self.assertEqual(result.status_code,429,result.json)
        with closing(sqlite3.connect(self.path)) as con:
            status,checkpoint=con.execute('SELECT status,checkpoint FROM jobs').fetchone()
        self.assertEqual(status,'failed')
        self.assertTrue(json.loads(checkpoint)['scenes'])
        self.assertEqual(len(self.calls),2)
        self.assertNotIn('episode:1',self.a.get(base).json['state']['official'])

    def test_synchronous_ai_route_uses_logged_in_owner(self):
        base='/api/projects/'+self.pa
        r=self.a.post(base+'/edit',json={'key':'plan:0','text':'제목: 검사\n로그라인: 검객의 여정\n장르: 무협\n시놉시스: 검객이 떠난다.'})
        self.assertEqual(r.status_code,200)
        version=self.a.get(base).json['state']['drafts']['plan:0']['version']
        for action in ('review','approve'):
            self.assertEqual(self.a.post(base+'/draft',json={'key':'plan:0','version':version,'action':action}).status_code,200)
        self.response={'beginning':'출발','middle':'여정','ending':'귀환','episodes':[{'number':n,'event':'사건'} for n in range(1,4)]}
        self.assertEqual(self.a.post(base+'/length-plan',json={'selection':{'total_episodes':3,'target_chars':4000}}).status_code,200)
        self.assertEqual(self.a.get('/api/usage').json['personal_used'],1)
        self.assertEqual(self.b.get('/api/usage').json['personal_used'],0)

    def test_env_defaults_override_and_invalid_values(self):
        for values,expected in [({},(60,400)),({'USER_DAILY_LIMIT':'7','TOTAL_DAILY_LIMIT':'19'},(7,19)),
                                ({'USER_DAILY_LIMIT':'bad','TOTAL_DAILY_LIMIT':'-2'},(60,400)),
                                ({'USER_DAILY_LIMIT':'0','TOTAL_DAILY_LIMIT':'1.5'},(60,400))]:
            with self.subTest(values=values),patch.dict(os.environ,values,clear=True):
                app=create_app(str(Path(self.temp.name)/'env.sqlite3'),lambda _: {})
                try:self.assertEqual((app.config['USER_DAILY_LIMIT'],app.config['TOTAL_DAILY_LIMIT']),expected)
                finally:app.extensions['storyloom_workers'].shutdown(wait=True)

    def test_pre_dispatch_missing_key_is_not_counted(self):
        with patch.dict(os.environ,{},clear=True),patch('app.load_dotenv'):
            app=create_app(str(Path(self.temp.name)/'no-key.sqlite3'),auth_required=False)
            try:
                c=app.test_client();pid=c.post('/api/projects',json={'title':'키 없음'}).json['id']
                self.assertEqual(self.send(c,pid).status_code,502)
                self.assertEqual(c.get('/api/usage').json['used'],0)
            finally:app.extensions['storyloom_workers'].shutdown(wait=True)

    def test_sdk_boundary_invokes_reservation_once_and_blocks_dispatch(self):
        client=Mock()
        client.models.generate_content.return_value=types.GenerateContentResponse(candidates=[types.Candidate(
            finish_reason='STOP',content=types.Content(parts=[types.Part(text='{"reply":"ok","updates":[]}')]))])
        reserve=Mock()
        with patch.dict(os.environ,{'GEMINI_API_KEY':'fake'}),patch('ai_client.get_client',return_value=client):
            gemini('test',before_request=reserve)
            self.assertEqual(reserve.call_count,1)
            self.assertEqual(client.models.generate_content.call_count,1)
            reserve.side_effect=AIUsageLimit('user')
            with self.assertRaises(AIUsageLimit):gemini('test',before_request=reserve)
            self.assertEqual(client.models.generate_content.call_count,1)

    def test_stream_sdk_request_has_single_reservation(self):
        client=Mock()
        chunk=types.GenerateContentResponse(candidates=[types.Candidate(index=0,finish_reason='STOP',
            content=types.Content(parts=[types.Part(text='{"reply":"ok","updates":[]}')]))])
        client.models.generate_content_stream.return_value=iter([chunk])
        reserve=Mock()
        with patch.dict(os.environ,{'GEMINI_API_KEY':'fake'}),patch('ai_client.get_client',return_value=client):
            gemini('test',on_progress=lambda *_:None,before_request=reserve)
        self.assertEqual(reserve.call_count,1)
        self.assertEqual(client.models.generate_content_stream.call_count,1)
        self.assertFalse(client.models.generate_content.called)

if __name__=='__main__':unittest.main()
