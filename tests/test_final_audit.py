"""Final data-integrity checks; fake AI and disposable SQLite only."""
import json
import tempfile
import unittest
from pathlib import Path
from app import create_app

class FinalAuditTests(unittest.TestCase):
    def test_backup_preserves_review_findings_and_intent_with_remapped_versions(self):
        self.edit('서린은 항구에 도착했다.');self.approve()
        package=self.c.get(self.base+'/export').json
        record=package['state']['records']['1']
        old_version=record['version']
        finding={'kind':'characters','name':'서린','fact':'항구에 도착함',
                 'quote':'서린은 항구에 도착했다.','reason':''}
        record.update(findings=[finding],setting_review='done',findings_checked=old_version,
                      intentional_development={'version':old_version,'issues':['계획된 공개'],'note':'후반에 설명'})
        package['state']['official']['episode:1']['findings']=[finding]
        response=self.c.post('/api/projects/import',json=package)
        self.assertEqual(response.status_code,200,response.json)
        state=self.state('/api/projects/'+response.json['id'])
        restored=state['records']['1'];version=state['official']['episode:1']['version']
        self.assertNotEqual(version,old_version)
        self.assertEqual(restored['findings'],[finding])
        self.assertEqual(restored['setting_review'],'done')
        self.assertEqual(restored['findings_checked'],version)
        self.assertEqual(restored['intentional_development'],{
            'version':version,'issues':['계획된 공개'],'note':'후반에 설명'})
        self.assertEqual(state['official']['episode:1']['findings'],[finding])

    def test_bad_review_version_is_rejected_without_creating_partial_project(self):
        self.edit('서린은 기다렸다.');self.approve()
        package=self.c.get(self.base+'/export').json
        count=len(self.c.get('/api/projects').json)
        package['state']['records']['1']['intentional_development']={
            'version':999999,'issues':['설명'],'note':'메모'}
        response=self.c.post('/api/projects/import',json=package)
        self.assertEqual(response.status_code,400,response.json)
        self.assertEqual(len(self.c.get('/api/projects').json),count)

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.response={'reply':'완료','updates':[]}
        self.app=create_app(str(Path(self.tmp.name)/'audit.sqlite3'),lambda _:self.response)
        self.c=self.app.test_client()
        self.pid=self.c.post('/api/projects',json={'title':'검증'}).json['id']
        self.base='/api/projects/'+self.pid
    def tearDown(self):
        self.app.extensions['storyloom_workers'].shutdown(wait=True)
        self.tmp.cleanup()
    def state(self,base=None):return self.c.get(base or self.base).json['state']
    def edit(self,text):
        item=self.state()['drafts'].get('episode:1') or self.state()['official'].get('episode:1') or {}
        return self.c.post(self.base+'/edit',json={'key':'episode:1','text':text,'base_version':item.get('version')}).json['version']
    def approve(self):
        v=self.state()['drafts']['episode:1']['version']
        for a in ['review','approve']:
            self.assertEqual(self.c.post(self.base+'/draft',json={'key':'episode:1','version':v,'action':a}).status_code,200)
    def test_backup_roundtrip_preserves_stale_metadata_and_official_records(self):
        self.edit('공식 본문');self.approve();self.edit('수정 본문')
        package=self.c.get(self.base+'/export').json
        result=self.c.post('/api/projects/import',json=package)
        self.assertEqual(result.status_code,200)
        state=self.state('/api/projects/'+result.json['id'])
        self.assertTrue(state['official']['episode:1']['metadata_stale'])
        self.assertTrue(state['drafts']['episode:1']['metadata_stale'])
        self.assertEqual(state['records']['1']['version'],state['official']['episode:1']['version'])
    def test_import_rejects_non_numeric_record_version(self):
        self.edit('본문');package=self.c.get(self.base+'/export').json
        package['state']['drafts']['episode:1']['version']=[]
        self.assertEqual(self.c.post('/api/projects/import',json=package).status_code,400)
    def test_invalid_ai_settings_are_never_saved_as_a_proposal(self):
        self.response={'reply':'설정','updates':[{'kind':'settings','content':'{"target_chars":-1}'}]}
        result=self.c.post(self.base+'/chat',json={'request_id':'invalid-settings-001','text':'분량 변경'})
        self.assertEqual(result.status_code,502)
        self.assertNotIn('settings:0',self.state()['drafts'])
    def test_completed_metadata_request_is_idempotent_after_version_changed(self):
        v=self.edit('원문');self.response={'summary':'요약','facts':'사실'}
        payload={'request_id':'metadata-repeat-001','text':'요약 갱신','operation':{'type':'refresh_metadata','key':'episode:1','base_version':v}}
        self.assertEqual(self.c.post(self.base+'/chat',json=payload).status_code,200)
        calls=self.c.get('/api/usage').json['used']
        repeat=self.c.post(self.base+'/chat',json=payload)
        self.assertEqual(repeat.status_code,200)
        self.assertTrue(repeat.json['duplicate'])
        self.assertEqual(self.c.get('/api/usage').json['used'],calls)

    def test_exported_backup_over_two_mb_can_be_imported(self):
        text='가'*250000
        self.edit(text)
        package=self.c.get(self.base+'/export').json
        payload=json.dumps(package,ensure_ascii=True)
        self.assertGreater(len(payload),2*1024*1024)
        result=self.c.post('/api/projects/import',data=payload,content_type='application/json')
        self.assertEqual(result.status_code,200)
        self.assertEqual(self.state('/api/projects/'+result.json['id'])['drafts']['episode:1']['text'],text)
