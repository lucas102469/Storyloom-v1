"""Gemini dispatch, KST accounting and atomic quota reservations."""
from .common import AIUsageLimit, datetime, gemini, has_request_context, jsonify, session, time, timedelta, timezone
from .context import ApplicationContext


def install(ctx: ApplicationContext):
    def provider_generate(prompt, task='chat', before_request=None):
        if ctx.ai is not None:
            before_request()
            return ctx.ai(prompt)
        callback = getattr(ctx.job_context, 'callback', None)
        last = [0.0]
        def receive(text, count):
            if time.monotonic() - last[0] >= .5:
                callback(task, text, count)
                last[0] = time.monotonic()
        return ctx.compat.gemini(prompt, task=task, on_progress=receive if callback and ctx.app.config['AI_STREAMING'] else None,
                      before_request=before_request)
    ctx.provider_generate = provider_generate

    def usage_day():
        now = ctx.compat.datetime.now(timezone(timedelta(hours=9)))
        return now.date().isoformat(), (now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).isoformat()
    ctx.usage_day = usage_day

    def usage_summary(con):
        day, reset = ctx.usage_day()
        used = con.execute('SELECT count(*) FROM api_usage WHERE day=?', (day,)).fetchone()[0]
        owner = session.get('uid') or 'local' if has_request_context() else 'local'
        own = con.execute('SELECT count(*) FROM api_usage WHERE day=? AND owner=?', (day, owner)).fetchone()[0]
        total_limit, user_limit = ctx.app.config['TOTAL_DAILY_LIMIT'], ctx.app.config['USER_DAILY_LIMIT']
        total_remaining, personal_remaining = max(0,total_limit-used), max(0,user_limit-own)
        return dict(used=used, personal_used=own, limit=total_limit,
                    remaining=min(total_remaining,personal_remaining), unlimited=False, resets_at=reset,
                    personal_limit=user_limit, personal_remaining=personal_remaining,
                    total_used=used, total_limit=total_limit, total_remaining=total_remaining,
                    scope='이 앱 전체 · 한국시간 하루', unit='호출 시도', provider_quota=False)
    ctx.usage_summary = usage_summary

    def generate_attempt(prompt, task='chat'):
        # One shared row reserves both limits immediately before SDK dispatch.
        # Sent requests that fail or time out remain counted, as before.
        call_id = None
        def reserve():
            nonlocal call_id
            owner = getattr(ctx.job_context, 'owner', None) or (session.get('uid') if has_request_context() else None) or 'local'
            with ctx.db() as con:
                con.execute('BEGIN IMMEDIATE')
                day, _ = ctx.usage_day()
                used = con.execute('SELECT count(*) FROM api_usage WHERE day=?',(day,)).fetchone()[0]
                own = con.execute('SELECT count(*) FROM api_usage WHERE day=? AND owner=?',(day,owner)).fetchone()[0]
                if used >= ctx.app.config['TOTAL_DAILY_LIMIT']:
                    raise AIUsageLimit('total')
                if own >= ctx.app.config['USER_DAILY_LIMIT']:
                    raise AIUsageLimit('user')
                call_id = con.execute('INSERT INTO api_usage(day,owner,task,status,created) VALUES(?,?,?,?,?)',
                                      (day,owner,task,'started',time.time())).lastrowid
            ctx.job_context.calls = getattr(ctx.job_context,'calls',0) + 1
        try:
            result = ctx.provider_generate(prompt, task, before_request=reserve)
        except BaseException:
            if call_id is not None:
                with ctx.db() as con:
                    con.execute("UPDATE api_usage SET status='failed' WHERE id=?", (call_id,))
            raise
        else:
            if call_id is not None:
                with ctx.db() as con:
                    con.execute("UPDATE api_usage SET status='succeeded' WHERE id=?", (call_id,))
            return result
    ctx.generate_attempt = generate_attempt

    @ctx.app.get('/api/usage')
    def usage_status():
        with ctx.db() as con:
            return jsonify(ctx.usage_summary(con))
    ctx.usage_status = usage_status

