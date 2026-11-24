"""Deployment configuration checks with isolated temporary databases only."""
import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch
import app as entry

ROOT=Path(__file__).resolve().parents[1]

class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.apps=[]
    def tearDown(self):
        for a in self.apps:a.extensions['storyloom_workers'].shutdown(wait=True)
        self.tmp.cleanup()
    def make(self,host=''):
        with patch.dict(os.environ,{'RENDER_EXTERNAL_HOSTNAME':host}):
            a=entry.create_app(str(Path(self.tmp.name)/'test.sqlite3'),ai=lambda _: {'reply':'ok','updates':[]},auth_required=True)
        self.apps.append(a)
        return a
    def test_render_access_auth_origin_and_static(self):
        a=self.make('deployment-test.onrender.com');c=a.test_client();base='https://deployment-test.onrender.com'
        self.assertTrue(a.config['SESSION_COOKIE_SECURE'])
        self.assertEqual(c.get('/',base_url=base).status_code,200)
        self.assertEqual(c.get('/workspace-preview',base_url=base).status_code,200)
        with c.get('/static/studio-ui.js',base_url=base) as response:
            self.assertEqual(response.status_code,200)
        self.assertEqual(c.get('/api/projects',base_url=base).status_code,401)
        self.assertEqual(c.post('/api/projects/missing/chat',base_url=base,json={'text':'hi'}).status_code,401)
        self.assertEqual(c.get('/',base_url='https://unknown.example').status_code,403)
        self.assertEqual(c.post('/api/auth/login',base_url=base,json={},headers={'Origin':'https://unknown.example'}).status_code,403)
        password='test-only-password'
        account=dict(username='testuser',nickname='test',password=password,password_confirm=password)
        self.assertEqual(c.post('/api/auth/register',base_url=base,json=account).status_code,200)
        self.assertEqual(c.post('/api/auth/logout',base_url=base,json={}).status_code,200)
        self.assertEqual(c.post('/api/auth/login',base_url=base,json=account).status_code,200)
        self.assertEqual(c.post('/api/projects',base_url=base,json={'title':'temporary'}).status_code,200)
        outsider=a.test_client()
        self.assertEqual(outsider.get('/api/projects',base_url=base).status_code,401)
    def test_local_defaults_preserved(self):
        a=self.make();self.assertFalse(a.config['SESSION_COOKIE_SECURE'])
        self.assertEqual(a.test_client().get('/').status_code,200)
        self.assertEqual(a.test_client().get('/',base_url='https://deployment-test.onrender.com').status_code,403)
    def test_wsgi_factory_uses_isolated_db(self):
        factory=entry.create_app
        def isolated():
            with patch('app.load_dotenv'):
                a=factory(database=str(Path(self.tmp.name)/'wsgi.sqlite3'))
            self.apps.append(a);return a
        with patch('app.create_app',side_effect=isolated):
            result=runpy.run_path(str(ROOT/'wsgi.py'))
        self.assertEqual(result['app'].test_client().get('/').status_code,200)
    def test_gunicorn_single_process_and_port(self):
        with patch.dict(os.environ,{'PORT':'12345'}):cfg=runpy.run_path(str(ROOT/'gunicorn.conf.py'))
        self.assertEqual(cfg['bind'],'0.0.0.0:12345')
        self.assertEqual(cfg['workers'],1)
        self.assertEqual(cfg['threads'],4)
        self.assertFalse(cfg['preload_app'])

if __name__=='__main__':unittest.main()
