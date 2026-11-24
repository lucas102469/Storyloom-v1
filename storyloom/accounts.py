"""Registration, account identity, sessions and nickname changes."""
from .common import check_password_hash, generate_password_hash, jsonify, request, session, uuid
from .context import ApplicationContext


def install(ctx: ApplicationContext):
    @ctx.app.get('/api/auth/session')
    def auth_session():
        with ctx.db() as con:
            user = con.execute('SELECT username,nickname FROM users WHERE id=?', (session.get('uid'),)).fetchone()
        return jsonify(required=ctx.use_auth, logged_in=bool(user) or not ctx.use_auth,
                       username=user['username'] if user else None,
                       nickname=user['nickname'] if user else None)
    ctx.auth_session = auth_session

    def account_nickname(value):
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 20 or any(ord(c) < 32 for c in value):
            raise ValueError('닉네임은 1~20자로 입력하세요.')
        return value.strip()
    ctx.account_nickname = account_nickname

    @ctx.app.post('/api/profile/nickname')
    def update_nickname():
        nickname = ctx.account_nickname(request.json.get('nickname'))
        with ctx.db() as con:
            con.execute('BEGIN IMMEDIATE')
            if con.execute('SELECT 1 FROM users WHERE nickname=? COLLATE NOCASE AND id<>?',
                           (nickname, session.get('uid'))).fetchone():
                raise ValueError('이미 사용 중인 닉네임입니다.')
            changed = con.execute('UPDATE users SET nickname=? WHERE id=?', (nickname, session.get('uid'))).rowcount
        if not changed:
            return jsonify(error='로그인해주세요.'), 401
        return jsonify(nickname=nickname)
    ctx.update_nickname = update_nickname

    @ctx.app.post('/api/auth/register')
    def register():
        data = request.json
        username, nickname = data.get('username'), data.get('nickname')
        password, confirmation = data.get('password'), data.get('password_confirm')
        if not isinstance(username, str) or not 4 <= len(username.strip()) <= 50 or not username.strip().isprintable():
            raise ValueError('아이디는 4~50자로 입력하세요.')
        username = username.strip().lower()
        nickname = ctx.account_nickname(nickname)
        if not isinstance(password, str) or not 8 <= len(password) <= 200:
            raise ValueError('비밀번호는 8~200자로 입력하세요.')
        if password != confirmation:
            raise ValueError('비밀번호가 서로 일치하지 않습니다.')
        uid = str(uuid.uuid4())
        with ctx.db() as con:
            con.execute('BEGIN IMMEDIATE')
            if con.execute('SELECT 1 FROM users WHERE username=?', (username,)).fetchone():
                raise ValueError('이미 사용 중인 아이디입니다.')
            if con.execute('SELECT 1 FROM users WHERE nickname=? COLLATE NOCASE', (nickname,)).fetchone():
                raise ValueError('이미 사용 중인 닉네임입니다.')
            first = not con.execute('SELECT 1 FROM users').fetchone()
            con.execute('INSERT INTO users(id,username,nickname,password) VALUES(?,?,?,?)',
                        (uid, username, nickname, generate_password_hash(password)))
            if first:
                con.execute('UPDATE projects SET owner=? WHERE owner IS NULL', (uid,))
        session.clear()
        session.update(uid=uid)
        return jsonify(ok=True)
    ctx.register = register

    @ctx.app.post('/api/auth/login')
    def login():
        username, password = request.json.get('username'), request.json.get('password')
        if not isinstance(username, str) or not 1 <= len(username.strip()) <= 50 or not username.strip().isprintable() or not isinstance(password, str) or len(password) > 200:
            raise ValueError('아이디와 비밀번호를 확인하세요.')
        with ctx.db() as con:
            user = con.execute('SELECT * FROM users WHERE username=?', (username.strip().lower(),)).fetchone()
        if not user or not check_password_hash(user['password'], password):
            return jsonify(error='아이디 또는 비밀번호를 확인하세요.'), 401
        session.clear()
        session.update(uid=user['id'])
        return jsonify(ok=True)
    ctx.login = login

    @ctx.app.post('/api/auth/logout')
    def logout():
        session.clear()
        return jsonify(ok=True)
    ctx.logout = logout

