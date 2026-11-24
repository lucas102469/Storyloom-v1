import io
import json
import sqlite3
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from app import create_app
from workflow import apply_patches, build_context


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name)/'app.sqlite3')
        self.app = create_app(self.path, lambda p: {'reply': 'ok', 'updates': []})
        self.client = self.app.test_client()
        self.pid = self.client.post('/api/projects', json={'title': '워크플로우'}).json['id']
        self.base = '/api/projects/'+self.pid

    def test_initial_length_can_leave_episode_total_open(self):
        pid = self.client.post('/api/projects', json={
            'title': '분량을 나중에 정하는 작품', 'defer_length_setup': True,
        }).json['id']
        base = '/api/projects/' + pid
        state = self.client.get(base).json['state']
        self.assertTrue(state['length_setup_pending'])
        self.assertTrue(state['messages'])
        self.assertIsNone(state['settings']['total_episodes'])
        values = {'target_chars': 4500, 'total_episodes': None}
        restored = self.client.post('/api/projects/import', json=self.client.get(base + '/export').json)
        self.assertEqual(restored.status_code, 200)
        restored_base = '/api/projects/' + restored.json['id']
        self.assertTrue(self.client.get(restored_base).json['state']['length_setup_pending'])
        self.assertEqual(self.client.post(restored_base + '/initial-length', json=values).status_code, 200)
        self.assertEqual(self.client.post(base + '/initial-length', json=values).status_code, 200)
        state = self.client.get(base).json['state']
        self.assertFalse(state.get('length_setup_pending'))
        self.assertEqual(state['settings'], values)
        self.assertEqual(self.client.post(base + '/initial-length', json=values).status_code, 409)

    def tearDown(self):
        self.app.extensions['storyloom_workers'].shutdown(wait=True)
        self.tmp.cleanup()

    def state(self):
        return self.client.get(self.base).json['state']

    def edit(self, key, text, base=None):
        return self.client.post(self.base+'/edit', json={'key':key, 'text':text, 'base_version':base})

    def approve(self, key):
        vid=self.state()['drafts'][key]['version']
        for action in ('review','approve'):
            self.assertEqual(self.client.post(self.base+'/draft', json={'key':key,'version':vid,'action':action}).status_code,200)

    def test_manual_autosave_reject_compare_and_conflict(self):
        vid=self.edit('episode:1','첫 줄\n마지막 줄').json['version']
        self.assertFalse(self.state()['official'])
        self.approve('episode:1')
        official=self.state()['official']['episode:1']
        changed=self.edit('episode:1','바뀐 줄\n마지막 줄',official['version'])
        self.assertEqual(changed.status_code,200)
        self.assertEqual(self.edit('episode:1','덮어쓰기',official['version']).status_code,409)
        diff=self.client.get(self.base+'/compare?key=episode:1').json
        self.assertTrue(any(x['type']!='equal' for x in diff['changes']))
        self.client.post(self.base+'/draft',json={'key':'episode:1','version':changed.json['version'],'action':'reject'})
        self.assertNotIn('episode:1',self.state()['drafts'])
        self.assertEqual(self.state()['official']['episode:1'],official)

    def test_structured_settings_require_approval(self):
        self.assertEqual(self.edit('settings:0',json.dumps({'target_chars':4800,'total_episodes':50})).status_code,200)
        self.assertEqual(self.state()['settings']['target_chars'],4000)
        self.approve('settings:0')
        self.assertEqual(self.state()['settings'],{'target_chars':4800,'total_episodes':50})
        self.assertEqual(self.edit('settings:0','{"total_episodes":0}',self.state()['official']['settings:0']['version']).status_code,400)

    def test_key_validation_and_export_without_credentials(self):
        for key in ('episode:0','world:1','unknown:0','../env'):
            self.assertEqual(self.edit(key,'bad').status_code,400)
        self.edit('characters:0','유진은 등대지기')
        self.approve('characters:0')
        data=self.client.get(self.base+'/export?format=zip')
        self.assertEqual(data.status_code,200)
        with zipfile.ZipFile(io.BytesIO(data.data)) as archive:
            self.assertEqual(set(archive.namelist()),{'backup.json','official.md'})
            self.assertIn('등대지기',archive.read('official.md').decode())
            backup=json.loads(archive.read('backup.json'))
            self.assertNotIn('GEMINI_API_KEY',str(backup))
        self.assertEqual(len(self.client.get(self.base+'/search?q=등대').json),1)

    def test_archive_and_rename_preserve_material(self):
        self.edit('plan:0','보존할 기획')
        self.client.post(self.base+'/manage',json={'title':'새 제목','archived':True})
        self.assertEqual(self.client.get(self.base).json['title'],'새 제목')
        self.assertEqual(self.edit('plan:0','수정').status_code,400)
        self.assertEqual(self.state()['drafts']['plan:0']['text'],'보존할 기획')
        self.client.post(self.base+'/manage',json={'archived':False})
        self.assertFalse(self.state()['archived'])

    def test_backup_roundtrip_creates_new_project_with_history(self):
        self.edit('world:0','보존할 세계관')
        self.approve('world:0')
        self.edit('world:0','새 초안',self.state()['official']['world:0']['version'])
        backup=self.client.get(self.base+'/export?format=json').json
        restored=self.client.post('/api/projects/import',json=backup)
        self.assertEqual(restored.status_code,200)
        self.assertNotEqual(restored.json['id'],self.pid)
        restored_base='/api/projects/'+restored.json['id']
        state=self.client.get(restored_base).json['state']
        self.assertEqual(state['official']['world:0']['text'],'보존할 세계관')
        self.assertEqual(state['drafts']['world:0']['text'],'새 초안')
        self.assertEqual(len(self.client.get(restored_base+'/versions').json),
                         len(self.client.get(self.base+'/versions').json))
        self.assertEqual(self.state()['drafts']['world:0']['text'],'새 초안')

    def test_download_selected_episodes_settings_and_restore_full_zip(self):
        self.edit('plan:0','확정 기획안')
        self.approve('plan:0')
        self.edit('characters:0','## ## 서윤\n역할: 주인공')
        for number in (1, 2, 3):
            self.edit(f'episode:{number}',f'{number}화 원고 본문')
            if number != 3:
                self.approve(f'episode:{number}')
        single = self.client.get(self.base+'/export?format=episodes&episode=2')
        self.assertEqual(single.status_code, 200)
        self.assertIn('filename*=',single.headers['Content-Disposition'])
        self.assertEqual(single.data.decode('utf-8-sig'),'2화\n\n2화 원고 본문\n')
        headers = {'Origin':'http://localhost'}
        multi = self.client.post(self.base+'/export',headers=headers,data={'format':'episodes','episode':['2','1','2']})
        self.assertEqual(multi.status_code,200)
        with zipfile.ZipFile(io.BytesIO(multi.data)) as archive:
            self.assertEqual(archive.namelist(),['01화.txt','02화.txt'])
            self.assertNotIn('확정 기획안',archive.read('01화.txt').decode('utf-8-sig'))
        self.assertEqual(self.client.get(self.base+'/export?format=episodes&episode=3').status_code,400)
        settings = self.client.get(self.base+'/export?format=settings-txt').data.decode('utf-8-sig')
        self.assertIn('확정 기획안',settings)
        self.assertNotIn('원고 본문',settings)
        full = self.client.get(self.base+'/export?format=zip')
        imported = self.client.post('/api/projects/import',headers=headers,data={'backup':(io.BytesIO(full.data),'backup.zip')})
        self.assertEqual(imported.status_code,200,imported.json)
        copied = self.client.get('/api/projects/'+imported.json['id']).json['state']
        self.assertEqual(copied['drafts']['episode:3']['text'],'3화 원고 본문')
        self.assertEqual(copied['drafts']['characters:0']['text'],'## 서윤\n역할: 주인공')
        self.assertEqual(copied['official']['plan:0']['text'],'확정 기획안')
        rejected = self.client.post('/api/projects/import',headers=headers,data={'backup':(io.BytesIO(multi.data),'episodes.zip')})
        self.assertEqual(rejected.status_code,400)
        rejected = self.client.post('/api/projects/import',headers={'Origin':'https://example.com'},
                                    data={'backup':(io.BytesIO(full.data),'backup.zip')})
        self.assertEqual(rejected.status_code,403)

    def test_exact_partial_patch_preserves_other_text(self):
        self.assertEqual(apply_patches('앞\n원문\n뒤',[{'old':'원문','new':'수정'}]),'앞\n수정\n뒤')
        with self.assertRaises(ValueError):
            apply_patches('같음 같음',[{'old':'같음','new':'변경'}])
        with self.assertRaises(ValueError):
            apply_patches('원문',[{'old':'없는 문장','new':'변경'}])

    def test_background_job_polling_and_same_request_deduplication(self):
        started,release=threading.Event(),threading.Event()
        def ai(prompt):
            started.set()
            if not release.wait(5):
                raise ValueError('test worker timeout')
            return {'reply':'완료','updates':[]}
        self.app.extensions['storyloom_workers'].shutdown()
        self.app=create_app(self.path,ai)
        self.client=self.app.test_client()
        request={'request_id':'background-001','text':'대화','background':True}
        try:
            result=self.client.post(self.base+'/chat',json=request)
            self.assertEqual(result.status_code,202)
            self.assertTrue(started.wait(2))
            job=self.client.get(self.base+'/jobs/background-001').json
            self.assertEqual(job['status'],'running')
            self.assertEqual(self.client.post(self.base+'/chat',json=request).status_code,409)
        finally:
            release.set()
            self.app.extensions['storyloom_workers'].shutdown(wait=True)
        self.assertEqual(self.client.get(self.base+'/jobs/background-001').json['status'],'done')
        self.assertEqual(len(self.state()['messages']),3)

    def test_failed_scene_retries_from_checkpoint(self):
        calls=[]
        fail=[True]
        def ai(prompt):
            calls.append(prompt)
            if prompt.startswith('REFERENCE_BACKFILL'):
                return {'updates':[]}
            if prompt.startswith('당신은'):
                return {'reply':'원고','updates':[],'write_episode':1,'scene_goals':['첫장면','둘째장면']}
            if prompt.startswith('한국어 소설의 다음 장면'):
                return {'text':'첫 장면 내용'*250+'\n\n'+'둘째 장면 내용'*290}
            if fail[0]:
                fail[0]=False
                raise ValueError('일시 실패')
            return {'summary':'전체 요약','facts':'새 사실','issues':[],'findings':[]}
        self.app.extensions['storyloom_workers'].shutdown()
        self.app=create_app(self.path,ai)
        self.client=self.app.test_client()
        payload={'request_id':'checkpoint-001','text':'1화 집필'}
        self.assertEqual(self.client.post(self.base+'/chat',json=payload).status_code,502)
        self.assertFalse(self.state()['drafts'])
        self.assertEqual(self.client.post(self.base+'/chat',json=payload).status_code,200)
        self.assertEqual(sum('목표: 첫장면' in x for x in calls),1)
        self.assertEqual(sum(x.startswith('당신은') for x in calls),1)
        self.assertEqual(self.state()['drafts']['episode:1']['text'],'첫 장면 내용'*250+'\n\n'+'둘째 장면 내용'*290)


