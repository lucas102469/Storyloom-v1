"""Export and import formats and transactional restoration."""
from .common import KINDS, REFERENCE_DOCUMENTS, Response, hashlib, io, json, jsonify, parse_key, re, request, restore_review, send_file, session, settings_value, time, uuid, zipfile
from .context import ApplicationContext


def install(ctx: ApplicationContext):
    @ctx.app.route('/api/projects/<pid>/export', methods=['GET', 'POST'])
    def export_project(pid):
        options = request.form if request.method == 'POST' else request.args
        fmt = options.get('format', 'json')
        with ctx.db() as con:
            row, state = ctx.load(con, pid)
            versions = ([dict(r) for r in con.execute('SELECT * FROM versions WHERE project=? ORDER BY id', (pid,))]
                        if fmt in ('json', 'zip') else [])
            snapshots = ([{**dict(r), 'payload': json.loads(r['payload'])} for r in con.execute(
                'SELECT episode,version,payload,created FROM episode_setting_snapshots WHERE project=? ORDER BY episode,version',
                (pid,))] if fmt in ('json', 'zip') else [])
        package = {'schema_version': 1, 'title': row['title'], 'state': state, 'versions': versions,
                   'episode_setting_snapshots': snapshots}
        if fmt == 'episodes':
            selected = options.getlist('episode')
            if not selected or any(not re.fullmatch(r'[1-9][0-9]{0,4}', item) for item in selected):
                raise ValueError('내려받을 확정 회차를 선택하세요.')
            files = []
            for number in sorted(set(map(int, selected))):
                record = state['official'].get(f'episode:{number}')
                if not record:
                    raise ValueError('확정된 회차만 내려받을 수 있어요.')
                title = str(record.get('title') or '')
                safe_title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', title).strip(' .')[:100]
                name = f'{number:02d}화' + (f' - {safe_title}' if safe_title else '')
                heading = f'{number}화' + (f' · {title}' if title else '')
                files.append((name + '.txt', (heading + '\n\n' + record['text'] + '\n').encode('utf-8-sig')))
            if len(files) == 1:
                name, data = files[0]
                return send_file(io.BytesIO(data), mimetype='text/plain', as_attachment=True, download_name=name)
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
                for name, data in files:
                    archive.writestr(name, data)
            buffer.seek(0)
            return send_file(buffer, mimetype='application/zip', as_attachment=True, download_name='회차 원고.zip')
        if fmt == 'settings-txt':
            labels = {'plan': '기획안', 'characters': '등장인물', 'world': '세계관', 'places': '장소',
                      'lore': '주요 설정', 'plot': '플롯·회차 구성'}
            sections = [label + '\n\n' + state['official'][f'{kind}:0']['text']
                        for kind, label in labels.items() if f'{kind}:0' in state['official']]
            if not sections:
                raise ValueError('내려받을 확정 기획안이나 설정이 없어요.')
            data = (row['title'] + '\n\n' + '\n\n'.join(sections) + '\n').encode('utf-8-sig')
            return send_file(io.BytesIO(data), mimetype='text/plain', as_attachment=True,
                             download_name='기획안과 설정.txt')
        if fmt == 'json':
            return Response(json.dumps(package, ensure_ascii=False, indent=2), mimetype='application/json',
                            headers={'Content-Disposition': 'attachment; filename="storyloom-backup.json"'})
        if fmt not in ('txt', 'md', 'zip'):
            raise ValueError('내보내기는 json/txt/md/zip을 지원합니다.')
        text = '# ' + row['title'] + '\n\n'
        for key, record in sorted(state['official'].items(), key=lambda x: (x[0].split(':')[0], int(x[0].split(':')[1]))):
            text += '## ' + key + ' (확정)\n\n' + record['text'] + '\n\n'
        if fmt == 'zip':
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('backup.json', json.dumps(package, ensure_ascii=False, indent=2))
                archive.writestr('official.md', text)
            return Response(buffer.getvalue(), mimetype='application/zip', headers={'Content-Disposition': 'attachment; filename="storyloom.zip"'})
        return Response(text, mimetype='text/plain', headers={'Content-Disposition': f'attachment; filename="storyloom.{fmt}"'})
    ctx.export_project = export_project

    @ctx.app.post('/api/projects/import')
    def import_backup():
        uploaded = request.files.get('backup') if request.mimetype == 'multipart/form-data' else None
        if uploaded:
            try:
                with zipfile.ZipFile(uploaded.stream) as archive:
                    entry = archive.getinfo('backup.json')
                    if entry.file_size > 31 * 1024 * 1024:
                        raise ValueError('백업 안의 JSON이 31MB를 넘어요.')
                    package = json.loads(archive.read(entry))
            except (zipfile.BadZipFile, KeyError, RuntimeError, NotImplementedError, UnicodeDecodeError, json.JSONDecodeError):
                raise ValueError('Storyloom 전체 백업 ZIP을 선택하세요.') from None
        else:
            package = request.get_json(silent=True)
        if not isinstance(package, dict) or package.get('schema_version') != 1 or not isinstance(package.get('state'), dict):
            raise ValueError('Storyloom JSON 백업 파일을 선택하세요.')
        source = package['state']
        state = {'messages': [], 'official': {}, 'drafts': {}, 'settings': settings_value(source.get('settings', {})),
                 'selected': 'plan', 'episode': 1, 'archived': False}
        origin = source.get('branch_origin')
        if origin is not None:
            if (not isinstance(origin, dict) or type(origin.get('episode')) is not int
                    or not 1 <= origin['episode'] <= 10000
                    or any(not isinstance(origin.get(field), str) or not 1 <= len(origin[field]) <= 200
                           for field in ('id', 'title', 'author'))
                    or not isinstance(origin.get('body'), str)
                    or not re.fullmatch(r'[a-f0-9]{64}', origin['body'])
                    or not isinstance(origin.get('prefix'), dict)
                    or set(origin['prefix']) != {str(n) for n in range(1, origin['episode'])}
                    or any(not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value)
                           for value in origin['prefix'].values())):
                raise ValueError('다른 줄거리의 원작 정보가 올바르지 않습니다.')
            if ('setup_flow' in origin and (type(origin['setup_flow']) is not int or origin['setup_flow'] != 1)
                    or 'setup_complete' in origin and type(origin['setup_complete']) is not bool):
                raise ValueError('다른 줄거리 준비 단계가 올바르지 않습니다.')
            state['branch_origin'] = {field: origin[field] for field in
                                      ('id', 'title', 'author', 'episode', 'body', 'prefix')}
            for field in ('settings_source', 'coverColor', 'presentation_version', 'setup_flow', 'setup_complete'):
                if field in origin:
                    state['branch_origin'][field] = origin[field]
        for flag in ('initial_references_pending', 'length_setup_pending'):
            if source.get(flag) is True:
                state[flag] = True
        messages = source.get('messages', [])
        if not isinstance(messages, list) or len(messages) > 100000:
            raise ValueError('대화 백업 형식이 올바르지 않습니다.')
        for message in messages:
            if not isinstance(message, dict) or message.get('role') not in ('user', 'assistant') or not isinstance(message.get('text'), str):
                raise ValueError('대화 백업 형식이 올바르지 않습니다.')
            state['messages'].append({'role': message['role'], 'text': message['text']})
        pid = str(uuid.uuid4())
        title = str(package.get('title', '가져온 작품'))[:100] + ' (복사본)'
        with ctx.db() as con:
            con.execute('BEGIN IMMEDIATE')
            con.execute('INSERT INTO projects(id,title,state,owner) VALUES(?,?,?,?)', (pid, title, '{}', session.get('uid')))
            mapping = {}
            history_keys = {}
            history = package.get('versions', [])
            if not isinstance(history, list) or len(history) > 100000:
                raise ValueError('버전 백업 형식이 올바르지 않습니다.')
            for old in history:
                if not isinstance(old, dict) or not isinstance(old.get('id'), int) or isinstance(old['id'], bool) or old['id'] in mapping:
                    raise ValueError('버전 번호가 없거나 중복된 백업입니다.')
                kind, ep = parse_key(str(old.get('kind')) + ':' + str(old.get('episode')))
                content = json.loads(old['content']) if isinstance(old.get('content'), str) else old.get('content')
                if not isinstance(content, dict) or not isinstance(content.get('text'), str):
                    raise ValueError('버전 본문이 올바르지 않습니다.')
                status = old.get('status')
                if status not in ('draft', 'review', 'confirmed', 'rejected'):
                    raise ValueError('버전 상태가 올바르지 않습니다.')
                mapping[old['id']] = ctx.version(con, pid, kind, ep, content, status)
                history_keys[old['id']] = (kind, ep)
            for group in ('official', 'drafts'):
                records = source.get(group, {})
                if not isinstance(records, dict):
                    raise ValueError('자료 백업 형식이 올바르지 않습니다.')
                for key, record in records.items():
                    kind, ep = parse_key(key)
                    if not isinstance(record, dict) or not isinstance(record.get('text'), str):
                        raise ValueError('자료 본문이 올바르지 않습니다.')
                    if any(not isinstance(record.get(name, ''), str) for name in ('summary', 'facts', 'impact')):
                        raise ValueError('자료 요약 형식이 올바르지 않습니다.')
                    content = {name: str(record.get(name, '')) for name in ('text', 'summary', 'facts', 'impact')}
                    if kind in ('plan', *REFERENCE_DOCUMENTS):
                        manual = record.get('manual_lines')
                        if isinstance(manual, list) and all(isinstance(line, str) for line in manual):
                            content['manual_lines'] = manual
                    if kind in ('characters','world','places','lore'):
                        materialized = record.get('reference_materialized_facts')
                        if materialized is not None:
                            if not isinstance(materialized,list) or not all(isinstance(fact,str) for fact in materialized):
                                raise ValueError('백업의 설정 수정 기록이 올바르지 않습니다.')
                            content['reference_materialized_facts'] = materialized
                        if isinstance(record.get('reference_edit_base'),str):
                            content['reference_edit_base'] = record['reference_edit_base']
                    if kind == 'plot' and state.get('branch_origin',{}).get('setup_flow') and record.get('branch_setup_plot'):
                        if (not isinstance(record.get('branch_retained_plot'),str) or not isinstance(record.get('branch_setup_basis'),str)):
                            raise ValueError('다른 줄거리의 회차 구성 기록이 올바르지 않습니다.')
                        content.update(branch_setup_plot=True,branch_retained_plot=record['branch_retained_plot'],
                                       branch_setup_settings=settings_value(record['branch_setup_settings']),branch_setup_basis=record['branch_setup_basis'])
                    if kind == 'plan' and record.get('plan_reconciled') is True:
                        content['plan_reconciled'] = True
                    if kind == 'episode':
                        content.update(restore_review(record, mapping))
                        stale = record.get('metadata_stale', not bool(content['summary'] and content['facts']))
                        if not isinstance(stale, bool):
                            raise ValueError('회차 요약 갱신 상태가 올바르지 않습니다.')
                        content['metadata_stale'] = stale
                        if isinstance(record.get('title'), str) and record['title'].strip():
                            content['title'] = record['title'].strip()[:120]
                    if kind == 'settings':
                        content['text'] = json.dumps(settings_value(json.loads(content['text']), state['settings']), ensure_ascii=False)
                    status = 'confirmed' if group == 'official' else ('review' if record.get('status') == 'review' else 'draft')
                    vid = mapping.get(record.get('version'))
                    if vid is not None:
                        saved = con.execute('SELECT kind,episode,content,status FROM versions WHERE id=?', (vid,)).fetchone()
                        saved_text = json.loads(saved['content'])['text']
                        current_text = content['text']
                        if kind in ('characters', 'world', 'places', 'lore'):
                            saved_text = ctx.normalize_reference_headings(saved_text)
                            current_text = ctx.normalize_reference_headings(current_text)
                        if saved['kind'] != kind or saved['episode'] != ep or saved_text != current_text:
                            raise ValueError('자료와 버전 이력이 서로 일치하지 않습니다.')
                        if (group == 'official' and saved['status'] != 'confirmed') or (group == 'drafts' and saved['status'] not in ('draft', 'review')):
                            raise ValueError('자료와 버전 상태가 서로 일치하지 않습니다.')
                    else:
                        vid = ctx.version(con, pid, kind, ep, content, status)
                    state[group][key] = {**content, 'version': vid, 'status': status}
                    if group == 'drafts' and 'base_official_version' in record:
                        old_base = record['base_official_version']
                        if old_base is not None and old_base not in mapping:
                            raise ValueError('제안의 기준 버전 이력이 없습니다.')
                        state[group][key]['base_official_version'] = mapping.get(old_base)
                    setup_base = record.get('setup_edit_base')
                    if group == 'drafts' and kind in ('plan', *REFERENCE_DOCUMENTS) and setup_base is not None:
                        if (not isinstance(setup_base, dict) or type(setup_base.get('version')) is not int
                                or setup_base['version'] not in mapping
                                or setup_base.get('status') not in ('draft', 'review', 'confirmed')):
                            raise ValueError('수정 전 자료의 버전 이력이 올바르지 않습니다.')
                        base_id = mapping[setup_base['version']]
                        baseline = con.execute('SELECT kind,episode FROM versions WHERE id=?', (base_id,)).fetchone()
                        if baseline['kind'] != kind or baseline['episode'] != ep or base_id >= vid:
                            raise ValueError('수정 전 자료의 버전 이력이 올바르지 않습니다.')
                        old_official = setup_base.get('base_official_version')
                        if old_official is not None and (type(old_official) is not int or old_official not in mapping):
                            raise ValueError('수정 전 자료의 기준 버전 이력이 없습니다.')
                        state[group][key]['setup_edit_base'] = {
                            'version': base_id, 'status': setup_base['status'],
                            'base_official_version': mapping.get(old_official),
                        }
            state['records'] = {key.split(':')[1]: {
                'version': item['version'], 'summary': item.get('summary', ''),
                'facts': item.get('facts', ''), 'metadata_stale': item.get('metadata_stale', True)
            } for key, item in state['official'].items() if key.startswith('episode:')}
            source_records = source.get('records', {})
            if not isinstance(source_records, dict):
                raise ValueError('백업의 회차 기록이 올바르지 않습니다.')
            for number, record in state['records'].items():
                original = source_records.get(number, {})
                if not isinstance(original, dict):
                    raise ValueError('백업의 회차 기록이 올바르지 않습니다.')
                old_version = original.get('version')
                if old_version is not None and (type(old_version) is not int or mapping.get(old_version) != record['version']):
                    raise ValueError('백업의 회차 기록과 원고 버전이 일치하지 않습니다.')
                record.update(restore_review(original, mapping))
            snapshots = package.get('episode_setting_snapshots', [])
            if not isinstance(snapshots, list) or len(snapshots) > 100000:
                raise ValueError('백업의 당시 설정 기록 형식이 올바르지 않습니다.')
            seen_snapshots = set()
            for snapshot in snapshots:
                if not isinstance(snapshot, dict):
                    raise ValueError('백업의 당시 설정 기록 형식이 올바르지 않습니다.')
                number, old_version = snapshot.get('episode'), snapshot.get('version')
                payload = snapshot.get('payload')
                if (type(number) is not int or not 0 <= number <= 10000
                        or type(old_version) is not int or history_keys.get(old_version) != (('plan',0) if number==0 else ('episode', number))
                        or (number, old_version) in seen_snapshots or not isinstance(payload, dict)
                        or any(not isinstance(payload.get(field), dict)
                               for field in ('official', 'records', 'settings', 'episodes'))
                        or set(payload['episodes']) != {str(n) for n in range(1, number + 1)}):
                    raise ValueError('백업의 당시 설정과 회차 버전이 일치하지 않습니다.')
                seen_snapshots.add((number, old_version))
                payload = json.loads(json.dumps(payload))
                payload['settings'] = settings_value(payload['settings'])
                for key, document in payload['official'].items():
                    kind, ep = parse_key(key)
                    if (kind == 'episode' or not isinstance(document, dict)
                            or not isinstance(document.get('text'), str)):
                        raise ValueError('백업의 당시 설정 본문이 올바르지 않습니다.')
                    if 'version' in document:
                        old_id = document['version']
                        if type(old_id) is not int or history_keys.get(old_id) != (kind, ep):
                            raise ValueError('백업의 당시 설정 버전이 올바르지 않습니다.')
                        document['version'] = mapping[old_id]
                for n, record in payload['records'].items():
                    if (n not in payload['episodes'] or not isinstance(record, dict)
                            or type(record.get('version')) is not int
                            or history_keys.get(record['version']) != ('episode', int(n))):
                        raise ValueError('백업의 당시 회차 기록이 올바르지 않습니다.')
                    restored = restore_review(record, mapping)
                    record['version'] = mapping[record['version']]
                    record.update(restored)
                for n, digest in payload['episodes'].items():
                    if not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest):
                        raise ValueError('백업의 당시 원고 식별 정보가 올바르지 않습니다.')
                body = con.execute('SELECT content FROM versions WHERE id=?', (mapping[old_version],)).fetchone()
                if number and hashlib.sha256(json.loads(body['content'])['text'].encode()).hexdigest() != payload['episodes'][str(number)]:
                    raise ValueError('백업의 당시 원고와 설정이 일치하지 않습니다.')
                con.execute('INSERT INTO episode_setting_snapshots VALUES(?,?,?,?,?)',
                            (pid, number, mapping[old_version], json.dumps(payload, ensure_ascii=False), time.time()))
            batch = source.get('episode_batch')
            if batch is not None:
                if not isinstance(batch, dict):
                    raise ValueError('백업의 이어 쓰기 정보가 올바르지 않습니다.')
                start, next_episode, end = (batch.get(field) for field in ('start', 'next', 'end'))
                status = batch.get('status')
                versions = batch.get('draft_versions')
                if (any(type(number) is not int for number in (start, next_episode, end))
                        or not 1 <= start <= end <= 10000 or not start <= next_episode <= end + 1
                        or status not in ('running', 'stopping', 'paused', 'done') or not isinstance(versions, dict)):
                    raise ValueError('백업의 이어 쓰기 범위가 올바르지 않습니다.')
                restored_versions = {}
                for episode, vid in versions.items():
                    if (not re.fullmatch(r'[1-9][0-9]{0,4}', episode) or not start <= int(episode) < next_episode
                            or type(vid) is not int or history_keys.get(vid) != ('episode', int(episode))):
                        raise ValueError('백업의 이어 쓰기 버전이 올바르지 않습니다.')
                    restored_versions[episode] = mapping[vid]
                paused = status in ('running', 'stopping')
                restored_batch = {'id': uuid.uuid4().hex, 'start': start, 'next': next_episode, 'end': end,
                                  'status': 'paused' if paused else status, 'draft_versions': restored_versions,
                                  'reason': '저장된 회차부터 이어 쓸 수 있어요.' if paused else str(batch.get('reason', ''))}
                needs_approval = batch.get('needs_approval')
                if needs_approval is not None:
                    if type(needs_approval) is not int or str(needs_approval) not in restored_versions:
                        raise ValueError('백업의 검토 대기 회차가 올바르지 않습니다.')
                    restored_batch['needs_approval'] = needs_approval
                old_records = source.get('records', {})
                if isinstance(old_records, dict):
                    for episode, record in state['records'].items():
                        if not start <= int(episode) <= end:
                            continue
                        old_record = old_records.get(episode)
                        if not isinstance(old_record, dict):
                            continue
                        old_review = old_record.get('setting_review')
                        if old_review in ('done', 'continuity'):
                            record['setting_review'] = old_review
                        elif old_review in ('organize', 'review') or str(needs_approval) == episode:
                            record['setting_review'] = 'organize'
                state['episode_batch'] = restored_batch
            if 'settings:0' in state['official']:
                state['settings'] = settings_value(json.loads(state['official']['settings:0']['text']))
            names = source.get('linked_names')
            if isinstance(names, dict) and all(isinstance(old, str) and old and isinstance(new, str) and new
                                                for old, new in names.items()):
                state['linked_names'] = names
            impact = source.get('revision_impact')
            if isinstance(impact, dict) and impact.get('status') == 'pending':
                changes = impact.get('changes')
                if not isinstance(changes, dict):
                    raise ValueError('백업의 변경 사항 형식이 올바르지 않습니다.')
                restored_changes = {}
                for key, change in changes.items():
                    kind, _ = parse_key(key)
                    if kind not in ('settings', 'plan', 'episode', *REFERENCE_DOCUMENTS) or not isinstance(change, dict):
                        raise ValueError('백업의 변경 사항 형식이 올바르지 않습니다.')
                    before, after = change.get('before'), change.get('after')
                    if kind == 'settings':
                        before = json.loads(before) if isinstance(before, str) else before
                        after = json.loads(after) if isinstance(after, str) else after
                        if not isinstance(before, dict) or not isinstance(after, dict):
                            raise ValueError('백업의 분량 변경 형식이 올바르지 않습니다.')
                        before, after = settings_value(before), settings_value(after)
                    elif not isinstance(before, str) or not isinstance(after, str):
                        raise ValueError('백업의 자료 변경 형식이 올바르지 않습니다.')
                    restored_changes[key] = {'before': before, 'after': after}
                if restored_changes:
                    pending = {'id': uuid.uuid4().hex, 'status': 'pending', 'changes': restored_changes}
                    renames = impact.get('linked_renames')
                    if isinstance(renames, dict) and all(isinstance(old, str) and old and isinstance(new, str) and new
                                                         for old, new in renames.items()):
                        pending['linked_renames'] = renames
                    if impact.get('setup_review') is True:
                        pending['setup_review'] = True
                    superseded = impact.get('superseded_drafts', {})
                    if isinstance(superseded, dict):
                        pending['superseded_drafts'] = {
                            key: mapping[vid] for key, vid in superseded.items()
                            if type(vid) is int and vid in mapping and key.startswith('episode:')
                            and state['drafts'].get(key, {}).get('version') == mapping[vid]
                        }
                    state['revision_impact'] = pending
            if source.get('selected') in KINDS:
                state['selected'] = source['selected']
            if isinstance(source.get('episode'), int) and 1 <= source['episode'] <= 10000:
                state['episode'] = source['episode']
            ctx.save(con, pid, state)
        return jsonify(id=pid)
    ctx.import_backup = import_backup

