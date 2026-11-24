"""Account input and migration checks using disposable databases only."""
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path
from werkzeug.security import generate_password_hash
from app import create_app


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = str(Path(self.folder.name) / 'accounts.sqlite3')
        self.app = create_app(self.path, lambda _: {}, auth_required=True)
        self.client = self.app.test_client()
        self.data = dict(username='Writer_01', nickname='첫 작가',
                         password='Testing123!', password_confirm='Testing123!')

    def tearDown(self):
        self.app.extensions['storyloom_workers'].shutdown(wait=True)
        self.folder.cleanup()

    def test_signup_login_and_session_without_email(self):
        self.assertEqual(self.client.post('/api/auth/register', json=self.data).status_code, 200)
        session = self.client.get('/api/auth/session').json
        self.assertEqual((session['username'], session['nickname']), ('writer_01', '첫 작가'))
        self.assertNotIn('email', session)
        self.client.post('/api/auth/logout', json={})
        self.assertEqual(self.client.post('/api/auth/login', json={
            'username': 'WRITER_01', 'password': 'Testing123!'}).status_code, 200)

    def test_nickname_update_persists_without_changing_login(self):
        path = '/api/profile/nickname'
        self.assertEqual(self.client.post(path, json={'nickname': '새 작가'}).status_code, 401)
        self.client.post('/api/auth/register', json=self.data)
        response = self.client.post(path, json={'nickname': '  새 작가  '})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['nickname'], '새 작가')
        self.assertEqual(self.client.post(path, json={'nickname': ''}).status_code, 400)
        self.client.post('/api/auth/logout', json={})
        self.assertEqual(self.client.post('/api/auth/login', json={
            'username': 'writer_01', 'password': self.data['password']}).status_code, 200)
        session = self.client.get('/api/auth/session').json
        self.assertEqual((session['username'], session['nickname']), ('writer_01', '새 작가'))

    def test_invalid_signup_does_not_create_user(self):
        for overrides in ({'password_confirm': 'different'}, {'password_confirm': None},
                          {'nickname': ''}, {'nickname': 123}, {'username': ''},
                          {'username': ' '*3}, {'username': 'abc'}, {'username': 'x'*51}, {'password': 'short'}):
            with self.subTest(overrides=overrides):
                response = self.client.post('/api/auth/register', json={**self.data, **overrides})
                self.assertEqual(response.status_code, 400)
        with closing(sqlite3.connect(self.path)) as con, con:
            self.assertEqual(con.execute('SELECT count(*) FROM users').fetchone()[0], 0)

    def test_ids_allow_korean_symbols_and_optional_at_sign(self):
        for username in ('작가님이', 'write@home.test', '나의 작가!'):
            with self.subTest(username=username):
                self.assertEqual(self.client.post('/api/auth/register', json={
                    **self.data, 'username': username, 'nickname': username[:20]}).status_code, 200)
                self.client.post('/api/auth/logout', json={})
                self.assertEqual(self.client.post('/api/auth/login', json={
                    'username': username, 'password': self.data['password']}).status_code, 200)

    def test_deleted_account_session_cannot_access_works(self):
        self.client.post('/api/auth/register', json=self.data)
        pid = self.client.post('/api/projects', json={'title': '보존'}).json['id']
        with closing(sqlite3.connect(self.path)) as con, con:
            con.execute('DELETE FROM users')
            con.execute('UPDATE projects SET owner=NULL')
        self.assertEqual(self.client.get('/api/projects/' + pid).status_code, 401)
        self.client.post('/api/auth/register', json={**self.data, 'username': 'newwriter'})
        self.assertEqual(self.client.get('/api/projects/' + pid).status_code, 200)

    def test_duplicate_id_preserves_original_account_and_password_is_hashed(self):
        self.client.post('/api/auth/register', json=self.data)
        self.assertEqual(self.client.post('/api/auth/register', json={
            **self.data, 'username': 'writer_01'}).status_code, 400)
        with closing(sqlite3.connect(self.path)) as con, con:
            password = con.execute('SELECT password FROM users').fetchone()[0]
            self.assertNotEqual(password, self.data['password'])

    def test_single_character_nickname_and_duplicate_protection(self):
        self.assertEqual(self.client.post('/api/auth/register', json={
            **self.data, 'username': 'abcd', 'nickname': '봄',
            'password': '12345678', 'password_confirm': '12345678'}).status_code, 200)
        duplicate = self.client.post('/api/auth/register', json={
            **self.data, 'username': 'other', 'nickname': ' 봄 '})
        self.assertEqual(duplicate.status_code, 400)
        self.assertIn('닉네임', duplicate.json['error'])
        self.assertEqual(self.client.post('/api/auth/register', json={
            **self.data, 'username': 'ABCD', 'nickname': '달'}).status_code, 400)
        self.assertEqual(self.client.post('/api/auth/register', json={
            **self.data, 'username': 'other', 'nickname': '달'}).status_code, 200)
        self.assertEqual(self.client.post('/api/profile/nickname', json={'nickname': '봄'}).status_code, 400)
        self.assertEqual(self.client.post('/api/profile/nickname', json={'nickname': '달'}).status_code, 200)
        self.assertEqual(self.client.post('/api/profile/nickname', json={'nickname': '별'}).status_code, 200)
        with closing(sqlite3.connect(self.path)) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM users').fetchone()[0], 2)

    def test_legacy_email_migration_keeps_identity_password_and_works(self):
        # Simulate the exact old users schema; all files live in the test folder.
        self.client.post('/api/auth/register', json=self.data)
        pid = self.client.post('/api/projects', json={'title': '보존할 작품'}).json['id']
        with closing(sqlite3.connect(self.path)) as con, con:
            con.execute("UPDATE projects SET owner='legacy-user' WHERE id=?", (pid,))
            con.execute('DROP TABLE users')
            con.execute('CREATE TABLE users(id TEXT PRIMARY KEY,email TEXT UNIQUE NOT NULL,password TEXT NOT NULL)')
            con.execute('INSERT INTO users VALUES(?,?,?)',
                        ('legacy-user', 'old@example.test', generate_password_hash('OldPass123!')))
        migrated = create_app(self.path, lambda _: {}, auth_required=True)
        try:
            client = migrated.test_client()
            self.assertEqual(client.post('/api/auth/login', json={
                'username': 'old@example.test', 'password': 'OldPass123!'}).status_code, 401)
            self.assertEqual(client.post('/api/auth/login', json={
                'username': 'old', 'password': 'OldPass123!'}).status_code, 200)
            self.assertEqual(client.get('/api/projects/' + pid).status_code, 200)
            with closing(sqlite3.connect(self.path)) as con, con:
                self.assertEqual(con.execute('SELECT id FROM users').fetchone()[0], 'legacy-user')
                self.assertNotIn('email', [row[1] for row in con.execute('PRAGMA table_info(users)')])
                self.assertEqual(con.execute('SELECT owner FROM projects WHERE id=?', (pid,)).fetchone()[0], 'legacy-user')
        finally:
            migrated.extensions['storyloom_workers'].shutdown(wait=True)
