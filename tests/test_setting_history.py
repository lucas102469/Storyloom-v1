import json
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from app import create_app


class SettingHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=str(Path(self.temp.name)/'history.sqlite3')
        self.calls=[]
        def ai(prompt):
            self.calls.append(prompt);return {'findings':[],'issues':[],'items':[]}
        self.app=create_app(self.path,ai,auth_required=False);self.c=self.app.test_client()
        self.pid=self.c.post('/api/projects',json={'title':'기록 시험'}).json['id']
        self.base='/api/projects/'+self.pid

    def tearDown(self):
        self.app.extensions['storyloom_workers'].shutdown(wait=True);self.temp.cleanup()

    def seed(self,chapters=False):
        with closing(sqlite3.connect(self.path)) as con, con:
            s=json.loads(con.execute('SELECT state FROM projects WHERE id=?',(self.pid,)).fetchone()[0])
            s['length_setup_pending']=False;s['initial_references_pending']=False
            def record(kind,episode,text,status='confirmed'):
                content={'text':text,'summary':'요약','facts':'사실','impact':''}
                vid=con.execute('INSERT INTO versions(project,kind,episode,content,status,created) VALUES(?,?,?,?,?,?)',
                    (self.pid,kind,episode,json.dumps(content,ensure_ascii=False),status,time.time())).lastrowid
                return {**content,'version':vid,'status':status}
            for kind in ('plan','characters','world','places','lore','plot'):
                s['official'][kind+':0']=record(kind,0,'## 초기자료\n설명: 초기 기록 '+kind)
            if chapters:
                s['official']['episode:1']=record('episode',1,'첫 회차의 원고입니다.')
                s['drafts']['episode:2']=record('episode',2,'아직 승인하지 않은 미래 원고입니다.','draft')
                s.setdefault('records',{})['1']={'version':s['official']['episode:1']['version'],'setting_review':'organize'}
            con.execute('UPDATE projects SET state=? WHERE id=?',(json.dumps(s,ensure_ascii=False),self.pid))

    def test_initial_snapshot_is_immutable_and_survives_export_import(self):
        self.seed()
        self.assertEqual(self.c.post(self.base+'/view',json={'selected':'characters'}).status_code,200)
        first=self.c.get(self.base+'/setting-history?episode=0').json
        self.assertTrue(first['available']);self.assertIn('초기 기록 characters',first['official']['characters:0']['text'])
        with closing(sqlite3.connect(self.path)) as con, con:
            s=json.loads(con.execute('SELECT state FROM projects WHERE id=?',(self.pid,)).fetchone()[0])
            s['official']['characters:0']['text']='## 새정보\n설명: 미래 정보'
            content=s['official']['characters:0']
            content['version']=con.execute('INSERT INTO versions(project,kind,episode,content,status,created) VALUES(?,?,?,?,?,?)',
                (self.pid,'characters',0,json.dumps({k:v for k,v in content.items() if k not in ('version','status')}),'confirmed',time.time())).lastrowid
            con.execute('UPDATE projects SET state=? WHERE id=?',(json.dumps(s),self.pid))
        self.assertEqual(self.c.get(self.base+'/setting-history?episode=0').json['official'],first['official'])
        package=self.c.get(self.base+'/export').json
        restored=self.c.post('/api/projects/import',json=package)
        self.assertEqual(restored.status_code,200,restored.json)
        saved=self.c.get('/api/projects/'+restored.json['id']+'/setting-history?episode=0').json
        self.assertTrue(saved['available']);self.assertIn('초기 기록 characters',saved['official']['characters:0']['text'])

    def test_completed_review_saves_snapshot_even_with_later_draft_and_without_confirmation_click(self):
        self.seed(chapters=True)
        s=self.c.get(self.base).json['state']
        response=self.c.post(self.base+'/episode-settings-review',json={
            'episode':1,'version':s['official']['episode:1']['version'],'action':'organize'})
        self.assertEqual(response.status_code,200,response.json)
        saved=self.c.get(self.base+'/setting-history?episode=1').json
        self.assertTrue(saved['available'],saved)
        self.assertEqual(set(saved['records']),{'1'})
        self.assertNotIn('episode:2',saved['official'])
        calls=len(self.calls)
        self.c.get(self.base+'/setting-history?episode=1')
        self.assertEqual(len(self.calls),calls)

    def test_missing_history_never_uses_current_settings(self):
        self.seed(chapters=True)
        saved=self.c.get(self.base+'/setting-history?episode=1').json
        self.assertFalse(saved['available']);self.assertNotIn('official',saved)
        self.assertFalse(self.c.get(self.base+'/setting-history?episode=0').json['available'])
        self.assertEqual(self.c.get(self.base+'/setting-history?episode=-1').status_code,400)
