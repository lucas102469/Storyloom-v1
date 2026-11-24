"""Publication, public reading, comments and alternate stories."""
from .common import branch_setup, datetime, display_project_title, hashlib, json, jsonify, parse_key, re, request, session, time, timedelta, timezone, uuid
from .context import ApplicationContext


def install(ctx: ApplicationContext):
    def publishable_episodes(state):
        episodes = []
        for key, record in state['official'].items():
            if re.fullmatch(r'episode:[1-9][0-9]*', key) and record.get('text', '').strip():
                number = int(key.split(':')[1])
                episodes.append({'number': number, 'title': record.get('title') or f'{number}화',
                                 'text': record['text']})
        return sorted(episodes, key=lambda episode: episode['number'])
    ctx.publishable_episodes = publishable_episodes

    @ctx.app.get('/api/discover/publishable')
    def publishable_works():
        with ctx.db() as con:
            rows = con.execute('SELECT id,title,state FROM projects WHERE (?=0 OR owner=?) ORDER BY rowid DESC',
                               (int(ctx.use_auth), session.get('uid')))
            works = []
            for row in rows:
                state = json.loads(row['state'])
                row = ctx.repair_alternative_presentation(con, row, state)
                if state.get('archived'):
                    continue
                works.append({'id': row['id'], 'title': display_project_title(row['title']),
                              'episodeCount': len(ctx.publishable_episodes(state))})
        return jsonify(works=works)
    ctx.publishable_works = publishable_works

    @ctx.app.get('/api/discover/publishable/<pid>')
    def publication_source(pid):
        with ctx.db() as con:
            row, state = ctx.load(con, pid)
            if state.get('archived'):
                return jsonify(error='휴지통에 있는 작품은 공개할 수 없어요.'), 400
            publication = con.execute('SELECT * FROM published_works WHERE project=?', (pid,)).fetchone()
            previous = {ep['number']: ep for ep in json.loads(publication['episodes'])} if publication else {}
            latest = {ep['number']: ep for ep in ctx.publishable_episodes(state)}
            episodes = []
            for number in sorted(previous.keys() | latest.keys()):
                current = latest.get(number)
                old = previous.get(number)
                episodes.append({**(current or old), 'published': old,
                                 'changed': bool(old and current and old != current),
                                 'hasSource': current is not None})
            title = display_project_title(row['title'])
            intro = ctx.publication_intro(state)
            public_metadata = json.loads(publication['metadata']) if publication else None
            metadata_changed = bool(public_metadata and any(
                public_metadata.get(key) != value for key, value in {'title': title, **intro}.items()))
            return jsonify(title=title, revision=row['revision'], episodes=episodes, **intro,
                           origin=({key: state['branch_origin'][key] for key in ('id', 'title', 'author', 'episode')}
                                   if state.get('branch_origin') else None),
                           metadataChanged=metadata_changed,
                           publication=({**ctx.public_work_metadata(con, publication),
                                         'active': bool(publication['active']), 'updated': publication['updated']}
                                        if publication else None))
    ctx.publication_source = publication_source

    @ctx.app.get('/api/discover/publication/<pid>')
    def publication_status(pid):
        with ctx.db() as con:
            ctx.load(con, pid)
            row = con.execute('SELECT id,active FROM published_works WHERE project=?', (pid,)).fetchone()
            return jsonify(exists=bool(row), active=bool(row and row['active']))
    ctx.publication_status = publication_status

    def publication_intro(state):
        text = (state['official'].get('plan:0') or {}).get('text', '').replace('**', '')
        inline_field = r'(?<=\S)[ \t]+(?=(?:장르|로그라인|총 회차|회차 목표)[ \t]*[:：])'
        text = '\n'.join(line if line.lstrip().startswith('|') else re.sub(inline_field, '\n', line)
                         for line in text.splitlines())

        def field(label):
            for pattern in (
                rf'(?m)^[ \t]*(?:[-*+]\s+)?{label}[ \t]*[:：][ \t]*([^\r\n]+)',
                rf'(?m)^[ \t]*\|[ \t]*{label}[ \t]*\|[ \t]*([^|\r\n]+)',
                rf'(?m)^[ \t]*#{{1,6}}[ \t]+{label}[ \t]*\r?\n[ \t]*([^#\r\n]+)',
            ):
                match = re.search(pattern, text)
                if match:
                    return match.group(1).strip()
            return ''

        genre_text = re.sub(r'[(（][^()（）]*(?:대기|미정|미확정|추후\s*결정)[^()（）]*[)）]', '', field('장르'))
        genres = [part for part in re.split(r'[,，、;；/／]+', genre_text)
                  if not re.search(r'미정|미확정|미분류|선택\s*(?:대기|예정|필요)|정하지\s*않|추후\s*(?:결정|선택)', part)]
        words = re.findall(r'[^\W_]+(?:[-+][^\W_]+)*', ' '.join(genres), flags=re.UNICODE)
        return {'genres': list(dict.fromkeys('SF' if word.upper() == 'SF' else word for word in words)) or ['기타'],
                'synopsis': field('로그라인')}
    ctx.publication_intro = publication_intro

    @ctx.app.post('/api/discover/publish')
    def publish_work():
        data = request.json
        pid, numbers = data.get('project'), data.get('episodes')
        if not isinstance(pid, str) or not isinstance(numbers, list) or any(type(n) is not int or n < 1 for n in numbers):
            return jsonify(error='공개할 확정 회차를 골라 주세요.'), 400
        updates = data.get('updateEpisodes', numbers)
        update_metadata = data.get('updateMetadata', True)
        if 'allowBranches' in data and type(data['allowBranches']) is not bool:
            raise ValueError('다른 줄거리 허용 여부를 확인해 주세요.')
        if not isinstance(updates, list) or any(type(n) is not int or n not in numbers for n in updates) or type(update_metadata) is not bool:
            return jsonify(error='반영할 변경 사항을 확인해 주세요.'), 400
        with ctx.db() as con:
            con.execute('BEGIN IMMEDIATE')
            row, state = ctx.load(con, pid)
            if state.get('archived'):
                return jsonify(error='휴지통에 있는 작품은 공개할 수 없어요.'), 400
            if type(data.get('revision')) is not int or row['revision'] != data['revision']:
                return jsonify(error='작품이 변경됐어요. 작품을 다시 선택하고 미리보기를 확인해 주세요.'), 409
            existing = con.execute('SELECT * FROM published_works WHERE project=?', (pid,)).fetchone()
            if ('publicationUpdated' in data and data['publicationUpdated'] != (existing['updated'] if existing else None)):
                return jsonify(error='공개본이 변경됐어요. 공개 관리를 다시 열어 주세요.'), 409
            if not numbers and not existing:
                return jsonify(error='공개할 확정 회차를 골라 주세요.'), 400
            previous = {ep['number']: ep for ep in json.loads(existing['episodes'])} if existing else {}
            latest = {ep['number']: ep for ep in ctx.publishable_episodes(state)}
            episodes = []
            for number in sorted(set(numbers)):
                record = latest.get(number) if number in updates or number not in previous else previous[number]
                if not record:
                    return jsonify(error='확정된 회차만 공개할 수 있어요.'), 400
                episodes.append(record)
            user = con.execute('SELECT nickname FROM users WHERE id=?', (session.get('uid'),)).fetchone()
            public_id = existing['id'] if existing else str(uuid.uuid4())
            metadata = {'id': public_id, 'title': display_project_title(row['title']),
                        'author': user['nickname'] if user else '작가', 'status': '연재중', 'views': 0, 'likes': 0,
                        'episodeCount': len(episodes), **ctx.publication_intro(state)}
            branch = state.get('branch_origin')
            if branch and numbers:
                start = branch['episode']
                selected = {ep['number']: ep for ep in episodes}
                if any(n not in selected for n in range(1, start + 1)):
                    raise ValueError('다른 줄거리는 시작 회차까지 함께 공개해 주세요.')
                for number, digest in branch['prefix'].items():
                    if hashlib.sha256(selected[int(number)]['text'].encode()).hexdigest() != digest:
                        raise ValueError('분기 이전 회차는 원작 그대로 유지해 주세요.')
                if hashlib.sha256(selected[start]['text'].encode()).hexdigest() == branch['body']:
                    raise ValueError('다른 줄거리의 시작 회차를 수정하고 확정한 뒤 공개해 주세요.')
                metadata['origin'] = {k: branch[k] for k in ('id', 'title', 'author', 'episode')}
                if branch.get('coverColor'):
                    metadata['branchCoverColor'] = branch['coverColor']
            if existing and not update_metadata:
                metadata = {**json.loads(existing['metadata']), 'episodeCount': len(episodes)}
            metadata['allowBranches'] = data.get('allowBranches', bool(json.loads(existing['metadata']).get('allowBranches')) if existing else False)
            if not episodes:
                episodes = list(previous.values())
                metadata['episodeCount'] = len(episodes)
            con.execute('''INSERT INTO published_works(id,project,metadata,episodes,updated,active) VALUES(?,?,?,?,?,?)
                           ON CONFLICT(project) DO UPDATE SET metadata=excluded.metadata,
                           episodes=excluded.episodes, updated=excluded.updated, active=excluded.active''',
                        (public_id, pid, json.dumps(metadata, ensure_ascii=False),
                         json.dumps(episodes, ensure_ascii=False), time.time(), int(bool(numbers))))
            for episode in episodes:
                n = episode['number']
                digest = hashlib.sha256(episode['text'].encode()).hexdigest()
                current = state['official'].get(f'episode:{n}', {})
                saved = (ctx.setting_snapshot(con, pid, n, current, state.get('linked_names', {}))
                         if current.get('text') == episode['text'] else None)
                if saved and current.get('text') == episode['text']:
                    con.execute('INSERT OR REPLACE INTO published_episode_settings VALUES(?,?,?,?)',
                                (public_id, n, digest, saved['payload']))
                else:
                    con.execute('DELETE FROM published_episode_settings WHERE work=? AND episode=? AND body<>?',
                                (public_id, n, digest))
        return jsonify(id=public_id)
    ctx.publish_work = publish_work

    def public_work_metadata(con, row):
        metadata = json.loads(row['metadata'])
        colors = ['#ad613a', '#6f7c50', '#576a78', '#8d627e', '#655b50', '#638076']
        metadata['coverColor'] = colors[sum(ord(char) for char in row['project']) % len(colors)]
        if metadata.get('origin') and re.fullmatch(r'#[a-fA-F0-9]{6}', metadata.get('branchCoverColor', '')):
            metadata['coverColor'] = metadata['branchCoverColor']
        owner = con.execute('SELECT owner FROM projects WHERE id=?', (row['project'],)).fetchone()
        metadata['canManage'] = bool(owner and session.get('uid') and owner['owner'] == session['uid'])
        if metadata['canManage']:
            metadata['manageProject'] = row['project']
        metadata.update(ctx.public_work_stats(con, metadata['id']))
        viewer = 'user:' + session['uid'] if session.get('uid') else 'guest:' + session.get('public_viewer', '')
        progress = con.execute('SELECT position,updated FROM public_reading_progress WHERE work=? AND viewer=?',
                               (metadata['id'], viewer)).fetchone()
        metadata['readingProgress'] = ({**json.loads(progress['position']), 'updated': progress['updated']}
                                       if progress else None)
        return metadata
    ctx.public_work_metadata = public_work_metadata

    @ctx.app.post('/api/discover/works/<public_id>/progress')
    def save_public_reading(public_id):
        position = request.json
        episode = position.get('episode')
        if (type(episode) is not int or type(position.get('scroll')) not in (int, float)
                or not 0 <= position['scroll'] <= 100000000
                or type(position.get('ratio')) not in (int, float) or not -10 <= position['ratio'] <= 1
                or type(position.get('width')) is not int or not 1 <= position['width'] <= 10000
                or type(position.get('updated')) not in (int, float)
                or not 0 < position['updated'] <= time.time() + 300):
            return jsonify(error='읽던 위치를 확인해 주세요.'), 400
        if not session.get('uid') and not session.get('public_viewer'):
            session['public_viewer'] = str(uuid.uuid4())
        viewer = 'user:' + session['uid'] if session.get('uid') else 'guest:' + session['public_viewer']
        with ctx.db() as con:
            con.execute('BEGIN IMMEDIATE')
            work = con.execute('SELECT episodes FROM published_works WHERE id=? AND active=1', (public_id,)).fetchone()
            if not work or not any(ep['number'] == episode for ep in json.loads(work['episodes'])):
                return jsonify(error='공개된 회차를 찾을 수 없어요.'), 404
            payload = {key: position[key] for key in ('episode', 'scroll', 'ratio', 'width')}
            con.execute('''INSERT INTO public_reading_progress(work,viewer,position,updated) VALUES(?,?,?,?)
                           ON CONFLICT(work,viewer) DO UPDATE SET position=excluded.position,updated=excluded.updated
                           WHERE excluded.updated >= public_reading_progress.updated''',
                        (public_id, viewer, json.dumps(payload), position['updated']))
        return jsonify(ok=True)
    ctx.save_public_reading = save_public_reading

    def public_work_stats(con, public_id):
        likes = con.execute('SELECT count(*) FROM public_work_likes WHERE work=?', (public_id,)).fetchone()[0]
        views = con.execute('SELECT count(*) FROM public_episode_views WHERE work=?', (public_id,)).fetchone()[0]
        liked = bool(con.execute('SELECT 1 FROM public_work_likes WHERE work=? AND owner=?',
                                 (public_id, session.get('uid'))).fetchone())
        return dict(likes=likes, views=views, liked=liked)
    ctx.public_work_stats = public_work_stats

    @ctx.app.post('/api/discover/works/<public_id>/like')
    def like_public_work(public_id):
        liked = request.json.get('liked')
        if type(liked) is not bool:
            return jsonify(error='좋아요 상태를 확인해 주세요.'), 400
        with ctx.db() as con:
            con.execute('BEGIN IMMEDIATE')
            if not con.execute('SELECT 1 FROM users WHERE id=?', (session.get('uid'),)).fetchone():
                return jsonify(error='로그인 후 좋아요를 누를 수 있어요.'), 401
            if not con.execute('SELECT 1 FROM published_works WHERE id=? AND active=1', (public_id,)).fetchone():
                return jsonify(error='공개 작품을 찾을 수 없어요.'), 404
            if liked:
                con.execute('INSERT OR IGNORE INTO public_work_likes(work,owner) VALUES(?,?)',
                            (public_id, session['uid']))
            else:
                con.execute('DELETE FROM public_work_likes WHERE work=? AND owner=?', (public_id, session['uid']))
            return jsonify(**ctx.public_work_stats(con, public_id))
    ctx.like_public_work = like_public_work

    @ctx.app.post('/api/discover/works/<public_id>/episodes/<int:number>/view')
    def record_public_view(public_id, number):
        if not session.get('uid') and not session.get('public_viewer'):
            session['public_viewer'] = str(uuid.uuid4())
        viewer = 'user:' + session['uid'] if session.get('uid') else 'guest:' + session['public_viewer']
        day = ctx.compat.datetime.now(timezone(timedelta(hours=9))).date().isoformat()
        with ctx.db() as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT episodes FROM published_works WHERE id=? AND active=1', (public_id,)).fetchone()
            if not row or not any(ep['number'] == number for ep in json.loads(row['episodes'])):
                return jsonify(error='공개된 회차를 찾을 수 없어요.'), 404
            con.execute('INSERT OR IGNORE INTO public_episode_views(work,episode,viewer,day) VALUES(?,?,?,?)',
                        (public_id, number, viewer, day))
            return jsonify(**ctx.public_work_stats(con, public_id))
    ctx.record_public_view = record_public_view

    @ctx.app.get('/api/discover/works')
    def published_works():
        with ctx.db() as con:
            return jsonify(works=[ctx.public_work_metadata(con, row) for row in con.execute(
                'SELECT metadata,project FROM published_works WHERE active=1 ORDER BY updated DESC')
                if not json.loads(row['metadata']).get('origin')])
    ctx.published_works = published_works

    @ctx.app.get('/api/discover/works/<public_id>')
    def published_work(public_id):
        with ctx.db() as con:
            row = con.execute('SELECT metadata,episodes,project FROM published_works WHERE id=? AND active=1', (public_id,)).fetchone()
            if not row:
                return jsonify(error='공개 작품을 찾을 수 없어요.'), 404
            branches = [ctx.public_work_metadata(con, item) for item in con.execute(
                'SELECT metadata,project FROM published_works WHERE active=1 ORDER BY updated DESC')
                if json.loads(item['metadata']).get('origin', {}).get('id') == public_id]
            available = {item['episode']: item['body'] for item in con.execute(
                'SELECT episode,body FROM published_episode_settings WHERE work=?', (public_id,))}
            branch_episodes = [ep['number'] for ep in json.loads(row['episodes'])
                               if available.get(ep['number']) == hashlib.sha256(ep['text'].encode()).hexdigest()]
            return jsonify(**ctx.public_work_metadata(con, row), episodes=json.loads(row['episodes']), branches=branches,
                           branchEpisodes=branch_episodes,
                           comments=ctx.public_work_comments(con, public_id))
    ctx.published_work = published_work

    @ctx.app.post('/api/discover/works/<public_id>/branch-permission')
    def branch_permission(public_id):
        allowed = request.json.get('allowed')
        if type(allowed) is not bool:
            raise ValueError('허용 여부를 확인해 주세요.')
        with ctx.db() as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT * FROM published_works WHERE id=? AND active=1', (public_id,)).fetchone()
            if not row:
                return jsonify(error='공개 작품을 찾을 수 없어요.'), 404
            ctx.load(con, row['project'])
            metadata = json.loads(row['metadata'])
            metadata['allowBranches'] = allowed
            con.execute('UPDATE published_works SET metadata=?,updated=? WHERE id=?',
                        (json.dumps(metadata, ensure_ascii=False), time.time(), public_id))
        return jsonify(allowed=allowed)
    ctx.branch_permission = branch_permission

    @ctx.app.post('/api/discover/works/<public_id>/branch')
    def branch_work(public_id):
        number = request.json.get('episode')
        if type(number) is not int or not 1 <= number <= 10000:
            raise ValueError('다른 줄거리를 시작할 회차를 골라 주세요.')
        with ctx.db() as con:
            source = con.execute('SELECT * FROM published_works WHERE id=? AND active=1', (public_id,)).fetchone()
            if not source:
                return jsonify(error='공개 작품을 찾을 수 없어요.'), 404
            metadata = json.loads(source['metadata'])
            if not metadata.get('allowBranches'):
                return jsonify(error='원작자가 다른 줄거리 만들기를 허용하지 않았어요.'), 403
            chapters = sorted((ep for ep in json.loads(source['episodes']) if ep['number'] <= number),
                              key=lambda ep: ep['number'])
            if [ep['number'] for ep in chapters] != list(range(1, number + 1)):
                raise ValueError('1화부터 선택한 회차까지 모두 공개된 작품에서 시작할 수 있어요.')
            saved = con.execute('SELECT payload,body FROM published_episode_settings WHERE work=? AND episode=?',
                                (public_id, number)).fetchone()
            if not saved or saved['body'] != hashlib.sha256(chapters[-1]['text'].encode()).hexdigest():
                return jsonify(error='이 회차를 마쳤을 당시의 확정 설정 기록이 없어 가져올 수 없어요.'), 409
            snapshot = json.loads(saved['payload'])
            if any(snapshot.get('episodes', {}).get(str(ep['number'])) != hashlib.sha256(ep['text'].encode()).hexdigest()
                   for ep in chapters):
                return jsonify(error='공개 원고가 당시 확정 설정 기록과 달라 가져올 수 없어요.'), 409
            colors = ['#ad613a', '#6f7c50', '#576a78', '#8d627e', '#655b50', '#638076']
            cover_color = colors[sum(ord(char) for char in source['project']) % len(colors)]
            pid = str(uuid.uuid4())
            state = {'messages': [{'role': 'assistant', 'text': f'{number}화 당시 확정한 기획·설정을 가져왔어요. 먼저 {number}화에서 바꾸고 싶은 내용을 알려주세요. 수정한 회차와 설정 검토를 마친 뒤, 앞으로의 분량과 회차 구성을 정할 수 있어요.'}],
                     'official': {}, 'drafts': {}, 'records': {},
                     'selected': 'episode', 'episode': number,
                     'settings': snapshot['settings'], 'archived': False,
                     'branch_origin': {'id': public_id, 'title': metadata['title'], 'author': metadata['author'],
                                       'episode': number, 'settings_source': 'snapshot', 'coverColor': cover_color, 'presentation_version': 2,
                                       'setup_flow': 1, 'setup_complete': False,
                                       'body': hashlib.sha256(chapters[-1]['text'].encode()).hexdigest(),
                                       'prefix': {str(ep['number']): hashlib.sha256(ep['text'].encode()).hexdigest()
                                                  for ep in chapters[:-1]}}}
            con.execute('INSERT INTO projects(id,title,state,owner) VALUES(?,?,?,?)',
                        (pid, metadata['title'], '{}', session.get('uid')))
            for key, record in snapshot['official'].items():
                if key.startswith('episode:'):
                    continue
                kind, ep = parse_key(key)
                content = {k: v for k, v in record.items() if k not in ('version', 'status', 'base_official_version')}
                if kind == 'plot':
                    content.update(text=branch_setup.retained_plot(content['text'],number,snapshot.get('records',{})),summary='',facts='',impact='')
                vid = ctx.version(con, pid, kind, ep, content, 'confirmed')
                state['official'][key] = {**content, 'version': vid, 'status': 'confirmed'}
            for ep in chapters:
                n = ep['number']
                prior = snapshot.get('records', {}).get(str(n), {})
                content = {'text': ep['text'], 'title': ep['title'],
                           'summary': prior.get('summary', ''), 'facts': prior.get('facts', ''),
                           'metadata_stale': prior.get('metadata_stale', not bool(prior.get('summary') and prior.get('facts')))}
                vid = ctx.version(con, pid, 'episode', n, content, 'confirmed')
                state['official'][f'episode:{n}'] = {**content, 'version': vid, 'status': 'confirmed'}
                state['records'][str(n)] = {**prior, 'version': vid, 'setting_review': 'done'}
            ctx.save(con, pid, state)
        return jsonify(id=pid)
    ctx.branch_work = branch_work

    @ctx.app.get('/api/discover/liked')
    def liked_public_works():
        with ctx.db() as con:
            return jsonify(works=[ctx.public_work_metadata(con, row) for row in con.execute(
                '''SELECT w.metadata,w.project FROM published_works w
                   JOIN public_work_likes l ON l.work=w.id
                   WHERE l.owner=? AND w.active=1 ORDER BY ''' + ('w.updated DESC,l.rowid DESC'
                   if request.args.get('sort') == 'updated' else 'l.rowid DESC'), (session.get('uid'),))])
    ctx.liked_public_works = liked_public_works

    def public_work_comments(con, public_id):
        return [dict(comment) for comment in con.execute(
            '''SELECT c.id,c.episode,c.text,c.created,COALESCE(u.nickname,'작가') AS author
               FROM public_work_comments c LEFT JOIN users u ON u.id=c.owner
               WHERE c.work=? ORDER BY c.id DESC''', (public_id,))]
    ctx.public_work_comments = public_work_comments

    @ctx.app.post('/api/discover/works/<public_id>/comments')
    def post_public_comment(public_id):
        text, episode = request.json.get('text'), request.json.get('episode')
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000:
            return jsonify(error='댓글을 1~2,000자로 입력해 주세요.'), 400
        if episode is not None and (type(episode) is not int or episode < 1):
            return jsonify(error='댓글을 남길 회차를 확인해 주세요.'), 400
        with ctx.db() as con:
            con.execute('BEGIN IMMEDIATE')
            if not con.execute('SELECT 1 FROM users WHERE id=?', (session.get('uid'),)).fetchone():
                return jsonify(error='로그인 후 댓글을 남길 수 있어요.'), 401
            work = con.execute('SELECT episodes FROM published_works WHERE id=? AND active=1', (public_id,)).fetchone()
            if not work:
                return jsonify(error='공개 작품을 찾을 수 없어요.'), 404
            if episode is not None and not any(ep['number'] == episode for ep in json.loads(work['episodes'])):
                return jsonify(error='공개된 회차에만 댓글을 남길 수 있어요.'), 400
            con.execute('INSERT INTO public_work_comments(work,owner,episode,text,created) VALUES(?,?,?,?,?)',
                        (public_id, session['uid'], episode, text.strip(), time.time()))
            return jsonify(comments=ctx.public_work_comments(con, public_id))
    ctx.post_public_comment = post_public_comment

