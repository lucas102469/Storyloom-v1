"""Storyloom entry point and application initialization."""
import sys
from storyloom.common import (
    AIUsageLimit,
    AI_MANUSCRIPT_LENGTH_MARGIN,
    CHARACTER_ROLE_STYLE,
    CONTINUITY_CHECK,
    CONTINUITY_RULES,
    DEFAULT_SETTINGS,
    Flask,
    GeminiUnavailable,
    HTTPException,
    KINDS,
    Path,
    REFERENCE_DESCRIPTION_STYLE,
    REFERENCE_DOCUMENTS,
    Response,
    RetryableAIError,
    SETTING_FINDING_FIELDS,
    ThreadPoolExecutor,
    adjust_revision_length,
    apply_patches,
    apply_revision_patches,
    branch_edit,
    branch_pending_start,
    branch_setup,
    build_context,
    build_episode_context,
    check_password_hash,
    classify_document_updates,
    compare_text,
    complete_reference_descriptions,
    confirmed_character_aliases,
    contextmanager,
    daily_limit,
    datetime,
    difflib,
    display_project_title,
    empty_reference_cards,
    episode_continuity_stamp,
    episode_findings,
    expansion_span,
    fill_reference_descriptions,
    gemini,
    generate_password_hash,
    has_request_context,
    hashlib,
    io,
    is_concrete_place_name,
    is_plan_without_plot,
    json,
    jsonify,
    linked_entity_renames,
    load_dotenv,
    manuscript_style,
    name_only_changes,
    needs_reference_classification,
    normalize_character_layout,
    normalize_legacy_plan,
    normalize_legacy_reference,
    normalize_manuscript_breaks,
    normalize_metadata,
    normalize_plan_heading,
    normalize_reference_topics,
    os,
    parse_key,
    premise_guard,
    re,
    readable_manuscript,
    reference_body,
    render_template,
    repair_reviewed_manuscript,
    repeated_passage_issues,
    replace_linked_names,
    replays_existing_manuscript,
    request,
    restore_review,
    reusable_findings,
    revision_timeline,
    secrets,
    send_file,
    session,
    setting_card_scope,
    setting_edit_source,
    setting_heading_at,
    setting_materialized_facts,
    setting_rename_request,
    setting_requested_target,
    settings_value,
    source_fingerprint,
    sqlite3,
    story_additions,
    threading,
    time,
    timedelta,
    timezone,
    urlparse,
    uuid,
    validate_ai_result,
    zipfile,
)
from storyloom.context import ApplicationContext
from storyloom.database import install as install_database
from storyloom.usage import install as install_usage
from storyloom.security import install as install_security
from storyloom.accounts import install as install_accounts
from storyloom.repository import install as install_repository
from storyloom.pages import install as install_pages
from storyloom.catalog import install as install_catalog
from storyloom.projects import install as install_projects
from storyloom.planning import install as install_planning
from storyloom.revisions import install as install_revisions
from storyloom.generation import install as install_generation
from storyloom.manuscripts import install as install_manuscripts
from storyloom.backups import install as install_backups


def create_app(database=None, ai=None, auth_required=None):
    if ai is None:
        load_dotenv(dotenv_path=Path(__file__).with_name('.env'), override=False)

    app = Flask(__name__)

    app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024

    app.config['AI_STREAMING'] = False

    db_path = database or os.environ.get('STORYLOOM_DB', str(Path(__file__).with_name('storyloom.sqlite3')))

    use_auth = (ai is None) if auth_required is None else auth_required

    secret_path = Path(db_path).with_suffix('.session-key')

    if use_auth:
        try:
            with secret_path.open('x', encoding='ascii') as stream:
                stream.write(secrets.token_hex(32))
        except FileExistsError:
            pass
        app.secret_key = secret_path.read_text(encoding='ascii')
    else:
        app.secret_key = secrets.token_hex(32)

    render_hostname = os.environ.get('RENDER_EXTERNAL_HOSTNAME', '').strip().lower()
    app.config['STORYLOOM_ALLOWED_HOSTS'] = {'127.0.0.1', 'localhost'}
    if render_hostname:
        app.config['STORYLOOM_ALLOWED_HOSTS'].add(render_hostname)
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict',
                      SESSION_COOKIE_SECURE=bool(render_hostname))

    workers = ThreadPoolExecutor(max_workers=2, thread_name_prefix='storyloom')

    active_episode_batches = set()

    active_home_summaries = set()

    home_summary_lock = threading.Lock()

    app.extensions['storyloom_workers'] = workers

    app.config['USER_DAILY_LIMIT'] = daily_limit('USER_DAILY_LIMIT', 60)

    app.config['TOTAL_DAILY_LIMIT'] = daily_limit('TOTAL_DAILY_LIMIT', 400)

    job_context = threading.local()

    ctx = ApplicationContext(app=app, ai=ai, db_path=db_path, use_auth=use_auth, workers=workers, active_episode_batches=active_episode_batches, active_home_summaries=active_home_summaries, home_summary_lock=home_summary_lock, job_context=job_context, compat=sys.modules[__name__])
    install_database(ctx)
    install_usage(ctx)
    install_security(ctx)
    install_accounts(ctx)
    install_repository(ctx)
    install_pages(ctx)
    install_catalog(ctx)
    install_projects(ctx)
    install_planning(ctx)
    install_revisions(ctx)
    install_generation(ctx)
    install_manuscripts(ctx)
    install_backups(ctx)
    return app


if __name__ == '__main__':
    create_app().run(host='127.0.0.1', port=5000, threaded=True)
