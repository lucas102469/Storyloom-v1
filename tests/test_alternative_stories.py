"""Alternative stories copy only released chapters, with independent ownership."""
import copy
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from app import create_app


class AlternativeStoryTests(unittest.TestCase):
    def test_branch_requires_changed_confirmed_start_before_writing_following_chapters(self):
        from workflow import branch_pending_start
        self.enable()
        pid=self.reader.post(self.root+'/branch',json={'episode':2}).json['id']
        base='/api/projects/'+pid
        self.assertEqual(branch_pending_start(self.state(pid)),2)
        self.assertTrue(self.reader.get(base).json['state']['branch_origin']['revision_required'])
        blocked=self.reader.post(base+'/episode-batch',json={'action':'start','count':2})
        self.assertEqual(blocked.status_code,409)
        self.assertIn('가져온 2화를 먼저 수정하고 확정',blocked.json['error'])
        request='3화를 이어 써줘. 직전 확정 회차의 마지막 장면과 미해결 사건, 인물들이 알고 있는 정보를 이어받고 확정 설정과 회차 계획을 지켜줘. 이번 화의 목표와 끝맺음을 먼저 계획하고 원고를 작성해줘.'
        self.reader.post(base+'/view',json={'selected':'episode','episode':3})
        blocked=self.reader.post(base+'/chat',json={'request_id':'unmodified-branch-write','text':request})
        self.assertNotEqual(blocked.status_code,200)
        self.assertIn('가져온 2화를 먼저 수정하고 확정',blocked.json['error'])
        self.assertEqual(self.prompts,[])
        old=self.state(pid)['official']['episode:2']
        self.reader.post(base+'/edit',json={'key':'episode:2','base_version':old['version'],'text':'Changed branch direction'})
        self.assertEqual(branch_pending_start(self.state(pid)),2,'an unapproved draft must not unlock later writing')
        with closing(sqlite3.connect(self.database)) as con,con:
            state=self.state(pid);draft=state['drafts'].pop('episode:2');draft['status']='confirmed'
            state['official']['episode:2']=draft
            con.execute('UPDATE projects SET state=? WHERE id=?',(json.dumps(state),pid))
        self.assertIsNone(branch_pending_start(self.state(pid)))
        self.assertFalse(self.reader.get(base).json['state']['branch_origin']['revision_required'])

    def test_branch_gate_also_rejects_ai_returned_later_episode_updates(self):
        self.enable()
        pid=self.reader.post(self.root+'/branch',json={'episode':2}).json['id']
        self.prepared={'reply':'새 회차','updates':[{'kind':'episode','episode':3,'content':'Must not be saved'}]}
        response=self.reader.post('/api/projects/'+pid+'/chat',json={'request_id':'branch-direct-update','text':'새로운 회차를 써줘'})
        self.assertNotEqual(response.status_code,200)
        self.assertIn('가져온 2화를 먼저 수정하고 확정',response.json['error'])
        self.assertNotIn('episode:3',self.state(pid)['drafts'])

    def test_name_only_new_versions_keep_their_historical_settings(self):
        with closing(sqlite3.connect(self.database)) as con, con:
            state=self.state(self.pid)
            state['linked_names']={'Public':'Renamed'}
            for number in (1,2):
                key=f'episode:{number}'
                old=state['official'][key]
                content={k:v for k,v in old.items() if k not in ('version','status')}
                content['text']=content['text'].replace('Public','Renamed')
                vid=con.execute('INSERT INTO versions(project,kind,episode,content,status,created) VALUES(?,?,?,?,?,1)',
                                (self.pid,'episode',number,json.dumps(content),'confirmed')).lastrowid
                state['official'][key]={**content,'version':vid,'status':'confirmed'}
                state['records'][str(number)]['version']=vid
            con.execute('UPDATE projects SET state=? WHERE id=?',(json.dumps(state),self.pid))
        self.assertEqual(self.publish(self.owner,self.pid,[1,2]).status_code,200)
        self.enable()
        detail=self.guest.get(self.root).json
        self.assertEqual(detail['branchEpisodes'],[1,2])
        copied=self.reader.post(self.root+'/branch',json={'episode':2})
        self.assertEqual(copied.status_code,200,copied.json)
        state=self.state(copied.json['id'])
        self.assertEqual(state['official']['episode:1']['text'],'Renamed chapter 1')
        self.assertEqual(state['official']['world:0']['text'],'## 확정 세계관\n설명: 2화에서 확정한 설정')
        self.assertNotIn('PRIVATE_LATER_SETTING_SECRET',json.dumps(state))

    def test_story_change_cannot_reuse_an_older_setting_snapshot(self):
        with closing(sqlite3.connect(self.database)) as con, con:
            state=self.state(self.pid)
            content={'text':'An entirely different event','title':'Changed story'}
            vid=con.execute('INSERT INTO versions(project,kind,episode,content,status,created) VALUES(?,?,?,?,?,1)',
                            (self.pid,'episode',1,json.dumps(content),'confirmed')).lastrowid
            state['official']['episode:1']={**content,'version':vid,'status':'confirmed'}
            con.execute('UPDATE projects SET state=? WHERE id=?',(json.dumps(state),self.pid))
        self.assertEqual(self.publish(self.owner,self.pid,[1]).status_code,200)
        self.enable()
        self.assertEqual(self.guest.get(self.root).json['branchEpisodes'],[])
        self.assertEqual(self.reader.post(self.root+'/branch',json={'episode':1}).status_code,409)

    def test_backup_restores_historical_settings_for_branching(self):
        package=self.owner.get('/api/projects/'+self.pid+'/export').json
        self.assertEqual(len(package['episode_setting_snapshots']),3)
        imported=self.owner.post('/api/projects/import',json=package)
        self.assertEqual(imported.status_code,200,imported.json)
        pid=imported.json['id']
        public=self.publish(self.owner,pid,[1,2,3])
        self.assertEqual(public.status_code,200,public.json)
        root='/api/discover/works/'+public.json['id']
        self.assertEqual(self.owner.post(root+'/branch-permission',json={'allowed':True}).status_code,200)
        self.assertEqual(self.guest.get(root).json['branchEpisodes'],[1,2,3])
        copied=self.reader.post(root+'/branch',json={'episode':2})
        self.assertEqual(copied.status_code,200,copied.json)
        state=self.state(copied.json['id'])
        self.assertEqual(state['official']['world:0']['text'],'## 확정 세계관\n설명: 2화에서 확정한 설정')
        self.assertNotIn('PRIVATE_LATER_SETTING_SECRET',json.dumps(state))

    def test_invalid_snapshot_version_rejects_import_atomically(self):
        package=self.owner.get('/api/projects/'+self.pid+'/export').json
        before=len(self.owner.get('/api/projects').json)
        package['episode_setting_snapshots'][0]['version']=999999
        response=self.owner.post('/api/projects/import',json=package)
        self.assertEqual(response.status_code,400,response.json)
        self.assertEqual(len(self.owner.get('/api/projects').json),before)

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.database = str(Path(self.folder.name) / 'alternatives.sqlite3')
        self.prompts = []
        self.prepared = {kind: '공개 원고에서 정리한 자료' for kind in
                         ('plan', 'characters', 'world', 'places', 'lore', 'plot')}
        self.prepared['plan'] = '장르: 판타지\n로그라인: 공개된 이야기'
        self.prepared['records'] = {str(n): {'summary': f'{n}화 요약', 'facts': f'{n}화 사실'} for n in range(1, 4)}
        self.app = create_app(self.database, self.generate, auth_required=True)
        self.owner, self.reader, self.guest = [self.app.test_client() for _ in range(3)]
        for client, name in ((self.owner, 'original'), (self.reader, 'alternative')):
            client.post('/api/auth/register', json={
                'username': name, 'nickname': name, 'password': 'Testing123!',
                'password_confirm': 'Testing123!'})
        self.pid = self.owner.post('/api/projects', json={'title': 'Original'}).json['id']
        for number in range(1, 4):
            with closing(sqlite3.connect(self.database)) as con, con:
                state = json.loads(con.execute('SELECT state FROM projects WHERE id=?', (self.pid,)).fetchone()[0])
                state['official']['plan:0'] = {'text': '제목: Original\n장르: 판타지, 코미디\n로그라인: Original logline'}
                state['official']['world:0'] = {'text': f'## 확정 세계관\n설명: {number}화에서 확정한 설정'}
                state['official']['places:0'] = {'text': '## 귀여움 관리국 최하층 보관소\n위치: 수도 지하'}
                content = {'text': f'Public chapter {number}', 'title': f'Title {number}',
                           'summary': f'Summary {number}', 'facts': f'Facts {number}'}
                vid = con.execute('INSERT INTO versions(project,kind,episode,content,status,created) VALUES(?,?,?,?,?,0)',
                                  (self.pid, 'episode', number, json.dumps(content), 'confirmed')).lastrowid
                state['official'][f'episode:{number}'] = {**content, 'version': vid, 'status': 'confirmed'}
                state.setdefault('records', {})[str(number)] = {
                    'version': vid, 'setting_review': 'review', 'summary': f'Summary {number}',
                    'facts': f'Facts {number}', 'findings': [{'reason': '검토 대기', 'removed': False, 'quote': content['text'], 'kind': 'world', 'name': '확정 세계관'}]}
                con.execute('UPDATE projects SET state=? WHERE id=?', (json.dumps(state), self.pid))
            reviewed = self.owner.post(f'/api/projects/{self.pid}/episode-finding', json={
                'episode': number, 'version': vid, 'index': 0, 'action': 'remove'})
            self.assertEqual(reviewed.status_code, 200, reviewed.json)
        with closing(sqlite3.connect(self.database)) as con, con:
            state = json.loads(con.execute('SELECT state FROM projects WHERE id=?', (self.pid,)).fetchone()[0])
            state['official']['world:0']['text'] = 'PRIVATE_LATER_SETTING_SECRET'
            state['official']['episode:4'] = {'text': 'UNPUBLISHED_SECRET', 'title': 'Secret'}
            self.original = copy.deepcopy(state)
            con.execute('UPDATE projects SET state=? WHERE id=?', (json.dumps(state), self.pid))
        self.public_id = self.publish(self.owner, self.pid, [1, 2, 3]).json['id']
        self.root = '/api/discover/works/' + self.public_id

    def generate(self, prompt):
        self.prompts.append(prompt)
        return copy.deepcopy(self.prepared)

    def tearDown(self):
        self.app.extensions['storyloom_workers'].shutdown(wait=True)
        self.folder.cleanup()

    def publish(self, client, pid, numbers):
        source = client.get('/api/discover/publishable/' + pid).json
        return client.post('/api/discover/publish', json={
            'project': pid, 'episodes': numbers, 'revision': source['revision']})

    def state(self, pid):
        with closing(sqlite3.connect(self.database)) as con:
            return json.loads(con.execute('SELECT state FROM projects WHERE id=?', (pid,)).fetchone()[0])

    def enable(self):
        self.assertEqual(self.owner.post(self.root + '/branch-permission', json={'allowed': True}).status_code, 200)

    def test_permission_and_authentication(self):
        self.assertEqual(self.guest.post(self.root + '/branch', json={'episode': 2}).status_code, 401)
        self.assertEqual(self.reader.post(self.root + '/branch', json={'episode': 2}).status_code, 403)
        self.assertNotEqual(self.reader.post(self.root + '/branch-permission', json={'allowed': True}).status_code, 200)
        self.assertFalse(self.guest.get(self.root).json.get('allowBranches', False))
        self.enable()
        for number in (True, 0, 4, '2'):
            self.assertEqual(self.reader.post(self.root + '/branch', json={'episode': number}).status_code, 400)
        self.assertEqual(self.prompts, [])

    def test_publication_management_saves_branch_permission(self):
        source = self.owner.get('/api/discover/publishable/' + self.pid).json
        payload = {'project': self.pid, 'episodes': [1, 2, 3], 'revision': source['revision'],
                   'updateEpisodes': [], 'updateMetadata': False, 'allowBranches': True}
        self.assertEqual(self.owner.post('/api/discover/publish', json=payload).status_code, 200)
        self.assertTrue(self.guest.get(self.root).json['allowBranches'])
        payload['allowBranches'] = False
        self.assertEqual(self.owner.post('/api/discover/publish', json=payload).status_code, 200)
        self.assertFalse(self.guest.get(self.root).json['allowBranches'])
        self.assertEqual(self.reader.post(self.root + '/branch', json={'episode': 1}).status_code, 403)
        payload['allowBranches'] = 'false'
        self.assertEqual(self.owner.post('/api/discover/publish', json=payload).status_code, 400)

    def test_private_copy_edit_publish_and_origin_links(self):
        self.enable()
        response = self.reader.post(self.root + '/branch', json={'episode': 2})
        self.assertEqual(response.status_code, 200, response.json)
        pid = response.json['id']
        state = self.state(pid)
        self.assertEqual(state['episode'], 2)
        self.assertEqual(state['selected'], 'episode')
        self.assertEqual(state['drafts'], {})
        self.assertNotIn('episode:3', state['official'])
        self.assertNotIn('episode:4', state['official'])
        self.assertEqual(self.prompts, [])
        self.assertEqual(state['official']['world:0']['text'], '## 확정 세계관\n설명: 2화에서 확정한 설정')
        self.assertEqual(state['official']['places:0']['text'], self.original['official']['places:0']['text'])
        self.assertEqual(self.reader.get('/api/projects').json[0]['title'], 'Original')
        self.assertNotIn('PRIVATE_LATER_SETTING_SECRET', json.dumps(state))
        self.assertNotIn('UNPUBLISHED_SECRET', json.dumps(state))
        self.assertNotEqual(self.owner.get('/api/projects/' + pid).status_code, 200)
        self.assertEqual(len(self.guest.get('/api/discover/works').json['works']), 1)
        self.assertEqual(self.reader.get('/api/projects').json[0]['origin']['id'], self.public_id)
        backup = self.reader.get(f'/api/projects/{pid}/export?format=json').json
        restored = self.reader.post('/api/projects/import', json=backup)
        self.assertEqual(restored.status_code, 200, restored.json)
        self.assertEqual(self.state(restored.json['id'])['branch_origin'], state['branch_origin'])
        self.assertEqual(self.publish(self.reader, pid, [1, 2]).status_code, 400)
        record = state['official']['episode:2']
        edited = self.reader.post(f'/api/projects/{pid}/edit', json={
            'key': 'episode:2', 'base_version': record['version'], 'text': 'New direction'})
        self.assertEqual(edited.status_code, 200, edited.json)
        self.assertEqual(self.state(self.pid), self.original)
        # Simulate completing the existing review flow, without altering that flow.
        with closing(sqlite3.connect(self.database)) as con, con:
            state = self.state(pid)
            revised = state['drafts'].pop('episode:2')
            revised['status'] = 'confirmed'
            state['official']['episode:2'] = revised
            con.execute('UPDATE projects SET state=? WHERE id=?', (json.dumps(state), pid))
        result = self.publish(self.reader, pid, [1, 2])
        self.assertEqual(result.status_code, 200, result.json)
        branch = self.guest.get('/api/discover/works/' + result.json['id']).json
        self.assertEqual(branch['origin'], {'id': self.public_id, 'title': 'Original', 'author': 'original', 'episode': 2})
        self.assertEqual(self.guest.get(self.root).json['branches'][0]['id'], branch['id'])
        catalog = self.guest.get('/api/discover/works').json['works']
        self.assertEqual([work['id'] for work in catalog], [self.public_id])
        self.assertNotIn(branch['id'], [work['id'] for work in catalog])
        self.assertEqual(self.reader.post('/api/discover/works/' + branch['id'] + '/like',
                                         json={'liked': True}).status_code, 200)
        interests = self.reader.get('/api/discover/liked').json['works']
        self.assertIn(branch['id'], [work['id'] for work in interests])
        with closing(sqlite3.connect(self.database)) as con, con:
            state['official']['episode:1']['text'] = 'Changed prefix'
            con.execute('UPDATE projects SET state=? WHERE id=?', (json.dumps(state), pid))
        self.assertEqual(self.publish(self.reader, pid, [1, 2]).status_code, 400)

    def test_missing_historical_settings_are_not_recreated(self):
        self.enable()
        with closing(sqlite3.connect(self.database)) as con, con:
            con.execute('DELETE FROM published_episode_settings WHERE work=? AND episode=2', (self.public_id,))
        self.assertEqual(self.reader.post(self.root + '/branch', json={'episode': 2}).status_code, 409)
        self.assertEqual(self.reader.get('/api/projects').json, [])
        self.assertEqual(self.prompts, [])

    def test_snapshots_remain_frozen_after_later_settings_and_drafts(self):
        self.enable()
        detail = self.guest.get(self.root).json
        self.assertEqual(detail['branchEpisodes'], [1, 2, 3])
        response = self.reader.post(self.root + '/branch', json={'episode': 1})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertIn('1화에서 확정한 설정', self.state(response.json['id'])['official']['world:0']['text'])
        with closing(sqlite3.connect(self.database)) as con:
            original = json.loads(con.execute(
                'SELECT payload FROM episode_setting_snapshots WHERE project=? AND episode=2', (self.pid,)).fetchone()[0])
        self.assertEqual(original['official']['world:0']['text'], '## 확정 세계관\n설명: 2화에서 확정한 설정')

    def test_old_copy_presentation_repair_preserves_manuscript(self):
        self.enable()
        pid = self.reader.post(self.root + '/branch', json={'episode': 2}).json['id']
        state = self.state(pid)
        state['branch_origin'].pop('presentation_version')
        state['branch_origin'].pop('settings_source')
        state['official']['places:0']['text'] = '## 보관소: 지하 시설. ## 중앙 광장: 도심.'
        state['official']['plan:0']['text'] = '장르: 판타지, 코미디 ## 로그라인: 공개된 이야기'
        with closing(sqlite3.connect(self.database)) as con, con:
            con.execute('UPDATE projects SET title=?,state=? WHERE id=?',
                        ('Original · 다른 줄거리', json.dumps(state), pid))
        work = self.reader.get('/api/projects').json[0]
        self.assertEqual(work['title'], 'Original')
        self.assertEqual(work['genre'], '판타지, 코미디')
        repaired = self.reader.get('/api/projects/' + pid).json['state']
        self.assertEqual(repaired['official']['episode:2'], state['official']['episode:2'])
        self.assertEqual(repaired['official']['places:0']['text'], '## 보관소\n설명: 지하 시설.\n## 중앙 광장\n설명: 도심.')
        self.assertEqual(self.state(self.pid), self.original)
