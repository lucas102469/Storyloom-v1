"""Setting impact review and propagation to accepted manuscripts."""
from .common import AIUsageLimit, AI_MANUSCRIPT_LENGTH_MARGIN, REFERENCE_DOCUMENTS, RetryableAIError, adjust_revision_length, apply_patches, apply_revision_patches, branch_setup, build_episode_context, hashlib, json, jsonify, linked_entity_renames, name_only_changes, normalize_manuscript_breaks, normalize_metadata, normalize_reference_topics, parse_key, re, readable_manuscript, repair_reviewed_manuscript, replace_linked_names, request, revision_timeline, session, time, uuid
from .context import ApplicationContext


def install(ctx: ApplicationContext):
    def record_revision_impact(state, key, before, after, *, after_current=False):
        if before == after:
            return
        episode = int(key.split(':')[1]) if key.startswith('episode:') else 0
        linked_settings = key.split(':')[0] in REFERENCE_DOCUMENTS and any(
            f'{kind}:0' != key and (f'{kind}:0' in state['official'] or f'{kind}:0' in state['drafts'])
            for kind in ('plan', *REFERENCE_DOCUMENTS))
        if not linked_settings and not any(item.startswith('episode:') and int(item.split(':')[1]) > episode
                                          for item in state['official']):
            return
        impact = state.get('revision_impact', {})
        if after_current:
            changes = impact.setdefault('followup_changes', {})
        else:
            superseded = dict(impact.get('superseded_drafts', {}))
            for entry in impact.get('results', []):
                key_id = f"episode:{entry['episode']}"
                if (not entry.get('decision') and entry.get('version')
                        and state['drafts'].get(key_id, {}).get('version') == entry['version']):
                    superseded[key_id] = entry['version']
            rejected = [*impact.get('rejected_results', []),
                        *(entry for entry in impact.get('results', [])
                          if entry.get('decision') == 'kept' or (entry.get('patch_error') and not entry.get('decision')))]
            changes = {**(impact.get('reviewed_changes', {}) if rejected else {}),
                       **impact.get('changes', {}), **impact.get('followup_changes', {})}
        changes[key] = {'before': changes.get(key, {}).get('before', before), 'after': after}
        if not after_current:
            state['revision_impact'] = {'id': uuid.uuid4().hex, 'changes': changes, 'status': 'pending'}
            if impact.get('linked_renames'):
                state['revision_impact']['linked_renames'] = impact['linked_renames']
            if rejected:
                state['revision_impact']['rejected_results'] = rejected
            if superseded:
                state['revision_impact']['superseded_drafts'] = superseded
    ctx.record_revision_impact = record_revision_impact

    def start_followup_impact(state, impact):
        if (impact.get('followup_changes') and impact.get('status') in ('confirmed', 'resolved')
                and not any(entry.get('decision') == 'kept' for entry in impact.get('results', []))):
            state['revision_impact'] = {'id': uuid.uuid4().hex, 'changes': impact['followup_changes'], 'status': 'pending'}
    ctx.start_followup_impact = start_followup_impact

    def revision_basis(state, episodes=None):
        return {
            'official': {key: record['version'] for key, record in state['official'].items()},
            'drafts': {key: record['version'] for key, record in state['drafts'].items()
                       if key.startswith('episode:') and (episodes is None or int(key.split(':')[1]) in episodes)},
            'settings': state['settings'],
        }
    ctx.revision_basis = revision_basis

    def revision_basis_matches(impact, state):
        saved = impact.get('basis')
        if not saved:
            return False
        episodes = {entry['episode'] for entry in impact.get('results', []) if not entry.get('decision') or entry.get('decision') == 'kept'}
        return ctx.revision_basis(state, episodes) == {
            'official': saved.get('official'),
            'drafts': {key: vid for key, vid in saved.get('drafts', {}).items()
                       if key.startswith('episode:') and int(key.split(':')[1]) in episodes},
            'settings': saved.get('settings'),
        }
    ctx.revision_basis_matches = revision_basis_matches

    def revision_impact_blocks_writing(state):
        impact = state.get('revision_impact', {})
        return (impact.get('status') in ('pending', 'awaiting_settings', 'settings_ready', 'running', 'ready', 'blocked', 'failed')
                or any(entry.get('decision') == 'kept' for entry in impact.get('results', [])))
    ctx.revision_impact_blocks_writing = revision_impact_blocks_writing

    def finish_linked_setting_review(state, key, before=None, after=None):
        impact = state.get('revision_impact', {})
        waiting = impact.get('waiting_settings', [])
        if impact.get('status') != 'awaiting_settings' or key not in waiting:
            return
        if before is not None and before != after:
            impact['changes'][key] = {'before': before, 'after': after}
        waiting.remove(key)
        if not waiting:
            if any(key.startswith('episode:') for key in state['official']):
                impact['status'] = 'settings_ready'
            else:
                impact.update(status='done', changes={})
    ctx.finish_linked_setting_review = finish_linked_setting_review

    def save_linked_draft(con, pid, state, key, text, reason, renames=None, *, preserve_new=False):
        kind, episode = parse_key(key)
        if kind == 'plan' and 'plan:0' in state['official']:
            return
        official = state['official'].get(key, {})
        current = state['drafts'].get(key) or official
        if kind in ('world', 'places', 'lore') and not current.get('manual_lines'):
            text = normalize_reference_topics(text, '\n'.join(
                state[g].get('characters:0', {}).get('text', '') for g in ('official', 'drafts')))
        content = {field: replace_linked_names(current.get(field, ''), renames, preserve_new=preserve_new)
                   for field in ('summary', 'facts')}
        if (kind == 'plot' and state.get('branch_origin',{}).get('setup_flow')
                and not state['branch_origin'].get('setup_complete') and not current.get('branch_setup_plot')):
            text = branch_setup.retained_plot(text,state['branch_origin']['episode'],state.get('records',{}))
        content.update(text=text, impact=reason, linked_setting=True)
        if current.get('reference_materialized_facts'):
            content['reference_materialized_facts'] = current['reference_materialized_facts']
        if current.get('manual_lines'):
            content['manual_lines'] = [replace_linked_names(line, renames, preserve_new=preserve_new)
                                       for line in current['manual_lines']]
        vid = ctx.version(con, pid, kind, episode, content, 'draft')
        state['drafts'][key] = {**content, 'version': vid, 'status': 'draft',
                                'base_official_version': current.get('base_official_version', official.get('version'))}
    ctx.save_linked_draft = save_linked_draft

    def confirm_revision_batch(con, pid, state, impact, episode=None):
        for entry in impact['results']:
            if not entry.get('version') or entry.get('decision'):
                continue
            if episode is not None and entry['episode'] != episode:
                continue
            key = f"episode:{entry['episode']}"
            draft = state['drafts'].pop(key)
            content = {name: draft[name] for name in ('text', 'title', 'summary', 'facts', 'metadata_stale', 'findings')
                       if name in draft}
            vid = draft['version']
            previous_record = state.get('records', {}).get(str(entry['episode']), {})
            previous_findings = previous_record.get('findings', content.get('findings', []))
            rename_only = name_only_changes(impact.get('changes', {}), impact.get('linked_renames', {}))
            if rename_only:
                previous_findings = content.get('findings', previous_findings)
            content['findings'] = [finding for finding in previous_findings
                                   if finding.get('quote') and finding['quote'] in content['text']]
            # Surviving old evidence does not verify newly added/changed facts.
            body_changed = content['text'] != state['official'].get(key, {}).get('text')
            needs_review = (len(content['findings']) != len(previous_findings)
                            or (body_changed and not rename_only))
            con.execute("UPDATE versions SET status='confirmed', created=?, content=? WHERE id=? AND project=?",
                        (time.time(), json.dumps(content, ensure_ascii=False), vid, pid))
            state['official'][key] = {**content, 'version': vid, 'status': 'confirmed'}
            state.setdefault('records', {})[str(entry['episode'])] = {
                'version': vid, 'summary': content['summary'], 'facts': content['facts'],
                'metadata_stale': False, 'findings': content['findings'],
                'setting_review': 'organize' if needs_review else previous_record.get('setting_review', 'done'),
            }
            if rename_only:
                record = state['records'][str(entry['episode'])]
                for field in ('intentional_development', 'continuity_issues'):
                    if field in previous_record:
                        def rename_review(value):
                            if isinstance(value, str):
                                return replace_linked_names(value, impact['linked_renames'], preserve_new=True)
                            if isinstance(value, list):
                                return [rename_review(item) for item in value]
                            if isinstance(value, dict):
                                return {key: rename_review(item) for key, item in value.items()}
                            return value
                        record[field] = rename_review(previous_record[field])
                if record.get('intentional_development', {}).get('version') == previous_record.get('version'):
                    record['intentional_development']['version'] = vid
            if not needs_review:
                state['records'][str(entry['episode'])]['findings_checked'] = vid
            entry['decision'] = 'accepted'
        if episode is None:
            impact.update(status='confirmed', changes={})
    ctx.confirm_revision_batch = confirm_revision_batch

    @ctx.app.post('/api/projects/<pid>/revision-impact')
    def revision_impact(pid):
        data = request.json
        mode = data.get('mode')
        if mode not in ('auto', 'manual', 'approve', 'accept', 'keep', 'acknowledge', 'retry'):
            raise ValueError('수정 방식을 선택하세요.')
        with ctx.db() as con:
            con.execute('BEGIN IMMEDIATE')
            _, state = ctx.load(con, pid)
            impact = state.get('revision_impact', {})
            if state['archived'] or impact.get('id') != data.get('id'):
                return jsonify(error='최신 변경 내용을 다시 확인해 주세요.'), 409
            if mode in ('accept', 'keep', 'acknowledge'):
                episode = data.get('episode')
                rows = [entry for entry in impact.get('results', []) if entry['episode'] == episode]
                if (type(episode) is not int or not rows or any(entry.get('decision') for entry in rows)
                        or impact.get('status') not in ('ready', 'blocked', 'done')
                        or not ctx.revision_basis_matches(impact, state)):
                    return jsonify(error='원고나 점검 결과가 바뀌었어요. 최신 결과를 확인해 주세요.'), 409
                if con.execute("SELECT 1 FROM jobs WHERE project=? AND status='running'", (pid,)).fetchone():
                    return jsonify(error='진행 중인 AI 작업이 끝난 뒤 선택해 주세요.'), 409
                proposal = next((entry for entry in rows if entry.get('version')), None)
                key = f'episode:{episode}'
                if mode == 'acknowledge':
                    if proposal or any(entry.get('patch_error') for entry in rows):
                        raise ValueError('수정안을 반영할지 선택해 주세요.')
                    decision = 'acknowledged'
                else:
                    draft = state['drafts'].get(key)
                    if not proposal or not draft or draft['version'] != proposal['version']:
                        return jsonify(error='현재 수정안이 바뀌었어요. 최신 원고를 확인해 주세요.'), 409
                    if mode == 'accept':
                        ctx.confirm_revision_batch(con, pid, state, impact, episode)
                        decision = 'accepted'
                    else:
                        con.execute("UPDATE versions SET status='rejected' WHERE id=? AND project=?",
                                    (draft['version'], pid))
                        del state['drafts'][key]
                        decision = 'kept'
                for entry in rows:
                    entry['decision'] = decision
                if all(entry.get('decision') for entry in impact['results']):
                    if impact['changes']:
                        impact['reviewed_changes'] = impact['changes']
                    impact.update(status='resolved', changes={})
                else:
                    impact['status'] = 'blocked'
                impact['basis'] = ctx.revision_basis(state)
                ctx.start_followup_impact(state, impact)
                ctx.save(con, pid, state)
                return jsonify(ok=True)
            if mode == 'approve':
                if impact.get('status') != 'ready' or not ctx.revision_basis_matches(impact, state):
                    return jsonify(error='원고나 설정이 바뀌었거나 점검이 끝나지 않았어요.'), 409
                if con.execute("SELECT 1 FROM jobs WHERE project=? AND status='running'", (pid,)).fetchone():
                    return jsonify(error='진행 중인 AI 작업이 끝난 뒤 확정해 주세요.'), 409
                ctx.confirm_revision_batch(con, pid, state, impact)
                ctx.start_followup_impact(state, impact)
                ctx.save(con, pid, state)
                return jsonify(ok=True)
            if impact.get('status') == 'running' and time.time() - impact['started'] > 600:
                impact['status'] = impact.pop('retry_status', 'failed')
            retry = mode == 'retry'
            episode = data.get('episode')
            retained = []
            retry_notes = []
            superseded = impact.get('superseded_drafts', {})
            if retry:
                rows = impact.get('results', [])
                previous_rows = [entry for entry in rows if entry['episode'] == episode]
                retryable = previous_rows and (
                    all(entry.get('decision') == 'kept' for entry in previous_rows)
                    or (not any(entry.get('version') or entry.get('decision') for entry in previous_rows)
                        and any(entry.get('blocked') for entry in previous_rows)))
                key = f'episode:{episode}'
                if (type(episode) is not int or not retryable or impact.get('status') not in ('blocked', 'resolved')
                        or not ctx.revision_basis_matches(impact, state)
                        or (key in state['drafts'] and state['drafts'][key]['version'] != superseded.get(key))
                        or key not in state['official']):
                    return jsonify(error='이 회차의 최신 원고와 초안을 확인해 주세요.'), 409
                retained = [entry for entry in rows if entry['episode'] != episode]
                retry_notes = [entry['reason'] for entry in previous_rows if entry.get('reason')]
            elif impact.get('status') not in ('pending', 'failed', 'settings_ready') and not (
                    impact.get('status') == 'blocked' and not any(entry.get('version') and not entry.get('decision')
                    for entry in impact.get('results', []))
                    and name_only_changes(impact.get('changes', {}), impact.get('linked_renames', {}))):
                return jsonify(error='이미 점검을 시작했거나 완료했어요.'), 409
            ctx.recover_stale(con)
            if con.execute("SELECT 1 FROM jobs WHERE project=? AND status='running'", (pid,)).fetchone():
                return jsonify(error='진행 중인 AI 작업이 끝난 뒤 시작해 주세요.'), 409
            changes = (impact['changes'] or impact.get('reviewed_changes', {})) if retry else impact['changes']
            character_change = changes.get('characters:0', {})
            if isinstance(character_change.get('before'), str) and isinstance(character_change.get('after'), str):
                detected = linked_entity_renames(character_change['before'], character_change['after'], characters=True)
                impact.setdefault('linked_renames', {}).update(detected)
            rename_only = name_only_changes(changes, impact.get('linked_renames', {}))
            settings_phase = not retry and not impact.get('settings_checked') and any(
                key.split(':')[0] in REFERENCE_DOCUMENTS for key in changes)
            if settings_phase and impact.get('setup_review') and any(
                item.get('setup_edit_base') for item in state['drafts'].values()
            ):
                return jsonify(error='직접 수정한 설정을 먼저 변경하거나 복원해 주세요.'), 409
            chapters = {int(key.split(':')[1]): item for key, item in state['official'].items() if key.startswith('episode:')}
            rejected_by_episode = {}
            if not retry:
                for entry in impact.get('rejected_results', []):
                    if entry['episode'] in chapters:
                        rejected_by_episode.setdefault(entry['episode'], []).append(entry)
            if retry:
                targets = [episode]
                impact['retry_status'] = impact['status']
                impact.pop('retry_error', None)
            else:
                first_episode = min(int(key.split(':')[1]) if key.startswith('episode:') else 0 for key in changes)
                targets = sorted(number for number in chapters if number > first_episode or number in rejected_by_episode)
            basis = ctx.revision_basis(state, targets)
            reference_draft_basis = {key: item['version'] for key, item in state['drafts'].items()
                                     if key.split(':')[0] in REFERENCE_DOCUMENTS}
            run_id = uuid.uuid4().hex
            impact.update(status='running', run=run_id, checked=0, total=len(targets), started=time.time())
            impact.pop('error', None)
            ctx.save(con, pid, state)
        owner = session.get('uid')

        def perform():
            ctx.job_context.owner = owner
            revised, results = {}, []
            linked_drafts = {}
            context = json.dumps({
                '변경': changes, '분량': state['settings'], '이전 점검 의견': retry_notes,
                '확정 설정': {key: item['text'] for key, item in state['official'].items() if not key.startswith('episode:') and key != 'guidelines:0'},
            }, ensure_ascii=False)

            def update_progress(checked, phase='수정할 회차 확인 중'):
                with ctx.db() as con:
                    con.execute('BEGIN IMMEDIATE')
                    _, latest = ctx.load(con, pid)
                    current = latest.get('revision_impact', {})
                    if current.get('run') != run_id or current['status'] != 'running' or latest['archived'] or ctx.revision_basis(latest, targets) != basis:
                        raise ValueError('점검 중 작품이 변경됐어요.')
                    current.update(checked=checked, phase=phase, started=time.time())
                    ctx.save(con, pid, latest)

            try:
                old_length = changes.get('settings:0', {}).get('before')
                if isinstance(old_length, str):
                    old_length = json.loads(old_length)
                minimum_chars = state['settings']['target_chars']
                length_changed = isinstance(old_length, dict) and old_length.get('target_chars') != minimum_chars
                linked_sources = {key: record for key, record in {**state['official'], **state['drafts']}.items()
                                  if key.split(':')[0] in REFERENCE_DOCUMENTS and (key not in changes or len(changes) > 1)}
                if settings_phase:
                    renames = impact.get('linked_renames', {})
                    preserve_new = bool(impact.get('setup_review'))
                    for kind in ('plan', *REFERENCE_DOCUMENTS):
                        key = f'{kind}:0'
                        if kind == 'plan' and 'plan:0' in state['official']:
                            continue
                        source = state['drafts'].get(key) or state['official'].get(key)
                        if source:
                            text = replace_linked_names(source['text'], renames, preserve_new=preserve_new)
                            if text != source['text']:
                                linked_drafts[key] = (text, '확정한 이름 변경 반영')
                    linked_sources = {key: {**source, 'text': linked_drafts.get(key, (source['text'], ''))[0],
                                            'manual_lines': [replace_linked_names(line, renames, preserve_new=preserve_new)
                                                             for line in source.get('manual_lines', [])]}
                                      for key, source in linked_sources.items()
                                      if preserve_new or key not in linked_drafts or len(changes) > 1}
                if settings_phase and linked_sources and not rename_only:
                    proposal = ctx.generate_attempt(
                        '확정 설정 변경과 다른 설정 자료 사이의 직접 모순만 찾으세요. '
                        '모순이 있는 자료만 updates에 넣으세요. 전체 수정본은 기존 항목·형식·회차 번호와 무관한 문구를 유지해 content에 쓰세요. '
                        '초안, 직접 수정한 줄이 있는 자료, 이번에 확정 변경된 자료는 content를 비우고 '
                        '충돌하는 구절만 patches에 넣으세요. old에는 초안에 정확히 한 번 나오는 원문을, new에는 교체 문구를 쓰세요. '
                        '직접 수정한 줄은 그대로 유지하세요. 직접 수정한 줄에 충돌이 있으면 고치지 말고 conflict에 설명하세요. '
                        '없는 사실을 창작하지 마세요. 모순이 없으면 updates는 빈 배열입니다. '
                        'impact에 무엇을 반영했는지 한 문장으로 쓰세요.\n확정 변경:\n'
                        + json.dumps(changes, ensure_ascii=False) + '\n연결 자료:\n'
                        + json.dumps({key: {'초안': key in state['drafts'], '본문': value['text'],
                                            '직접 수정한 줄': value['manual_lines']}
                                      for key, value in linked_sources.items()}, ensure_ascii=False),
                        task='linked_references')
                    updates = proposal.get('updates') if isinstance(proposal, dict) else None
                    if not isinstance(updates, list):
                        raise ValueError('설정 변경 반영 결과를 확인하지 못했어요. 다시 점검해 주세요.')
                    seen_updates = set()
                    for item in updates:
                        if not isinstance(item, dict):
                            raise ValueError('설정 수정안의 형식을 확인하지 못했어요.')
                        key = f"{item.get('kind')}:0"
                        if key not in linked_sources or key in seen_updates:
                            raise ValueError('설정 수정안의 대상이 잘못되었거나 중복됐어요. 다시 점검해 주세요.')
                        seen_updates.add(key)
                        source = linked_sources[key]
                        locked = source['manual_lines']
                        conflict = item.get('conflict') if isinstance(item.get('conflict'), str) else ''
                        text = item.get('content')
                        if key in state['drafts'] or key in changes or locked:
                            text = source['text']
                            replaced = item.get('patches')
                            if replaced is None and conflict and not item.get('content'):
                                replaced = []
                            if not isinstance(replaced, list) or (not replaced and item.get('content')):
                                raise ValueError('이 설정은 교체할 구절을 지정해야 해요. 다시 점검해 주세요.')
                            for patch in replaced:
                                try:
                                    revised_text = apply_patches(text, [patch])
                                except ValueError as error:
                                    raise ValueError('설정 수정 구절을 적용하지 못했어요. 기존 자료는 유지했고 다시 점검해야 해요.') from error
                                if any(revised_text.count(line) < text.count(line) for line in locked):
                                    conflict = conflict or '직접 수정한 내용과 다른 설정이 충돌해요. 직접 확인해 주세요.'
                                else:
                                    text = revised_text
                        if isinstance(text, str) and text.strip() and (text.strip() != source['text'].strip() or conflict):
                            linked_drafts[key] = (text.strip(), conflict or str(item.get('impact') or '확정 설정 변경 반영'))
                if settings_phase:
                    with ctx.db() as con:
                        con.execute('BEGIN IMMEDIATE')
                        _, latest = ctx.load(con, pid)
                        current = latest.get('revision_impact', {})
                        if current.get('run') != run_id or latest['archived'] or ctx.revision_basis(latest, targets) != basis:
                            raise ValueError('점검 중 작품이 변경됐어요.')
                        if {key: item['version'] for key, item in latest['drafts'].items()
                                if key.split(':')[0] in REFERENCE_DOCUMENTS} != reference_draft_basis:
                            raise ValueError('점검 중 설정 초안이 바뀌었어요. 최신 자료로 다시 점검해 주세요.')
                        waiting = []
                        for key, (text, reason) in linked_drafts.items():
                            if latest['drafts'].get(key) != state['drafts'].get(key):
                                raise ValueError('점검 중 설정 초안이 바뀌었어요. 최신 자료로 다시 점검해 주세요.')
                            ctx.save_linked_draft(con, pid, latest, key, text, reason, renames, preserve_new=preserve_new)
                            waiting.append(key)
                        current.update(settings_checked=True, waiting_settings=waiting, linked_drafts=waiting[:],
                                       status='awaiting_settings' if waiting else 'settings_ready' if targets else 'done')
                        if not waiting and not targets:
                            current['changes'] = {}
                        ctx.save(con, pid, latest)
                    return
                for index, number in enumerate(targets):
                    update_progress(index)
                    manuscript = chapters[number]['text']
                    original_manuscript = manuscript
                    if set(changes) == {'plot:0'} and not retry:
                        checked = ctx.generate_attempt(
                            '플롯 변경이 확정 원고와 실제로 충돌하는지만 읽기 전용으로 점검하세요. '
                            '원고에 이미 나온 사건을 플롯에 정리한 것은 원고 수정 사유가 아닙니다. '
                            '앞으로 일어날 사건·관계 변화는 현재 회차에 미리 등장하거나 설명할 필요가 없습니다. '
                            '작가가 역할을 아직 정하지 않은 인물은 미정이라는 설명을 본문에 넣지 마세요. '
                            '예정 전개·작가 요청·미래 시점 메모는 인물의 현재 지식이나 현재 발생한 사건이 아닙니다. '
                            '변경 후 플롯과 이 원고에 동시에 성립할 수 없는 명시적 사실만 issues에 적고, '
                            '각 항목에는 충돌하는 원고 구절과 변경 내용을 함께 쓰세요. '
                            '충돌이 없거나 기존 원고를 그대로 두고 이후 회차에서 전개할 수 있으면 issues는 빈 배열입니다. '
                            '수정문이나 patches는 반환하지 마세요.\n' + context
                            + f'\n검토 회차: {number}\n확정 원고:\n' + manuscript,
                            task='episode_continuity')
                        issues = checked.get('issues') if isinstance(checked, dict) else None
                        if not isinstance(issues, list) or any(not isinstance(item,str) or not item.strip() for item in issues):
                            raise ValueError('플롯 변경과 원고의 충돌 여부를 확인하지 못했어요.')
                        issues = ctx.actual_continuity_issues(issues)
                        if not issues:
                            results.append({'episode':number, 'reason':'플롯 변경과 충돌하지 않아 기존 원고를 유지했어요.', 'patches':[], 'blocked':False})
                            continue
                        plot_conflicts = '\n실제 충돌 확인 결과:\n' + '\n'.join(issues)
                    else:
                        plot_conflicts = ''
                    if not rename_only:
                        manuscript = replace_linked_names(manuscript, impact.get('linked_renames', {}), preserve_new=True)
                    rejected_rows = rejected_by_episode.get(number, [])
                    rejected_notes = '\n'.join(row['reason'] for row in rejected_rows if row.get('reason'))
                    maximum_chars = state['settings']['target_chars'] + AI_MANUSCRIPT_LENGTH_MARGIN
                    prompt = (
                        '확정 변경 때문에 이 원고에서 직접 수정해야 할 구절만 찾아 최소한으로 고치세요. '
                        '설정 자체를 바꾸지 마세요. 회차 계획과 겹치는 등장·사건은 현재 회차 안에서 덜어내거나 장면을 재구성해 직접 수정하세요. '
                        '이미 확정된 새 설정을 원고에 묘사하는 것은 설정 변경이 아닙니다. '
                        '현재 장면에 반영해야 하는 인물의 감정이나 배경이 빠졌고 기존 문장에 자연스럽게 보완할 수 있다면, 의견만 반환하지 말고 patches에 수정 구절을 넣으세요. '
                        '이 회차에서 드러낼 필요가 없는 설정의 미등장은 수정 사유가 아닙니다. '
                        '플롯의 예정 사건이나 관계 변화, 작가 요청·미정 메모를 본문에 설명하거나 현재 인물의 지식으로 넣지 마세요. 이미 원고와 맞는 플롯 변경은 수정하지 마세요. '
                        + ('이 회차의 새 수정안을 작성하세요. 현재 확정 원고와 설정을 기준으로 이전 점검 의견을 확인하고, 현재 회차 안에서 해결할 수 있는 문제는 실제 patches로 반환하세요. ' if retry else '') +
                        '기존 인물·장소·설정이 원고에서 사라지거나 새로운 인물·장소·설정이 필요하면 impact에 구체적으로 적고 blocked=true를 반환하세요. '
                        'patches의 old에는 원고에서 유일하게 나오는 연속 구절을 그대로 인용하세요. '
                        '변경이 필요 없으면 빈 patches를 반환하세요. 애매하거나 설정 변경이 필요하면 blocked=true입니다.\n'
                        f'수정한 원고 전체는 공백 포함 최대 {maximum_chars}자입니다.\n'
                        + context + plot_conflicts + (f'\n이 회차에서 거절한 수정안의 점검 의견을 다시 해결하세요:\n{rejected_notes}' if rejected_rows else '')
                        + f'\n{number}화 원고:\n' + manuscript)
                    key = f'episode:{number}'
                    existing = state['drafts'].get(key)
                    can_edit = not existing or existing['version'] == superseded.get(key)
                    if rename_only:
                        updated = replace_linked_names(manuscript, impact['linked_renames'], preserve_new=True)
                        if updated != manuscript:
                            if can_edit:
                                revised[number] = updated
                                results.append({'episode': number, 'reason': '확정한 이름과 조사만 변경했어요.',
                                                'patches': [{'old': manuscript, 'new': updated}], 'blocked': False})
                            else:
                                results.append({'episode': number, 'reason': '기존 초안을 먼저 확인해 주세요.',
                                                'blocked': True})
                        continue
                    failure = ''
                    if can_edit and manuscript != original_manuscript:
                        revised[number] = manuscript
                    correction = ''
                    for attempt in range(2):
                        try:
                            answer = ctx.generate_attempt(prompt + correction, task='batch_revision')
                        except RetryableAIError:
                            failure = 'Gemini 응답을 받지 못했어요. 이 회차만 다시 시도할 수 있어요.'
                            break
                        if (not isinstance(answer, dict) or type(answer.get('blocked')) is not bool
                                or not isinstance(answer.get('impact'), str) or not isinstance(answer.get('patches'), list)):
                            failure = 'AI 수정안의 형식을 확인하지 못했어요.'
                        else:
                            patches = [{**patch, 'new': normalize_manuscript_breaks(patch['new'])}
                                       if isinstance(patch, dict) and isinstance(patch.get('new'), str) else patch
                                       for patch in answer['patches']]
                            patches = [patch for patch in patches
                                       if not (isinstance(patch, dict) and isinstance(patch.get('old'), str)
                                               and patch.get('old') == patch.get('new'))]
                            try:
                                updated = apply_revision_patches(manuscript, patches) if patches and can_edit else manuscript
                                updated = readable_manuscript(replace_linked_names(updated, impact.get('linked_renames', {}), preserve_new=True))
                            except ValueError:
                                failure = 'AI가 제안한 수정 구절을 원고에 적용하지 못했어요.'
                            else:
                                failure = ''
                                if updated.strip() and updated != original_manuscript and can_edit and (len(updated) <= maximum_chars or length_changed):
                                    revised[number] = updated
                                break
                        if not attempt:
                            update_progress(index, f'{number}화 수정 구절 다시 찾는 중')
                            correction = (
                                '\n직전 제안은 적용되지 않았습니다. 수정 필요성부터 다시 판단하세요. '
                                '수정이 필요하면 원고에서 문장부호와 띄어쓰기까지 동일한 앞뒤 문맥을 old에 넣고, '
                                '반복된 문구는 위치마다 서로 다른 문맥을 인용하세요. 각 old가 원고에 정확히 한 번 있고 수정 범위가 서로 겹치지 않게 하세요. '
                                '수정이 필요 없으면 blocked=false와 빈 patches를 반환하세요.\n직전 제안:\n'
                                + json.dumps(answer, ensure_ascii=False)
                            )
                    if failure:
                        if rejected_rows:
                            results.extend({**row, 'reason': '\n'.join(filter(None, (row.get('reason'), failure)))}
                                           for row in rejected_rows)
                        else:
                            results.append({'episode': number, 'reason': failure, 'blocked': True, 'patch_error': True})
                        continue
                    candidate = revised.get(number, manuscript)
                    if number in revised and original_manuscript != manuscript:
                        patches = [{'old': original_manuscript, 'new': candidate}]
                    if (length_changed and (not existing or existing['version'] == superseded.get(key))
                            and not minimum_chars <= len(candidate) <= maximum_chars):
                        length_basis = hashlib.sha256(json.dumps(['episode-scope-v1', candidate, changes, state['settings'],
                            {k: v['text'] for k, v in state['official'].items() if not k.startswith('episode:') and k != 'guidelines:0'}],
                            ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                        saved_length = impact.get('length_candidates', {}).get(str(number), {})
                        def checkpoint_length(text):
                            with ctx.db() as con:
                                con.execute('BEGIN IMMEDIATE')
                                _, latest = ctx.load(con, pid)
                                current = latest.get('revision_impact', {})
                                if current.get('run') != run_id or ctx.revision_basis(latest, targets) != basis or latest['archived']:
                                    raise ValueError('분량 조정 중 작품이 변경됐어요.')
                                current.setdefault('length_candidates', {})[str(number)] = {'basis': length_basis, 'text': text}
                                ctx.save(con, pid, latest)
                        try:
                            text = adjust_revision_length(candidate, minimum_chars, maximum_chars,
                                context+'\n회차 범위:\n'+json.dumps(json.loads(build_episode_context(state,number)).get('episode_scope',{}),ensure_ascii=False),
                                ctx.generate_attempt, lambda phase: update_progress(index, f'{number}화 '+phase),
                                saved=saved_length.get('text') if saved_length.get('basis') == length_basis else None,
                                checkpoint=checkpoint_length,
                                normalize=lambda text: replace_linked_names(text, impact.get('linked_renames', {}), preserve_new=True))
                        except RetryableAIError:
                            text = None
                        if text:
                            revised[number] = text
                            patches = [{'old': original_manuscript, 'new': text}]
                        if not minimum_chars <= len(revised.get(number, manuscript)) <= maximum_chars:
                            revised.pop(number, None)
                            results.append({'episode': number, 'reason': f'새 분량 {minimum_chars:,}~{maximum_chars:,}자에 맞추지 못했어요.',
                                            'blocked': True, 'patch_error': True})
                            continue
                    if rejected_rows and number not in revised:
                        results.extend(rejected_rows)
                        continue
                    if existing and number not in revised and (patches or key in superseded):
                        results.append({'episode': number, 'reason': '기존 초안이 남아 있어요. 초안을 확인하거나 이 회차를 다시 점검해 주세요.',
                                        'blocked': True, 'patch_error': True})
                        continue
                    if retry and not rejected_rows and not patches and not answer['blocked'] and number not in revised:
                        results.append({'episode': number, 'reason': '다시 점검한 결과 수정할 부분이 없어요.',
                                        'blocked': False, 'decision': 'acknowledged'})
                        continue
                    if patches or answer['blocked']:
                        results.append({'episode': number, 'reason': answer['impact'], 'patches': patches,
                                        'blocked': bool(rejected_rows) or bool(existing) or answer['blocked'] or (bool(patches) and number not in revised)})
                def auto_repair(number, issues):
                    key = f'episode:{number}'
                    existing = state['drafts'].get(key)
                    if number not in targets or (existing and existing['version'] != superseded.get(key)):
                        return False
                    timeline = revision_timeline(chapters, revised, state['drafts'], impact.get('results', []))
                    source = timeline[number]
                    update_progress(len(targets), f'{number}화 점검 의견을 원고에 반영 중')
                    try:
                        scoped_context=context+'\n회차 범위:\n'+json.dumps(json.loads(build_episode_context(state,number)).get('episode_scope',{}),ensure_ascii=False)
                        text = repair_reviewed_manuscript(source, issues, scoped_context,
                            {n: timeline[n] for n in (number-1,number,number+1) if n in timeline},
                            ctx.generate_attempt, normalize=lambda value: replace_linked_names(value,
                                impact.get('linked_renames', {}), preserve_new=True))
                        if text and not minimum_chars <= len(text) <= minimum_chars + AI_MANUSCRIPT_LENGTH_MARGIN:
                            text = adjust_revision_length(text, minimum_chars, minimum_chars + AI_MANUSCRIPT_LENGTH_MARGIN,
                                scoped_context, ctx.generate_attempt, lambda phase: update_progress(len(targets), f'{number}화 '+phase),
                                normalize=lambda value: replace_linked_names(value, impact.get('linked_renames', {}), preserve_new=True))
                    except RetryableAIError:
                        return False
                    if not text:
                        return False
                    revised[number] = text
                    results[:] = [row for row in results if row['episode'] != number]
                    results.append({'episode': number, 'reason': '점검에서 확인한 문제를 원고에 반영했어요.',
                                    'patches': [{'old': chapters[number]['text'], 'new': text}], 'blocked': False})
                    return True

                if not rename_only:
                    needs_repair = {row['episode'] for row in results if row.get('blocked')}
                    for number in sorted(needs_repair):
                        issues = [row['reason'] for row in results if row['episode'] == number and row.get('reason')]
                        auto_repair(number, issues)
                audited_metadata = {}
                def remember_audit_metadata(number, manuscript, nearby, result):
                    if isinstance(result, dict) and all(isinstance(result.get(k), str) and result[k].strip()
                                                       for k in ('summary', 'facts')):
                        audited_metadata[number] = (manuscript, dict(nearby), normalize_metadata(result))

                metadata_instruction = ('\n같은 검토 회차 원고에서 summary(줄거리 요약)와 facts(실제 사건·인물별 지식·미해결 복선)도 '
                                        '함께 반환하세요. 다른 회차의 사건과 예정 사건을 섞지 마세요.')
                if revised and not rename_only:
                    timeline = revision_timeline(chapters, revised, state['drafts'], impact.get('results', []))
                    audited_neighbors = {}
                    for number in sorted(timeline):
                        update_progress(len(targets), f'{number}화 일관성 재점검 중')
                        # Keep every candidate chapter in the review, with full adjacent manuscripts.
                        nearby = {n: timeline[n] for n in (number - 1, number, number + 1) if n in timeline}
                        prompt = (
                            '수정 이후 원고의 일관성을 점검하세요. 실제 모순이나 판단할 수 없는 항목만 issues에 반환하세요. '
                            '확정 설정과 주변 원고를 함께 비교하세요. 인물·장소·설정의 추가나 삭제가 필요하거나 '
                            '분량 변경에 따른 회차 삽입·재배치가 필요하면 반드시 issues에 적으세요.\n' + context + '\n'
                            + json.dumps({'검토 회차': number, '주변 원고': nearby,
                                          '다른 회차 기록': {n: chapters[n].get('summary', '') for n in chapters if n not in nearby}}, ensure_ascii=False))
                        try:
                            checked = ctx.generate_attempt(prompt + (metadata_instruction if number in revised else ''),
                                task='episode_review_metadata' if number in revised else 'episode_continuity')
                            issues = checked.get('issues')
                            if number in revised:
                                remember_audit_metadata(number, timeline[number], nearby, checked)
                        except RetryableAIError:
                            issues = ['Gemini 응답을 받지 못해 이 회차의 일관성은 추가 확인이 필요해요.']
                        if not isinstance(issues, list) or any(not isinstance(issue, str) or not issue.strip() for issue in issues):
                            raise ValueError('일관성 점검 결과를 확인할 수 없어요.')
                        issues = ctx.actual_continuity_issues(issues)
                        if issues and auto_repair(number, issues):
                            timeline = revision_timeline(chapters, revised, state['drafts'], impact.get('results', []))
                            nearby = {n: timeline[n] for n in (number-1,number,number+1) if n in timeline}
                            update_progress(len(targets), f'{number}화 자동 수정 결과 재점검 중')
                            checked = ctx.generate_attempt(
                                '수정 이후 원고의 일관성을 점검하세요. 방금 수정한 실제 원고에서 해결되지 않은 모순만 issues로 반환하세요. '
                                '이전 의견을 그대로 반복하지 마세요.\n'+context+'\n'
                                + json.dumps({'검토 회차':number,'주변 원고':nearby},ensure_ascii=False)
                                + (metadata_instruction if number in revised else ''),
                                task='episode_review_metadata' if number in revised else 'episode_continuity')
                            if number in revised:
                                remember_audit_metadata(number, timeline[number], nearby, checked)
                            remaining = checked.get('issues')
                            if not isinstance(remaining,list) or any(not isinstance(item,str) or not item.strip() for item in remaining):
                                raise ValueError('자동 수정 후 일관성 점검 결과를 확인하지 못했어요.')
                            issues = ctx.actual_continuity_issues(remaining)
                        audited_neighbors[number] = dict(nearby)
                        if issues:
                            kept = next((entry for entry in results if entry['episode'] == number
                                         and entry.get('decision') == 'kept'), None)
                            if kept:
                                kept['reason'] = '\n'.join(filter(None, [kept.get('reason'), *issues]))
                            else:
                                results.append({'episode': episode if retry else number,
                                                'reason': (f'{number}화: ' if retry and number != episode else '') + '\n'.join(issues),
                                                'blocked': True})
                    final_timeline = revision_timeline(chapters, revised, state['drafts'], impact.get('results', []))
                    for number, audited in audited_neighbors.items():
                        nearby = {n: final_timeline[n] for n in (number-1,number,number+1) if n in final_timeline}
                        if nearby == audited:
                            continue
                        update_progress(len(targets), f'{number}화 주변 회차 수정 후 연결 재확인 중')
                        checked = ctx.generate_attempt('수정 이후 원고의 일관성을 점검하세요. 다른 회차의 자동 수정 이후에도 남은 실제 모순만 issues로 반환하세요.\n'
                            + context+'\n'+json.dumps({'검토 회차':number,'주변 원고':nearby},ensure_ascii=False)
                            + (metadata_instruction if number in revised else ''),
                            task='episode_review_metadata' if number in revised else 'episode_continuity')
                        if number in revised:
                            remember_audit_metadata(number, final_timeline[number], nearby, checked)
                        issues = checked.get('issues')
                        if not isinstance(issues,list) or any(not isinstance(item,str) or not item.strip() for item in issues):
                            raise ValueError('회차 연결 재점검 결과를 확인하지 못했어요.')
                        issues = ctx.actual_continuity_issues(issues)
                        if issues:
                            results.append({'episode':episode if retry else number,'reason':'\n'.join(issues),'blocked':True})
                metadata = {}
                for number, manuscript in list(revised.items()):
                    if rename_only:
                        metadata[number] = {field: replace_linked_names(chapters[number].get(field, ''),
                                            impact['linked_renames'], preserve_new=True) for field in ('summary', 'facts')}
                        continue
                    cached_metadata = audited_metadata.get(number)
                    final_nearby = {n: final_timeline[n] for n in (number-1,number,number+1) if n in final_timeline}
                    if cached_metadata and cached_metadata[0] == manuscript and cached_metadata[1] == final_nearby:
                        metadata[number] = cached_metadata[2]
                        continue
                    update_progress(len(targets), f'{number}화 사건 기록 갱신 중')
                    try:
                        metadata[number] = normalize_metadata(ctx.generate_attempt(
                            '이 원고의 줄거리와 실제 사건을 정리하세요. JSON {"summary":"요약","facts":"사건 기록"}만 반환하세요.\n'
                            + manuscript, task='metadata'))
                    except RetryableAIError:
                        revised.pop(number)
                        results.append({'episode': number, 'reason': 'Gemini가 사건 기록을 완료하지 못했어요. 이 회차만 다시 시도해 주세요.',
                                        'blocked': True, 'patch_error': True})
                with ctx.db() as con:
                    con.execute('BEGIN IMMEDIATE')
                    _, latest = ctx.load(con, pid)
                    current = latest.get('revision_impact', {})
                    if current.get('run') != run_id or latest['archived'] or ctx.revision_basis(latest, targets) != basis:
                        raise ValueError('점검 중 작품이 변경됐어요.')
                    for entry in results:
                        number = entry['episode']
                        if 'patches' not in entry or number not in revised:
                            continue
                        key = f'episode:{number}'
                        original = chapters[number]
                        findings = [dict(finding) for finding in
                                    latest.get('records', {}).get(str(number), {}).get('findings', [])]
                        if impact.get('linked_renames'):
                            findings = [{field: replace_linked_names(value, impact['linked_renames'], preserve_new=True)
                                         if isinstance(value, str) else value for field, value in finding.items()}
                                        for finding in findings]
                        for finding in findings:
                            quote = finding.get('quote')
                            if quote and quote not in revised[number]:
                                pattern = r'\s+'.join(re.escape(part) for part in re.split(r'\s+', quote.strip()))
                                matches = list(re.finditer(pattern, revised[number]))
                                if len(matches) == 1:
                                    finding['quote'] = matches[0][0]
                        if any(entry.get('quote') and entry['quote'] not in revised[number]
                               for entry in findings if not entry.get('removed')):
                            entry['blocked'] = True
                            entry['reason'] += '\n수정으로 기존 설정의 근거 구절이 달라졌어요. 설정 자료를 확인해 주세요.'
                        content = {**metadata[number], 'text': revised[number], 'metadata_stale': False,
                                   'findings': findings,
                                   'impact': entry['reason'], 'base_official_version': original['version']}
                        if original.get('title'):
                            content['title'] = original['title']
                        vid = ctx.version(con, pid, 'episode', number, content, 'draft')
                        if key in superseded and latest['drafts'].get(key, {}).get('version') == superseded[key]:
                            con.execute("UPDATE versions SET status='rejected' WHERE project=? AND id=?", (pid, superseded[key]))
                            current['superseded_drafts'].pop(key, None)
                        latest['drafts'][key] = {**content, 'version': vid, 'status': 'draft'}
                        entry['version'] = vid
                        entry['base_version'] = original['version']
                    blocked = any(entry['blocked'] or entry.get('decision') == 'kept' for entry in results)
                    if retry and not results:
                        results = previous_rows
                        current['retry_error'] = '새 수정안을 만들지 못했어요. 점검 의견을 보고 직접 편집할 수 있어요.'
                    current.update(results=retained + results, checked=len(targets), basis=ctx.revision_basis(latest),
                                   status='blocked' if blocked else 'ready' if revised else 'done')
                    current.pop('rejected_results', None)
                    if retry:
                        current.pop('retry_status')
                        current['status'] = 'resolved' if all(entry.get('decision') for entry in current['results']) else 'blocked'
                        if current['status'] == 'resolved' and current['changes']:
                            current['reviewed_changes'] = current['changes']
                            current['changes'] = {}
                    # Automatic generation/review never implies the user's approval.
                    if current['status'] == 'done':
                        current['changes'] = {}
                    ctx.save(con, pid, latest)
            except Exception as error:
                ctx.app.logger.warning('Revision impact processing failed', exc_info=False)
                with ctx.db() as con:
                    con.execute('BEGIN IMMEDIATE')
                    _, latest = ctx.load(con, pid)
                    current = latest.get('revision_impact', {})
                    if current.get('run') == run_id:
                        current['status'] = current.pop('retry_status', 'failed')
                        current['error'] = str(error) if isinstance(error, (ValueError, AIUsageLimit)) else '변경 반영을 완료하지 못했어요. 기존 자료는 유지됩니다.'
                        if retry:
                            current['retry_error'] = '새 수정안을 만들지 못했어요. 다시 시도할 수 있어요.'
                        ctx.save(con, pid, latest)
            finally:
                ctx.job_context.owner = None
                ctx.job_context.calls = 0

        ctx.workers.submit(perform)
        return jsonify(ok=True), 202
    ctx.revision_impact = revision_impact