class AuthTests(unittest.TestCase):
    def test_login_ownership_and_legacy_project_migration(self):
        with tempfile.TemporaryDirectory() as folder:
            path=str(Path(folder)/'auth.sqlite3')
            legacy=create_app(path,lambda p:{})
            pid=legacy.test_client().post('/api/projects',json={'title':'기존 작품'}).json['id']
            app=create_app(path,lambda p:{},auth_required=True)
            a,b=app.test_client(),app.test_client()
            self.assertEqual(a.get('/api/projects').status_code,401)
            self.assertEqual(a.post('/api/auth/register',json={'username':'writer_a','nickname':'작가A','password':'password123','password_confirm':'password123'}).status_code,200)
            self.assertEqual(a.get('/api/projects').json[0]['id'],pid)
            b.post('/api/auth/register',json={'username':'writer_b','nickname':'작가B','password':'password456','password_confirm':'password456'})
            self.assertEqual(b.get('/api/projects').json,[])
            self.assertEqual(b.get('/api/projects/'+pid).status_code,400)
            a.post('/api/auth/logout',json={})
            self.assertEqual(a.get('/api/projects').status_code,401)
            self.assertEqual(a.post('/api/auth/login',json={'username':'writer_a','password':'wrong'}).status_code,401)
            self.assertEqual(a.post('/api/auth/login',json={'username':'writer_a','nickname':'작가A','password':'password123','password_confirm':'password123'}).status_code,200)
            with sqlite3.connect(path) as con:
                password=con.execute('SELECT password FROM users WHERE username=?',('writer_a',)).fetchone()[0]
                self.assertNotEqual(password,'password123')
            con.close()
            app.extensions['storyloom_workers'].shutdown()
            legacy.extensions['storyloom_workers'].shutdown()
