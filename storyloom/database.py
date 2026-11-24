"""SQLite connections and unchanged schema initialization."""
from .common import contextmanager, re, sqlite3, time
from .context import ApplicationContext


def install(ctx: ApplicationContext):
    @contextmanager
    def db():
        con = sqlite3.connect(ctx.db_path, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys=ON')
        try:
            with con:
                yield con
        finally:
            con.close()
    ctx.db = db

    with ctx.db() as con:
        con.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,title TEXT NOT NULL,state TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS versions(id INTEGER PRIMARY KEY,project TEXT NOT NULL,kind TEXT NOT NULL,episode INTEGER NOT NULL,content TEXT NOT NULL,status TEXT NOT NULL,created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,project TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL,result TEXT,error TEXT,created REAL NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS one_running ON jobs(project) WHERE status='running';
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,username TEXT UNIQUE NOT NULL,nickname TEXT NOT NULL,password TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS published_works(
            id TEXT PRIMARY KEY, project TEXT NOT NULL UNIQUE, metadata TEXT NOT NULL,
            episodes TEXT NOT NULL, updated REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS public_work_likes(
            work TEXT NOT NULL REFERENCES published_works(id), owner TEXT NOT NULL,
            PRIMARY KEY(work,owner)
        );
        CREATE TABLE IF NOT EXISTS public_episode_views(
            work TEXT NOT NULL REFERENCES published_works(id), episode INTEGER NOT NULL,
            viewer TEXT NOT NULL, day TEXT NOT NULL,
            PRIMARY KEY(work,episode,viewer,day)
        );
        CREATE TABLE IF NOT EXISTS public_work_comments(
            id INTEGER PRIMARY KEY, work TEXT NOT NULL REFERENCES published_works(id),
            owner TEXT NOT NULL, episode INTEGER, text TEXT NOT NULL, created REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS public_comments_work ON public_work_comments(work,id DESC);
        CREATE TABLE IF NOT EXISTS public_reading_progress(
            work TEXT NOT NULL REFERENCES published_works(id), viewer TEXT NOT NULL,
            position TEXT NOT NULL, updated REAL NOT NULL, PRIMARY KEY(work,viewer)
        );
        CREATE TABLE IF NOT EXISTS api_usage(id INTEGER PRIMARY KEY,day TEXT NOT NULL,owner TEXT NOT NULL,task TEXT NOT NULL,status TEXT NOT NULL,created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS episode_setting_snapshots(
            project TEXT NOT NULL, episode INTEGER NOT NULL, version INTEGER NOT NULL,
            payload TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(project,episode,version)
        );
        CREATE TABLE IF NOT EXISTS published_episode_settings(
            work TEXT NOT NULL, episode INTEGER NOT NULL, body TEXT NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY(work,episode)
        );
        CREATE INDEX IF NOT EXISTS api_usage_day ON api_usage(day);
        CREATE INDEX IF NOT EXISTS api_usage_day_owner ON api_usage(day, owner);
        CREATE INDEX IF NOT EXISTS jobs_project_recent ON jobs(project, created DESC);
        CREATE INDEX IF NOT EXISTS jobs_running_project ON jobs(project) WHERE status='running';
        CREATE INDEX IF NOT EXISTS versions_project_recent ON versions(project, created DESC, id DESC);
        CREATE INDEX IF NOT EXISTS versions_project_kind_recent ON versions(project, kind, created DESC, id DESC);
        ''')
        publication_columns = {r['name'] for r in con.execute('PRAGMA table_info(published_works)')}
        if 'active' not in publication_columns:
            con.execute('ALTER TABLE published_works ADD COLUMN active INTEGER NOT NULL DEFAULT 1')
        columns = {r['name'] for r in con.execute('PRAGMA table_info(projects)')}
        if 'owner' not in columns:
            con.execute('ALTER TABLE projects ADD COLUMN owner TEXT')
        job_columns = {r['name'] for r in con.execute('PRAGMA table_info(jobs)')}
        for column in ('progress', 'checkpoint', 'partial', 'run_token', 'lease'):
            if column not in job_columns:
                con.execute(f'ALTER TABLE jobs ADD COLUMN {column} TEXT')
        con.execute('BEGIN IMMEDIATE')
        user_columns = {r['name'] for r in con.execute('PRAGMA table_info(users)')}
        if 'email' in user_columns:
            # One-time migration: remove email from the account schema entirely.
            # Preserve primary IDs, password hashes and project ownership.
            legacy = con.execute('SELECT * FROM users ORDER BY rowid').fetchall()
            con.execute('CREATE TABLE users_next(id TEXT PRIMARY KEY,username TEXT UNIQUE NOT NULL,nickname TEXT NOT NULL,password TEXT NOT NULL)')
            assigned = set()
            for user in legacy:
                previous = user['username'] if 'username' in user_columns else ''
                prefix = user['email'].split('@')[0]
                base = previous if previous and re.fullmatch(r'[a-z0-9_-]{3,30}', previous) else re.sub(r'[^a-z0-9_-]', '_', prefix.lower())[:30]
                if len(base) < 3:
                    base = 'writer_' + base
                username, number = base, 2
                while username in assigned:
                    suffix = '_' + str(number)
                    username = base[:30-len(suffix)] + suffix
                    number += 1
                assigned.add(username)
                nickname = user['nickname'] if 'nickname' in user_columns and user['nickname'] else prefix
                con.execute('INSERT INTO users_next VALUES(?,?,?,?)', (user['id'], username, nickname[:20] or '작가', user['password']))
            con.execute('DROP TABLE users')
            con.execute('ALTER TABLE users_next RENAME TO users')
        # A worker cannot survive process restart. Keep saved drafts and allow retry.
        con.execute("UPDATE jobs SET status='failed',error='중단된 작업입니다. 저장된 장면부터 재시도하세요.' WHERE status='running' AND (lease IS NULL OR CAST(lease AS REAL)<?)", (time.time()-90,))

