"""Publication snapshots change through their owner's explicit choices."""
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from app import create_app


class PublicationManagementTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.database = str(Path(self.folder.name) / 'publication.sqlite3')
        self.app = create_app(self.database, lambda _: {}, auth_required=True)
        self.owner, self.other, self.guest = [self.app.test_client() for _ in range(3)]
        for client, name in ((self.owner, 'owner'), (self.other, 'reader')):
            client.post('/api/auth/register', json={
                'username': name, 'nickname': name, 'password': 'Testing123!',
                'password_confirm': 'Testing123!'})
        self.pid = self.owner.post('/api/projects', json={'title': 'Published work'}).json['id']
        with closing(sqlite3.connect(self.database)) as con, con:
            state = json.loads(con.execute('SELECT state FROM projects WHERE id=?', (self.pid,)).fetchone()[0])
            state['official'] = {
                'plan:0': {'text': '장르: 판타지\n로그라인: Original introduction'},
                'episode:1': {'title': 'First', 'text': 'Original first'},
                'episode:2': {'title': 'Second', 'text': 'Original second'}}
            state['drafts']['episode:3'] = {'text': 'Private third', 'status': 'draft'}
            con.execute('UPDATE projects SET state=? WHERE id=?', (json.dumps(state), self.pid))

    def tearDown(self):
        self.app.extensions['storyloom_workers'].shutdown(wait=True)
        self.folder.cleanup()

    def source(self):
        return self.owner.get('/api/discover/publishable/' + self.pid).json

    def publish(self, numbers, updates=(), metadata=False, source=None, client=None):
        source = source or self.source()
        return (client or self.owner).post('/api/discover/publish', json={
            'project': self.pid, 'revision': source['revision'], 'episodes': numbers,
            'updateEpisodes': list(updates), 'updateMetadata': metadata,
            'publicationUpdated': source['publication']['updated'] if source['publication'] else None})

    def test_selective_updates_stop_and_resume_preserve_reader_records(self):
        public_id = self.publish([1, 2], metadata=True).json['id']
        root = '/api/discover/works/' + public_id
        self.other.post(root + '/like', json={'liked': True})
        self.other.post(root + '/episodes/1/view', json={})
        self.other.post(root + '/comments', json={'text': 'Reader comment', 'episode': 1})
        self.assertTrue(self.owner.get(root).json['canManage'])
        self.assertFalse(self.guest.get(root).json['canManage'])
        self.assertNotIn('manageProject', self.guest.get(root).json)
        with closing(sqlite3.connect(self.database)) as con, con:
            state = json.loads(con.execute('SELECT state FROM projects WHERE id=?', (self.pid,)).fetchone()[0])
            state['official']['episode:1']['text'] = 'Revised first'
            state['official']['episode:3'] = {'title': 'Third', 'text': 'Confirmed third'}
            state['official']['plan:0']['text'] = '장르: SF\n로그라인: New introduction'
            expected_state = json.dumps(state)
            con.execute('UPDATE projects SET state=?,revision=revision+1 WHERE id=?', (expected_state, self.pid))
        source = self.source()
        self.assertTrue(source['metadataChanged'])
        self.assertTrue(source['episodes'][0]['changed'])
        self.assertIsNone(source['episodes'][2]['published'])
        self.assertEqual(self.publish([1, 2, 3]).status_code, 200)
        detail = self.guest.get(root).json
        self.assertEqual(detail['episodes'][0]['text'], 'Original first')
        self.assertEqual(detail['synopsis'], 'Original introduction')
        self.assertEqual(detail['episodes'][2]['text'], 'Confirmed third')
        self.assertEqual(self.publish([1, 2, 3], updates=[1], metadata=True).status_code, 200)
        detail = self.guest.get(root).json
        self.assertEqual(detail['episodes'][0]['text'], 'Revised first')
        self.assertEqual(detail['synopsis'], 'New introduction')
        self.assertEqual(self.publish([1, 3]).status_code, 200)
        self.assertEqual([ep['number'] for ep in self.guest.get(root).json['episodes']], [1, 3])
        self.assertEqual(self.publish([]).status_code, 200)
        self.assertEqual(self.guest.get(root).status_code, 404)
        self.assertEqual(self.guest.get('/api/discover/works').json['works'], [])
        self.assertEqual(self.other.get('/api/discover/liked').json['works'], [])
        self.assertEqual(self.other.post(root + '/like', json={'liked': True}).status_code, 404)
        self.assertEqual(self.other.post(root + '/episodes/1/view', json={}).status_code, 404)
        self.assertFalse(self.source()['publication']['active'])
        self.assertEqual(self.publish([1, 3]).json['id'], public_id)
        detail = self.guest.get(root).json
        self.assertEqual((detail['likes'], detail['views']), (1, 1))
        self.assertEqual(detail['comments'][0]['text'], 'Reader comment')
        self.assertEqual(len(self.other.get('/api/discover/liked').json['works']), 1)
        with closing(sqlite3.connect(self.database)) as con:
            self.assertEqual(con.execute('SELECT state FROM projects WHERE id=?', (self.pid,)).fetchone()[0],
                             expected_state)

    def test_owner_validation_and_stale_public_snapshot(self):
        self.assertEqual(self.publish([3]).status_code, 400)
        self.assertEqual(self.publish([1], client=self.other).status_code, 400)
        self.assertEqual(self.guest.get('/api/discover/publication/' + self.pid).status_code, 401)
        self.assertEqual(self.other.get('/api/discover/publication/' + self.pid).status_code, 400)
        self.assertEqual(self.publish([1]).status_code, 200)
        stale = self.source()
        self.assertEqual(self.publish([1, 2]).status_code, 200)
        self.assertEqual(self.publish([], source=stale).status_code, 409)
        root = '/api/discover/works/' + self.source()['publication']['id']
        self.assertEqual(len(self.guest.get(root).json['episodes']), 2)


if __name__ == '__main__':
    unittest.main()
