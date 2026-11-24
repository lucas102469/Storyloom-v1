"""Only explicit additions to an existing chapter; never changes new-chapter writing."""
import copy
import re
from workflow import apply_patches

FUTURE = re.compile(r'극후반|후반(?:부)?|중반(?:부)?|초반(?:부)?|마지막(?:\s*회차)?|훗날|나중에|언젠가|[0-9]+\s*화(?:에서|에)')
ADD = re.compile(r'추가|등장|출현|출연|넣어|넣자|소개|만나|건네|주며|써\s*줘|써\s*주세요')
APPEAR = re.compile(r'(?:등장|출현|출연)\s*(?:시켜|시키|하게\s*(?:해|하자)|해\s*줘)')
PERSON = re.compile(r'인물|캐릭터|소녀|소년|사람|누군가|동료|친구|주인공|조연')

INSTRUCTION = """[기존 회차의 명시적 추가 요청]
사용자가 원고에 인물 추가를 요청했다면 현재 회차의 episode patches를 반환하세요. characters 전체 수정안은 반환하지 마세요. 등장한 인물의 실제 사실은 원고 확정 후 기존 회차 설정 검토에서 반영합니다. 기존 인물을 삭제하거나 다른 인물까지 재작성하지 마세요.
현재 원고에 이름이 나오지 않는 인물은 소녀·문지기 등 그 원고의 실제 호칭을 제목으로 쓰세요. 자료집에만 새 이름을 짓지 마세요.
작가가 정한 훗날·극후반의 예정 사건은 현재 인물이 아는 사실이나 현재의 다짐이 아닙니다. 친절을 받았다는 이유만으로 아직 위기에 처하지 않은 상대를 구하겠다는 맹세를 넣지 마세요.
현재 회차에는 요청한 만남과 행동·반응만 넣고, 미래 사건을 지금 실행하거나 그 미래를 인물이 예언하는 대사·독백으로 쓰지 마세요.
미래 계획이 있으면 future_plan={"timing":"사용자가 지정한 시점 그대로","note":"실행 전인 예정 사건"}에 기록하세요. 극후반 같은 시점을 생략하거나 앞당기지 마세요.
예정 전개는 인물의 현재 목표나 이미 일어난 사실·memory에 섞지 마세요. 기획안·분량은 변경하지 마세요.
"""


def requested(state, text, scope=None, operation=None):
    if scope or operation:
        return False
    has_chapter = any(key.startswith('episode:') for group in ('official', 'drafts') for key in state.get(group, {}))
    return has_chapter and bool(APPEAR.search(text) or (ADD.search(text) and (PERSON.search(text) or FUTURE.search(text))))


def cards(text):
    matches=list(re.finditer(r'(?m)^#{1,6}[ \t]+([^\n]+)\s*$',text))
    return [(m[1].strip(),text[m.start():matches[i+1].start() if i+1<len(matches) else len(text)].strip())
            for i,m in enumerate(matches)]


def merge_characters(before, proposed):
    old=cards(before);incoming=cards(proposed)
    if not incoming:
        raise ValueError('추가 인물의 항목을 확인하지 못했어요. 기존 자료는 유지돼요.')
    # Existing cards stay intact for an addition request. Append only genuinely new cards.
    known={name for name,_ in old}
    additions=[body for name,body in incoming if name not in known]
    return '\n\n'.join([before.rstrip(),*additions]).strip()


