import tempfile
import unittest
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from app import create_app
from workflow import normalize_plan_heading


class ReferenceSpeedTests(unittest.TestCase):
    def test_same_version_summary_is_single_flight_and_failure_allows_retry(self):
        for fail_first in (False,True):
            with self.subTest(fail_first=fail_first), tempfile.TemporaryDirectory() as folder:
                entered=threading.Event();release=threading.Event();calls=[]
                def ai(prompt):
                    calls.append(prompt)
                    if len(calls)==1:
                        entered.set()
                        if not release.wait(5):raise AssertionError('summary test timed out')
                        if fail_first:raise ValueError('temporary failure')
                    return {'items':[{'kind':'world','title':'정원','description':'시간이 멈추는 정원.'}]}
                app=create_app(str(Path(folder)/'test.sqlite3'),ai)
                workers=app.extensions['storyloom_workers']
                try:
                    c=app.test_client();pid=c.post('/api/projects',json={'title':'동시 요약 검사'}).json['id'];base='/api/projects/'+pid
                    v=c.post(base+'/edit',json={'key':'world:0','text':'## 정원\n설명: 시간이 멈춘다.','base_version':None}).json['version']
                    with patch.object(workers,'submit') as submit:
                        for action in ('review','approve'):
                            response=c.post(base+'/draft',json={'key':'world:0','version':v,'action':action})
                            self.assertEqual(response.status_code,200,response.json)
                        fn,*args=submit.call_args.args
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        running=pool.submit(fn,*args)
                        try:
                            self.assertTrue(entered.wait(5))
                            fn(*args)
                            self.assertEqual(len(calls),1)
                        finally:release.set()
                        running.result(timeout=5)
                    fn(*args)
                    self.assertEqual(len(calls),2 if fail_first else 1)
                    self.assertTrue(c.get(base).json['state'].get('home_settings'))
                finally:
                    release.set();workers.shutdown(wait=True)

    def test_synopsis_heading_is_fixed_without_changing_any_story_words(self):
        before = ('제목: 달빛 정원\n로그라인: 연우가 시온을 만난다.\n장르: 힐링 판타지\n\n'
                  '## 시간이 멈춘 정원에서의 재회\n해고된 연우가 골목의 문을 열고 정원에 들어선다. '
                  '관리자 시온과 차를 마시고 닫힌 수첩을 탁자 위에 올려놓는다.')
        after = normalize_plan_heading(before)
        self.assertEqual(after, before.replace('## 시간이 멈춘 정원에서의 재회', '## 시놉시스'))
        self.assertEqual(normalize_plan_heading(after), after)
        calls = []
        def ai(prompt):
            calls.append(prompt)
            if len(calls) > 1:
                raise AssertionError('제목 표시를 고치려고 AI에 다시 요청했습니다.')
            return {'reply':'기획안','updates':[{'kind':'plan','content':before}]}
        with tempfile.TemporaryDirectory() as folder:
            app = create_app(str(Path(folder)/'test.sqlite3'), ai)
            try:
                c = app.test_client(); pid = c.post('/api/projects',json={'title':'표시 형식'}).json['id']
                r = c.post('/api/projects/'+pid+'/chat',json={'request_id':'plan-heading-speed','text':'기획해줘'})
                self.assertEqual(r.status_code,200,r.json)
                self.assertEqual(c.get('/api/projects/'+pid).json['state']['drafts']['plan:0']['text'],after)
                self.assertEqual(len(calls),1)
            finally:
                app.extensions['storyloom_workers'].shutdown(wait=True)

    def test_ambiguous_or_incomplete_planning_stays_on_the_existing_validation_path(self):
        prefix = '제목: 정원\n로그라인: 문을 연다.\n장르: 판타지\n'
        for body in ('## 회차 구성\n1화: 문을 연다.', '## 첫 장면\n'+'긴 장면 설명. '*10,
                     '## 개요\n- 등장인물 소개\n- 설정 소개', '## 이야기\n짧다.',
                     '## 개요\n'+'줄거리. '*10+'\n## 후반부\n'+'마무리. '*10):
            self.assertEqual(normalize_plan_heading(prefix+body),prefix+body)

    def test_home_summary_waits_for_related_drafts_and_handles_rejection(self):
        for final_action in ('approve','reject'):
            with self.subTest(final_action=final_action), tempfile.TemporaryDirectory() as folder:
                calls = []
                def ai(prompt):
                    calls.append(prompt)
                    return {'items':[{'kind':'world','title':'시간의 틈','description':'현실 시간이 멈춘다.'}]}
                app = create_app(str(Path(folder)/'test.sqlite3'), ai)
                try:
                    c=app.test_client();pid=c.post('/api/projects',json={'title':'개요 요약'}).json['id'];base='/api/projects/'+pid
                    versions={}
                    for key,text in (('world:0','## 시간의 틈\n설명: 현실의 시간이 멈춘다.'),('lore:0','## 꿈의 씨앗\n설명: 옛 꿈에 반응한다.')):
                        versions[key]=c.post(base+'/edit',json={'key':key,'text':text,'base_version':None}).json['version']
                    workers=app.extensions['storyloom_workers']
                    with patch.object(workers,'submit',wraps=workers.submit) as submit:
                        for action in ('review','approve'):
                            self.assertEqual(c.post(base+'/draft',json={'key':'world:0','version':versions['world:0'],'action':action}).status_code,200)
                        self.assertEqual(submit.call_count,0)
                        if final_action=='approve':
                            self.assertEqual(c.post(base+'/draft',json={'key':'lore:0','version':versions['lore:0'],'action':'review'}).status_code,200)
                        self.assertEqual(c.post(base+'/draft',json={'key':'lore:0','version':versions['lore:0'],'action':final_action}).status_code,200)
                        self.assertEqual(submit.call_count,1)
                    workers.shutdown(wait=True)
                    state=c.get(base).json['state']
                    expected={name:state['official'][name+':0']['version'] for name in ('world','lore') if name+':0' in state['official']}
                    self.assertEqual(state['home_settings']['versions'],expected)
                    self.assertEqual(len(calls),1)
                finally:
                    app.extensions['storyloom_workers'].shutdown(wait=True)
