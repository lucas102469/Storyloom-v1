"""Narrow manuscript presentation rules; no story or plot rewriting."""
import re

INSTRUCTION = """[원고 대사와 서술 형식]
인물이 실제로 입 밖으로 하는 직접 대사는 반드시 큰따옴표(" " 또는 “ ”)로 감싸고, 서술과 빈 줄로 분리한 독립 문단에 쓰세요.
화자가 바뀌면 새 문단을 쓰세요. 직접 대사를 따옴표 없이 서술 문장 속에 나열하지 마세요. 간접 전달·생각·묘사까지 억지로 대사로 바꾸지는 마세요.
본문은 작품 속 서술만 쓰세요. '2화의 거대한 충돌', '이번 회차는 끝났다' 같은 외부 회차 번호·집필 설명을 소설 본문에 넣지 마세요.
다른 문장·사건은 변경하지 말고 현재 요청 범위를 지키세요.
"""

QUOTED = re.compile(r'"[^"\n]{1,1000}"|“[^”\n]{1,1000}”')
META = re.compile(r'(?<![가-힣A-Za-z0-9])(?:제\s*)?[0-9]+\s*화의[ \t]+(?=(?:(?:거대한|치열한|격렬한|마지막|새로운|첫)[ \t]+)?(?:충돌|전투|대결|갈등|이야기|전개|사건))')


def present(text):
    """Separate already quoted speech and remove narrow external episode labels.

    Never guess where an unquoted speaker's words begin; the generation prompt
    supplies those quotation marks. Quoted book/chapter references are untouched.
    """
    pieces=[];start=0
    for match in QUOTED.finditer(text):
        pieces.append(META.sub('',text[start:match.start()]))
        quote=match[0]
        before=text[:match.start()].rstrip()
        attached=re.match(r'(?:라고|라며|이라며|하고|하며|라는|이라는)',text[match.end():])
        separate=not before or before[-1] in '.!?。！？' or text[:match.start()].endswith('\n\n')
        if re.search(r'[.!?。！？]',quote[1:-1]) and separate and not attached:
            pieces.append('\n\n'+quote+'\n\n')
        else:pieces.append(quote)
        start=match.end()
    pieces.append(META.sub('',text[start:]))
    result=''.join(pieces)
    result=re.sub(r'[ \t]+\n','\n',result)
    result=re.sub(r'\n[ \t]+','\n',result)
    return re.sub(r'\n{3,}','\n\n',result).strip()
