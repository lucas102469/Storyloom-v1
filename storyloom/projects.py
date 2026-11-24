"""Library, project lifecycle, view selection and search."""
from .common import DEFAULT_SETTINGS, KINDS, REFERENCE_DOCUMENTS, display_project_title, json, jsonify, re, replace_linked_names, request, session, setting_edit_source, uuid
from .context import ApplicationContext


def install(ctx: ApplicationContext):
    @ctx.app.get('/api/projects')
    def projects():
        with ctx.db() as con:
            rows = con.execute('SELECT p.*,(SELECT MAX(created) FROM versions v WHERE v.project=p.id) AS updated FROM projects p WHERE (?=0 OR owner=?) ORDER BY p.rowid DESC', (int(ctx.use_auth), session.get('uid')))
            result = []
            for row in rows:
                state = json.loads(row['state'])
                original_row = row
                row = ctx.repair_alternative_presentation(con, row, state)
                plan = state['drafts'].get('plan:0') or state['official'].get('plan:0') or {}
                plan_text = plan.get('text', '')
                def plan_field(label):
                    match = re.search(r'(?m)^\s*(?:[-*]\s*)?(?:\*\*)?' + label + r'(?:\*\*)?\s*[:：]\s*(.+)$', plan_text)
                    if not match:
                        match = re.search(r'(?m)^\s*\|\s*' + label + r'\s*\|\s*([^|]+)', plan_text.replace('**', ''))
                    if not match:
                        match = re.search(r'(?m)^\s*#{1,6}\s*' + label + r'\s*\n\s*([^\n#]+)', plan_text.replace('**', ''))
                    return match.group(1).replace('**', '').strip() if match else ''
                genre = plan_field('장르')
                keywords = plan_field('(?:키워드|소재[·/ ]?키워드)')
                tags = list(dict.fromkeys(t.strip().lstrip('#') for t in re.split(r'[,，#·]', keywords) if t.strip()))[:20]
                synopsis = re.search(r'(?ms)^\s*#{1,6}\s*시놉시스\s*\n(.+?)(?=^\s*#{1,6}\s|\Z)', plan_text)
                description = plan_field('로그라인') or (synopsis.group(1).strip() if synopsis else '')
                if not description and not re.search(r'(?m)^\s*#', plan_text):
                    description = plan_text.replace('**', '').strip()
                episodes = [int(key.split(':')[1]) for key in set(state['drafts']) | set(state['official']) if key.startswith('episode:')]
                current = max(episodes) if episodes else None
                pending = state['drafts'].get(f'episode:{current}') if current else None
                result.append({'id': row['id'], 'title': display_project_title(row['title']), 'archived': state.get('archived', False),
                               'origin': state.get('branch_origin'),
                               'description': description[:200], 'genre': genre, 'tags': tags,
                               'stage': '집필중' if episodes else '기획중',
                               'current_work': f'{current}화 '+({'draft': '초안 작성중', 'review': '수정안 검토중'}.get(pending['status'], '초안 작성중') if pending else '확정') if current else '기획 정리중',
                               'updated': original_row['updated']})
            return jsonify(result)
    ctx.projects = projects

    @ctx.app.post('/api/projects')
    def new_project():
        title = request.json.get('title', '')
        if not isinstance(title, str):
            raise ValueError('작품 제목은 텍스트로 입력하세요.')
        title = title.strip()[:120]
        if not title:
            raise ValueError('작품 제목을 입력하세요.')
        pid = str(uuid.uuid4())
        state = {'messages': [{'role': 'assistant', 'text': '어떤 이야기를 함께 쓸까요? 떠오르는 아이디어, 장르나 주인공부터 편하게 알려주세요.'}],
                 'official': {}, 'drafts': {}, 'selected': 'plan', 'episode': 1, 'settings': dict(DEFAULT_SETTINGS), 'archived': False}
        if request.json.get('defer_length_setup') is True:
            state['length_setup_pending'] = True
            state['settings']['total_episodes'] = None
        with ctx.db() as con:
            con.execute('INSERT INTO projects(id,title,state,owner) VALUES(?,?,?,?)', (pid, title, json.dumps(state, ensure_ascii=False), session.get('uid')))
        return jsonify(id=pid)
    ctx.new_project = new_project

    @ctx.app.get('/api/projects/<pid>')
    def get_project(pid):
        with ctx.db() as con:
            row, state = ctx.load(con, pid)
            ctx.recover_stale(con)
            jobs = [dict(r) for r in con.execute('SELECT id,status,error,created,progress,payload FROM jobs WHERE project=? ORDER BY created DESC LIMIT 5', (pid,))]
            for job in jobs:
                payload = job.pop('payload')
                if job['status'] != 'failed':
                    continue
                operation = json.loads(payload).get('operation') or {}
                if operation.get('type') == 'resolve_continuity':
                    episode = operation['key'].split(':')[1]
                    if not state.get('records', {}).get(episode, {}).get('continuity_issues'):
                        job.update(status='done', error=None, progress='일관성 확인 완료')
            names = state.get('linked_names', {})
            plot_events = {}
            if names:
                for key, item in state['official'].items():
                    if not key.startswith('episode:') and key != 'guidelines:0':
                        continue
                    episode = key.split(':')[1]
                    facts = [entry['fact'] for entry in state.get('records', {}).get(episode, {}).get('findings', [])
                             if entry['kind'] == 'plot' and not entry.get('reason') and not entry.get('removed')]
                    event = ' · '.join(facts) or (item.get('summary', '') if not item.get('metadata_stale') else '')
                    if event:
                        plot_events[episode] = replace_linked_names(event, names)
            return jsonify(id=pid, title=display_project_title(row['title']), revision=row['revision'], state=state, jobs=jobs,
                           plot_events=plot_events, setting_edit_source=({
                               'key':state['selected']+':0', **setting_edit_source(state,state['selected']+':0')}
                               if state['selected'] in ('characters','world','places','lore') else None))
    ctx.get_project = get_project

    @ctx.app.get('/api/projects/<pid>/setting-history')
    def setting_history(pid):
        with ctx.db() as con:
            _, state=ctx.load(con,pid)
            requested=request.args.get('episode')
            episodes={int(key.split(':')[1]):record for key,record in state['official'].items() if key.startswith('episode:')}
            initial=con.execute('SELECT version FROM episode_setting_snapshots WHERE project=? AND episode=0 ORDER BY created LIMIT 1',(pid,)).fetchone()
            versions={row['episode']:row['version'] for row in con.execute(
                'SELECT s.episode,s.version FROM episode_setting_snapshots s WHERE s.project=?',(pid,))
                if row['episode'] in episodes and row['version']==episodes[row['episode']]['version']}
            options=[{'episode':0,'label':'초기 설정','available':bool(initial)}]+[
                {'episode':ep,'label':f'{ep}화 설정','available':ep in versions} for ep in sorted(episodes)]
            if requested is None:
                return jsonify(options=options)
            if not requested.isdigit() or not 0<=int(requested)<=10000:
                raise ValueError('설정 기록의 회차가 올바르지 않습니다.')
            ep=int(requested)
            version_id=initial['version'] if ep==0 and initial else versions.get(ep)
            saved=con.execute('SELECT payload FROM episode_setting_snapshots WHERE project=? AND episode=? AND version=?',
                (pid,ep,version_id)).fetchone() if version_id else None
            if not saved:
                return jsonify(options=options,available=False,episode=ep)
            payload=json.loads(saved['payload'])
            return jsonify(options=options,available=True,episode=ep,version=version_id,
                official={key:value for key,value in payload['official'].items() if key.split(':')[0] in REFERENCE_DOCUMENTS},
                records=payload.get('records',{}))
    ctx.setting_history = setting_history

    @ctx.app.post('/api/projects/<pid>/view')
    def view(pid):
        with ctx.db() as con:
            con.execute('BEGIN IMMEDIATE')
            _, state = ctx.load(con, pid)
            selected = request.json.get('selected', 'plan')
            if selected not in KINDS:
                raise ValueError('잘못된 화면입니다.')
            episode = request.json.get('episode', 1)
            if isinstance(episode, bool) or not isinstance(episode, int) or not 1 <= episode <= 10000:
                raise ValueError('회차 범위를 확인하세요.')
            state.update(selected=selected, episode=episode)
            ctx.save(con, pid, state)
        return jsonify(ok=True)
    ctx.view = view

    @ctx.app.post('/api/projects/<pid>/manage')
    def manage_project(pid):
        with ctx.db() as con:
            con.execute('BEGIN IMMEDIATE')
            row, state = ctx.load(con, pid)
            title = str(request.json.get('title', row['title'])).strip()[:120]
            if not title:
                raise ValueError('작품 제목을 입력하세요.')
            if 'archived' in request.json:
                if not isinstance(request.json['archived'], bool):
                    raise ValueError('보관 상태를 확인하세요.')
                if con.execute("SELECT 1 FROM jobs WHERE project=? AND status='running'", (pid,)).fetchone():
                    raise ValueError('생성 완료 또는 취소 후 휴지통으로 이동하세요.')
                state['archived'] = request.json['archived']
            con.execute('UPDATE projects SET title=? WHERE id=?', (title, pid))
            ctx.save(con, pid, state)
        return jsonify(ok=True)
    ctx.manage_project = manage_project

    @ctx.app.get('/api/projects/<pid>/search')
    def search_project(pid):
        query = request.args.get('q', '').strip()
        if not 1 <= len(query) <= 100:
            raise ValueError('검색어를 1~100자로 입력하세요.')
        with ctx.db() as con:
            _, state = ctx.load(con, pid)
            matches = []
            for group in ('official', 'drafts'):
                for key, record in state[group].items():
                    text = record['text']
                    index = text.casefold().find(query.casefold())
                    if index >= 0:
                        matches.append({'group': group, 'key': key, 'excerpt': text[max(0, index-60):index+140]})
            return jsonify(matches)
    ctx.search_project = search_project