def complete_missing_scene(state, text, result, generate):
    """Repair only an omitted requested scene, before any supporting draft is saved."""
    if result.get('write_episode'):
        return result
    docs={**state.get('official',{}),**state.get('drafts',{})}
    if any(u.get('kind')=='episode' and 'episode:'+str(u.get('episode',1)) in docs for u in result.get('updates',[])):
        return result
    number=state.get('episode')
    source=docs.get('episode:'+str(number))
    if not source:return result
    import json
    from manuscript_style import INSTRUCTION as STYLE
    answer=generate('STORY_ADDITION_PATCH\n사용자가 현재 원고에 인물이나 장면 추가를 명시했는데 직전 응답에서 원고 수정이 누락됐습니다. '
        '현재 '+str(number)+'화에서 요청한 등장·행동을 넣는 최소 old/new patches만 반환하세요. '
        '원고의 나머지 사건·문장·결말을 유지하고 다른 회차는 쓰지 마세요. 미래 사건은 현재 일어나거나 인물이 아는 사실로 쓰지 마세요. '
        'old는 원문에서 정확히 한 번 나오는 연속 구절입니다. 다른 자료나 분량은 반환하지 마세요.\n'
        +INSTRUCTION+STYLE+'\n'+json.dumps({'사용자 요청':text,'현재 원고':source['text'],
        '기획':state.get('official',{}).get('plan:0',{}).get('text','')},ensure_ascii=False),task='story_addition_edit')
    patches=answer.get('patches') if isinstance(answer,dict) else None
    revised=apply_patches(source['text'],patches)
    if revised==source['text']:raise ValueError('요청한 등장 장면이 원고에 추가되지 않았어요. 기존 자료는 유지돼요.')
    known={name for name,_ in cards(character_base(state))}
    requested_names={name for update in result.get('updates',[]) if update.get('kind')=='characters'
                     for name,_ in cards(update.get('content','')) if name in text and name not in known}
    if any(name not in revised for name in requested_names):
        raise ValueError('요청한 인물의 등장 장면을 확인하지 못했어요. 기존 자료는 유지돼요.')
    result=copy.deepcopy(result);result['updates'].append({'kind':'episode','episode':number,'patches':patches})
    result['reply']='요청한 인물이 등장하도록 원고 수정안을 만들었어요. 원고 확정 후 회차 설정 검토에서 반영할 수 있어요.'
    return result


def character_base(state):
    before=state.get('official',{}).get('characters:0',{}).get('text','')
    draft=state.get('drafts',{}).get('characters:0',{}).get('text','')
    if not draft:return before
    old=cards(before);revised=dict(cards(draft))
    blocks=[revised.pop(name,body) for name,body in old]
    blocks.extend(revised.values())
    return '\n\n'.join(blocks) if blocks else draft


def prepare(state, text, result):
    """Preserve explicit additions and author timing without inventing a future event."""
    result=copy.deepcopy(result)
    edits=[u for u in result.get('updates',[]) if u.get('kind')=='episode'
           and ('episode:'+str(u.get('episode',1))) in {**state.get('official',{}),**state.get('drafts',{})}]
    if result.get('write_episode') or not edits:
        return result,set()
    docs={**state.get('official',{}),**state.get('drafts',{})}
    allowed=set()
    # Manuscript additions use the existing post-approval episode settings review.
    # A separate character-sheet draft would require duplicate approval.
    result['updates']=[u for u in result['updates'] if u.get('kind')!='characters']
    timings=list(FUTURE.finditer(text))
    specific=[m for m in timings if m[0] not in ('나중에','훗날','언젠가')]
    timings=specific or timings
    if timings:
        allowed.add('plot')
        plan=result.get('future_plan') or {}
        note=plan.get('note') if isinstance(plan,dict) else None
        if not isinstance(note,str) or not note.strip():
            note='작가 요청: '+text.strip()
        # Preserve the exact user timing, regardless of the model's paraphrase.
        entry='시점: '+' · '.join(dict.fromkeys(m[0] for m in timings))+'\n예정 사건: '+note.strip()
        if len(timings)>1:
            entry+='\n작가의 시점·전개 지정: '+text[timings[0].start():].strip()
        entry+='\n현재 회차에서 이미 발생한 사건이나 인물의 현재 지식이 아님.'
        before=docs.get('plot:0',{}).get('text','')
        result['updates']=[u for u in result['updates'] if u['kind'] not in ('plot','memory')]
        if entry not in before:
            result['updates'].append({'kind':'plot','content':before.rstrip()+'\n\n## 예정 전개\n'+entry,
                                      'impact':'작가가 요청한 미래 전개의 시점을 보존한 추가안'})
    return result,allowed
