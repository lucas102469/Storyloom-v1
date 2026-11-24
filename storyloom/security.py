"""Local-host, origin, authentication gates and error responses."""
from .common import AIUsageLimit, HTTPException, jsonify, request, session, urlparse
from .context import ApplicationContext


def install(ctx: ApplicationContext):
    @ctx.app.before_request
    def local_requests():
        # Version history can make a valid exported backup larger than an edit request.
        if request.path == '/api/projects/import':
            request.max_content_length = 32 * 1024 * 1024
        if request.host.split(':')[0].lower() not in ctx.app.config['STORYLOOM_ALLOWED_HOSTS']:
            return jsonify(error='로컬 주소로 접속하세요.'), 403
        if request.method == 'POST':
            origin = request.headers.get('Origin')
            same_origin = bool(origin and urlparse(origin).netloc == request.host)
            permitted_form = same_origin and (
                (request.endpoint == 'export_project' and request.mimetype == 'application/x-www-form-urlencoded')
                or (request.endpoint == 'import_backup' and request.mimetype == 'multipart/form-data')
            )
            if (origin and not same_origin) or (not request.is_json and not permitted_form):
                return jsonify(error='허용되지 않은 요청입니다.'), 403
            if request.is_json and not isinstance(request.get_json(), dict):
                return jsonify(error='요청은 JSON 객체여야 합니다.'), 400
        public_catalog = (request.method in ('GET', 'HEAD') and request.endpoint in ('published_works', 'published_work')
                          or request.endpoint in ('record_public_view', 'save_public_reading') and request.method == 'POST')
        if ctx.use_auth and request.path.startswith('/api/') and not request.path.startswith('/api/auth/') and not public_catalog:
            with ctx.db() as con:
                user_exists = con.execute('SELECT 1 FROM users WHERE id=?', (session.get('uid'),)).fetchone()
            if not user_exists:
                session.clear()
                return jsonify(error='로그인해주세요.'), 401
    ctx.local_requests = local_requests

    @ctx.app.errorhandler(AIUsageLimit)
    def usage_limit_error(exc):
        return jsonify(error=str(exc), code='ai_usage_limit', scope=exc.scope), 429
    ctx.usage_limit_error = usage_limit_error

    @ctx.app.errorhandler(ValueError)
    def invalid(exc):
        return jsonify(error=str(exc)), 400
    ctx.invalid = invalid

    @ctx.app.errorhandler(HTTPException)
    def http_error(exc):
        return jsonify(error=exc.description), exc.code
    ctx.http_error = http_error

    @ctx.app.errorhandler(KeyError)
    @ctx.app.errorhandler(TypeError)
    def bad_input(exc):
        return jsonify(error='필수 입력 또는 자료 형식을 확인하세요.'), 400
    ctx.bad_input = bad_input

