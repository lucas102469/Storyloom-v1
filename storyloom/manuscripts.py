"""Drafts, approvals, episode settings review and manual edits."""
from .common import AI_MANUSCRIPT_LENGTH_MARGIN, CONTINUITY_CHECK, CONTINUITY_RULES, KINDS, REFERENCE_DOCUMENTS, SETTING_FINDING_FIELDS, branch_setup, build_episode_context, compare_text, difflib, display_project_title, episode_continuity_stamp, episode_findings, json, jsonify, linked_entity_renames, normalize_metadata, parse_key, re, replace_linked_names, request, reusable_findings, session, settings_value, time
from .context import ApplicationContext


def install(ctx: ApplicationContext):
    @ctx.app.post('/api/projects/<pid>/recover-episode-findings')
    def recover_episode_findings(pid):
        with ctx.db() as con:
            _, state = ctx.load(con, pid)
        if state['archived']:
            return jsonify(ok=True)
        pending = [(key.split(':')[1], record) for key, record in state['official'].items()
                   if key.startswith('episode:') and not state.get('records', {}).get(key.split(':')[1], {}).get('setting_review') and
                   (state.get('records', {}).get(key.split(':')[1], {}).get('findings_checked') != record['version'])][:3]
        if not pending:
            return jsonify(ok=True)
        ctx.job_context.owner = session.get('uid')
        try:
            result = ctx.generate_attempt(
                '확정 원고별로 이름이 명시된 등장인물, 세계관, 주요 설정, 플롯의 새 사실을 찾으세요. 회상·사진·대화에 나온 인물도 포함하세요. 반복되거나 중요한 공간은 places로 분류하고, role에는 근거가 있는 서사적 역할만 쓰고 직업이나 인물 소개를 대신 넣지 마세요. 근거가 없으면 빈 문자열로 두세요. '
                '원고에서 직접 확인할 수 있는 사실만 쓰고 추측은 검토 대상으로 표시하세요. '
                '인용문은 해당 회차 원문에서 12자 이상 그대로 복사하세요. '
                '인물 사실은 인물 카드에, 세계관과 주요 설정은 해당 주제 카드에 넣을 수 있도록 name에 실제 인물명 또는 짧은 설정명을 쓰세요. 기존 카드의 이름과 일치시키세요. 원고에 실명이 없으면 원문 호칭을 유지하고 새 이름을 짓지 마세요. '
                + SETTING_FINDING_FIELDS +
                'JSON {"episodes":[{"episode":"회차번호","findings":[{"kind":"characters|world|places|lore|plot","name":"인물명 또는 설정명","role":"인물의 서사적 역할, 근거 없거나 인물이 아니면 빈 문자열","field":"속성명 또는 빈 문자열","fact":"이름과 확인된 사실",'
                '"quote":"원문","status":"clear 또는 review","reason":"검토 이유 또는 빈 문자열"}]}]}만 반환하세요. '
                '확인한 모든 회차를 포함하고 추가할 내용이 없으면 findings는 빈 배열로 반환하세요.\n'
                + json.dumps({ep: record['text'] for ep, record in pending}, ensure_ascii=False), task='recover_findings',
            )
        finally:
            ctx.job_context.owner = None
            ctx.job_context.calls = 0
        results = {str(item.get('episode')): item['findings'] for item in result.get('episodes', [])
                   if isinstance(item, dict) and isinstance(item.get('findings'), list)}
        with ctx.db() as con:
            con.execute('BEGIN IMMEDIATE')
            _, latest = ctx.load(con, pid)
            if latest['archived']:
                return jsonify(ok=True)
            for ep, source in pending:
                if ep not in results or latest['official'].get('episode:' + ep, {}).get('version') != source['version']:
                    continue
                record = latest.setdefault('records', {}).setdefault(ep, {'version': source['version']})
                if record.get('findings_checked') == source['version']:
                    continue
                findings = record.setdefault('findings', [])
                known = {entry['fact'] for entry in findings}
                findings.extend(entry for entry in episode_findings(results[ep], source['text'])
                                if entry['fact'] not in known)
                record['findings_checked'] = source['version']
            ctx.save(con, pid, latest)
        return jsonify(ok=True)
    ctx.recover_episode_findings = recover_episode_findings

    @ctx.app.get('/api/projects/<pid>/versions')
    def history(pid):
        with ctx.db() as con:
            ctx.load(con, pid)
            kind = request.args.get('kind')
            if kind is not None and kind not in KINDS:
                raise ValueError('버전 종류를 확인하세요.')
            if kind is None:
                rows = con.execute('SELECT * FROM versions WHERE project=? ORDER BY created DESC, id DESC', (pid,))
            else:
                rows = con.execute('SELECT * FROM versions WHERE project=? AND kind=? ORDER BY created DESC, id DESC', (pid, kind))
            return jsonify([dict(row) for row in rows])
    ctx.history = history

    @ctx.app.post('/api/projects/<pid>/draft')
    def draft_action(pid):
        data = request.json
        home_sources = None
        with ctx.db() as con:
            con.execute('BEGIN IMMEDIATE')
            _, state = ctx.load(con, pid)
            if state['archived']:
                raise ValueError('휴지통에서 작품을 복원한 뒤 검토·승인하세요.')
            key = data['key']
            kind, ep = parse_key(key)
            if kind == 'plan' and 'plan:0' in state['official'] and data['action'] not in ('delete', 'reject'):
                return jsonify(error='확정 기획안은 변경할 수 없어요. 새 작품에서 시작해 주세요.'), 409
            if data['action'] == 'delete':
                vid = data.get('version')
                if type(vid) is not int:
                    raise ValueError('삭제할 버전 번호를 확인하세요.')
                protected = {record.get('version') for group in ('drafts', 'official')
                             for record in state[group].values()}
                protected.update(record.get('base_official_version') for record in state['drafts'].values())
                protected.update(state.get('episode_batch', {}).get('draft_versions', {}).values())
                protected.update(record['setup_edit_base']['version'] for record in state['drafts'].values()
                                 if record.get('setup_edit_base'))
                if vid in protected:
                    return jsonify(error='현재 사용 중인 버전은 삭제할 수 없습니다.'), 409
                deleted = con.execute(
                    'DELETE FROM versions WHERE id=? AND project=? AND kind=? AND episode=?',
                    (vid, pid, kind, ep)).rowcount
                if not deleted:
                    return jsonify(error='삭제할 버전을 찾을 수 없습니다.'), 404
                if 'conflicts' in state:
                    state['conflicts'] = [entry for entry in state['conflicts'] if entry.get('version') != vid]
                ctx.save(con, pid, state)
                return jsonify(ok=True)
            if data['action'] == 'restore':
                if isinstance(data.get('version'), bool) or not isinstance(data.get('version'), int):
                    raise ValueError('복원할 버전 번호를 확인하세요.')
                existing = state['drafts'].get(key) or state['official'].get(key) or {}
                if 'base_version' in data and data['base_version'] != existing.get('version'):
                    return jsonify(error='자료가 변경되어 복원을 멈췄습니다. 최신 자료를 확인하세요.'), 409
                old = con.execute('SELECT * FROM versions WHERE id=? AND project=? AND kind=? AND episode=?', (data['version'], pid, kind, ep)).fetchone()
                if not old:
                    raise ValueError('복원할 버전이 없습니다.')
                vid = old['id']
                official_version = state['official'].get(key, {}).get('version')
                if vid == official_version:
                    return jsonify(error='이미 현재 확정본인 버전입니다. 직접 편집에서 수정할 수 있습니다.'), 409
                draft = state['drafts'].get(key)
                if draft and draft['version'] == vid:
                    return jsonify(ok=True, version=vid, unchanged=True)
                content = json.loads(old['content'])
                con.execute("UPDATE versions SET status='draft' WHERE id=? AND project=?", (vid, pid))
                state['drafts'][key] = {**content, 'version': vid, 'status': 'draft',
                                        'base_official_version': official_version}
                if 'conflicts' in state:
                    state['conflicts'] = [entry for entry in state['conflicts'] if entry.get('version') != vid]
            else:
                current = state['drafts'].get(key)
                if not current or current['version'] != data.get('version'):
                    return jsonify(error='자료가 변경되었습니다. 새로 불러온 뒤 다시 검토하세요.'), 409
                change_setup = data['action'] == 'change_setup'
                restoring_setup = data['action'] == 'restore_setup'
                setup_base = current.get('setup_edit_base')
                plan_is_edited = kind == 'plan' and key not in state['official'] and bool(setup_base)
                if plan_is_edited and data['action'] in ('review', 'approve', 'change_setup'):
                    return jsonify(error='AI에 기획안 변경 반영을 요청한 뒤 확정해 주세요.'), 409
                if change_setup or restoring_setup:
                    if not setup_base or not (ctx.is_initial_setting_edit(state, kind) or (restoring_setup and plan_is_edited)):
                        return jsonify(error='직접 수정한 설정 초안을 다시 확인해 주세요.'), 409
                    base_row = con.execute('SELECT content FROM versions WHERE id=? AND project=? AND kind=? AND episode=?',
                                           (setup_base['version'], pid, kind, ep)).fetchone()
                    if not base_row:
                        return jsonify(error='수정 전 버전을 찾을 수 없어요.'), 409
                    setup_before = json.loads(base_row['content'])
                    if restoring_setup:
                        con.execute("UPDATE versions SET status='rejected' WHERE id=? AND project=?", (current['version'], pid))
                        if setup_base['status'] == 'confirmed':
                            del state['drafts'][key]
                        else:
                            state['drafts'][key] = {**setup_before, **setup_base}
                        if setup_base['status'] == 'confirmed':
                            ctx.finish_linked_setting_review(state, key)
                        ctx.save(con, pid, state)
                        return jsonify(ok=True)
                if data['action'] == 'review':
                    current['status'] = 'review'
                    con.execute("UPDATE versions SET status='review', created=? WHERE id=? AND project=?",
                                (time.time(), current['version'], pid))
                elif data['action'] == 'reject':
                    con.execute("UPDATE versions SET status='rejected' WHERE id=? AND project=?", (current['version'], pid))
                    del state['drafts'][key]
                    ctx.finish_linked_setting_review(state, key)
                elif change_setup or (data['action'] == 'approve' and current['status'] == 'review'):
                    batch = state.get('episode_batch', {})
                    if kind == 'episode' and str(ep) in batch.get('draft_versions', {}):
                        if batch.get('status') in ('running', 'stopping'):
                            return jsonify(error='이어 쓰기가 끝나거나 중단된 뒤 원고를 확정해 주세요.'), 409
                        if ep > batch['start'] and (
                            f'episode:{ep-1}' not in state['official']
                            or state.get('records', {}).get(str(ep-1), {}).get('setting_review') not in (None, 'done')
                        ):
                            return jsonify(error='이전 회차의 원고와 설정 검토를 먼저 마쳐 주세요.'), 409
                    if state.get('revision_impact', {}).get('superseded_drafts', {}).get(key) == current['version']:
                        return jsonify(error='이 수정안은 이전 설정으로 작성됐어요. 변경 사항을 다시 점검한 뒤 확정해 주세요.'), 409
                    if not change_setup and kind == 'plot' and state.get('length_setup_pending') and key not in state['official']:
                        return jsonify(error='플롯을 확정하기 전에 분량을 정해 주세요.'), 409
                    current_official = state['official'].get(key, {}).get('version')
                    if current.get('base_official_version', current_official) != current_official:
                        return jsonify(error='이 제안의 기준 확정본이 변경됐습니다. 최신 자료와 비교·복원 후 다시 검토하세요.'), 409
                    content = {k: current.get(k, '') for k in ('text', 'summary', 'facts', 'impact')}
                    branch_plan = (kind == 'plot' and current.get('branch_setup_plot')
                                   and state.get('branch_origin',{}).get('setup_flow') and not state['branch_origin'].get('setup_complete'))
                    if branch_plan:
                        if branch_setup.stage(state) != 'plan' or current.get('branch_setup_basis') != branch_setup.basis(state):
                            return jsonify(error='시작 회차나 설정이 바뀌었어요. 분량과 이후 회차 구성을 다시 정해 주세요.'),409
                        retained = branch_setup.events(current['branch_retained_plot'])
                        proposed = branch_setup.events(content['text'])
                        start = state['branch_origin']['episode']
                        total = current['branch_setup_settings']['total_episodes']
                        end = total if total is not None else min(10000,start+3)
                        if ({n:value for n,value in proposed.items() if n <= start} != retained
                                or sorted(n for n in proposed if n > start) != list(range(start+1,end+1))):
                            return jsonify(error='가져온 회차 구성은 유지하고 이후 회차 번호를 빠짐없이 작성해 주세요.'),409

                    if current.get('reference_materialized_facts'):
                        content['reference_materialized_facts'] = current['reference_materialized_facts']
                    if current.get('manual_lines'):
                        content['manual_lines'] = current['manual_lines']
                    if kind == 'episode':
                        if current.get('title'):
                            content['title'] = current['title']
                        content['metadata_stale'] = current.get('metadata_stale', not bool(content['summary'] and content['facts']))
                        findings = episode_findings([
                            {**entry, 'status': 'review' if entry.get('reason') else 'clear'}
                            for entry in current.get('findings', [])
                        ], content['text'])
                        basis = {id: record['version'] for id, record in state['official'].items()
                                 if id.split(':')[0] in ('characters', 'world', 'places', 'lore', 'plot')}
                        if current.get('findings_basis') != basis:
                            for entry in findings:
                                entry['reason'] = entry['reason'] or '추출 후 확정 설정이 바뀌었어요.'
                        content['findings'] = findings
                        if (current.get('findings_basis') == basis
                                and current.get('continuity_stamp') == episode_continuity_stamp(state, ep, content['text'])):
                            content['findings_basis'] = current['findings_basis']
                            content['continuity_stamp'] = current['continuity_stamp']
                            if current.get('continuity_issues'):
                                content['continuity_issues'] = current['continuity_issues']
                    if kind == 'settings':
                        state['settings'] = settings_value(json.loads(content['text']), state['settings'])
                    impact = state.get('revision_impact', {})
                    prior = state['official'].get(key)
                    reviewing_linked = impact.get('status') == 'awaiting_settings' and key in impact.get('waiting_settings', [])
                    manual_rows = [
                        entry for entry in impact.get('results', [])
                        if entry['episode'] == ep and (entry.get('decision') == 'kept'
                            or (not entry.get('decision') and (entry.get('version') == current['version']
                                or (entry.get('blocked') and not entry.get('version')))))
                    ] if (kind == 'episode' and prior and prior['text'] != content['text']
                          and impact.get('status') in ('ready', 'resolved', 'blocked')) else []
                    if manual_rows:
                        changes = impact.get('changes') or impact.get('reviewed_changes', {})
                        old_settings = changes.get('settings:0', {}).get('before')
                        if isinstance(old_settings, str):
                            old_settings = json.loads(old_settings)
                        minimum = state['settings']['target_chars']
                        maximum = minimum + AI_MANUSCRIPT_LENGTH_MARGIN
                        if (isinstance(old_settings, dict) and old_settings.get('target_chars') != minimum
                                and not minimum <= len(content['text']) <= maximum):
                            return jsonify(error=f'이 회차를 확정하려면 {minimum:,}~{maximum:,}자로 맞춰야 해요. '
                                                 f'현재 수정안은 {len(content["text"]):,}자예요.'), 409
                    reference_edit_base = current.get('reference_edit_base') if kind in ('characters','world','places','lore') else None
                    renames = linked_entity_renames(reference_edit_base or prior['text'], content['text'], characters=kind == 'characters') if prior and kind in ('characters', 'world', 'places', 'lore') else {}
                    recovered_rename = False
                    draft_rename_before = None
                    if not prior and kind in ('characters', 'world', 'places', 'lore'):
                        older_draft = con.execute(
                            'SELECT content FROM versions WHERE project=? AND kind=? AND episode=? AND id<? ORDER BY id DESC LIMIT 1',
                            (pid, kind, ep, current['version']),
                        ).fetchone()
                        if older_draft:
                            draft_rename_before = json.loads(older_draft['content'])['text']
                            renames = linked_entity_renames(draft_rename_before, content['text'], characters=kind == 'characters')
                    if not renames and prior and kind == 'characters':
                        older = con.execute(
                            "SELECT content FROM versions WHERE project=? AND kind='characters' AND id<? "
                            "ORDER BY (status='confirmed') DESC, id DESC LIMIT 1",
                            (pid, prior['version']),
                        ).fetchone()
                        if older:
                            historical_text = json.loads(older['content'])['text']
                            renames = {old: new for old, new in linked_entity_renames(historical_text, content['text'], characters=True).items()
                                       if old not in state.get('linked_names', {})}
                            recovered_rename = bool(renames)
                    if renames and not change_setup:
                        content['text'] = replace_linked_names(content['text'], renames)
                        if content.get('manual_lines'):
                            content['manual_lines'] = [replace_linked_names(line, renames) for line in content['manual_lines']]
                    if change_setup:
                        if kind != 'plot':
                            renames = linked_entity_renames(setup_before['text'], content['text'], characters=kind == 'characters')
                            content['text'] = replace_linked_names(content['text'], renames, preserve_new=True)
                            if content.get('manual_lines'):
                                content['manual_lines'] = [replace_linked_names(line, renames, preserve_new=True)
                                                           for line in content['manual_lines']]
                        ctx.record_revision_impact(state, key, setup_before['text'], content['text'])
                        if state.get('revision_impact', {}).get('status') == 'pending':
                            state['revision_impact']['setup_review'] = True
                    elif not branch_plan and not manual_rows and not reviewing_linked and not current.get('linked_setting') and kind in ('episode', 'plan', 'characters', 'world', 'places', 'lore', 'plot', 'settings'):
                        if recovered_rename:
                            ctx.record_revision_impact(state, key, historical_text, content['text'])
                        elif prior:
                            ctx.record_revision_impact(state, key, reference_edit_base or prior['text'], content['text'])
                        elif current.get('initial_setting_before') is not None:
                            ctx.record_revision_impact(state, key, current['initial_setting_before'], content['text'])
                        elif draft_rename_before is not None and ctx.is_initial_setting_edit(state, kind):
                            ctx.record_revision_impact(state, key, draft_rename_before, content['text'])
                        elif renames:
                            ctx.record_revision_impact(state, key, draft_rename_before, content['text'])
                    vid = current['version']
                    con.execute("UPDATE versions SET status='confirmed', created=?, content=? WHERE id=? AND project=?",
                                (time.time(), json.dumps(content, ensure_ascii=False), vid, pid))
                    state['official'][key] = {**content, 'version': vid, 'status': 'confirmed'}
                    del state['drafts'][key]
                    if branch_plan:
                        state['settings'] = current['branch_setup_settings']
                        settings_content = {'text':json.dumps(state['settings'],ensure_ascii=False),'summary':'','facts':'','impact':''}
                        settings_vid = ctx.version(con,pid,'settings',0,settings_content,'confirmed')
                        state['official']['settings:0'] = {**settings_content,'version':settings_vid,'status':'confirmed'}
                        state['branch_origin'].update(setup_complete=True,setup_stage='complete')
                    if reviewing_linked:
                        ctx.finish_linked_setting_review(state, key, prior['text'] if prior else '', content['text'])
                    if renames:
                        previous_names = state.get('linked_names', {})
                        state['linked_names'] = {**{old: renames.get(new, new) for old, new in previous_names.items()}, **renames}
                        active_impact = state.get('revision_impact', {})
                        if active_impact.get('status') in ('pending', 'awaiting_settings'):
                            active_impact.setdefault('linked_renames', {}).update(renames)
                            previous_length = impact.get('reviewed_changes', {}).get('settings:0')
                            if recovered_rename and previous_length and previous_length.get('after') == state['settings']:
                                target = state['settings']['target_chars']
                                if any(id.startswith('episode:') and not target <= len(document['text']) <= target + AI_MANUSCRIPT_LENGTH_MARGIN
                                       for id, document in state['official'].items()):
                                    active_impact['changes'].setdefault('settings:0', previous_length)
                    if kind == 'plan':
                        title_pattern = r'^\s*(?:[-*]\s+)?(?:\*\*)?(?:작품\s*제목|제목|가제)(?:\*\*)?\s*[:：]\s*(.+)$'
                        proposed_title = re.search(title_pattern, content['text'], re.MULTILINE)
                        if proposed_title:
                            title = display_project_title(proposed_title[1].strip().strip('*'))
                            if 0 < len(title) <= 120 and title not in ('미정', '제목 미정', '미확정', '없음'):
                                previous_title = re.search(title_pattern, prior['text'], re.MULTILINE) if prior else None
                                old_title = display_project_title(previous_title[1].strip().strip('*')) if previous_title else ''
                                if old_title and replace_linked_names(old_title, state.get('linked_names', {})) == title:
                                    con.execute("UPDATE projects SET title=? WHERE id=? AND title IN ('제목 미정',?)", (title, pid, old_title))
                                else:
                                    con.execute("UPDATE projects SET title=? WHERE id=? AND title='제목 미정'", (title, pid))
                    if kind == 'episode':
                        for entry in content['findings']:
                            entry['reason'] = entry['reason'] or '회차 설정 검토 대기'
                        state.setdefault('records', {})[str(ep)] = {'version': vid, 'summary': content['summary'], 'facts': content['facts'], 'metadata_stale': content['metadata_stale'], 'findings': content['findings']}
                        verified = current.get('continuity_stamp') == episode_continuity_stamp(state, ep, content['text'])
                        remaining = current.get('continuity_issues', []) if verified else []
                        state['records'][str(ep)]['setting_review'] = 'organize' if not remaining and (ep == 1 or verified) else 'continuity'
                        if remaining:
                            state['records'][str(ep)].update(continuity_issues=remaining, continuity_stamp=current['continuity_stamp'])
                        if findings:
                            state['records'][str(ep)]['findings_checked'] = vid
                        if manual_rows:
                            if not any(entry.get('version') == vid for entry in manual_rows):
                                ctx.record_revision_impact(state, key, prior['text'], content['text'], after_current=True)
                            for entry in manual_rows:
                                entry.update(decision='accepted', version=vid, base_version=prior['version'])
                            if all(entry.get('decision') for entry in impact['results']):
                                if impact.get('changes'):
                                    impact['reviewed_changes'] = impact['changes']
                                impact.update(status='resolved', changes={})
                            impact['basis'] = ctx.revision_basis(state)
                            ctx.start_followup_impact(state, impact)
                else:
                    raise ValueError('먼저 검토중으로 전환하세요.')
            ctx.save(con, pid, state)
            if kind in ('world', 'lore') and not any(name + ':0' in state['drafts'] for name in ('world', 'lore')):
                sources = {name: state['official'][name + ':0']
                           for name in ('world', 'lore') if name + ':0' in state['official']}
                versions = {name: item['version'] for name, item in sources.items()}
                if sources and state.get('home_settings', {}).get('versions') != versions:
                    home_sources = sources
        if home_sources:
            ctx.workers.submit(ctx.refresh_home_settings, pid, home_sources, session.get('uid'))
        return jsonify(ok=True)
    ctx.draft_action = draft_action

    @ctx.app.post('/api/projects/<pid>/episode-finding')
    def update_episode_finding(pid):
        data = request.json
        episode, index = data.get('episode'), data.get('index')
        action = data.get('action')
        if type(episode) is not int or type(index) is not int or index < 0 or action not in ('accept', 'remove'):
            raise ValueError('회차와 설정 항목을 확인하세요.')
        with ctx.db() as con:
            con.execute('BEGIN IMMEDIATE')
            _, state = ctx.load(con, pid)
            record = state.get('records', {}).get(str(episode), {})
            findings = record.get('findings', [])
            if state['archived'] or record.get('version') != data.get('version') or index >= len(findings):
                return jsonify(error='기록이 변경됐어요. 다시 불러와 주세요.'), 409
            finding = findings[index]
            if record.get('setting_review') in ('continuity', 'organize'):
                return jsonify(error='원고 화면에서 회차 설정을 먼저 정리해 주세요.'), 409
            if action == 'accept':
                if finding.get('removed') or not finding.get('reason'):
                    return jsonify(error='이미 처리된 항목이에요.'), 409
                if finding.get('reason') == '설정 이름을 확인하지 못했어요.':
                    return jsonify(error='이름이 없는 설정은 반영할 수 없어요.'), 409
                finding['reason'] = ''
            else:
                finding['removed'] = True
            if record.get('setting_review') == 'review' and not any(item.get('reason') and not item.get('removed') for item in findings):
                record['setting_review'] = 'done'
            ctx.save(con, pid, state)
        return jsonify(ok=True)
    ctx.update_episode_finding = update_episode_finding

    @ctx.app.post('/api/projects/<pid>/episode-settings-review')
    def review_episode_settings(pid):
        data = request.json
        episode = data.get('episode')
        action = data.get('action')
        if type(episode) is not int or action not in ('continuity', 'organize', 'approve', 'continue'):
            raise ValueError('회차 설정 작업을 확인해 주세요.')
        key = f'episode:{episode}'
        expected = {'approve': 'review', 'continue': 'continuity'}.get(action, action)
        with ctx.db() as con:
            _, state = ctx.load(con, pid)
        record = state.get('records', {}).get(str(episode), {})
        source = state['official'].get(key)
        if not source or state['archived'] or key in state['drafts'] or source['version'] != data.get('version'):
            return jsonify(error='현재 회차와 검토 단계를 다시 확인해 주세요.'), 409
        if action == 'approve' and record.get('setting_review') == 'done':
            return jsonify(ok=True,stage='done')
        if record.get('setting_review') != expected:
            return jsonify(error='현재 회차와 검토 단계를 다시 확인해 주세요.'), 409
        if (action == 'continuity' and record.get('continuity_issues')
                and record.get('continuity_stamp') == episode_continuity_stamp(state, episode, source['text'])):
            return jsonify(ok=True,stage='continuity',issues=record['continuity_issues'],cached=True)
        if action in ('organize','approve'):
            cached_findings=reusable_findings(state,episode)
            if cached_findings is not None:
                with ctx.db() as con:
                    con.execute('BEGIN IMMEDIATE')
                    _,latest=ctx.load(con,pid)
                    if (latest['archived'] or latest['official'].get(key,{}).get('version')!=source['version']
                            or reusable_findings(latest,episode)!=cached_findings):
                        return jsonify(error='검토 중 자료가 변경됐어요. 다시 확인해 주세요.'),409
                    current=latest['records'][str(episode)]
                    current.update(findings=cached_findings,findings_checked=source['version'],setting_review='done')
                    ctx.save(con,pid,latest)
                return jsonify(ok=True,stage='done',cached=True)
        changes = {}
        review_context = None
        if action == 'continue':
            if not record.get('continuity_issues'):
                return jsonify(error='확인할 충돌이 없습니다.'), 409
            note = data.get('note', '')
            if not isinstance(note, str) or len(note.strip()) > 500:
                raise ValueError('전개 의도는 500자 이내로 입력해 주세요.')
            changes.update(setting_review='organize', intentional_development={
                'version': source['version'], 'issues': record['continuity_issues'], 'note': note.strip(),
            })
        else:
            ctx.job_context.owner = session.get('uid')
            try:
                prompt = (CONTINUITY_CHECK
                          if action == 'continuity' else
                          '원고에서 새로 추가되거나 변경된 등장인물·세계관·주요 설정·플롯을 정리하세요. 회상·사진·대화의 인물도 포함하세요. '
                          '플롯을 제외한 모든 항목의 name에 실제 인물명 또는 짧은 설정명을 쓰세요. 기존 항목은 동일한 이름을 쓰세요. 원고에 실명이 없으면 원문 호칭을 유지하고 이름을 새로 짓지 마세요. '
                          '기존 세계관이나 주요 설정의 능력·규칙을 보충할 때는 속성의 값이 아닌 기존 카드 제목을 name에 쓰세요. 같은 능력을 새 카드로 중복 등록하지 마세요. '
                          '한 인물의 이름·성격·관계·취향은 동일한 name의 characters로 분류하세요. role에는 원고와 기존 자료에 근거가 있는 서사적 역할만 짧게 쓰고 직업이나 인물 소개를 대신 넣지 마세요. 근거가 없으면 빈 문자열을 반환하세요. 반복되거나 중요한 공간은 places로 분류하세요. 같은 사실을 중복하거나 여러 종류에 넣지 마세요. '
                          + SETTING_FINDING_FIELDS +
                          '다음 회차에 참고할 가치가 있고 기존 자료에 없는 사실만 포함하세요. 근거 없는 추측이나 일회성 묘사는 생략하세요. '
                          '명시된 사실은 clear, 원문에 단서가 있지만 해석이 갈리거나 기존 확정 설정을 바꿔야 하는 중요한 항목은 review로 표시하세요. '
                          '원문에서 연속된 구절을 12자 이상 그대로 인용하세요. JSON {"findings":[{"kind":"characters|world|places|lore|plot","name":"인물명 또는 설정명","role":"인물의 서사적 역할, 근거 없거나 인물이 아니면 빈 문자열","field":"속성명 또는 빈 문자열","fact":"내용","quote":"원문 인용","status":"clear|review","reason":"검토 이유 또는 빈 문자열"}]}만 반환하세요. 없으면 빈 배열입니다.')
                task = 'episode_continuity' if action == 'continuity' else 'episode_findings'
                prompt += ' 같은 인물의 실명이 명시적으로 밝혀진 경우에만 previous_name에 기존 호칭을 넣고 name에 밝혀진 이름을 쓰세요. quote에는 동일 인물임을 명시한 원문을 인용하세요. 불명확하면 previous_name은 비우세요. '
                review_context = build_episode_context(state, episode)
                result = ctx.generate_attempt(prompt + '\n' + CONTINUITY_RULES + '\n기존 자료:\n'
                                          + review_context + '\n검토 원고:\n' + source['text'], task=task)
            finally:
                ctx.job_context.owner = None
                ctx.job_context.calls = 0
            if action == 'continuity':
                issues = result.get('issues')
                if not isinstance(issues, list) or not all(isinstance(issue, str) and issue.strip() for issue in issues):
                    raise ValueError('일관성 검토 결과를 확인할 수 없어요.')
                issues = ctx.actual_continuity_issues(issues)
                changes.update(continuity_issues=issues[:20], setting_review='continuity' if issues else 'organize')
            else:
                findings = result.get('findings') if isinstance(result, dict) else None
                if not isinstance(findings, list):
                    raise ValueError('자료집 정리 결과를 확인할 수 없어요.')
                entries = [item for item in episode_findings(findings, source['text']) if item.get('quote')]
                for item in entries:
                    if item['kind'] != 'plot' and not item.get('name'):
                        item['reason'] = '설정 이름을 확인하지 못했어요.'
                changes.update(findings=entries, setting_review='done')
        with ctx.db() as con:
            con.execute('BEGIN IMMEDIATE')
            _, latest = ctx.load(con, pid)
            current = latest.get('records', {}).get(str(episode), {})
            metadata_fields = ('summary', 'facts', 'metadata_stale')
            if ({k: v for k, v in current.items() if k not in metadata_fields}
                    != {k: v for k, v in record.items() if k not in metadata_fields}
                    or (action in ('continuity', 'organize')
                        and build_episode_context({**latest, 'selected': state['selected']}, episode)
                        != review_context)
                    or latest['official'].get(key, {}).get('version') != source['version']
                    or key in latest['drafts'] or latest['archived']):
                return jsonify(error='검토 중 자료가 변경됐어요. 다시 확인해 주세요.'), 409
            current.update(changes)
            ctx.save(con, pid, latest)
        return jsonify(ok=True,stage=current.get('setting_review'),issues=current.get('continuity_issues',[]))
    ctx.review_episode_settings = review_episode_settings

    @ctx.app.post('/api/projects/<pid>/episode-metadata')
    def refresh_confirmed_episode_metadata(pid):
        data = request.json
        key = data.get('key')
        kind, ep = parse_key(key)
        if kind != 'episode':
            raise ValueError('확정 원고를 선택하세요.')
        with ctx.db() as con:
            _, state = ctx.load(con, pid)
        source = state['official'].get(key, {})
        if state['archived'] or not source or source['version'] != data.get('version'):
            return jsonify(error='확정 원고가 변경됐어요.'), 409
        if not source.get('metadata_stale') and source.get('summary') and source.get('facts'):
            return jsonify(ok=True)
        ctx.job_context.owner = session.get('uid')
        try:
            metadata = normalize_metadata(ctx.generate_attempt(
                '이 확정 원고의 줄거리, 실제 사건, 인물별로 알게 된 정보와 미해결 복선을 기록하세요. '
                'JSON {"summary":"요약","facts":"사건과 사실 기록"}만 반환하세요.\n' + source['text'],
                task='metadata'))
        except RuntimeError:
            return jsonify(error='요약·사건 기록을 갱신하지 못했어요. 원고는 저장되어 있어요.'), 502
        finally:
            ctx.job_context.owner = None
            ctx.job_context.calls = 0
        with ctx.db() as con:
            con.execute('BEGIN IMMEDIATE')
            _, latest = ctx.load(con, pid)
            current = latest['official'].get(key, {})
            if latest['archived'] or current.get('version') != source['version']:
                return jsonify(error='확정 원고가 변경됐어요.'), 409
            if current.get('metadata_stale') or not current.get('summary') or not current.get('facts'):
                fields = {**metadata, 'metadata_stale': False}
                current.update(fields)
                record = latest.get('records', {}).get(str(ep))
                if record and record.get('version') == source['version']:
                    record.update(fields)
                content = {k: v for k, v in current.items() if k not in ('version', 'status')}
                con.execute('UPDATE versions SET content=? WHERE id=? AND project=?',
                            (json.dumps(content, ensure_ascii=False), source['version'], pid))
                ctx.save(con, pid, latest)
        return jsonify(ok=True)
    ctx.refresh_confirmed_episode_metadata = refresh_confirmed_episode_metadata

    @ctx.app.post('/api/projects/<pid>/guidelines')
    def apply_guidelines(pid):
        data = request.json
        items = data.get('items')
        if (not isinstance(items, list) or any(not isinstance(item, str) or not item.strip()
                                               or '\n' in item or '\r' in item for item in items)):
            raise ValueError('각 지침은 한 줄로 입력하세요.')
        text = '\n'.join(item.strip() for item in items)
        if len(text) > 1_000_000:
            raise ValueError('작가 지침은 최대 100만자입니다.')
        key = 'guidelines:0'
        with ctx.db() as con:
            con.execute('BEGIN IMMEDIATE')
            _, state = ctx.load(con, pid)
            if state['archived']:
                raise ValueError('휴지통에서 작품을 복원한 뒤 수정하세요.')
            current = state['official'].get(key, {})
            draft = state['drafts'].get(key, {})
            if (current.get('version') != data.get('base_version')
                    or draft.get('version') != data.get('draft_version')):
                return jsonify(error='다른 지침이 먼저 저장됐어요. 다시 불러와 확인해 주세요.'), 409
            if text == current.get('text', '') and not draft:
                return jsonify(ok=True)
            content = {'text': text, 'summary': '', 'facts': '', 'impact': ''}
            vid = ctx.version(con, pid, 'guidelines', 0, content, 'confirmed')
            state['official'][key] = {**content, 'version': vid, 'status': 'confirmed'}
            state['drafts'].pop(key, None)
            ctx.save(con, pid, state)
        return jsonify(ok=True)
    ctx.apply_guidelines = apply_guidelines

    @ctx.app.post('/api/projects/<pid>/edit')
    def edit_document(pid):
        data = request.json
        kind, ep = parse_key(data.get('key'))
        text = data.get('text')
        title = data.get('title')
        if title is not None:
            if kind != 'episode' or not isinstance(title, str) or not 1 <= len(title.strip()) <= 120 or not title.strip().isprintable():
                raise ValueError('회차 제목은 한 줄로 1~120자 입력하세요.')
            title = title.strip()
        if not isinstance(text, str) or not text.strip() or len(text) > 1_000_000:
            raise ValueError('빈 자료는 저장할 수 없으며 최대 100만자입니다.')
        with ctx.db() as con:
            con.execute('BEGIN IMMEDIATE')
            _, state = ctx.load(con, pid)
            if state['archived']:
                raise ValueError('휴지통에서 작품을 복원한 뒤 수정하세요.')
            if kind == 'plan' and 'plan:0' in state['official']:
                return jsonify(error='확정한 기획안은 직접 편집할 수 없어요.'), 409
            current = state['drafts'].get(data['key']) or state['official'].get(data['key']) or {}
            if current.get('version') != data.get('base_version'):
                return jsonify(error='다른 수정이 먼저 저장됐습니다. 입력 내용은 유지하고 새 자료를 확인하세요.'), 409
            if kind == 'settings':
                text = json.dumps(settings_value(json.loads(text), state['settings']), ensure_ascii=False)
            if kind == 'episode' and title is not None and current.get('text') == text:
                # A title is metadata, not a new manuscript revision. Keep review
                # provenance and continuity checks tied to the existing body.
                current['title'] = title
                stored = con.execute('SELECT content FROM versions WHERE id=? AND project=?',
                                     (current.get('version'), pid)).fetchone()
                if stored:
                    stored_content = json.loads(stored['content'])
                    stored_content['title'] = title
                    con.execute('UPDATE versions SET content=? WHERE id=? AND project=?',
                                (json.dumps(stored_content, ensure_ascii=False), current['version'], pid))
                ctx.save(con, pid, state)
                return jsonify(ok=True, version=current.get('version'))
            if current.get('text') == text and (title is None or current.get('title') == title) and data['key'] in state['drafts']:
                return jsonify(ok=True, version=current['version'], unchanged=True)
            if (kind == 'plot' and state.get('branch_origin',{}).get('setup_flow')
                    and not state['branch_origin'].get('setup_complete') and not current.get('branch_setup_plot')):
                if any(number > state['branch_origin']['episode'] for number in branch_setup.events(text)):
                    return jsonify(error='이후 회차는 시작 회차의 설정 검토를 마친 뒤 분량·회차 구성 정하기에서 계획해 주세요.'),409
            content = {'text': text, 'summary': '', 'facts': '', 'impact': ''}
            if kind == 'plot' and current.get('branch_setup_plot') and state.get('branch_origin',{}).get('setup_flow'):
                for field in ('branch_setup_plot','branch_retained_plot','branch_setup_settings','branch_setup_basis'):
                    content[field] = current[field]
            if kind in ('characters','world','places','lore'):
                for field in ('reference_edit_base','reference_materialized_facts'):
                    if field in current:
                        content[field] = current[field]
            if kind in ('plan', *REFERENCE_DOCUMENTS) and current.get('text'):
                previous_lines = current['text'].splitlines()
                lines = text.splitlines()
                edited = {line for line in current.get('manual_lines', []) if line in text}
                for operation, before_start, before_end, start, end in difflib.SequenceMatcher(None, previous_lines, lines).get_opcodes():
                    if operation not in ('insert', 'replace'):
                        continue
                    for offset, line in enumerate(lines[start:end]):
                        if operation == 'replace' and before_end - before_start == end - start:
                            original = previous_lines[before_start + offset]
                            if original and not line.startswith('#'):
                                for change, _, _, left, right in difflib.SequenceMatcher(None, original, line).get_opcodes():
                                    if change in ('insert', 'replace') and line[left:right].strip():
                                        edited.add(line[left:right].strip())
                                continue
                        if line.strip():
                            edited.add(line)
                if edited:
                    content['manual_lines'] = sorted(edited)
            if kind == 'episode':
                if current.get('text') == text:
                    content = {field: current.get(field, '') for field in ('text', 'summary', 'facts', 'impact')}
                    for field in ('findings', 'findings_basis'):
                        if field in current:
                            content[field] = current[field]
                    content['metadata_stale'] = current.get('metadata_stale', not bool(content['summary'] and content['facts']))
                else:
                    content['metadata_stale'] = True
                episode_title = title or current.get('title')
                if episode_title:
                    content['title'] = episode_title
            if (kind == 'plan' or ctx.is_initial_setting_edit(state, kind)) and current.get('version') and text != current['text']:
                content['setup_edit_base'] = current.get('setup_edit_base') or {
                    field: current[field] for field in ('version', 'status', 'base_official_version') if field in current
                }
            vid = ctx.version(con, pid, kind, ep, content, 'draft')
            state['drafts'][data['key']] = {**content, 'version': vid, 'status': 'draft', 'base_official_version': state['official'].get(data['key'], {}).get('version')}
            ctx.save(con, pid, state)
        return jsonify(ok=True, version=vid)
    ctx.edit_document = edit_document

    @ctx.app.get('/api/projects/<pid>/compare')
    def compare_document(pid):
        key = request.args.get('key', '')
        kind, ep = parse_key(key)
        with ctx.db() as con:
            _, state = ctx.load(con, pid)
            before = state['official'].get(key, {}).get('text', '')
            after = state['drafts'].get(key, {}).get('text', before)
            if request.args.get('base') == 'draft':
                before = state['drafts'].get(key, {}).get('text', '')
            if request.args.get('version'):
                row = con.execute('SELECT content FROM versions WHERE project=? AND id=? AND kind=? AND episode=?',
                                  (pid, int(request.args['version']), kind, ep)).fetchone()
                if not row:
                    raise ValueError('비교할 버전이 없습니다.')
                before = json.loads(row['content'])['text']
            if request.args.get('after_version'):
                row = con.execute('SELECT content FROM versions WHERE project=? AND id=? AND kind=? AND episode=?',
                                  (pid, int(request.args['after_version']), kind, ep)).fetchone()
                if not row:
                    raise ValueError('비교할 버전이 없습니다.')
                after = json.loads(row['content'])['text']
            return jsonify(before_chars=len(before), after_chars=len(after), changes=compare_text(before, after))
    ctx.compare_document = compare_document

    @ctx.app.post('/api/projects/<pid>/select-changes')
    def select_changes(pid):
        data = request.json
        kind, ep = parse_key(data.get('key'))
        chosen = data.get('changes')
        if not isinstance(chosen, list) or any(isinstance(x, bool) or not isinstance(x, int) for x in chosen):
            raise ValueError('반영할 변경 항목을 선택하세요.')
        with ctx.db() as con:
            con.execute('BEGIN IMMEDIATE')
            _, state = ctx.load(con, pid)
            if state['archived']:
                raise ValueError('휴지통에서 복원 후 수정하세요.')
            draft = state['drafts'].get(data['key'])
            official = state['official'].get(data['key'])
            if not draft or not official or draft['version'] != data.get('draft_version') or official['version'] != data.get('official_version'):
                return jsonify(error='비교 중 원본이 바뀌었습니다. 다시 확인하세요.'), 409
            changes = compare_text(official['text'], draft['text'])
            valid = {i for i, c in enumerate(changes) if c['type'] != 'equal'}
            if not set(chosen) <= valid:
                raise ValueError('존재하지 않는 변경 항목입니다.')
            text = ''.join(c['after'] if c['type'] == 'equal' or i in chosen else c['before'] for i, c in enumerate(changes))
            if not text.strip():
                raise ValueError('빈 원고로 반영할 수 없습니다.')
            if kind == 'settings':
                text = json.dumps(settings_value(json.loads(text), state['settings']), ensure_ascii=False)
            content = {'text': text, 'summary': '', 'facts': '', 'impact': ''}
            if kind == 'episode':
                content['metadata_stale'] = True
                title = draft.get('title') or official.get('title')
                if title:
                    content['title'] = title
            vid = ctx.version(con, pid, kind, ep, content, 'draft')
            state['drafts'][data['key']] = {**content, 'version': vid, 'status': 'draft', 'base_official_version': official['version']}
            ctx.save(con, pid, state)
        return jsonify(ok=True, version=vid)
    ctx.select_changes = select_changes

