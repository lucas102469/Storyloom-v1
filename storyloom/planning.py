"""Initial planning, branch continuation and selected lengths."""
from .common import AIUsageLimit, REFERENCE_DOCUMENTS, apply_patches, branch_setup, build_context, difflib, json, jsonify, re, replace_linked_names, request, session, settings_value, source_fingerprint
from .context import ApplicationContext


def install(ctx: ApplicationContext):
    def is_initial_setting_edit(state, kind):
        return (kind in REFERENCE_DOCUMENTS and 'plan:0' in state['official']
                and not any(key.startswith('episode:') for group in ('official', 'drafts') for key in state[group]))
    ctx.is_initial_setting_edit = is_initial_setting_edit

    @ctx.app.post('/api/projects/<pid>/reconcile-plan')
    def reconcile_plan(pid):
        vid = request.json.get('version')
        with ctx.db() as con:
            _, state = ctx.load(con, pid)
            draft = state['drafts'].get('plan:0', {})
            base = draft.get('setup_edit_base')
            if state['archived'] or 'plan:0' in state['official'] or not base or draft.get('version') != vid:
                return jsonify(error='현재 직접 수정한 기획안 초안을 다시 확인해 주세요.'), 409
            previous = con.execute("SELECT content FROM versions WHERE project=? AND kind='plan' AND episode=0 AND id=?",
                                   (pid, base['version'])).fetchone()
            if not previous:
                return jsonify(error='수정 전 기획안 버전을 찾을 수 없어요.'), 409
        text = draft['text']
        protected = draft.get('manual_lines', [])
        before = json.loads(previous['content'])['text']
        # A single spelling extension can be propagated exactly, including repeated syllables.
        tokens = re.compile(r'[가-힣]+|[^가-힣]+')
        old_tokens, new_tokens = tokens.findall(before), tokens.findall(text)
        prepared = text
        if len(old_tokens) == len(new_tokens):
            edits = [(old, new) for old, new in zip(old_tokens, new_tokens) if old != new]
            if len(edits) == 1:
                old, new = edits[0]
                particle = re.search(r'(?:은|는|이|가|을|를|의|도|에게|에서)$', old)
                if particle and new.endswith(particle[0]):
                    old, new = old[:particle.start()], new[:-len(particle[0])]
                if (re.fullmatch(r'[가-힣]{2,4}', old) and re.fullmatch(r'[가-힣]{3,30}', new)
                        and len(new) > len(old) and old[0] == new[0]
                        and all(tag in ('equal', 'insert') for tag, *_ in difflib.SequenceMatcher(None, old, new).get_opcodes())
                        and before.count(old) > 1):
                    renames = {old: new}
                    if len(old) == 3:
                        renames[old[1:]] = new[1:]
                    candidate = replace_linked_names(text, renames, preserve_new=True)
                    if all(candidate.count(part) >= text.count(part) for part in protected):
                        prepared = candidate
        ctx.job_context.owner = session.get('uid')
        try:
            prompt = (
                'PLAN_RECONCILIATION\n사용자가 직접 수정한 기획안을 기준으로, 그 변경 때문에 충돌하는 다른 구절만 수정하세요. '
                '수정 전과 현재를 비교해 사용자가 바꾸거나 삭제한 내용을 보존하세요. 고유명사의 철자와 반복 글자를 그대로 유지하세요. '
                '로그라인, 시놉시스, 제목에서 같은 대상이 확실한 경우에만 이름을 맞추세요. '
                'old는 수정 전 자료가 아니라 사용자가 저장한 기획안에서 문장부호까지 그대로 인용하세요. '
                '같은 이름이 여러 번 나오면 각 위치의 앞뒤 문구를 포함해 서로 다른 old를 만드세요. '
                '같은 이름의 여러 위치는 각각 수정하고, 사용자가 고쳐 놓은 위치는 수정하지 마세요. '
                '새 설정이나 사건은 만들지 마세요. 손볼 곳이 없으면 patches를 빈 배열로 반환하세요.\n'
                + json.dumps({'수정 전 기획안': before, '사용자가 저장한 기획안': prepared, '직접 수정한 구절': protected}, ensure_ascii=False)
                + ('\n동일 인물의 이름 표기는 이미 맞췄습니다. 그 변경을 다시 제안하지 마세요.' if prepared != text else '')
            )
            correction = ''
            for attempt in range(2):
                response = ctx.generate_attempt(prompt + correction, task='plan_reconcile')
                patches = response.get('patches')
                try:
                    revised = prepared
                    if not isinstance(patches, list) or len(patches) > 30:
                        raise ValueError('patches 배열의 형식이 올바르지 않습니다.')
                    for index, patch in enumerate(patches, 1):
                        try:
                            candidate = apply_patches(revised, [patch])
                        except ValueError as exc:
                            raise ValueError(f'{index}번 수정안: {exc}') from exc
                        if any(candidate.count(part) < revised.count(part) for part in protected):
                            raise ValueError(f'{index}번 수정안이 사용자가 고친 구절을 변경합니다.')
                        revised = candidate
                    if not revised.strip() or len(revised) > 1_000_000:
                        raise ValueError('수정 결과의 길이가 올바르지 않습니다.')
                except ValueError as exc:
                    if prepared != text:
                        revised = prepared
                        break
                    if attempt:
                        raise
                    correction = '\n직전 수정안 검증 결과: ' + str(exc) + (
                        '\n직전 제안은 적용되지 않았습니다. 원래의 저장된 초안을 기준으로 '
                        '충돌이 있는 위치의 앞뒤 문구까지 정확히 인용해 수정안 전체를 다시 제안하세요.\n'
                    ) + json.dumps(response, ensure_ascii=False)
                else:
                    break
        except AIUsageLimit:
            raise
        except Exception:
            ctx.app.logger.exception('Initial plan reconciliation failed')
            return jsonify(error='AI가 기획안 변경을 반영하지 못했어요. 저장한 내용은 남아 있어요. 다시 시도해 주세요.'), 502
        finally:
            ctx.job_context.owner = None
        with ctx.db() as con:
            con.execute('BEGIN IMMEDIATE')
            _, latest = ctx.load(con, pid)
            if latest['archived'] or 'plan:0' in latest['official'] or latest['drafts'].get('plan:0') != draft:
                return jsonify(error='AI 작업 중 기획안이 바뀌었어요. 최신 초안에서 다시 요청해 주세요.'), 409
            content = {field: draft[field] for field in ('summary', 'facts', 'impact', 'manual_lines') if field in draft}
            content.update(text=revised, plan_reconciled=True)
            checked = ctx.version(con, pid, 'plan', 0, content, 'review')
            latest['drafts']['plan:0'] = {**content, 'version': checked, 'status': 'review', 'base_official_version': None}
            ctx.save(con, pid, latest)
        return jsonify(ok=True, version=checked)
    ctx.reconcile_plan = reconcile_plan

    @ctx.app.post('/api/projects/<pid>/branch-setup')
    def prepare_branch_continuation(pid):
        with ctx.db() as con:
            _, state = ctx.load(con,pid)
            origin = state.get('branch_origin',{})
            if not origin.get('setup_flow') or state['archived'] or branch_setup.stage(state) not in ('length','plan'):
                return jsonify(error=branch_setup.error(state) or '현재 작품은 다른 줄거리 준비 단계가 아니에요.'),409
            if con.execute("SELECT 1 FROM jobs WHERE project=? AND status='running'",(pid,)).fetchone():
                return jsonify(error='진행 중인 작업이 끝난 뒤 다시 요청해 주세요.'),409
            settings = settings_value(request.json.get('selection',{}),state['settings'])
            start = origin['episode']
            total = settings['total_episodes']
            if total is not None and total <= start:
                raise ValueError(f'총 회차수는 가져온 {start}화보다 크게 정하거나 자유로 두세요.')
            end = total if total is not None else min(10000,start+3)
            basis = source_fingerprint(state)
            retained = branch_setup.retained_plot(state['official'].get('plot:0',{}).get('text',''),start,state.get('records',{}))
            # The revised start chapter is an actual event, never the original future plan.
            known = branch_setup.events(retained)
            record = state.get('records',{}).get(str(start),{})
            if record.get('summary') and not record.get('metadata_stale'):
                known[start] = ' '.join(record['summary'].split())
                retained = '## 회차 구성\n'+'\n'.join(f'{n}화: {known[n]}' for n in sorted(known))
            context = {'기획':state['official'].get('plan:0',{}).get('text',''),
                       '현재 설정':{key:item['text'] for key,item in state['official'].items()
                                   if key.split(':')[0] in ('characters','world','places','lore')},
                       '완료한 회차 구성':retained,
                       '수정한 시작 회차':state['official'][f'episode:{start}']['text'],
                       '확인한 사실':{number:[item for item in record.get('findings',[]) if not item.get('removed') and not item.get('reason')] for number,record in state.get('records',{}).items()}}
        ctx.job_context.owner = session.get('uid')
        try:
            result = ctx.generate_attempt(
                f'다른 줄거리의 이후 회차만 계획하세요. {start}화까지의 실제 원고와 확인한 설정은 바꾸지 마세요. '
                f'수정한 {start}화의 결말에서 이어지는 {start+1}화부터 {end}화까지 episodes에 번호별 사건을 빠짐없이 쓰세요. '
                f'회차당 목표는 공백 포함 {settings["target_chars"]}자입니다. '
                '원작의 예정된 미래 전개를 따라야 한다고 간주하지 마세요. 현재 원고와 설정의 인과관계·인물별 지식·미해결 사건을 이어가세요. '
                'beginning, middle, ending에는 이 새 줄거리의 흐름을 쓰세요. 이전 회차 사건을 수정하거나 새로 발생한 것처럼 반복하지 마세요. '
                + ('선택한 마지막 회차에서 갈등을 마무리하세요. ' if total is not None else '총 회차수는 자유입니다. 다음 세 회차만 계획하고 임의로 완결하지 마세요. ')
                + 'JSON beginning, middle, ending, episodes:[{number,event}]만 반환하세요.\n'
                + json.dumps(context,ensure_ascii=False),task='plot_structure')
        except RuntimeError as error:
            return jsonify(error=str(error)),502
        finally:
            ctx.job_context.owner = None
            ctx.job_context.calls = 0
        rows = result.get('episodes') if isinstance(result,dict) else None
        stages = [result.get(key) for key in ('beginning','middle','ending')] if isinstance(result,dict) else []
        if (not isinstance(rows,list) or len(rows) != end-start
                or not all(isinstance(row,dict) and type(row.get('number')) is int and isinstance(row.get('event'),str) and row['event'].strip() for row in rows)
                or sorted(row['number'] for row in rows) != list(range(start+1,end+1))
                or not all(isinstance(value,str) and value.strip() for value in stages)):
            raise ValueError('이후 회차 구성을 완성하지 못했어요. 기존 회차와 설정은 유지했어요.')
        outline = '\n'.join(f'{label}: {" ".join(value.split())}' for label,value in zip(('시작','중간','결말'),stages))
        future = '\n'.join(f'{row["number"]}화: {" ".join(row["event"].split())}' for row in sorted(rows,key=lambda row:row['number']))
        text = '## 전체 플롯\n'+outline+'\n\n'+retained+'\n'+future
        with ctx.db() as con:
            con.execute('BEGIN IMMEDIATE')
            _, latest = ctx.load(con,pid)
            if latest['archived'] or branch_setup.stage(latest) not in ('length','plan') or source_fingerprint(latest) != basis:
                return jsonify(error='계획하는 동안 작품이 바뀌었어요. 최신 자료에서 다시 요청해 주세요.'),409
            current = latest['official'].get('plot:0',{})
            content = {'text':text,'summary':'','facts':'','impact':'다른 줄거리의 이후 회차 구성','branch_setup_plot':True,
                       'branch_retained_plot':retained,'branch_setup_settings':settings,'branch_setup_basis':branch_setup.basis(state)}
            vid = ctx.version(con,pid,'plot',0,content,'draft')
            previous = latest['drafts'].get('plot:0')
            if previous:
                con.execute("UPDATE versions SET status='rejected' WHERE id=? AND project=?",(previous['version'],pid))
            latest['drafts']['plot:0'] = {**content,'version':vid,'status':'draft','base_official_version':current.get('version')}
            latest['selected'] = 'plot'
            ctx.save(con,pid,latest)
        return jsonify(ok=True,version=vid)
    ctx.prepare_branch_continuation = prepare_branch_continuation

    @ctx.app.post('/api/projects/<pid>/initial-length')
    def initial_length(pid):
        values = settings_value(request.json)
        with ctx.db() as con:
            con.execute('BEGIN IMMEDIATE')
            _, state = ctx.load(con, pid)
            if state.get('branch_origin',{}).get('setup_flow') and not state['branch_origin'].get('setup_complete'):
                return jsonify(error='시작 회차의 수정·설정 검토 후 다른 줄거리의 분량 정하기를 이용해 주세요.'),409
            if state['archived'] or not state.get('length_setup_pending'):
                return jsonify(error='현재 작품의 첫 분량 설정을 저장할 수 없습니다.'), 409
            if con.execute("SELECT 1 FROM jobs WHERE project=? AND status='running'", (pid,)).fetchone():
                return jsonify(error='현재 작업이 끝난 뒤 분량을 설정해 주세요.'), 409
            state['settings'] = values
            state.pop('length_setup_pending')
            ctx.save(con, pid, state)
        return jsonify(ok=True)
    ctx.initial_length = initial_length

    @ctx.app.post('/api/projects/<pid>/length')
    def save_length(pid):
        with ctx.db() as con:
            con.execute('BEGIN IMMEDIATE')
            _, state = ctx.load(con, pid)
            if state.get('branch_origin',{}).get('setup_flow') and not state['branch_origin'].get('setup_complete'):
                return jsonify(error='시작 회차의 수정·설정 검토 후 다른 줄거리의 분량 정하기를 이용해 주세요.'),409
            if state['archived'] or state.get('length_setup_pending'):
                return jsonify(error='현재 작품의 분량을 변경할 수 없어요.'), 409
            ctx.recover_stale(con)
            if con.execute("SELECT 1 FROM jobs WHERE project=? AND status='running'", (pid,)).fetchone():
                return jsonify(error='현재 작업이 끝난 뒤 분량을 변경해 주세요.'), 409
            values = settings_value(request.json, state['settings'])
            draft = state['drafts'].get('settings:0')
            if values == state['settings'] and not draft:
                return jsonify(ok=True)
            content = {'text': json.dumps(values, ensure_ascii=False), 'summary': '', 'facts': '', 'impact': ''}
            if draft:
                con.execute("UPDATE versions SET status='rejected' WHERE project=? AND id=?", (pid, draft['version']))
                del state['drafts']['settings:0']
            vid = ctx.version(con, pid, 'settings', 0, content, 'confirmed')
            state['official']['settings:0'] = {**content, 'version': vid, 'status': 'confirmed'}
            ctx.record_revision_impact(state, 'settings:0', state['settings'], values)
            state['settings'] = values
            ctx.save(con, pid, state)
        return jsonify(ok=True)
    ctx.save_length = save_length

    @ctx.app.post('/api/projects/<pid>/length-plan')
    def suggest_length_plan(pid):
        selection = request.json.get('selection')
        with ctx.db() as con:
            _, state = ctx.load(con, pid)
            if state.get('branch_origin',{}).get('setup_flow') and not state['branch_origin'].get('setup_complete'):
                return jsonify(error='다른 줄거리의 분량과 이후 회차 구성을 먼저 정해 주세요.'),409
        plan = state['official'].get('plan:0') or (state['drafts'].get('plan:0') if selection is not None else None)
        if not plan or state['archived']:
            if selection is not None:
                return jsonify(error='현재 기획안으로 분량을 변경할 수 없어요. 다시 확인해 주세요.'), 409
            return jsonify(ok=True)
        original_settings = state['settings']
        settings = settings_value(selection, original_settings) if selection is not None else original_settings
        settings_version = state['official'].get('settings:0', {}).get('version')
        initial = state.get('length_setup_pending')
        plan_group = 'official' if 'plan:0' in state['official'] else 'drafts'
        original_basis = [plan['version'], original_settings]
        plot = state['drafts'].get('plot:0') or state['official'].get('plot:0')
        basis = [plan['version'], settings]
        previous = state.get('length_recommendation', {})
        total = settings['total_episodes']
        replan = selection is not None and total is not None and (
            total != original_settings['total_episodes'] or previous.get('basis') != basis)
        # Recommendations are disabled; only explicit selected episode counts
        # may request a plot restructure below.
        if selection is None:
            return jsonify(ok=True)
        result = {}
        minimum = 1
        reason = '사용자가 선택한 분량'
        suggested_chars = settings['target_chars']
        budget = (total or 0) * suggested_chars
        compressed = replan
        def format_structured_plot(structured):
            if not isinstance(structured, dict):
                return None
            stages = [structured.get(field) for field in ('beginning', 'middle', 'ending')]
            rows = structured.get('episodes')
            if (not all(isinstance(stage, str) and stage.strip() for stage in stages)
                    or not isinstance(rows, list) or len(rows) != total
                    or any(not isinstance(row, dict) or type(row.get('number')) is not int
                           or row['number'] != number or not isinstance(row.get('event'), str) or not row['event'].strip()
                           for number, row in enumerate(rows, 1))):
                return None
            return '\n'.join([
                '## 전체 아웃라인',
                *(f'{label}: {stage.strip()}' for label, stage in zip(('시작', '중간', '결말'), stages)),
                '## 회차 구성',
                *(f"{row['number']}화: {row['event'].strip()}" for row in rows),
            ])

        proposal = format_structured_plot(result) or result.get('plot')
        explanation = result.get('compression')
        if isinstance(proposal, str):
            proposal = '\n'.join(re.sub(r'^\s*(?:[-*+•]\s+|#{1,6}\s+)?(시작|중간|결말|\d+\s*화)\s*[:：]\s*',
                                         lambda match: re.sub(r'\s+', '', match[1]) + ': ',
                                         line.replace('**', '').replace('__', ''))
                                 for line in proposal.splitlines())
        episodes = re.findall(r'^(\d+)화[ \t]*:[ \t]*\S', proposal, re.M) if isinstance(proposal, str) else []
        if compressed and (
            not isinstance(proposal, str) or not all(
                re.search(pattern, proposal, re.M) for pattern in
                (r'^시작\s*:[ \t]*\S', r'^중간\s*:[ \t]*\S', r'^결말\s*:[ \t]*\S'))
            or list(map(int, episodes)) != list(range(1, total + 1))
        ):
            ctx.job_context.owner = session.get('uid')
            try:
                structured = ctx.generate_attempt(
                    f'이 작품의 플롯을 총 {total}화에 맞게 재구성하세요. 시작, 중간, 결말을 작성하고 '
                    f'episodes에 1화부터 {total}화까지 번호와 사건을 하나씩 넣으세요. '
                    '이미 집필한 회차는 유지하고 남은 회차에 핵심 사건을 배분하세요. '
                    '분량이 줄면 부차 사건을 덜어내고 마지막 화에서 핵심 갈등을 해결하세요. '
                    'event는 주요 사건 한 문장으로 쓰고 compression에 줄이거나 합친 사건을 간단히 적으세요.\n'
                    f'회차당 목표는 {settings["target_chars"]}자입니다. 기존 사건을 빽빽하게 몰아넣지 말고 이 분량에 맞춰 사건 규모를 조절하세요.\n'
                    + build_context({**state, 'settings': settings, 'messages': []}),
                    task='plot_structure')
            except RuntimeError:
                return jsonify(error='회차별 플롯을 다시 구성하지 못했어요. 다시 시도해 주세요.'), 502
            finally:
                ctx.job_context.owner = None
                ctx.job_context.calls = 0
            proposal = format_structured_plot(structured)
            if proposal is None:
                raise ValueError(f'AI가 시작·중간·결말과 1~{total}화 구성을 모두 반환하지 않았어요. 다시 시도해 주세요.')
            explanation = structured.get('compression') or explanation
        with ctx.db() as con:
            con.execute('BEGIN IMMEDIATE')
            _, latest = ctx.load(con, pid)
            if (latest['archived'] or [latest[plan_group].get('plan:0', {}).get('version'), latest['settings']] != original_basis
                    or (plan_group == 'drafts' and 'plan:0' in latest['official'])
                    or latest.get('length_setup_pending') != initial
                    or latest['official'].get('settings:0', {}).get('version') != settings_version):
                return jsonify(error='기획안이나 분량이 바뀌었어요. 다시 확인해 주세요.'), 409
            cached = latest.get('length_recommendation', {})
            if (selection is None and 'minimum_episodes' in cached and cached['basis'] == basis
                    and (not initial or 'recommended_target_chars' in cached)):
                return jsonify(ok=True)
            current_plot = latest['drafts'].get('plot:0') or latest['official'].get('plot:0')
            if compressed and current_plot != plot:
                return jsonify(error='플롯이 바뀌었어요. 다시 확인해 주세요.'), 409
            if selection is not None:
                if con.execute("SELECT 1 FROM jobs WHERE project=? AND status='running'", (pid,)).fetchone():
                    return jsonify(error='현재 작업이 끝난 뒤 분량을 변경해 주세요.'), 409
                if initial:
                    latest.pop('length_setup_pending')
                elif settings != original_settings or 'settings:0' in latest['drafts']:
                    draft = latest['drafts'].pop('settings:0', None)
                    if draft:
                        con.execute("UPDATE versions SET status='rejected' WHERE project=? AND id=?", (pid, draft['version']))
                    content = {'text': json.dumps(settings, ensure_ascii=False), 'summary': '', 'facts': '', 'impact': ''}
                    vid = ctx.version(con, pid, 'settings', 0, content, 'confirmed')
                    latest['official']['settings:0'] = {**content, 'version': vid, 'status': 'confirmed'}
                    ctx.record_revision_impact(latest, 'settings:0', original_settings, settings)
                latest['settings'] = settings
                old_proposal = previous.get('proposal_version')
                if not compressed and old_proposal and latest['drafts'].get('plot:0', {}).get('version') == old_proposal:
                    con.execute("UPDATE versions SET status='rejected' WHERE project=? AND id=?", (pid, old_proposal))
                    del latest['drafts']['plot:0']
            latest['length_recommendation'] = {'basis': basis, 'minimum_episodes': minimum, 'reason': reason.strip()[:500],
                                               'recommended_target_chars': suggested_chars, 'recommended_total_chars': budget}
            if compressed and (not plot or proposal.strip() != plot['text'].strip()):
                content = {'text': proposal.strip(), 'summary': '', 'facts': '', 'impact': explanation.strip()[:1000] if isinstance(explanation, str) else ''}
                vid = ctx.version(con, pid, 'plot', 0, content, 'draft')
                latest['drafts']['plot:0'] = {**content, 'version': vid, 'status': 'draft'}
                latest['length_recommendation']['proposal_version'] = vid
            ctx.save(con, pid, latest)
        return jsonify(ok=True)
    ctx.suggest_length_plan = suggest_length_plan

