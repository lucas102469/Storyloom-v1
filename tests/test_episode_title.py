import copy
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from app import create_app


class EpisodeTitleTests(unittest.TestCase):
    def test_title_preserves_manuscript_status_and_review_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            database = str(Path(folder) / 'title.sqlite3')
            app = create_app(database, lambda _: {}, auth_required=False)
            client = app.test_client()
            try:
                pid = client.post('/api/projects', json={'title': 'Title test'}).json['id']
                for group, status in (('official', 'confirmed'), ('drafts', 'draft'), ('drafts', 'review')):
                    with self.subTest(status=status):
                        with closing(sqlite3.connect(database)) as con, con:
                            state = json.loads(con.execute('SELECT state FROM projects WHERE id=?', (pid,)).fetchone()[0])
                            content = {'text': 'Unchanged body', 'title': 'Before', 'summary': 'Summary',
                                       'facts': 'Facts', 'findings': [], 'metadata_stale': False}
                            vid = con.execute('INSERT INTO versions(project,kind,episode,content,status,created) VALUES(?,?,?,?,?,0)',
                                              (pid, 'episode', 1, json.dumps(content), status)).lastrowid
                            state['official'] = {}
                            state['drafts'] = {}
                            state[group]['episode:1'] = {**content, 'version': vid, 'status': status}
                            state['records'] = {'1': {'version': vid, 'setting_review': 'done'}}
                            expected = copy.deepcopy(state)
                            expected[group]['episode:1']['title'] = 'After'
                            con.execute('UPDATE projects SET state=? WHERE id=?', (json.dumps(state), pid))
                        response = client.post(f'/api/projects/{pid}/edit', json={
                            'key': 'episode:1', 'text': content['text'], 'title': 'After', 'base_version': vid})
                        self.assertEqual(response.status_code, 200, response.json)
                        with closing(sqlite3.connect(database)) as con:
                            actual = json.loads(con.execute('SELECT state FROM projects WHERE id=?', (pid,)).fetchone()[0])
                            self.assertEqual(actual[group]['episode:1'], expected[group]['episode:1'])
                            self.assertEqual(actual['drafts'], expected['drafts'])
                            self.assertEqual(actual['records'], expected['records'])
                            self.assertEqual(json.loads(con.execute('SELECT content FROM versions WHERE id=?', (vid,)).fetchone()[0])['title'], 'After')
            finally:
                app.extensions['storyloom_workers'].shutdown(wait=True)
