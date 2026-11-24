"""Project loading, document versions and setting snapshots."""
from .common import DEFAULT_SETTINGS, REFERENCE_DOCUMENTS, branch_pending_start, branch_setup, confirmed_character_aliases, has_request_context, hashlib, is_concrete_place_name, json, normalize_legacy_plan, normalize_legacy_reference, re, replace_linked_names, session, time
from .context import ApplicationContext


def install(ctx: ApplicationContext):
    def recover_stale(con):
        con.execute("UPDATE jobs SET status='failed',partial=NULL,error='작업 연결이 중단됐습니다. 저장된 장면부터 재시도하세요.' WHERE status='running' AND (lease IS NULL OR CAST(lease AS REAL)<?)", (time.time()-90,))
    ctx.recover_stale = recover_stale

    def normalize_reference_headings(text):
        return re.sub(r'(?m)^(##[ \t]+)(?:#{1,6}[ \t]+)+(?=\S)', r'\1', text)
    ctx.normalize_reference_headings = normalize_reference_headings

    def actual_continuity_issues(issues):
        return [issue for issue in issues if not (
            re.match(r'^\s*(?:(?:설정|일관성)\s+)?충돌(?:이|은)?\s*(?:없음|없습니다|없어요)(?:[.!。]|\s*$)', issue)
            and not re.search(r'(?:다만|그러나|하지만|반면|단[,，])', issue)
        )]
    ctx.actual_continuity_issues = actual_continuity_issues

    def load(con, pid):
        row = con.execute('SELECT * FROM projects WHERE id=?', (pid,)).fetchone()
        if not row:
            raise ValueError('작품을 찾을 수 없습니다.')
        if ctx.use_auth and has_request_context() and row['owner'] != session.get('uid'):
            raise ValueError('작품을 찾을 수 없습니다.')
        state = json.loads(row['state'])
        row = ctx.repair_alternative_presentation(con, row, state)
        batch = state.get('episode_batch', {})
        if batch.get('status') in ('running', 'stopping') and batch.get('id') not in ctx.active_episode_batches:
            batch.update(status='paused', reason='서버가 다시 시작됐어요. 저장된 회차부터 이어 쓸 수 있어요.')
        state.setdefault('settings', dict(DEFAULT_SETTINGS))
        state.setdefault('archived', False)
        if state.get('branch_origin'):
            state['branch_origin']['revision_required'] = branch_pending_start(state) is not None
        if state.get('selected') == 'guidelines':
            state['selected'] = 'episode' if any(key.startswith('episode:') for key in state['official']) else 'plan'
        for documents in (state['drafts'], state['official']):
            for kind in ('characters', 'world', 'places', 'lore'):
                record = documents.get(f'{kind}:0')
                if record:
                    record['text'] = ctx.normalize_reference_headings(record['text'])
        for key, draft in state['drafts'].items():
            issues = draft.get('continuity_issues')
            if key.startswith('episode:') and issues:
                remaining = ctx.actual_continuity_issues(issues)
                draft['continuity_issues'] = remaining
                if draft.get('impact') == '\n'.join(issues):
                    draft['impact'] = '\n'.join(remaining)
        plan = state['drafts'].get('plan:0', {})
        if (state.get('initial_references_pending') and 'plan:0' not in state['official']
                and plan.get('status') == 'draft' and plan.get('manual_lines') and not plan.get('setup_edit_base')):
            older = con.execute(
                "SELECT id,status,content FROM versions WHERE project=? AND kind='plan' AND episode=0 "
                "AND id<? AND status IN ('draft','review') ORDER BY id DESC LIMIT 1",
                (pid, plan['version']),
            ).fetchone()
            if older and json.loads(older['content'])['text'] != plan['text']:
                plan['setup_edit_base'] = {'version': older['id'], 'status': older['status'], 'base_official_version': None}
        state['settings']['target_chars'] = min(5000, max(1000, state['settings']['target_chars']))
        for episode, record in state.get('records', {}).items():
            source = state['official'].get(f'episode:{episode}')
            issues = record.get('continuity_issues')
            if issues and source and source['version'] == record.get('version'):
                record['continuity_issues'] = ctx.actual_continuity_issues(issues)
                if not record['continuity_issues'] and record.get('setting_review') == 'continuity':
                    record['setting_review'] = 'organize'
            if (record.get('setting_review') == 'done' and source and record.get('version') == source['version']
                    and any(item.get('quote') and item['quote'] not in source['text'] and not item.get('removed')
                            for item in record.get('findings', []))):
                record['findings'] = [item for item in record['findings']
                                      if not item.get('quote') or item['quote'] in source['text']]
                record.pop('findings_checked', None)
                record['setting_review'] = 'organize'
            if record.get('setting_review') == 'review' and source and record.get('version') == source['version']:
                findings = []
                for item in record.get('findings', []):
                    if item.get('removed') or not item.get('reason'):
                        findings.append(item)
                    elif item.get('quote') and item['quote'] in source['text']:
                        if item['reason'] == '회차 설정 검토 대기' and (item['kind'] == 'plot' or item.get('name')):
                            item['reason'] = ''
                        findings.append(item)
                record.update(findings=findings, setting_review='done')
            for finding in record.get('findings', []):
                if finding.get('kind') == 'world' and is_concrete_place_name(finding.get('name')):
                    finding['kind'] = 'places'
        if state.get('branch_origin'):
            origin = state['branch_origin']
            branch_migrated = origin.get('settings_source') == 'snapshot' and not origin.get('setup_flow')
            if origin.get('settings_source') == 'snapshot' and not origin.get('setup_flow'):
                later = any(key.startswith('episode:') and int(key.split(':')[1]) > origin['episode']
                            for group in ('official','drafts') for key in state[group])
                origin.update(setup_flow=1,setup_complete=later)
                if not later:
                    original_plot_version = state['official'].get('plot:0',{}).get('version')
                    for group in ('official','drafts'):
                        current = state[group].get('plot:0')
                        if current:
                            trimmed = branch_setup.retained_plot(current['text'],origin['episode'],state.get('records',{}))
                            if trimmed != current['text']:
                                content = {key:value for key,value in current.items() if key not in ('version','status')}
                                content.update(text=trimmed,summary='',facts='',impact='')
                                vid = ctx.version(con,pid,'plot',0,content,current['status'])
                                state[group]['plot:0'] = {**content,'version':vid,'status':current['status']}
                            if group == 'drafts' and current.get('base_official_version') in (None,original_plot_version):
                                state[group]['plot:0']['base_official_version'] = state['official'].get('plot:0',{}).get('version')
            branch_setup.clean_findings(state)
            origin['setup_stage'] = branch_setup.stage(state)
            if branch_migrated:
                con.execute('UPDATE projects SET state=?,revision=revision+1 WHERE id=?',(json.dumps(state,ensure_ascii=False),pid))
        return row, state
    ctx.load = load

    def repair_alternative_presentation(con, row, state):
        origin = state.get('branch_origin')
        if not origin or origin.get('presentation_version') == 2:
            return row
        original = con.execute('SELECT project FROM published_works WHERE id=?', (origin['id'],)).fetchone()
        if original and not origin.get('coverColor'):
            colors = ['#ad613a', '#6f7c50', '#576a78', '#8d627e', '#655b50', '#638076']
            origin['coverColor'] = colors[sum(ord(c) for c in original['project']) % len(colors)]
        if origin.get('settings_source') != 'snapshot':
            for group in ('official', 'drafts'):
                for key, record in state[group].items():
                    before = record.get('text', '')
                    if key == 'plan:0':
                        record['text'] = normalize_legacy_plan(before)
                    elif key.split(':')[0] in ('characters', 'world', 'places', 'lore'):
                        record['text'] = normalize_legacy_reference(before)
                    if record.get('text') != before:
                        saved = con.execute('SELECT content FROM versions WHERE id=? AND project=?',
                                            (record.get('version'), row['id'])).fetchone()
                        if saved:
                            content = json.loads(saved['content'])
                            if content.get('text') == before:
                                content['text'] = record['text']
                                con.execute('UPDATE versions SET content=? WHERE id=?',
                                            (json.dumps(content, ensure_ascii=False), record['version']))
        title = row['title']
        if title == origin['title'][:105] + ' · 다른 줄거리':
            title = origin['title']
        origin['presentation_version'] = 2
        con.execute('UPDATE projects SET title=?,state=? WHERE id=?',
                    (title, json.dumps(state, ensure_ascii=False), row['id']))
        return con.execute('SELECT * FROM projects WHERE id=?', (row['id'],)).fetchone()
    ctx.repair_alternative_presentation = repair_alternative_presentation

    def setting_snapshot(con, pid, episode, current, renames):
        saved = con.execute('SELECT payload FROM episode_setting_snapshots WHERE project=? AND episode=? AND version=?',
                            (pid, episode, current.get('version'))).fetchone()
        if saved or not current.get('version'):
            return saved
        # Reuse only a real historical snapshot whose manuscript is identical
        # after the recorded name replacements. Never copy today's settings.
        candidates = con.execute(
            'SELECT s.payload,v.content FROM episode_setting_snapshots s JOIN versions v '
            'ON v.project=s.project AND v.id=s.version WHERE s.project=? AND s.episode=? ORDER BY s.created DESC',
            (pid, episode)).fetchall()
        for candidate in candidates:
            old_body = json.loads(candidate['content'])['text']
            if replace_linked_names(old_body, renames, preserve_new=True) != current['text']:
                continue
            payload = json.loads(candidate['payload'])
            if payload.get('episodes', {}).get(str(episode)) != hashlib.sha256(old_body.encode()).hexdigest():
                continue
            manuscripts = {hashlib.sha256(body['text'].encode()).hexdigest(): body['text']
                           for row in con.execute("SELECT content FROM versions WHERE project=? AND kind='episode' AND episode<=?",
                                                  (pid, episode))
                           for body in [json.loads(row['content'])]}
            if any(digest not in manuscripts for digest in payload['episodes'].values()):
                continue
            def rename(value):
                if isinstance(value, str):
                    return replace_linked_names(value, renames, preserve_new=True)
                if isinstance(value, list):
                    return [rename(item) for item in value]
                if isinstance(value, dict):
                    return {key: rename(item) for key, item in value.items()}
                return value
            payload['official'] = rename(payload['official'])
            payload['records'] = rename(payload.get('records', {}))
            payload['episodes'] = {number: hashlib.sha256(
                replace_linked_names(manuscripts[digest], renames, preserve_new=True).encode()).hexdigest()
                for number, digest in payload['episodes'].items()}
            record = payload['records'].get(str(episode))
            if record:
                old_version = record.get('version')
                record['version'] = current['version']
                if record.get('findings_checked') == old_version:
                    record['findings_checked'] = current['version']
                if record.get('intentional_development', {}).get('version') == old_version:
                    record['intentional_development']['version'] = current['version']
            con.execute('INSERT OR IGNORE INTO episode_setting_snapshots VALUES(?,?,?,?,?)',
                        (pid, episode, current['version'], json.dumps(payload, ensure_ascii=False), time.time()))
            return con.execute('SELECT payload FROM episode_setting_snapshots WHERE project=? AND episode=? AND version=?',
                               (pid, episode, current['version'])).fetchone()
        return None
    ctx.setting_snapshot = setting_snapshot

    def save(con, pid, state):
        if state.get('branch_origin'):
            branch_setup.clean_findings(state)
            state['branch_origin']['setup_stage'] = branch_setup.stage(state)
        aliases = confirmed_character_aliases(state)
        character = state.get('official', {}).get('characters:0')
        unresolved = {old: new for old, new in aliases.items()
                      if character and re.search(r'^#{1,6}[ \t]+' + re.escape(old) + r'[ \t]*$', character['text'], re.M)}
        if unresolved and 'characters:0' in state.get('drafts', {}):
            for record in state.get('records', {}).values():
                for finding in record.get('findings', []):
                    if (not finding.get('reason') and finding.get('previous_name') in unresolved
                            and finding.get('name') == unresolved[finding['previous_name']]):
                        finding['reason'] = '인물 설정 수정안을 먼저 처리한 뒤 같은 인물인지 확인해 주세요.'
            aliases = {}
        if aliases and character and 'characters:0' not in state.get('drafts', {}):
            updated = re.sub(r'^(#{1,6}[ \t]+)(.+?)([ \t]*)$',
                             lambda match: match[1] + aliases.get(match[2], match[2]) + match[3],
                             character['text'], flags=re.M)
            if updated != character['text']:
                content = {key: value for key, value in character.items() if key not in ('version', 'status')}
                content['text'] = updated
                vid = ctx.version(con, pid, 'characters', 0, content, 'confirmed')
                state['official']['characters:0'] = {**content, 'version': vid, 'status': 'confirmed'}
        previous = con.execute('SELECT state FROM projects WHERE id=?', (pid,)).fetchone()
        previous = json.loads(previous['state']) if previous else {}
        numbers = [int(key.split(':')[1]) for group in ('official', 'drafts')
                   for key in state[group] if key.startswith('episode:')]
        if (not numbers and state['official'].get('plan:0')
                and all(state['official'].get(f'{kind}:0') for kind in REFERENCE_DOCUMENTS)
                and not any(f'{kind}:0' in state['drafts'] for kind in ('plan', *REFERENCE_DOCUMENTS))
                and not state.get('initial_references_pending') and not state.get('length_setup_pending')):
            initial={'official':{key:value for key,value in state['official'].items() if key!='guidelines:0'},
                     'records':{},'settings':state['settings'],'episodes':{},'linked_names':state.get('linked_names',{})}
            if not con.execute('SELECT 1 FROM episode_setting_snapshots WHERE project=? AND episode=0',(pid,)).fetchone():
                con.execute('INSERT INTO episode_setting_snapshots VALUES(?,?,?,?,?)',
                    (pid,0,state['official']['plan:0']['version'],json.dumps(initial,ensure_ascii=False),time.time()))
        for number, record in state.get('records', {}).items():
            if not number.isdigit() or record.get('setting_review') != 'done':
                continue
            ep = int(number)
            source = state['official'].get(f'episode:{ep}')
            old = previous.get('records', {}).get(number, {})
            if source and source.get('version') == record.get('version') and old.get('version') != record.get('version'):
                ctx.setting_snapshot(con, pid, ep, source, state.get('linked_names', {}))
            if (not source or source.get('version') != record.get('version')
                    or any(key.startswith('episode:') and int(key.split(':')[1])>ep for key in state['official'])
                    or old.get('version') != record.get('version')
                    or old.get('setting_review') not in ('review', 'organize', 'continuity')):
                continue
            snapshot = {
                'official': {key: value for key, value in state['official'].items() if not key.startswith('episode:') and key != 'guidelines:0'},
                'records': {key: value for key, value in state.get('records', {}).items()
                            if key.isdigit() and int(key) <= ep},
                'settings': state['settings'],
                'linked_names': state.get('linked_names', {}),
                'episodes': {key.split(':')[1]: hashlib.sha256(value['text'].encode()).hexdigest()
                             for key, value in state['official'].items()
                             if key.startswith('episode:') and int(key.split(':')[1]) <= ep},
            }
            con.execute('INSERT OR IGNORE INTO episode_setting_snapshots VALUES(?,?,?,?,?)',
                        (pid, ep, source['version'], json.dumps(snapshot, ensure_ascii=False), time.time()))
        con.execute('UPDATE projects SET state=?,revision=revision+1 WHERE id=?', (json.dumps(state, ensure_ascii=False), pid))
    ctx.save = save

    def version(con, pid, kind, episode, content, status):
        return con.execute('INSERT INTO versions(project,kind,episode,content,status,created) VALUES(?,?,?,?,?,?)',
                           (pid, kind, episode, json.dumps(content, ensure_ascii=False), status, time.time())).lastrowid
    ctx.version = version

