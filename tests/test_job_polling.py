import json
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from app import create_app


class JobPollingTests(unittest.TestCase):
    def test_poll_preserves_retry_data_and_ownership_without_loading_project_body(self):
        with tempfile.TemporaryDirectory() as folder:
            path=str(Path(folder)/'test.sqlite3')
            app=create_app(path,lambda _: {},auth_required=True)
            try:
                client=app.test_client()
                credentials={'username':'polling_a','nickname':'조회검사','password':'Testing123!','password_confirm':'Testing123!'}
                self.assertEqual(client.post('/api/auth/register',json=credentials).status_code,200)
                pid=client.post('/api/projects',json={'title':'상태 조회 검사'}).json['id']
                jid='polling-check-001'
                with closing(sqlite3.connect(path)) as con,con:
                    con.execute('INSERT INTO jobs(id,project,payload,status,created,checkpoint) VALUES(?,?,?,?,?,?)',
                        (jid,pid,json.dumps({'text':'원고 작성'}),'failed',time.time(),
                         json.dumps({'scenes':['저장한 장면'],'plan':{'reply':'집필 계획'}})))
                statements=[];connect=sqlite3.connect
                def traced(*args,**kwargs):
                    con=connect(*args,**kwargs);con.set_trace_callback(statements.append);return con
                with patch('app.sqlite3.connect',side_effect=traced):
                    response=client.get('/api/projects/'+pid+'/jobs/'+jid)
                self.assertEqual(response.status_code,200,response.json)
                self.assertEqual(response.json['resume'],{'scenes_saved':1,'plan_saved':True})
                self.assertEqual(response.json['retry_payload'],{'request_id':jid,'text':'원고 작성'})
                project_reads=[sql.lower() for sql in statements if 'from projects' in sql.lower()]
                self.assertTrue(project_reads)
                self.assertTrue(all('select owner ' in sql for sql in project_reads),project_reads)
                other_client=app.test_client()
                self.assertEqual(other_client.post('/api/auth/register',json={**credentials,'username':'polling_b','nickname':'다른조회검사'}).status_code,200)
                self.assertEqual(other_client.get('/api/projects/'+pid+'/jobs/'+jid).status_code,400)
                other=client.post('/api/projects',json={'title':'다른 작품'}).json['id']
                self.assertEqual(client.get('/api/projects/'+other+'/jobs/'+jid).status_code,400)
            finally:app.extensions['storyloom_workers'].shutdown(wait=True)
