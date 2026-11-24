import json
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from app import create_app
from ai_client import GeminiUnavailable
from workflow import build_context

class FunctionalUpgradeTests(unittest.TestCase):
    def _interrupted_first_repair(self, still_conflicting=False, change_source=False):
        from ai_client import RetryableAIError
        self.c.post(self.base+'/length',json={'target_chars':1000,'total_episodes':3})
        self.edit('world:0','## 정원\n설명: 문을 열어야 들어간다.');self.approve('world:0')
        self.c.post(self.base+'/view',json={'selected':'episode','episode':1})
        original='연우는 닫힌 문을 통과했다. '+'가'*1100
        fixed=original.replace('닫힌 문을 통과했다.','문을 열고 들어갔다.')
        counts={'write':0,'repair':0,'audit':0};failed=[False]
        def respond(prompt):
            if prompt.startswith('1화 집필 계획'):
                return {'reply':'두 장면','scene_goals':['입장','차']}
            if prompt.startswith('한국어 소설의 다음 장면'):
                counts['write']+=1;return {'text':original}
            if prompt.startswith('아래 원고의 일관성 충돌'):
                counts['repair']+=1
                return {'text':fixed if counts['repair']==1 else fixed.replace('가','나'),'patches':[]}
            if prompt.startswith('이번 원고와 이전에 확정한'):
                counts['audit']+=1
                if '문을 열고 들어갔다.' in prompt and not failed[0]:
                    failed[0]=True;raise RetryableAIError('검토 응답 연결 중단 테스트')
                return {'issues':['문 통과 동작을 확인하세요.'] if still_conflicting or original in prompt else [],
                        'summary':'입장','facts':'정원 도착','findings':[]}
            return {'items':[]}
        self.response=respond
        payload={'request_id':'first-repaired-audit-resume','text':
            '확정한 기획안과 설정, 1화 회차 계획을 바탕으로 이야기의 첫 장면부터 1화를 작성해줘. 이번 화의 목표와 끝맺음을 먼저 계획하고 원고를 작성해줘.'}
        first=self.c.post(self.base+'/chat',json=payload)
        self.assertEqual(first.status_code,502,first.json)
        self.assertEqual(counts,{'write':1,'repair':1,'audit':2})
        self.assertNotIn('episode:1',self.state()['drafts'])
        if change_source:
            self.edit('world:0','## 정원\n설명: 문을 두드려야 들어간다.')
            before=dict(counts)
            retry=self.c.post(self.base+'/chat',json=payload)
            self.assertEqual(retry.status_code,409,retry.json)
            self.assertEqual(counts,before)
            return
        retry=self.c.post(self.base+'/chat',json=payload)
        self.assertEqual(retry.status_code,200,retry.json)
        draft=self.state()['drafts']['episode:1']
        self.assertEqual(counts['write'],1)
        if still_conflicting:
            self.assertEqual(counts['repair'],2)  # Resume keeps the original repair budget.
            self.assertTrue(draft['continuity_issues'])
        else:
            self.assertEqual(counts,{'write':1,'repair':1,'audit':3})
            self.assertEqual(draft['text'],fixed)
            self.assertFalse(draft.get('continuity_issues'))
            self.assertEqual(draft['summary'],'입장')

    def test_first_chapter_repaired_manuscript_resumes_with_required_audit(self):
        self._interrupted_first_repair()

    def test_first_chapter_resumed_repair_keeps_remaining_conflicts_and_budget(self):
        self._interrupted_first_repair(still_conflicting=True)

    def test_first_chapter_repaired_resume_rejects_changed_source(self):
        self._interrupted_first_repair(change_source=True)

    def test_first_chapter_invalid_manuscript_response_keeps_validated_plan_for_retry(self):
        self.c.post(self.base+'/length',json={'target_chars':1000,'total_episodes':3})
        self.edit('world:0','## 정원\n설명: 시간이 멈춘다.');self.approve('world:0')
        self.c.post(self.base+'/view',json={'selected':'episode','episode':1})
        plans=[];writes=[];audits=[]
        def respond(prompt):
            if prompt.startswith('1화 집필 계획'):
                plans.append(prompt)
                return {'reply':'두 장면','scene_goals':['입장','차']}
            if prompt.startswith('한국어 소설의 다음 장면'):
                writes.append(prompt)
                return {'text':'' if len(writes)==1 else '가'*1100}
            if prompt.startswith('이번 원고와 이전에 확정한'):
                audits.append(prompt)
                return {'issues':[],'summary':'정원 도착','facts':'차를 마셨다.','findings':[]}
            return {'items':[]}
        self.response=respond
        payload={'request_id':'first-empty-manuscript-resume','text':
            '확정한 기획안과 설정, 1화 회차 계획을 바탕으로 이야기의 첫 장면부터 1화를 작성해줘. 이번 화의 목표와 끝맺음을 먼저 계획하고 원고를 작성해줘.'}
        first=self.c.post(self.base+'/chat',json=payload)
        self.assertEqual(first.status_code,502,first.json)
        self.assertNotIn('episode:1',self.state()['drafts'])
        saved=self.c.get(self.base+'/jobs/'+payload['request_id']).json
        self.assertTrue(saved['resume']['plan_saved'])
        retry=self.c.post(self.base+'/chat',json=payload)
        self.assertEqual(retry.status_code,200,retry.json)
        self.assertEqual(len(plans),1)
        self.assertEqual(len(writes),2)
        self.assertEqual(len(audits),1)
        self.assertEqual(self.state()['drafts']['episode:1']['text'],'가'*1100)

    def test_first_chapter_verified_duplicate_findings_do_not_trigger_reextraction(self):
        self.c.post(self.base+'/length',json={'target_chars':1000,'total_episodes':3})
        self.edit('world:0','## 정원\n설명: 시간이 멈춘다.');self.approve('world:0')
        self.c.post(self.base+'/view',json={'selected':'episode','episode':1})
        quote='연우는 정원의 탁자 위에 닫힌 수첩을 올려놓았다.'
        body=quote+'가'*1100
        finding={'kind':'lore','name':'수첩','fact':'연우가 닫힌 수첩을 가지고 있다.',
                 'quote':quote,'status':'clear','reason':''}
        def respond(prompt):
            if prompt.startswith('1화 집필 계획'):
                return {'reply':'두 장면','scene_goals':['입장','수첩']}
            if prompt.startswith('한국어 소설의 다음 장면'):
                return {'text':body}
            if prompt.startswith('이번 원고와 이전에 확정한'):
                self.assertIn('같은 사실은 표현·인용 구절·분류가 달라도 한 번만',prompt)
                self.assertIn('원문 호칭을 유지',prompt)
                self.assertNotIn('이름이 없으면 작품에 어울리는 이름을 지으세요',prompt)
                self.assertIn('실제 충돌이 있으면 findings는 빈 배열 []',prompt)
                self.assertIn('서로 다른 새 사실이나 시간에 따른 실제 상태 변화는 중복으로 합치지',prompt)
                return {'issues':[],'summary':'수첩을 놓았다.','facts':'닫힌 수첩 소지',
                        'findings':[finding,dict(finding)]}
            if prompt.startswith('REFERENCE_BACKFILL'):
                raise AssertionError('검증된 중복 항목 때문에 전체 재추출하면 안 됨')
            return {'items':[]}
        self.response=respond
        self.calls.clear()
        r=self.c.post(self.base+'/chat',json={'request_id':'first-deduplicated-audit','text':
            '확정한 기획안과 설정, 1화 회차 계획을 바탕으로 이야기의 첫 장면부터 1화를 작성해줘. 이번 화의 목표와 끝맺음을 먼저 계획하고 원고를 작성해줘.'})
        self.assertEqual(r.status_code,200,r.json)
        self.assertEqual(len(self.state()['drafts']['episode:1']['findings']),1)
        writing_calls=[prompt for prompt in self.calls if prompt.startswith(
            ('1화 집필 계획','한국어 소설의 다음 장면','이번 원고와 이전에 확정한','REFERENCE_BACKFILL'))]
        self.assertEqual(len(writing_calls),3)

    def test_first_chapter_invalid_duplicate_finding_still_requires_reextraction(self):
        self.c.post(self.base+'/length',json={'target_chars':1000,'total_episodes':3})
        self.edit('world:0','## 정원\n설명: 시간이 멈춘다.');self.approve('world:0')
        self.c.post(self.base+'/view',json={'selected':'episode','episode':1})
        quote='연우는 정원의 탁자 위에 닫힌 수첩을 올려놓았다.'
        finding={'kind':'lore','name':'수첩','fact':'연우가 닫힌 수첩을 가지고 있다.',
                 'quote':quote,'status':'clear','reason':''}
        reextractions=[]
        def respond(prompt):
            if prompt.startswith('1화 집필 계획'):
                return {'reply':'두 장면','scene_goals':['입장','수첩']}
            if prompt.startswith('한국어 소설의 다음 장면'):
                return {'text':quote+'가'*1100}
            if prompt.startswith('이번 원고와 이전에 확정한'):
                return {'issues':[],'summary':'수첩을 놓았다.','facts':'닫힌 수첩 소지',
                        'findings':[finding,{**finding,'quote':'원고에 존재하지 않는 잘못된 인용 구절입니다.'}]}
            if prompt.startswith('REFERENCE_BACKFILL'):
                reextractions.append(prompt)
                self.assertIn('같은 사실은 표현·인용 구절·분류가 달라도 한 번만',prompt)
                self.assertIn('원문 호칭을 유지',prompt)
                self.assertNotIn('이름이 없으면 작품에 어울리는 이름을 지으세요',prompt)
                return {'findings':[finding]}
            return {'items':[]}
        self.response=respond
        r=self.c.post(self.base+'/chat',json={'request_id':'first-invalid-duplicate-audit','text':
            '확정한 기획안과 설정, 1화 회차 계획을 바탕으로 이야기의 첫 장면부터 1화를 작성해줘. 이번 화의 목표와 끝맺음을 먼저 계획하고 원고를 작성해줘.'})
        self.assertEqual(r.status_code,200,r.json)
        self.assertEqual(len(reextractions),1)
        self.assertEqual(self.state()['drafts']['episode:1']['findings'][0]['quote'],quote)

    def test_first_chapter_approval_keeps_exact_audit_for_settings_review_without_reextracting(self):
        self.c.post(self.base+'/length',json={'target_chars':1000,'total_episodes':3})
        self.edit('world:0','## 정원\n설명: 시간이 멈춘다.');self.approve('world:0')
        self.c.post(self.base+'/view',json={'selected':'episode','episode':1})
        scenes=[];audits=[]
        def respond(prompt):
            if prompt.startswith('1화 집필 계획'):return {'reply':'두 장면','scene_goals':['문','차']}
            if prompt.startswith('한국어 소설의 다음 장면'):
                scenes.append(prompt);return {'text':('가' if len(scenes)==1 else '나')*1100}
            if prompt.startswith('이번 원고와 이전에 확정한'):
                self.assertIn('내부 모순',prompt);audits.append(prompt)
                return {'issues':[],'summary':'정원에 도착','facts':'차를 마셨다.','findings':[]}
            if prompt.startswith('REFERENCE_BACKFILL'):raise AssertionError('동일 원고 설정 재추출')
            return {'items':[]}
        self.response=respond
        r=self.c.post(self.base+'/chat',json={'request_id':'first-audit-approval-cache','text':
            '확정한 기획안과 설정, 1화 회차 계획을 바탕으로 이야기의 첫 장면부터 1화를 작성해줘. 이번 화의 목표와 끝맺음을 먼저 계획하고 원고를 작성해줘.'})
        self.assertEqual(r.status_code,200,r.json)
        draft=self.state()['drafts']['episode:1'];self.approve('episode:1')
        official=self.state()['official']['episode:1']
        self.assertEqual(official['continuity_stamp'],draft['continuity_stamp'])
        self.assertEqual(official['findings_basis'],draft['findings_basis'])
        calls=len(self.calls)
        review=self.c.post(self.base+'/episode-settings-review',json={'episode':1,'version':official['version'],'action':'organize'})
        self.assertEqual(review.status_code,200,review.json)
        self.assertTrue(review.json['cached']);self.assertEqual(len(self.calls),calls)
        self.assertEqual(len(audits),1);self.assertEqual(len(scenes),1)

    def test_first_chapter_rejects_recycled_insertion_and_switches_strategy(self):
        self.c.post(self.base+'/length',json={'target_chars':1000,'total_episodes':3})
        self.edit('world:0','## 정원\n설명: 시간이 멈춘다.');self.approve('world:0')
        self.c.post(self.base+'/view',json={'selected':'episode','episode':1})
        scenes=['"문을 열었다." '+'가'*385,'"차를 마셨다." '+'나'*385]
        writes=[];expansions=[];rewrites=[]
        corrected='연우는 문을 열고 차를 마셨다. '+'다'*1080
        def respond(prompt):
            if prompt.startswith('1화 집필 계획'):return {'reply':'두 장면','scene_goals':['입장','차']}
            if prompt.startswith('한국어 소설의 다음 장면'):
                writes.append(prompt);return {'text':'\n\n'.join(scenes)}
            if '삽입할 새 본문만' in prompt:
                expansions.append(prompt)
                return {'text':' '.join(scenes).replace('"','')}
            if '재작성한 전체 원고' in prompt:
                rewrites.append(prompt);self.assertIn('직전 응답',prompt)
                self.assertIn('이번 집필에서 정한 장면 계획:\n["입장", "차"]',prompt)
                return {'text':corrected}
            return {'issues':[],'summary':'문과 차','facts':'정원에 도착했다.','findings':[]}
        self.response=respond
        r=self.c.post(self.base+'/chat',json={'request_id':'first-recycled-insertion','text':
            '확정한 기획안과 설정, 1화 회차 계획을 바탕으로 이야기의 첫 장면부터 1화를 작성해줘. 이번 화의 목표와 끝맺음을 먼저 계획하고 원고를 작성해줘.'})
        self.assertEqual(r.status_code,200,r.json)
        self.assertEqual(len(expansions),1);self.assertEqual(len(rewrites),1)
        self.assertEqual(self.state()['drafts']['episode:1']['text'],corrected)
        import re
        budgets=[re.search(r'이번 장면은 공백 포함 약 (\d+)자',p)[1] for p in writes]
        self.assertEqual(budgets,['1250'])

    def test_first_chapter_successful_length_change_clears_obsolete_failure_feedback(self):
        self.c.post(self.base+'/length',json={'target_chars':1000,'total_episodes':3})
        self.edit('world:0','## 정원\n설명: 시간이 멈춘다.');self.approve('world:0')
        self.c.post(self.base+'/view',json={'selected':'episode','episode':1})
        original='가'*390+'\n\n'+'나'*408
        rewritten='다'*440+'\n\n'+'라'*458
        expansions=[]
        def respond(prompt):
            if prompt.startswith('1화 집필 계획'):
                return {'reply':'두 장면','scene_goals':['입장','차']}
            if prompt.startswith('한국어 소설의 다음 장면'):
                return {'text':original}
            if '삽입할 새 본문만' in prompt:
                return {'text':original}  # Recycled insertion must be rejected.
            if '재작성한 전체 원고' in prompt:
                self.assertIn('현재 유효 원고는 여전히 800자',prompt)
                return {'text':rewritten}  # Closer, but still below the lower bound.
            if '보강한 수정 구간만' in prompt:
                expansions.append(prompt)
                self.assertIn('현재 900자',prompt)
                self.assertNotIn('현재 유효 원고는 여전히 800자',prompt)
                self.assertNotIn('직전 응답',prompt)
                return {'text':'마'*640}
            return {'issues':[],'summary':'입장과 차','facts':'정원 도착','findings':[]}
        self.response=respond
        response=self.c.post(self.base+'/chat',json={'request_id':'first-length-feedback-reset','text':
            '확정한 기획안과 설정, 1화 회차 계획을 바탕으로 이야기의 첫 장면부터 1화를 작성해줘. 이번 화의 목표와 끝맺음을 먼저 계획하고 원고를 작성해줘.'})
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(len(expansions),1)
        self.assertEqual(len(self.state()['drafts']['episode:1']['text']),1100)

    def test_reference_extraction_uses_target_chapter_without_later_drafts(self):
        body='유진은 오래된 항구에서 열쇠를 발견하고 비밀 지도를 펼쳤다.'
        future='미래회차전용표식: 아직 만나지 않은 적이 정체를 밝힌다.'
        self.edit('episode:1',body)
        self.edit('episode:2',future)
        self.response={'updates':[]}
        response=self.operation('extract_references','target-reference-context')
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.calls[0].count(body),1)
        self.assertNotIn(future,self.calls[0])
        self.assertIn('서사적 역할',self.calls[0])
        self.assertEqual(self.state()['drafts']['episode:2']['text'],future)

    def test_finding_refresh_uses_target_chapter_once_and_excludes_later_drafts(self):
        body='유진은 오래된 항구에서 열쇠를 발견하고 비밀 지도를 펼쳤다.'
        future='미래회차전용표식: 아직 만나지 않은 적이 정체를 밝힌다.'
        self.edit('episode:1',body)
        self.edit('episode:2',future)
        self.response={'findings':[]}
        response=self.operation('refresh_findings','target-findings-context')
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.calls[0].count(body),1)
        self.assertNotIn(future,self.calls[0])
        self.assertNotIn('직업 또는 역할',self.calls[0])
        self.assertIn('서사적 역할',self.calls[0])
        self.assertEqual(self.state()['drafts']['episode:2']['text'],future)

    def test_exact_repeated_passage_is_repaired_even_when_model_would_report_no_issues(self):
        from revision_tools import repeated_passage_issues
        self.c.post(self.base+'/length',json={'target_chars':1000,'total_episodes':3})
        self.edit('world:0','## 정원\n설명: 현실 시간이 멈춘다.')
        self.approve('world:0')
        shared=('연우는 시온이 건넨 따뜻한 찻잔을 두 손으로 감싸 쥐고 한참 동안 말없이 김이 피어오르는 모습을 바라보았다. '
                '시온은 아무것도 묻지 않고 연우가 자신의 복잡하고 어지러운 마음을 스스로 천천히 정리할 때까지 창가에서 조용히 기다리며 나무 의자를 다듬었다.')
        scenes=[shared+' 가'*210,shared+' 나'*210]
        corrected='연우는 수첩을 한 번만 내려놓았다. '+'다'*1080
        writes=[];repairs=[];audits=[]
        def respond(prompt):
            if prompt.startswith('1화 집필 계획'):
                return {'reply':'두 장면','scene_goals':['입장','차와 수첩']}
            if prompt.startswith('한국어 소설의 다음 장면'):
                writes.append(prompt)
                return {'text':'\n\n'.join(scenes)}
            if prompt.startswith('아래 원고의 일관성 충돌을 해결'):
                repairs.append(prompt)
                self.assertIn('같은 긴 연속 문장',prompt)
                return {'text':corrected,'patches':[]}
            if prompt.startswith('이번 원고와 이전에 확정한'):
                audits.append(prompt)
                self.assertIn(corrected,prompt)
                return {'issues':[],'summary':'차와 수첩','facts':'연우가 정원에서 쉰다.','findings':[]}
            return {'findings':[],'updates':[]}
        self.response=respond
        self.c.post(self.base+'/view',json={'selected':'episode','episode':1})
        r=self.c.post(self.base+'/chat',json={'request_id':'verified-repeat-repair','text':
            '확정한 기획안과 설정, 1화 회차 계획을 바탕으로 이야기의 첫 장면부터 1화를 작성해줘. 이번 화의 목표와 끝맺음을 먼저 계획하고 원고를 작성해줘.'})
        self.assertEqual(r.status_code,200,r.json)
        final=self.state()['drafts']['episode:1']['text']
        self.assertEqual(final,corrected)
        self.assertEqual(repeated_passage_issues(final),[])
        self.assertEqual(len(repairs),1)
        self.assertEqual(len(audits),1)

    def test_small_length_shortfall_expands_a_passage_without_rewriting_the_chapter(self):
        self.c.post(self.base+'/length',json={'target_chars':1000,'total_episodes':3})
        begin='연우가 문을 밀었다. 낯선 온기가 손끝에 닿았다. '
        ending='\n\n연우는 닫힌 수첩을 탁자 위에 올려놓았다.'
        body=begin+('시온은 찻잔을 닦으며 연우가 의자에 앉기를 기다렸다. '*40)
        body=body[:977-len(ending)]+ending
        self.edit('episode:1',body)
        expanded=[]
        def respond(prompt):
            if '보강한 수정 구간만' in prompt:
                passage=prompt.split('\n수정 구간:\n',1)[1].split('\n뒤 문맥:\n',1)[0]
                expanded.append(passage)
                return {'text':passage+' 연우는 손끝에 남은 차가운 감각이 천천히 누그러지는 것을 느꼈다.'}
            if prompt.startswith('당신은'):
                return {'reply':'분량 보완','updates':[{'kind':'episode','episode':1,'content':body}]}
            if prompt.startswith('REFERENCE_BACKFILL'):
                return {'updates':[]}
            return {'findings':[],'summary':'차를 마시며 기다린다.','facts':'연우가 정원에 도착했다.'}
        self.response=respond
        r=self.c.post(self.base+'/chat',json={'request_id':'shortfall-twenty-three','text':'1화 분량을 맞춰줘.'})
        self.assertEqual(r.status_code,200,r.json)
        result=self.state()['drafts']['episode:1']['text']
        self.assertGreaterEqual(len(result),1000)
        self.assertLessEqual(len(result),1500)
        self.assertTrue(result.startswith(begin))
        self.assertTrue(result.endswith(ending))
        self.assertEqual(len(expanded),1)
        self.assertFalse(any('전체 원고를 재작성하세요' in p for p in self.calls))

    def test_first_chapter_single_request_contains_all_beats_and_full_length_budget(self):
        self.c.post(self.base+'/length',json={'target_chars':1000,'total_episodes':3})
        self.edit('world:0','## 정원\n설명: 현실 시간이 멈추는 안식처.')
        self.approve('world:0')
        plans=[]
        def respond(prompt):
            if prompt.startswith('1화 집필 계획'):
                return {'reply':'두 장면으로 구성','scene_goals':['문을 발견하고 들어간다.','차를 마시고 닫힌 수첩을 놓는다.']}
            if prompt.startswith('한국어 소설의 다음 장면'):
                plan=json.JSONDecoder().raw_decode(prompt.split('\n장면별 계획:\n',1)[1])[0]
                plans.append(plan)
                self.assertEqual(plan['scene_goals'],['문을 발견하고 들어간다.','차를 마시고 닫힌 수첩을 놓는다.'])
                self.assertEqual(plan['target_chars'],1250)
                self.assertIn('1화 전체를 완성',prompt)
                self.assertIn('scene_length:{"minimum":1000,"maximum":1500',prompt)
                self.assertIn('episode_scope',prompt)
                self.assertIn('이후 회차 사건은 추가하지 마세요',prompt)
                self.assertNotIn('회차 전체 사건을 이 장면에서 끝내지 마세요',prompt)
                return {'text':'연우가 문을 열었다. '+'가'*540+'\n\n연우가 수첩을 놓았다. '+'나'*540}
            return {'issues':[],'findings':[],'summary':'문을 열고 수첩을 놓았다.','facts':'연우가 정원에 도착했다.'}
        self.response=respond
        self.c.post(self.base+'/view',json={'selected':'episode','episode':1})
        r=self.c.post(self.base+'/chat',json={'request_id':'scene-beat-boundaries','text':
            '확정한 기획안과 설정, 1화 회차 계획을 바탕으로 이야기의 첫 장면부터 1화를 작성해줘. 이번 화의 목표와 끝맺음을 먼저 계획하고 원고를 작성해줘.'})
        self.assertEqual(r.status_code,200,r.json)
        self.assertEqual(len(plans),1)

    def test_revision_reuses_metadata_from_the_exact_audited_manuscript(self):
        self.edit('episode:1', '서윤은 배를 탔다.')
        self.approve('episode:1')
        original = '서윤은 배에서 내렸다.' + '주변을 둘러보았다.' * 120
        self.edit('episode:2', original)
        self.approve('episode:2')
        self.edit('episode:1', '서윤은 기차를 탔다.')
        self.approve('episode:1')
        revised = original.replace('배에서', '기차에서')
        self.calls.clear()
        def respond(prompt):
            if '직접 수정해야 할 구절' in prompt:
                return {'blocked': False, 'impact': '교통수단 변경', 'patches': [
                    {'old': '서윤은 배에서 내렸다.', 'new': '서윤은 기차에서 내렸다.'}]}
            if '일관성을 점검' in prompt:
                if '"검토 회차": 1' in prompt:
                    return {'issues': []}
                self.assertIn('summary(줄거리 요약)', prompt)
                self.assertIn(revised, prompt)
                return {'issues': [], 'summary': '기차에서 내렸다.', 'facts': '서윤이 기차로 도착했다.'}
            raise AssertionError('검토한 동일 원고의 요약을 다시 요청했습니다.')
        self.response = respond
        state = self.run_linked_settings_check()['state']
        draft = state['drafts'].get('episode:2') or state['official']['episode:2']
        self.assertEqual(draft['text'], revised)
        self.assertEqual(draft['summary'], '기차에서 내렸다.')
        self.assertEqual(draft['facts'], '서윤이 기차로 도착했다.')
        self.assertEqual(len(self.calls), 3)

    def test_length_expansion_uses_same_current_episode_scope_as_scene_generation(self):
        self.c.post(self.base+'/length',json={'target_chars':1000,'total_episodes':5})
        self.edit('plot:0','## 회차 구성\n1화: 상실과 복수 결심\n2화: 마을 출발\n3화: 수련 시작')
        self.approve('plot:0')
        self.c.post(self.base+'/view',json={'selected':'episode','episode':1})
        scenes=[];expansions=[]
        def respond(prompt):
            if prompt.startswith('1화 집필 계획'):
                self.assertIn('episode_scope',prompt)
                return {'reply':'첫 회차 계획','scene_goals':['상실','복수 결심']}
            if prompt.startswith('한국어 소설의 다음 장면'):
                scenes.append(prompt);self.assertIn('reserved_for_later',prompt)
                return {'text':'가'*800}
            if '삽입할 새 본문만' in prompt:
                expansions.append(prompt)
                self.assertIn('episode_scope',prompt);self.assertIn('상실과 복수 결심',prompt)
                self.assertIn('마을 출발',prompt);self.assertIn('이후 회차',prompt)
                return {'text':'나'*250}
            return {'issues':[],'findings':[],'updates':[],'summary':'결심','facts':'복수를 결심했다.'}
        self.response=respond
        result=self.c.post(self.base+'/chat',json={'request_id':'scope-expansion-test',
            'text':'확정한 기획안과 설정, 1화 회차 계획을 바탕으로 이야기의 첫 장면부터 1화를 작성해줘. 이번 화의 목표와 끝맺음을 먼저 계획하고 원고를 작성해줘.'})
        self.assertEqual(result.status_code,200,result.json)
        self.assertEqual(len(scenes),1);self.assertEqual(len(expansions),1)

    def test_first_episode_request_plans_opening_without_a_previous_episode(self):
        self.c.post(self.base+'/length',json={'target_chars':1000,'total_episodes':5})
        self.c.post(self.base+'/view',json={'selected':'episode','episode':1})
        plans=[]
        def respond(prompt):
            if prompt.startswith('1화 집필 계획'):
                plans.append(prompt)
                self.assertIn('1화는 이야기의 시작입니다.',prompt)
                self.assertNotIn('첫 장면은 직전 회차에서 이어지고',prompt)
                return {'reply':'첫 장면 계획','scene_goals':['이야기 시작','첫 사건']}
            if prompt.startswith('한국어 소설의 다음 장면'):
                return {'text':'가'*1080}
            return {'updates':[],'issues':[],'findings':[],'summary':'이야기 시작','facts':'첫 사건'}
        self.response=respond
        response=self.c.post(self.base+'/chat',json={'request_id':'first-opening-test',
            'text':'확정한 기획안과 설정, 1화 회차 계획을 바탕으로 이야기의 첫 장면부터 1화를 작성해줘. 이번 화의 목표와 끝맺음을 먼저 계획하고 원고를 작성해줘.'})
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(len(plans),1)
        self.assertIn('episode:1',self.state()['drafts'])

    def test_post_revision_audit_also_repairs_and_rechecks_before_finishing(self):
        self.c.post(self.base+'/length',json={'target_chars':1000,'total_episodes':5})
        self.edit('world:0','## 마을\n설명: 조용하다.')
        self.approve('world:0')
        original=('사라가 미래 인물을 만났다. '*70).strip()
        first=('사라가 관리자를 일찍 만났다. '*70).strip()
        corrected=('사라는 조용한 골목을 걸으며 풍경을 살폈다. '*55).strip()
        self.edit('episode:1',original);self.approve('episode:1')
        self.edit('world:0','## 마을\n설명: 조용하고 한적하다.');self.approve('world:0')
        audits=[];repairs=[]
        def respond(prompt):
            if '직접 수정해야 할 구절' in prompt:
                return {'blocked':False,'impact':'배경 반영','patches':[{'old':original,'new':first}]}
            if prompt.startswith('점검 의견을 원고에 직접 반영'):
                repairs.append(prompt);return {'text':corrected,'patches':[]}
            if '일관성을 점검' in prompt:
                audits.append(prompt)
                return {'issues':['관리자 등장 사건이 다음 회차를 침범한다.'] if len(audits)==1 else []}
            return {'updates':[],'summary':'골목을 걸었다.','facts':'사라가 마을을 살폈다.'}
        self.response=respond
        state=self.run_linked_settings_check()['state']
        if state['revision_impact']['status']=='settings_ready':state=self.run_linked_settings_check()['state']
        self.assertEqual(len(repairs),1);self.assertEqual(len(audits),2)
        self.assertIn(corrected.split()[0],audits[-1])
        state = self.accept_ready_revision(state)
        self.assertEqual(state['revision_impact']['status'],'confirmed')
        self.assertEqual(state['official']['episode:1']['text'].split(),corrected.split())

    def test_actionable_review_repairs_manuscript_in_same_run_without_manual_retry(self):
        self.c.post(self.base+'/length',json={'target_chars':1000,'total_episodes':5})
        self.edit('world:0','## 골목\n설명: 조용한 마을이다.')
        self.approve('world:0')
        self.edit('episode:1','사라가 미래의 인물을 만났다. '*70)
        self.approve('episode:1')
        self.edit('world:0','## 골목\n설명: 조용하고 인적이 드문 마을이다.')
        self.approve('world:0')
        corrected=('사라는 조용한 골목을 걸으며 풍경을 살폈다. '*55).strip()
        repairs=[]
        def respond(prompt):
            if '직접 수정해야 할 구절' in prompt:
                return {'blocked':True,'impact':'다음 회차 등장인물이 너무 일찍 나온다.','patches':[]}
            if prompt.startswith('점검 의견을 원고에 직접 반영'):
                repairs.append(prompt)
                return {'text':corrected,'patches':[]}
            return {'updates':[],'issues':[],'summary':'골목을 걸었다.','facts':'사라가 마을을 살폈다.'}
        self.response=respond
        state=self.run_linked_settings_check()['state']
        if state['revision_impact']['status']=='settings_ready':
            state=self.run_linked_settings_check()['state']
        self.assertEqual(len(repairs),1)
        state = self.accept_ready_revision(state)
        self.assertEqual(state['revision_impact']['status'],'confirmed')
        self.assertEqual(state['official']['episode:1']['text'].split(),corrected.split())
        self.assertFalse(any(row.get('blocked') for row in state['revision_impact']['results']))

    def test_mixed_setting_changes_apply_known_names_to_every_target_before_ai_review(self):
        self.edit('characters:0','## 서윤\n역할: 주인공\n성격: 차분함\n목표: 마을을 걷는다.\n관계: 도준과 친구다.')
        self.approve('characters:0')
        self.edit('world:0','## 겨울 마을\n설명: 눈이 내린다.')
        self.approve('world:0')
        for n in (1,2):
            self.edit(f'episode:{n}',f'서윤은 {n}번째 길을 걸었다.')
            self.approve(f'episode:{n}')
        self.edit('characters:0','## 사라\n역할: 주인공\n성격: 호기심이 많음\n목표: 마을을 걷는다.\n관계: 도준과 친구다.')
        self.approve('characters:0')
        self.edit('world:0','## 겨울 마을\n설명: 조용히 눈이 내린다.')
        self.approve('world:0')
        manuscripts=[]
        def respond(prompt):
            if '직접 수정해야 할 구절' in prompt:
                manuscript=prompt.rsplit('화 원고:\n',1)[1]
                manuscripts.append(manuscript)
                self.assertNotIn('서윤',manuscript)
                return {'blocked':False,'impact':'','patches':[]}
            return {'updates':[],'issues':[],'summary':'산책','facts':'사라가 길을 걸었다.'}
        self.response=respond
        state=self.run_linked_settings_check()['state']
        if state['revision_impact']['status']=='settings_ready':
            state=self.run_linked_settings_check()['state']
        self.assertEqual(len(manuscripts),2)
        state = self.accept_ready_revision(state)
        for n in (1,2):
            self.assertIn('사라',state['official'][f'episode:{n}']['text'])
            self.assertNotIn('서윤',state['official'][f'episode:{n}']['text'])

    def test_name_only_change_never_rewrites_story_or_calls_ai(self):
        old = '## 강진혁\n역할: 주인공\n설명: 은신처에서 반격을 준비한다.'
        self.edit('characters:0', old)
        self.approve('characters:0')
        self.edit('episode:1', '강진혁은 숨었다. 진혁이 기다렸다. 사건은 그대로다.')
        self.approve('episode:1')
        original = self.state()['official']['episode:1']
        with closing(sqlite3.connect(str(Path(self.temp.name)/'test.sqlite3'))) as con, con:
            state=self.state()
            state['records']['1'].update(setting_review='done', intentional_development={
                'version':original['version'],'issues':['강진혁의 의도한 선택'],'note':'강진혁의 선택을 유지한다.'})
            con.execute('UPDATE projects SET state=? WHERE id=?',(json.dumps(state),self.pid))
        self.edit('characters:0', old.replace('강진혁', '소리케'))
        self.approve('characters:0')
        self.calls.clear()
        self.response = AssertionError('이름 변경에는 AI를 호출하면 안 됩니다.')
        state = self.run_linked_settings_check()['state']
        if state['revision_impact']['status'] == 'settings_ready':
            state = self.run_linked_settings_check()['state']
        self.assertFalse(self.calls)
        state = self.accept_ready_revision(state)
        self.assertEqual(state['revision_impact']['status'], 'confirmed')
        self.assertEqual(state['official']['episode:1']['text'], '소리케는 숨었다. 소리케가 기다렸다. 사건은 그대로다.')
        self.assertNotEqual(state['official']['episode:1']['version'], original['version'])
        intent=state['records']['1']['intentional_development']
        self.assertEqual(intent['note'],'소리케의 선택을 유지한다.')
        self.assertEqual(intent['issues'],['소리케의 의도한 선택'])
        self.assertEqual(intent['version'],state['official']['episode:1']['version'])

    def test_episode_batch_starts_at_one_and_uses_previous_draft(self):
        for kind in ('plan', 'characters', 'world', 'places', 'lore', 'plot'):
            self.edit(kind+':0', '## 확정 자료\n설명: 항구에서 출발한다.')
            self.approve(kind+':0')
        self.c.post(self.base+'/length', json={'target_chars': 1000, 'total_episodes': 5})
        plans = []
        audits = []
        disconnected = False
        second_scenes = 0

        def respond(prompt):
            nonlocal disconnected, second_scenes
            if prompt.startswith(('1화 집필 계획', '2화 집필 계획')):
                plans.append(prompt)
                return {'reply': '계획', 'scene_goals': ['시작', '끝']}
            if prompt.startswith('한국어 소설의 다음 장면'):
                if len(plans) == 2:
                    second_scenes += 1
                    if second_scenes == 1 and not disconnected:
                        disconnected = True
                        raise ValueError('테스트 연결 중단')
                return {'text': ('가' if len(plans) == 1 else '나') * 1080, 'summary': '사건', 'facts': '사실'}
            if prompt.startswith('이번 원고와 이전에 확정한'):
                audits.append(prompt)
            return {'issues': ['충돌 없음. 플롯과 일관됩니다.'], 'findings': [], 'summary': '사건', 'facts': '사실'}

        self.response = respond
        start = self.c.post(self.base+'/episode-batch', json={'action': 'start', 'count': 2})
        self.assertEqual(start.status_code, 202, start.json)
        for _ in range(200):
            state = self.state()
            if state['episode_batch']['status'] != 'running':
                break
            time.sleep(.02)
        self.assertEqual(state['episode_batch']['status'], 'paused')
        self.assertEqual(set(state['episode_batch']['draft_versions']), {'1'})
        self.assertEqual(self.c.post(self.base+'/episode-batch', json={'action': 'resume'}).status_code, 202)
        for _ in range(200):
            state = self.state()
            if state['episode_batch']['status'] != 'running':
                break
            time.sleep(.02)
        self.assertEqual(state['episode_batch']['status'], 'done', state['episode_batch'])
        self.assertEqual(set(state['episode_batch']['draft_versions']), {'1', '2'})
        self.assertEqual(len(plans), 2)
        self.assertIn('가' * 100, plans[1])
        self.assertIn('가' * 100, audits[1])
        self.assertFalse(state['drafts']['episode:2'].get('continuity_issues'))
        self.assertNotIn('episode:1', state['official'])
        self.assertNotIn('episode:2', state['official'])
        version = state['drafts']['episode:2']['version']
        for action, expected in (('review', 200), ('approve', 409)):
            result = self.c.post(self.base+'/draft', json={'key':'episode:2', 'version':version, 'action':action})
            self.assertEqual(result.status_code, expected)
        restored = self.c.post('/api/projects/import', json=self.c.get(self.base+'/export').json)
        self.assertEqual(restored.status_code, 200, restored.json)
        copy_base = '/api/projects/'+restored.json['id']
        copy = self.c.get(copy_base).json['state']
        self.assertNotEqual(copy['episode_batch']['id'], state['episode_batch']['id'])
        self.assertEqual(copy['episode_batch']['draft_versions']['2'], copy['drafts']['episode:2']['version'])
        denied = self.c.post(copy_base+'/draft', json={'key':'episode:2', 'version':copy['drafts']['episode:2']['version'],
                                                       'action':'approve'})
        self.assertEqual(denied.status_code, 409)
        self.assertNotIn('episode:2', self.c.get(copy_base).json['state']['official'])

    def test_episode_batch_pauses_on_continuity_conflict(self):
        for kind in ('plan', 'characters', 'world', 'places', 'lore', 'plot'):
            self.edit(kind+':0', '## 확정 자료\n설명: 항구에서 출발한다.')
            self.approve(kind+':0')
        self.c.post(self.base+'/length', json={'target_chars': 1000, 'total_episodes': 5})
        plans = []

        def respond(prompt):
            if '화 집필 계획을 세우세요' in prompt:
                plans.append(prompt)
                return {'reply': '계획', 'scene_goals': ['시작', '끝']}
            if prompt.startswith('한국어 소설의 다음 장면'):
                return {'text': '가' * 1080, 'summary': '사건', 'facts': '사실'}
            if prompt.startswith('이번 원고와 이전에 확정한'):
                return {'issues': ['확정 플롯보다 사건이 앞당겨졌어요.'], 'findings': []}
            return {'text': '', 'patches': [], 'findings': []}

        self.response = respond
        self.assertEqual(self.c.post(self.base+'/episode-batch', json={'action': 'start', 'count': 3}).status_code, 202)
        for _ in range(200):
            state = self.state()
            if state['episode_batch']['status'] != 'running':
                break
            time.sleep(.02)
        self.assertEqual(state['episode_batch']['status'], 'paused')
        self.assertEqual(state['episode_batch']['next'], 2)
        self.assertEqual(len(plans), 1)
        self.assertIn('episode:1', state['drafts'])
        self.assertNotIn('episode:2', state['drafts'])
        self.assertEqual(self.c.post(self.base+'/episode-batch', json={'action': 'resume'}).status_code, 409)

    def _plot_impact_precheck(self, conflict=False):
        self.edit('plot:0', '1화: 소녀와 만난다.')
        self.approve('plot:0')
        self.edit('episode:1', '소녀 화림이 객잔에 들어왔다.')
        self.approve('episode:1')
        self.edit('plot:0', '1화: 소녀와 만난다.\n## 예정 전개\n나중에 쓸 인물이며 역할은 아직 정하지 않는다.')
        self.approve('plot:0')
        state = self.state()
        state['revision_impact']['settings_checked'] = True
        with closing(sqlite3.connect(str(Path(self.temp.name)/'test.sqlite3'))) as con, con:
            con.execute('UPDATE projects SET state=? WHERE id=?',(json.dumps(state),self.pid))
        prompts=[]
        def respond(prompt):
            prompts.append(prompt)
            if prompt.startswith('플롯 변경이 확정 원고'):
                return {'issues':['원고의 화림 등장과 변경한 플롯의 인물 부재가 충돌한다.'] if conflict else []}
            if conflict and '직접 수정해야 할 구절' in prompt:
                return {'blocked':False,'impact':'확인','patches':[]}
            self.fail('예상하지 않은 원고 수정 요청이 발생함')
        self.response=respond
        impact=state['revision_impact']
        self.assertEqual(self.c.post(self.base+'/revision-impact',json={'id':impact['id'],'mode':'auto'}).status_code,202)
        for _ in range(100):
            state=self.state()
            if state['revision_impact']['status']!='running':break
            time.sleep(.02)
        self.assertIn(state['revision_impact']['status'],('ready','done'))
        self.assertNotIn('episode:1',state['drafts'])
        self.assertEqual(state['official']['episode:1']['text'],'소녀 화림이 객잔에 들어왔다.')
        self.assertEqual(len(prompts),2 if conflict else 1)

    def test_plot_impact_checks_conflict_before_requesting_manuscript_edits(self):
        self._plot_impact_precheck()

    def test_plot_impact_keeps_revision_path_for_confirmed_conflicts(self):
        self._plot_impact_precheck(conflict=True)

    def test_revision_impact_manual_and_auto(self):
        self.edit('episode:1', '진우가 떠났다.')
        self.approve('episode:1')
        self.edit('episode:2', '진우가 돌아왔다.')
        self.approve('episode:2')

        def run_batch(mode, old, new, blocked=False, make_patch=True):
            self.edit('episode:1', f'{new}가 떠났다.')
            self.approve('episode:1')
            impact = self.state()['revision_impact']
            def respond(prompt):
                if '직접 수정해야 할 구절' in prompt:
                    return {'blocked': blocked, 'impact': '이름 반영',
                            'patches': [{'old': f'{old}가 돌아왔다.', 'new': f'{new}가 돌아왔다.'}] if make_patch else []}
                if '일관성을 점검' in prompt:
                    return {'issues': []}
                return {'summary': '귀환', 'facts': '주인공 귀환'}
            self.response = respond
            payload = {'id': impact['id'], 'mode': mode}
            self.assertEqual(self.c.post(self.base+'/revision-impact', json=payload).status_code, 202)
            for _ in range(100):
                state = self.state()
                if state['revision_impact']['status'] != 'running':
                    return state, payload
                time.sleep(.02)
            self.fail('회차 점검이 완료되지 않음')

        state, payload = run_batch('manual', '진우', '도윤')
        self.assertEqual(state['revision_impact']['status'], 'ready')
        self.assertEqual(state['official']['episode:2']['text'], '진우가 돌아왔다.')
        self.edit('guidelines:0', '짧은 문단으로 나누세요.')
        self.assertEqual(self.c.post(self.base+'/revision-impact', json={**payload, 'mode': 'approve'}).status_code, 200)
        self.assertIn('guidelines:0', self.state()['drafts'])
        state, _ = run_batch('auto', '도윤', '은호')
        state = self.accept_ready_revision(state)
        self.assertEqual(state['revision_impact']['status'], 'confirmed')
        self.assertEqual(state['official']['episode:2']['text'], '은호가 돌아왔다.')
        state, payload = run_batch('auto', '은호', '유진', blocked=True)
        self.assertEqual(state['revision_impact']['status'], 'blocked')
        self.assertEqual(state['official']['episode:2']['text'], '은호가 돌아왔다.')
        decision = {**payload, 'episode': 2, 'mode': 'keep'}
        self.assertEqual(self.c.post(self.base+'/revision-impact', json=decision).status_code, 200)
        state = self.state()
        self.assertEqual(state['revision_impact']['results'][0]['decision'], 'kept')
        self.assertNotIn('episode:2', state['drafts'])
        self.assertEqual(state['official']['episode:2']['text'], '은호가 돌아왔다.')
        self.assertEqual(self.c.post(self.base+'/revision-impact', json=decision).status_code, 409)
        batch_response = self.response
        self.response = {'reply': '다음 회차', 'updates': [], 'write_episode': 3, 'scene_goals': ['시작', '끝']}
        denied = self.c.post(self.base+'/chat', json={'request_id': 'write-with-rejected-impact', 'text': '3화를 써줘'})
        self.assertIn('변경 사항을 모두 확정', denied.json['error'])
        self.response = batch_response
        self.assertEqual(self.c.post(self.base+'/revision-impact', json={**decision, 'mode': 'retry'}).status_code, 202)
        for _ in range(100):
            state = self.state()
            if state['revision_impact']['status'] != 'running':
                break
            time.sleep(.02)
        self.assertEqual(state['official']['episode:2']['text'], '은호가 돌아왔다.')
        self.assertEqual(state['drafts']['episode:2']['text'], '유진가 돌아왔다.')
        self.assertNotIn('decision', state['revision_impact']['results'][0])
        self.assertEqual(self.c.post(self.base+'/revision-impact', json=decision).status_code, 200)

        state, payload = run_batch('auto', '은호', '하나', blocked=True)
        decision = {**payload, 'episode': 2, 'mode': 'accept'}
        self.assertEqual(self.c.post(self.base+'/revision-impact', json=decision).status_code, 200)
        state = self.state()
        self.assertEqual(state['revision_impact']['status'], 'resolved')
        self.assertEqual(state['revision_impact']['results'][0]['decision'], 'accepted')
        self.assertEqual(state['official']['episode:2']['text'], '하나가 돌아왔다.')

        state, payload = run_batch('auto', '하나', '유나', blocked=True)
        self.assertEqual(self.c.post(self.base+'/revision-impact',
                                     json={**payload, 'episode': 2, 'mode': 'keep'}).status_code, 200)
        self.edit('episode:2', '유나가 돌아왔다.')
        self.approve('episode:2')
        state = self.state()
        self.assertEqual(state['revision_impact']['status'], 'resolved')
        self.assertEqual(state['revision_impact']['results'][0]['decision'], 'accepted')
        self.assertEqual(state['official']['episode:2']['text'], '유나가 돌아왔다.')

        state, payload = run_batch('auto', '유나', '나라', blocked=True, make_patch=False)
        decision = {**payload, 'episode': 2, 'mode': 'acknowledge'}
        self.assertEqual(self.c.post(self.base+'/revision-impact', json=decision).status_code, 200)
        state = self.state()
        self.assertEqual(state['revision_impact']['results'][0]['decision'], 'acknowledged')
        self.assertEqual(state['official']['episode:2']['text'], '유나가 돌아왔다.')

    def test_revision_retries_ambiguous_passage_and_allows_scoped_repair(self):
        for episode, text in enumerate(('진우가 출발했다.', '진우가 웃었다. 그날 밤에도 진우가 웃었다.'), 1):
            self.edit(f'episode:{episode}', text)
            self.approve(f'episode:{episode}')
        self.edit('episode:1', '도윤이 출발했다.')
        self.approve('episode:1')
        attempts = []
        def respond(prompt):
            if '직접 수정해야 할 구절' in prompt:
                attempts.append(prompt)
                return {'blocked': False, 'impact': '이름 변경',
                        'patches': [{'old': '진우가 웃었다.', 'new': '도윤이 웃었다.'}]}
            return {'issues': []}
        self.response = respond
        impact = self.state()['revision_impact']
        base = {'id': impact['id'], 'episode': 2}
        self.assertEqual(self.c.post(self.base+'/revision-impact', json={**base, 'mode': 'auto'}).status_code, 202)
        for _ in range(100):
            impact = self.state()['revision_impact']
            if impact['status'] != 'running':
                break
            time.sleep(.02)
        self.assertEqual(len(attempts), 2)
        self.assertIn('직전 제안은 적용되지 않았습니다', attempts[1])
        self.assertTrue(impact['results'][0]['patch_error'])
        self.assertNotIn('위치를 더 구체적으로', impact['results'][0]['reason'])
        self.assertEqual(self.c.post(self.base+'/revision-impact', json={**base, 'mode': 'acknowledge'}).status_code, 400)
        state = self.state()
        self.assertEqual(state['revision_impact']['status'], 'blocked')
        original = state['official']['episode:2']
        quote = '진우가 웃었다.'
        self.response = lambda prompt: {'replacement': '도윤이 웃었다.'} if '선택 구절의 대체' in prompt else {'findings': []}
        response = self.c.post(self.base+'/chat', json={'request_id': 'impact-scope-repair', 'text': '이름을 바꿔줘',
            'edit_scope': {'key': 'episode:2', 'quote': quote, 'start': original['text'].rindex(quote), 'version': original['version']}})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(self.state()['drafts']['episode:2']['text'], '진우가 웃었다. 그날 밤에도 도윤이 웃었다.')
        self.approve('episode:2')
        self.assertEqual(self.state()['revision_impact']['status'], 'resolved')

    def test_revision_acceptance_refreshes_findings_and_manual_revision_queues_later_episodes(self):
        old = '진우는 붉은 열쇠를 하린에게 건넸다.'
        updated = '도윤은 푸른 열쇠를 하린에게 건넸다.'
        for episode, text in enumerate(('진우가 출발한다.', old, '하린은 붉은 열쇠를 꺼냈다.'), 1):
            self.edit(f'episode:{episode}', text)
            self.approve(f'episode:{episode}')
        with closing(sqlite3.connect(Path(self.temp.name)/'test.sqlite3')) as db, db:
            state = self.state()
            state['records']['2'].update(setting_review='done', findings=[
                {'kind': 'lore', 'name': '열쇠', 'fact': '붉은 열쇠를 건넸다.', 'quote': old, 'reason': ''},
            ])
            db.execute('UPDATE projects SET state=? WHERE id=?', (json.dumps(state), self.pid))

        def respond(prompt):
            if '직접 수정해야 할 구절' in prompt:
                patches = [{'old': old, 'new': updated}] if '\n2화 원고:\n' in prompt else []
                return {'blocked': False, 'impact': '열쇠 변경', 'patches': patches}
            if '일관성을 점검' in prompt:
                return {'issues': []}
            return {'summary': '열쇠를 건넨다.', 'facts': '푸른 열쇠'}

        def assess():
            impact = self.state()['revision_impact']
            payload = {'id': impact['id'], 'episode': 2}
            self.assertEqual(self.c.post(self.base+'/revision-impact', json={**payload, 'mode': 'auto'}).status_code, 202)
            for _ in range(100):
                if self.state()['revision_impact']['status'] == 'blocked':
                    return payload
                time.sleep(.02)
            self.fail('근거 변경으로 점검이 중단되어야 한다.')

        self.response = respond
        self.edit('episode:1', '도윤이 출발한다.')
        self.approve('episode:1')
        payload = assess()
        self.edit('guidelines:0', '짧은 문단으로 나누세요.')
        self.assertEqual(self.c.post(self.base+'/revision-impact', json={**payload, 'mode': 'accept'}).status_code, 200)
        record = self.state()['records']['2']
        self.assertEqual(record['findings'], [])
        self.assertEqual(record['setting_review'], 'organize')
        self.assertNotIn('findings_checked', record)

        # Regenerate from another 1화 change, then repair a rejected 2화 manually.
        old = updated
        updated = '도윤은 은빛 열쇠를 하린에게 건넸다.'
        self.edit('episode:1', '도윤은 열쇠를 찾아 출발한다.')
        self.approve('episode:1')
        original_respond = respond
        self.response = lambda prompt: ({**original_respond(prompt), 'blocked': True}
                                        if '직접 수정해야 할 구절' in prompt and '\n2화 원고:\n' in prompt
                                        else original_respond(prompt))
        payload = assess()
        self.assertEqual(self.c.post(self.base+'/revision-impact', json={**payload, 'mode': 'keep'}).status_code, 200)
        self.edit('episode:2', updated)
        self.approve('episode:2')
        impact = self.state()['revision_impact']
        self.assertEqual(impact['status'], 'pending')
        self.assertEqual(set(impact['changes']), {'episode:2'})
        self.assertEqual(impact['changes']['episode:2'], {'before': old, 'after': updated})

        def downstream(prompt):
            if '직접 수정해야 할 구절' in prompt:
                self.assertIn('\n3화 원고:\n', prompt)
                return {'blocked': False, 'impact': '은빛 열쇠로 수정', 'patches': [
                    {'old': '하린은 붉은 열쇠를 꺼냈다.', 'new': '하린은 은빛 열쇠를 꺼냈다.'},
                ]}
            if '일관성을 점검' in prompt:
                return {'issues': []}
            return {'summary': '열쇠를 꺼낸다.', 'facts': '은빛 열쇠'}

        self.response = downstream
        self.assertEqual(self.c.post(self.base+'/revision-impact',
                                     json={'id': impact['id'], 'mode': 'auto'}).status_code, 202)
        for _ in range(100):
            state = self.state()
            if state['revision_impact']['status'] != 'running':
                break
            time.sleep(.02)
        state = self.accept_ready_revision(state)
        self.assertEqual(state['revision_impact']['status'], 'confirmed')
        self.assertEqual(state['official']['episode:3']['text'], '하린은 은빛 열쇠를 꺼냈다.')

    def test_impact_preserves_valid_proposals_and_unresolved_rows_across_new_changes(self):
        for key, text in [
            ('world:0', '## 서부 왕국\n배경: 해안 왕국.'),
            ('episode:1', '진우가 출발했다.'),
            ('episode:2', '진우는 열쇠를 받았다.'),
            ('episode:3', '열쇠가 반짝였다. 열쇠가 반짝였다.'),
        ]:
            self.edit(key, text)
            self.approve(key)
        self.edit('episode:1', '도윤이 출발했다.')
        self.approve('episode:1')
        new_batch = False

        def respond(prompt):
            if prompt.startswith('확정 설정 변경과 다른 설정 자료'):
                return {'updates': []}
            if '직접 수정해야 할 구절' in prompt:
                if new_batch:
                    self.assertIn('episode:1', prompt)
                    return {'blocked': False, 'impact': '', 'patches': []}
                old, new = (('진우는 열쇠를 받았다.', '도윤은 열쇠를 받았다.')
                            if '\n2화 원고:\n' in prompt else ('열쇠가 반짝였다.', '열쇠가 빛났다.'))
                return {'blocked': False, 'impact': '원고 수정', 'patches': [{'old': old, 'new': new}]}
            if '일관성을 점검' in prompt:
                return {'issues': []}
            return {'summary': '사건 요약', 'facts': '열쇠'}

        def check():
            payload = {'id': self.state()['revision_impact']['id']}
            self.assertEqual(self.c.post(self.base+'/revision-impact', json={**payload, 'mode': 'auto'}).status_code, 202)
            for _ in range(100):
                state = self.state()
                if state['revision_impact']['status'] == 'settings_ready':
                    self.assertEqual(self.c.post(self.base+'/revision-impact', json={**payload, 'mode': 'auto'}).status_code, 202)
                    continue
                if state['revision_impact']['status'] != 'running':
                    return state, payload
                time.sleep(.03)
            self.fail('점검이 완료되지 않았다.')

        self.response = respond
        state, payload = check()
        self.assertEqual(state['revision_impact']['status'], 'blocked')
        self.assertEqual(state['drafts']['episode:2']['text'], '도윤은 열쇠를 받았다.')
        self.assertNotIn('episode:3', state['drafts'])
        self.assertTrue(next(row for row in state['revision_impact']['results'] if row['episode'] == 3)['patch_error'])
        self.assertEqual(self.c.post(self.base+'/revision-impact',
                                     json={**payload, 'mode': 'keep', 'episode': 2}).status_code, 200)

        self.edit('world:0', '## 서부 왕국\n배경: 해안 왕국에 항구가 있다.')
        self.approve('world:0')
        new_batch = True
        state, _ = check()
        impact = state['revision_impact']
        self.assertEqual(impact['status'], 'blocked')
        self.assertEqual(next(row for row in impact['results'] if row['episode'] == 2)['decision'], 'kept')
        self.assertEqual(state['official']['episode:2']['text'], '진우는 열쇠를 받았다.')
        self.assertTrue(next(row for row in impact['results'] if row['episode'] == 3)['patch_error'])

    def test_recheck_replaces_old_ai_draft_and_isolates_provider_failure(self):
        for key, text in [
            ('world:0', '## 서부 왕국\n배경: 해안 왕국.'),
            ('episode:1', '진우가 출발했다.'),
            ('episode:2', '진우는 열쇠를 받았다.'),
            ('episode:3', '진우는 배에 탔다.'),
        ]:
            self.edit(key, text)
            self.approve(key)
        self.edit('episode:1', '도윤이 출발했다.')
        self.approve('episode:1')
        stage = 0

        def respond(prompt):
            if prompt.startswith('확정 설정 변경과 다른 설정 자료'):
                return {'updates': []}
            if '직접 수정해야 할 구절' in prompt:
                if '\n1화 원고:\n' in prompt:
                    return {'blocked': False, 'impact': '', 'patches': []}
                if '\n2화 원고:\n' in prompt:
                    replacement = '도윤은 항구에서 열쇠를 받았다.' if stage else '도윤은 열쇠를 받았다.'
                    return {'blocked': not stage, 'impact': '새 이름', 'patches': [
                        {'old': '진우는 열쇠를 받았다.', 'new': replacement},
                    ]}
                if stage == 1:
                    raise GeminiUnavailable('503')
                return {'blocked': False, 'impact': '새 이름', 'patches': [] if not stage else [
                    {'old': '진우는 배에 탔다.', 'new': '도윤은 항구에서 배에 탔다.'},
                ]}
            if '일관성을 점검' in prompt:
                return {'issues': []}
            return {'summary': '요약', 'facts': '사실'}

        def inspect(mode='auto', episode=None):
            impact = self.state()['revision_impact']
            payload = {'id': impact['id'], 'mode': mode}
            if episode:
                payload['episode'] = episode
            self.assertEqual(self.c.post(self.base+'/revision-impact', json=payload).status_code, 202)
            for _ in range(100):
                state = self.state()
                if state['revision_impact']['status'] == 'settings_ready':
                    self.assertEqual(self.c.post(self.base+'/revision-impact', json=payload).status_code, 202)
                    continue
                if state['revision_impact']['status'] != 'running':
                    return state
                time.sleep(.03)
            self.fail('점검이 완료되지 않았다.')

        self.response = respond
        state = inspect()
        obsolete = state['drafts']['episode:2']['version']
        self.edit('world:0', '## 서부 왕국\n배경: 항구가 있는 해안 왕국.')
        self.approve('world:0')
        self.assertEqual(self.c.post(self.base+'/draft',
                                     json={'key': 'episode:2', 'version': obsolete, 'action': 'review'}).status_code, 200)
        self.assertEqual(self.c.post(self.base+'/draft',
                                     json={'key': 'episode:2', 'version': obsolete, 'action': 'approve'}).status_code, 409)
        stage = 1
        state = inspect()
        self.assertEqual(state['revision_impact']['status'], 'blocked')
        self.assertNotEqual(state['drafts']['episode:2']['version'], obsolete)
        self.assertEqual(state['drafts']['episode:2']['text'], '도윤은 항구에서 열쇠를 받았다.')
        self.assertTrue(next(row for row in state['revision_impact']['results'] if row['episode'] == 3)['patch_error'])
        self.assertEqual(state['official']['episode:2']['text'], '진우는 열쇠를 받았다.')
        self.edit('episode:1', '도윤이 천천히 출발했다.')
        payload = {'id': state['revision_impact']['id'], 'mode': 'accept'}
        self.assertEqual(self.c.post(self.base+'/revision-impact', json={**payload, 'episode': 2}).status_code, 200)
        stage = 2
        state = inspect('retry', 3)
        self.assertEqual(state['drafts']['episode:3']['text'], '도윤은 항구에서 배에 탔다.')
        self.assertEqual(self.c.post(self.base+'/revision-impact', json={**payload, 'episode': 3}).status_code, 200)
        state = self.state()
        self.assertEqual(state['revision_impact']['status'], 'resolved')
        self.assertEqual(state['official']['episode:3']['text'], '도윤은 항구에서 배에 탔다.')
        self.assertEqual(state['drafts']['episode:1']['text'], '도윤이 천천히 출발했다.')

    def test_character_rename_updates_relations_and_links_plot_draft(self):
        old = '## 사사라\n관계: 아이리의 제자\n\n## 아이리\n역할: 스승\n목표: 마지막 검을 완성한다.'
        plot = '## 전체 아웃라인\n시작: 사사라가 아이리를 만난다.\n중간: 아이리는 가르친다.\n결말: 둘은 함께 이긴다.\n\n## 회차 구성\n1화: 아이리가 사사라를 돕는다.'
        self.edit('characters:0', old)
        self.approve('characters:0')
        title = '불꽃을 쫓는 아이리'
        self.assertEqual(self.c.post(self.base+'/manage', json={'title': title}).status_code, 200)
        self.edit('plan:0', f'제목: {title}\n로그라인: 사사라가 아이리를 만난다.')
        self.approve('plan:0')
        self.edit('plot:0', plot)
        self.approve('plot:0')
        self.edit('episode:1', '사사라가 아이리를 만났다.')
        self.approve('episode:1')
        self.response = {'findings': [{'kind': 'plot', 'status': 'clear', 'fact': '사사라가 아이리를 만났다.',
                                      'quote': '사사라가 아이리를 만났다.'}]}
        episode = self.state()['official']['episode:1']
        self.assertEqual(self.c.post(self.base+'/episode-settings-review',
                                     json={'action': 'organize', 'episode': 1, 'version': episode['version']}).status_code, 200)
        self.edit('characters:0', old.replace('## 아이리', '## 아이라라\n성격: 냉소적'))
        self.approve('characters:0')
        result = self.run_linked_settings_check()
        state = result['state']
        self.assertIn('아이라라의 제자', state['official']['characters:0']['text'])
        self.assertEqual(state['official']['plot:0']['text'], plot)
        self.assertIn('아이라라를 만난다.', state['drafts']['plot:0']['text'])
        self.assertNotIn('plan:0', state['drafts'])
        self.assertEqual(state['official']['plan:0']['text'], f'제목: {title}\n로그라인: 사사라가 아이리를 만난다.')
        self.assertEqual(self.c.post(self.base+'/edit', json={
            'key': 'plan:0', 'base_version': state['official']['plan:0']['version'], 'text': '직접 수정하기',
        }).status_code, 409)
        self.assertEqual(result['title'], title)
        self.assertEqual(self.c.get(self.base).json['title'], title)
        self.assertEqual(result['plot_events']['1'], '사사라가 아이라라를 만났다.')
        self.assertEqual(state['official']['episode:1']['text'], episode['text'])

    def test_character_rename_with_added_card_matches_full_and_short_names(self):
        old = '## 이정우\n역할: 주인공\n목표: 무사히 졸업한다.'
        self.edit('characters:0', old)
        self.approve('characters:0')
        place = '## 교실\n설명: 이정우가 배정받았으며 정우에게는 낯선 장소다.'
        self.edit('places:0', place)
        self.approve('places:0')
        self.edit('episode:1', '이정우가 교실에 들어갔고 정우는 창가에 앉았다.')
        self.approve('episode:1')
        chapter = self.state()['official']['episode:1']
        self.response = {'findings': [{'kind': 'plot', 'status': 'clear', 'fact': '이정우가 교실에 들어갔고 정우는 창가에 앉았다.',
                                      'quote': '이정우가 교실에 들어갔고 정우는 창가에 앉았다.'}]}
        self.assertEqual(self.c.post(self.base+'/episode-settings-review',
                                     json={'action': 'organize', 'episode': 1, 'version': chapter['version']}).status_code, 200)
        self.edit('characters:0', old.replace('## 이정우', '## 이정우우우우우') + '\n\n## 민준\n역할: 정우의 짝꿍')
        self.approve('characters:0')
        result = self.run_linked_settings_check()
        state = result['state']
        self.assertIn('정우우우우우의 짝꿍', state['official']['characters:0']['text'])
        self.assertEqual(state['official']['places:0']['text'], place)
        self.assertIn('이정우우우우우가 배정받았으며 정우우우우우에게는', state['drafts']['places:0']['text'])
        self.assertEqual(result['plot_events']['1'], '이정우우우우우가 교실에 들어갔고 정우우우우우는 창가에 앉았다.')
        impact = state['revision_impact']
        self.assertEqual(impact['status'], 'awaiting_settings')
        self.assertEqual(self.c.post(self.base+'/revision-impact',
                                     json={'id': impact['id'], 'mode': 'auto'}).status_code, 409)
        self.approve('places:0')
        self.assertEqual(self.state()['revision_impact']['status'], 'settings_ready')

    def test_impact_revises_confirmed_episode_to_new_length_target(self):
        self.assertEqual(self.c.post(self.base+'/length', json={'target_chars': 3000, 'total_episodes': 5}).status_code, 200)
        self.edit('episode:1', '가' * 3271)
        self.approve('episode:1')
        self.assertEqual(self.c.post(self.base+'/length', json={'target_chars': 4000, 'total_episodes': 5}).status_code, 200)
        calls = []

        def respond(prompt):
            if '직접 수정해야 할 구절' in prompt:
                return {'blocked': False, 'impact': '', 'patches': []}
            if '분량 조정한 전체 원고' in prompt:
                self.assertIn('4000~4500자', prompt)
                calls.append(prompt)
                return {'text': '가' * 3500}
            if '결말 직전에 삽입할 본문만' in prompt:
                self.assertIn('가' * 3500, prompt)
                calls.append(prompt)
                return {'text': '나' * 698}
            if '일관성을 점검' in prompt:
                return {'issues': []}
            return {'summary': '요약', 'facts': '사실'}

        self.response = respond
        state = self.run_linked_settings_check()['state']
        state = self.accept_ready_revision(state)
        self.assertEqual(state['revision_impact']['status'], 'confirmed')
        self.assertEqual(len(state['official']['episode:1']['text']), 4200)
        self.assertEqual(len(calls), 2)

    def test_backup_restores_pending_length_impact_and_can_apply_it(self):
        self.c.post(self.base+'/length', json={'target_chars': 3500, 'total_episodes': 5})
        self.edit('episode:1', '가' * 3568)
        self.approve('episode:1')
        self.c.post(self.base+'/length', json={'target_chars': 4000, 'total_episodes': 5})
        original_base = self.base
        original_impact = self.state()['revision_impact']
        backup = self.c.get(self.base+'/export').json
        restored = self.c.post('/api/projects/import', json=backup)
        self.assertEqual(restored.status_code, 200, restored.json)
        self.base = '/api/projects/'+restored.json['id']
        impact = self.state()['revision_impact']
        self.assertEqual(impact['status'], 'pending')
        self.assertNotEqual(impact['id'], original_impact['id'])
        self.assertEqual(impact['changes'], original_impact['changes'])

        def respond(prompt):
            if '분량 조정한 전체 원고' in prompt:
                return {'text': '가' * 4200}
            if '직접 수정해야 할 구절' in prompt:
                return {'blocked': False, 'impact': '', 'patches': []}
            return {'issues': [], 'summary': '요약', 'facts': '사실'}

        self.response = respond
        state = self.run_linked_settings_check()['state']
        state = self.accept_ready_revision(state)
        self.assertEqual(state['revision_impact']['status'], 'confirmed')
        self.assertEqual(len(state['official']['episode:1']['text']), 4200)
        original = self.c.get(original_base).json['state']
        self.assertEqual(original['revision_impact']['status'], 'pending')
        self.assertEqual(len(original['official']['episode:1']['text']), 3568)

    def test_scoped_repair_cannot_complete_length_review_outside_new_range(self):
        self.c.post(self.base+'/length', json={'target_chars': 3500, 'total_episodes': 5})
        original = '기존 구절.\n\n' + '가' * 3560
        self.edit('episode:1', original)
        self.approve('episode:1')
        self.c.post(self.base+'/length', json={'target_chars': 4000, 'total_episodes': 5})
        def respond(prompt):
            if '분량 조정한 전체 원고' in prompt:
                return {'text': original}
            if '직접 수정해야 할 구절' in prompt:
                return {'blocked': False, 'impact': '', 'patches': []}
            return {'issues': [], 'findings': []}
        self.response = respond
        state = self.run_linked_settings_check()['state']
        original_version = state['official']['episode:1']['version']
        self.assertTrue(state['revision_impact']['results'][0]['patch_error'])
        scope = {'key': 'episode:1', 'quote': '기존 구절.', 'start': 0, 'version': original_version}
        for index, suffix in enumerate(('바뀐 구절.', '나' * 450)):
            self.response = lambda prompt: {'replacement': suffix} if '선택 구절의 대체' in prompt else {'findings': []}
            response = self.c.post(self.base+'/chat', json={'request_id': f'length-repair-{index}',
                'text': '선택 부분 수정', 'edit_scope': scope})
            self.assertEqual(response.status_code, 200, response.json)
            draft = self.state()['drafts']['episode:1']
            for action in ('review', 'approve'):
                decision = self.c.post(self.base+'/draft', json={'action': action, 'key': 'episode:1', 'version': draft['version']})
            if index == 0:
                self.assertEqual(decision.status_code, 409)
                self.assertEqual(self.state()['revision_impact']['status'], 'blocked')
                self.assertEqual(self.state()['official']['episode:1']['text'], original)
                scope.update(quote=suffix, version=draft['version'])
            else:
                self.assertEqual(decision.status_code, 200)
                self.assertEqual(self.state()['revision_impact']['status'], 'resolved')

    def test_setting_change_proposes_linked_draft_before_first_episode(self):
        lore = '## 마법 규칙\n규칙: 주문은 밤에만 쓸 수 있다.'
        world = '## 왕국\n배경: 밤에만 마법을 쓴다.'
        self.edit('lore:0', lore)
        self.approve('lore:0')
        self.edit('world:0', world)
        self.approve('world:0')
        self.edit('lore:0', lore.replace('밤에만', '낮에만'))
        self.approve('lore:0')
        impact = self.state()['revision_impact']
        self.response = {'updates': [{'kind': 'world', 'content': world.replace('밤에만', '낮에만')}]}
        self.assertEqual(self.c.post(self.base+'/revision-impact', json={'id': impact['id'], 'mode': 'auto'}).status_code, 202)
        for _ in range(100):
            state = self.state()
            if state['revision_impact']['status'] != 'running':
                break
            time.sleep(.02)
        self.assertEqual(state['revision_impact']['status'], 'awaiting_settings')
        self.assertEqual(state['official']['world:0']['text'], world)
        self.assertIn('낮에만', state['drafts']['world:0']['text'])
        self.approve('world:0')
        self.assertEqual(self.state()['revision_impact']['id'], impact['id'])
        self.assertEqual(self.state()['revision_impact']['status'], 'done')

    def run_linked_settings_check(self):
        impact = self.state()['revision_impact']
        self.assertEqual(self.c.post(self.base+'/revision-impact', json={'id': impact['id'], 'mode': 'auto'}).status_code, 202)
        for _ in range(100):
            result = self.c.get(self.base).json
            if result['state']['revision_impact']['status'] != 'running':
                return result
            time.sleep(.02)
        self.fail('설정 영향 점검이 완료되지 않았다.')

    def test_first_character_approval_refreshes_name_in_user_edited_place_draft(self):
        character = '## 루이\n역할: 주인공\n설명: 분장의 귀재.'
        self.edit('characters:0', character)
        self.edit('places:0', '## 뒷골목 공방\n설명: 루이가 분장 도구를 만든다.')
        place = '## 지하 공방\n설명: 루이가 밤에만 분장 도구를 만든다.'
        edited = self.edit('places:0', place)
        name = '루루루루루루루이'
        self.edit('characters:0', character.replace('루이', name))
        self.approve('characters:0')
        self.assertEqual(self.state()['drafts']['places:0']['version'], edited)
        state = self.run_linked_settings_check()['state']
        self.assertEqual(state['revision_impact']['status'], 'awaiting_settings')
        self.assertEqual(state['drafts']['places:0']['text'], place.replace('루이', name))
        self.assertNotIn('places:0', state['official'])
        self.approve('places:0')
        self.assertEqual(self.state()['revision_impact']['status'], 'done')

    def test_first_approval_links_name_when_character_traits_are_also_edited(self):
        character = '## 맹구\n역할: 주인공\n성격: 차분함\n목표: 돌멩이를 모은다.\n관계: 이웃과 친하다.\n설명: 매일 강에 간다.'
        place = '## 은빛 강가\n설명: 맹구가 돌멩이를 채집한다.'
        title = '제목: 맹구의 돌멩이 수집일지'
        self.edit('characters:0', character)
        self.edit('places:0', place)
        self.edit('plan:0', title)
        self.approve('plan:0')
        name = '맹구구구국구'
        revised = character.replace('맹구', name).replace('차분함', '호기심이 많음')
        self.edit('characters:0', revised)
        self.approve('characters:0')
        state = self.state()
        self.assertEqual(state['revision_impact']['status'], 'pending')
        self.assertEqual(state['linked_names'], {'맹구': name})
        state = self.run_linked_settings_check()['state']
        self.assertEqual(state['official']['characters:0']['text'], revised)
        self.assertEqual(state['official']['plan:0']['text'], title)
        self.assertNotIn('plan:0', state['drafts'])
        self.assertEqual(state['drafts']['places:0']['text'], place.replace('맹구', name))

    def test_initial_setting_edit_restore_and_apply_before_other_reviews(self):
        self.edit('plan:0', '제목: 파도 소리')
        self.approve('plan:0')
        source = '## 등대\n설명: 밤에 문을 연다.'
        self.edit('places:0', source)
        place_version = self.state()['drafts']['places:0']['version']
        self.edit('world:0', '## 바다 마을\n설명: 등대가 마을을 비춘다.')
        self.edit('lore:0', '## 파도석\n설명: 파도의 힘을 모은다.')
        lore_before = self.state()['drafts']['lore:0']
        for body in ('## 새 등대\n설명: 밤에 문을 연다.', '## 새 등대\n설명: 저녁에 문을 연다.'):
            latest = self.edit('places:0', body)
            self.assertEqual(self.state()['drafts']['places:0']['setup_edit_base']['version'], place_version)
        response = self.c.post(self.base+'/draft', json={'action': 'restore_setup', 'key': 'places:0', 'version': latest})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.state()['drafts']['places:0']['version'], place_version)
        self.assertEqual(self.state()['drafts']['places:0']['text'], source)
        latest = self.edit('places:0', '## 새 등대\n설명: 밤에 문을 연다.')
        response = self.c.post(self.base+'/draft', json={'action': 'change_setup', 'key': 'places:0', 'version': latest})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.state()['revision_impact']['setup_review'])
        state = self.run_linked_settings_check()['state']
        self.assertEqual(state['revision_impact']['waiting_settings'], ['world:0'])
        self.assertEqual(state['official']['places:0']['text'], '## 새 등대\n설명: 밤에 문을 연다.')
        self.assertIn('새 등대가', state['drafts']['world:0']['text'])
        self.assertEqual(state['drafts']['lore:0'], lore_before)
        self.approve('world:0')
        self.assertEqual(self.state()['revision_impact']['status'], 'done')
        self.assertIn('lore:0', self.state()['drafts'])

    def test_initial_setting_restore_from_official_and_scope_excludes_episode_drafts(self):
        self.edit('plan:0', '제목: 등대의 밤')
        self.approve('plan:0')
        text = '## 등대\n설명: 밤에 문을 연다.'
        self.edit('places:0', text)
        self.approve('places:0')
        official = self.state()['official']['places:0']
        version = self.edit('places:0', text.replace('밤', '낮'))
        restore = {'action': 'restore_setup', 'key': 'places:0', 'version': version}
        self.assertEqual(self.c.post(self.base+'/draft', json=restore).status_code, 200)
        self.assertNotIn('places:0', self.state()['drafts'])
        self.assertEqual(self.state()['official']['places:0'], official)
        self.edit('episode:1', '등대에 불이 켜졌다.')
        self.edit('places:0', text.replace('밤', '낮'))
        self.assertNotIn('setup_edit_base', self.state()['drafts']['places:0'])

    def test_later_setting_change_merges_only_conflicting_span_into_existing_draft(self):
        lore = '## 마법 규칙\n규칙: 주문은 밤에만 쓸 수 있다.'
        original = '## 동쪽 공방\n설명: 밤에만 주문을 쓴다.'
        edited = '## 지하 공방\n설명: 밤에만 주문을 쓴다. 벽에 직접 만든 목각 인형을 걸었다.'
        self.edit('lore:0', lore)
        self.approve('lore:0')
        self.edit('places:0', original)
        self.approve('places:0')
        self.edit('episode:1', '여행자는 공방을 찾아갔다.')
        self.approve('episode:1')
        unrelated = self.edit('characters:0', '## 서진\n역할: 나그네')
        self.edit('places:0', edited)
        self.edit('lore:0', lore.replace('밤에만', '낮에만'))
        self.approve('lore:0')
        self.response = {'updates': [{
            'kind': 'places', 'content': '## 원치 않는 전체 교체',
            'patches': [{'old': '밤에만 주문을 쓴다.', 'new': '낮에만 주문을 쓴다.'}],
        }]}
        state = self.run_linked_settings_check()['state']
        self.assertEqual(state['revision_impact']['waiting_settings'], ['places:0'])
        self.assertEqual(state['drafts']['places:0']['text'], edited.replace('밤에만', '낮에만'))
        self.assertEqual(state['official']['places:0']['text'], original)
        self.assertEqual(state['drafts']['characters:0']['version'], unrelated)

    def test_combined_world_and_character_changes_keep_manual_name_and_personality(self):
        characters = '## 도윤\n성격: 차분하다.\n설명: 지하철로 출근한다.'
        world = '## 시대\n배경: 현대 도시.'
        self.edit('characters:0', characters)
        self.approve('characters:0')
        self.edit('world:0', world)
        self.approve('world:0')
        self.edit('characters:0', characters.replace('도윤', '도윤진').replace('차분하다', '낙천적이다'))
        self.approve('characters:0')
        self.edit('world:0', world.replace('현대 도시', '중세 왕국'))
        self.approve('world:0')
        self.response = {'updates': [{'kind': 'characters', 'content': '', 'patches': [
            {'old': '## 도윤진', 'new': '## 도윤'},
            {'old': '낙천적이다.', 'new': '엄격하다.'},
            {'old': '지하철로 출근한다.', 'new': '말을 타고 영지에 간다.'},
        ]}]}
        state = self.run_linked_settings_check()['state']
        draft = state['drafts']['characters:0']
        self.assertEqual(draft['text'], '## 도윤진\n성격: 낙천적이다.\n설명: 말을 타고 영지에 간다.')
        self.assertIn('직접 수정한 내용', draft['impact'])
        self.assertEqual(state['revision_impact']['waiting_settings'], ['characters:0'])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.calls=[]
        self.response={'summary':'요약','facts':'유진은 비밀을 알게 됨. 복선: 열쇠의 주인.'}
        def ai(prompt):
            self.calls.append(prompt)
            if isinstance(self.response,Exception):raise self.response
            if callable(self.response):return self.response(prompt)
            return self.response
        self.app=create_app(str(Path(self.temp.name)/'test.sqlite3'),ai)
        self.c=self.app.test_client()
        self.pid=self.c.post('/api/projects',json={'title':'기능 검사'}).json['id']
        self.base='/api/projects/'+self.pid
    def tearDown(self):
        self.app.extensions['storyloom_workers'].shutdown(wait=True)
        self.temp.cleanup()
    def accept_ready_revision(self,state):
        self.assertEqual(state['revision_impact']['status'],'ready')
        response=self.c.post(self.base+'/revision-impact',json={
            'id':state['revision_impact']['id'],'mode':'approve'})
        self.assertEqual(response.status_code,200,response.json)
        return self.state()

    def state(self):return self.c.get(self.base).json['state']
    def edit(self,key,text):
        current=self.state()['drafts'].get(key) or self.state()['official'].get(key)
        return self.c.post(self.base+'/edit',json={'key':key,'text':text,'base_version':current['version'] if current else None}).json['version']
    def approve(self,key):
        version=self.state()['drafts'][key]['version']
        for action in ('review','approve'):
            self.assertEqual(self.c.post(self.base+'/draft',json={'key':key,'version':version,'action':action}).status_code,200)
    def operation(self,kind,rid='operation-001',version=None):
        item=self.state()['drafts'].get('episode:1') or self.state()['official'].get('episode:1')
        return self.c.post(self.base+'/chat',json={'request_id':rid,'text':'원고 점검','operation':{'type':kind,'key':'episode:1','base_version':item['version'] if version is None else version}})
    def test_metadata_refresh_preserves_manuscript_and_is_not_automatic(self):
        old=self.edit('episode:1','유진이 비밀을 알게 됐다.\n열쇠를 쥐었다.')
        self.assertTrue(self.state()['drafts']['episode:1']['metadata_stale'])
        self.assertEqual(self.operation('refresh_metadata').status_code,200)
        state=self.state();record=state['drafts']['episode:1']
        self.assertEqual(record['text'],'유진이 비밀을 알게 됐다.\n열쇠를 쥐었다.')
        self.assertFalse(record['metadata_stale']);self.assertNotEqual(record['version'],old)
        self.assertFalse(state['official']);self.assertEqual(len(self.calls),1)
        self.assertEqual(state['last_work_result']['applied'],['episode:1'])
        self.approve('episode:1')
        self.assertIn('비밀',self.state()['records']['1']['facts'])
        self.assertEqual(len(self.calls),1)
    def test_first_chapter_metadata_allows_no_additional_facts(self):
        self.edit('episode:1','준서가 길을 걸었다.')
        self.response={'summary':'준서가 길을 걷는 첫 장면','facts':[]}
        self.assertEqual(self.operation('refresh_metadata').status_code,200)
        item=self.state()['drafts']['episode:1']
        self.assertEqual(item['text'],'준서가 길을 걸었다.')
        self.assertEqual(item['facts'],'추가로 추출된 사실 없음')
        self.assertFalse(item['metadata_stale'])

    def test_confirmed_metadata_retry_keeps_the_same_manuscript_version(self):
        key = 'episode:1'
        manuscript = '준서가 길을 걸었다.'
        self.edit(key, manuscript)
        self.approve(key)
        approved = self.state()['official'][key]
        payload = {'key': key, 'version': approved['version']}
        versions = self.c.get(self.base + '/versions').json

        self.response = {'summary': '', 'facts': []}
        failed = self.c.post(self.base + '/episode-metadata', json=payload)
        self.assertEqual(failed.status_code, 400)
        self.assertEqual(self.state()['official'][key], approved)

        self.response = {'summary': '준서가 길을 걷는다.', 'facts': []}
        self.assertEqual(self.c.post(self.base + '/episode-metadata', json=payload).status_code, 200)
        state = self.state()
        self.assertEqual(state['official'][key]['version'], approved['version'])
        self.assertEqual(state['official'][key]['text'], manuscript)
        self.assertFalse(state['records']['1']['metadata_stale'])
        self.assertEqual(state['records']['1']['summary'], self.response['summary'])
        self.assertEqual(len(self.c.get(self.base + '/versions').json), len(versions))
        self.assertEqual(self.c.post(self.base + '/episode-metadata', json=payload).status_code, 200)
        self.assertEqual(len(self.calls), 2)

    def test_length_recommendation_request_does_not_call_ai_or_change_state(self):
        self.edit('plan:0', '진우와 은하가 추적을 따돌린다.')
        self.approve('plan:0')
        before = self.state()
        calls = len(self.calls)
        self.assertEqual(self.c.post(self.base+'/length-plan', json={}).status_code, 200)
        self.assertEqual(self.state(), before)
        self.assertEqual(len(self.calls), calls)

    def test_length_selection_saves_only_after_complete_replan(self):
        self.edit('plan:0', '진우와 은하가 추적을 따돌린다.')
        self.approve('plan:0')
        self.edit('plot:0', '기존 플롯')
        self.approve('plot:0')
        self.c.post(self.base+'/length', json={'target_chars': 4000, 'total_episodes': 12})
        before = self.state()
        selection = {'target_chars': 4000, 'total_episodes': 6}
        payload = {'selection': selection}
        incomplete = {'minimum_episodes': 4, 'reason': '추적과 탈출을 전개할 분량', 'plot': '1화: 각성'}
        self.response = incomplete
        self.assertEqual(self.c.post(self.base+'/length-plan', json=payload).status_code, 400)
        self.assertEqual(self.state(), before)

        def respond(prompt):
            if prompt.startswith('이 작품의 플롯'):
                return {'beginning': '각성', 'middle': '추적', 'ending': '탈출',
                        'episodes': [{'number': n, 'event': f'{n}번째 사건'} for n in range(1, 7)]}
            return incomplete

        self.response = respond
        self.assertEqual(self.c.post(self.base+'/length-plan', json=payload).status_code, 200)
        state = self.state()
        self.assertEqual(state['settings'], selection)
        self.assertEqual(state['official']['plot:0'], before['official']['plot:0'])
        self.assertIn('6화: 6번째 사건', state['drafts']['plot:0']['text'])

    def test_length_selection_without_recommendation_generates_plot_once(self):
        pid = self.c.post('/api/projects', json={'title': '분량 설정', 'defer_length_setup': True}).json['id']
        self.base = '/api/projects/' + pid
        self.edit('plan:0', '진우와 은하가 추적을 따돌린다.')
        self.approve('plan:0')
        self.response = {'minimum_episodes': 25, 'reason': '추적과 탈출을 전개할 분량',
                         'recommended_target_chars': 2000, 'plot': ''}
        self.assertEqual(self.c.post(self.base+'/length-plan', json={}).status_code, 200)
        self.assertNotIn('length_recommendation', self.state())
        self.assertEqual(len(self.calls), 0)
        calls = len(self.calls)

        def respond(prompt):
            self.assertTrue(prompt.startswith('이 작품의 플롯'))
            return {'beginning': '각성', 'middle': '추적', 'ending': '탈출', 'compression': '추격을 한 사건으로 합침',
                    'episodes': [{'number': n, 'event': f'{n}번째 사건'} for n in range(1, 26)]}

        self.response = respond
        selection = {'target_chars': 2000, 'total_episodes': 25}
        self.assertEqual(self.c.post(self.base+'/length-plan', json={'selection': selection}).status_code, 200)
        self.assertEqual(len(self.calls), calls + 1)
        state = self.state()
        self.assertEqual(state['settings'], selection)
        self.assertIn('25화: 25번째 사건', state['drafts']['plot:0']['text'])
        self.assertEqual(state['drafts']['plot:0']['impact'], '추격을 한 사건으로 합침')

    def test_stale_metadata_operation_is_rejected_without_provider_call(self):
        old=self.edit('episode:1','원문');self.edit('episode:1','새 문장')
        self.assertEqual(self.operation('refresh_metadata',version=old).status_code,409)
        self.assertFalse(self.calls)
    def test_reference_proposal_preserves_official_setting_and_manuscript(self):
        self.edit('characters:0','## 유진\n성격: 신중함');self.approve('characters:0')
        self.edit('episode:1','유진이 하린을 만났다.')
        before=self.state()['official']['characters:0'].copy()
        self.response={'updates':[{'kind':'characters','content':'## 유진\n성격: 신중함\n\n## 하린\n관계: 유진의 동료','impact':'1화에서 처음 만남'}]}
        self.assertEqual(self.operation('extract_references').status_code,200)
        state=self.state();self.assertEqual(state['official']['characters:0'],before)
        self.assertIn('하린',state['drafts']['characters:0']['text'])
        self.assertEqual(state['drafts']['episode:1']['text'],'유진이 하린을 만났다.')
        self.assertEqual(len(self.calls),1)
    def test_continuity_check_does_not_change_any_document(self):
        self.edit('episode:1','유진은 처음 듣는 비밀을 알고 말했다.')
        before=self.state()['drafts'].copy();self.response={'reply':'1화 근거: 지식 공개 시점 확인 필요'}
        self.assertEqual(self.operation('continuity').status_code,200)
        self.assertEqual(self.state()['drafts'],before)
        self.assertEqual(self.state()['last_work_result']['status'],'conversation')
    def test_partial_acceptance_keeps_both_prior_versions(self):
        self.edit('episode:1','시작\n\n첫 구간\n첫 줄\n유지\n마지막 줄');self.approve('episode:1')
        official=self.state()['official']['episode:1']
        draft=self.edit('episode:1','시작\n첫 구간\n첫 줄 수정\n유지\n마지막 수정')
        differences=self.c.get(self.base+'/compare?key=episode:1').json['changes']
        self.assertEqual(differences[1], {'type':'delete','before':'\n','after':''})
        chosen=[i for i,c in enumerate(differences) if c['type']!='equal' and c['before'].strip()!=c['after'].strip()][:1]
        response=self.c.post(self.base+'/select-changes',json={'key':'episode:1','draft_version':draft,'official_version':official['version'],'changes':chosen})
        self.assertEqual(response.status_code,200)
        state=self.state();self.assertEqual(state['drafts']['episode:1']['text'],'시작\n\n첫 구간\n첫 줄 수정\n유지\n마지막 줄')
        self.assertEqual(state['official']['episode:1'],official)
        self.assertTrue(state['drafts']['episode:1']['metadata_stale'])
        self.assertIn(draft,[v['id'] for v in self.c.get(self.base+'/versions').json])
        self.assertEqual(self.c.post(self.base+'/select-changes',json={'key':'episode:1','draft_version':draft,'official_version':official['version'],'changes':chosen}).status_code,409)
    def test_generation_records_findings_without_creating_setting_drafts(self):
        self.edit('characters:0','## 유진\n성격: 신중함');self.approve('characters:0')
        before=self.state()['official']['characters:0'].copy()
        audits=[]
        def ai(prompt):
            if prompt.startswith('이번 원고와 이전에 확정한'):
                audits.append(prompt)
                if '유진은 조심스럽게 문을 열었다.' in prompt:
                    self.assertIn('같은 검토 원고에서만 summary', prompt)
                    return {'issues': [], 'summary': '유진은 조심스럽게 문을 열고 출발했다.', 'facts': '하린이 동행한다.',
                            'findings': [{'kind': 'characters', 'name': '하린', 'fact': '하린이 유진과 동행한다.',
                                          'quote': '유진과 하린이 길을 떠났다.', 'status': 'clear'}]}
                return {'issues': ['유진의 신중한 행동이 원고에 반영되지 않았다.']}
            if prompt.startswith('아래 원고의 일관성 충돌'):
                return {'text': '', 'patches': [{'old': '유진은 문을 걷어찼다.', 'new': '유진은 조심스럽게 문을 열었다.'}]}
            if prompt.startswith('REFERENCE_BACKFILL'):
                self.fail('근거가 확인된 설정 추출 결과를 재사용해야 한다.')
            if prompt.startswith('당신은'):
                return {'reply':'1화 초안','updates':[],'write_episode':1,'scene_goals':['만남','출발']}
            prefix = '유진은 문을 걷어찼다.\n\n' if '목표: 만남' in prompt else ''
            if prompt.startswith('한국어 소설의 다음 장면'):
                return {'text':prefix+'유진과 하린이 길을 떠났다.'*280,'summary':'두 사람의 출발','facts':'하린이 동행한다.'}
            self.fail('검토 응답에 수정된 원고의 요약이 있으면 별도 요약 호출이 필요하지 않다.')
        self.app.extensions['storyloom_workers'].shutdown(wait=True)
        self.app=create_app(str(Path(self.temp.name)/'test.sqlite3'),ai)
        self.c=self.app.test_client()
        result=self.c.post(self.base+'/chat',json={'request_id':'write-chapter-001','text':'1화를 써줘'})
        self.assertEqual(result.status_code,200)
        state=self.state();self.assertEqual(state['official']['characters:0'],before)
        self.assertNotIn('characters:0',state['drafts'])
        self.assertEqual(state['drafts']['episode:1']['findings'][0]['fact'],'하린이 유진과 동행한다.')
        self.assertIn('episode:1',state['last_work_result']['applied'])
        self.assertEqual(len(audits),2)
        self.assertTrue(state['drafts']['episode:1']['text'].startswith('유진은 조심스럽게 문을 열었다.'))
        self.assertEqual(state['drafts']['episode:1']['summary'], '유진은 조심스럽게 문을 열고 출발했다.')

    def test_unresolved_continuity_keeps_draft_and_limits_scene_calls(self):
        self.edit('characters:0', '## 유진\n능력: 빗물 한 모금만 움직인다.')
        self.approve('characters:0')
        issue = '원고의 도시 전역 강우 조종이 확정된 능력 범위를 초과한다.'
        scenes = []
        def ai(prompt):
            if prompt.startswith('당신은'):
                return {'reply': '1화', 'updates': [], 'write_episode': 1,
                        'scene_goals': ['발견', '연습', '시도', '마무리']}
            if prompt.startswith('한국어 소설의 다음 장면'):
                scenes.append(prompt)
                return {'text': '유진은 빗줄기의 방향을 바꿨다.' * 250,
                        'summary': '빗줄기를 바꾼다.', 'facts': '유진이 능력을 사용했다.'}
            if prompt.startswith('이번 원고와 이전에 확정한'):
                return {'issues': [issue]}
            if prompt.startswith('아래 원고의 일관성 충돌'):
                return {'text': '', 'patches': []}
            return {'findings': []}
        self.response = ai
        response = self.c.post(self.base+'/chat', json={'request_id': 'draft-with-issues-001', 'text': '1화를 써줘'})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(len(scenes), 1)
        self.assertIn('시도', scenes[-1])
        self.assertEqual(self.state()['drafts']['episode:1']['continuity_issues'], [issue])
        self.approve('episode:1')
        state = self.state()
        self.assertEqual(state['records']['1']['setting_review'], 'continuity')
        calls = len(self.calls)
        self.assertEqual(self.c.post(self.base+'/episode-settings-review', json={
            'episode': 1, 'version': state['official']['episode:1']['version'], 'action': 'continuity',
        }).status_code, 200)
        self.assertEqual(len(self.calls), calls)

    def test_transient_failure_reuses_completed_plan(self):
        from ai_client import RetryableAIError
        prompts=[];failed=[False]
        def ai(prompt):
            prompts.append(prompt)
            if prompt.startswith('REFERENCE_BACKFILL'):return {'updates':[]}
            if prompt.startswith('당신은'):return {'reply':'1화','updates':[],'write_episode':1,'scene_goals':['시작','끝']}
            if not failed[0]:
                failed[0]=True
                raise RetryableAIError('일시적 서버 혼잡 (503)')
            return {'text':'원고'*2100,'summary':'요약','facts':'사실'}
        self.app.extensions['storyloom_workers'].shutdown(wait=True)
        self.app=create_app(str(Path(self.temp.name)/'test.sqlite3'),ai);self.c=self.app.test_client()
        payload={'request_id':'transient-retry-001','text':'1화를 써줘'}
        self.assertEqual(self.c.post(self.base+'/chat',json=payload).status_code,502)
        info=self.c.get(self.base+'/jobs/transient-retry-001').json
        self.assertTrue(info['resume']['plan_saved'])
        self.assertEqual(self.c.post(self.base+'/chat',json=payload).status_code,200)
        self.assertEqual(sum(p.startswith('당신은') for p in prompts),1)

    def test_maximum_length_target_resumes_from_closest_manuscript(self):
        self.assertEqual(self.c.post(self.base+'/length', json={
            'target_chars': 5000, 'total_episodes': 5,
        }).status_code, 200)
        ready = False
        calls = []
        def respond(prompt):
            if prompt.startswith('당신은'):
                return {'reply': '1화', 'updates': [], 'write_episode': 1, 'scene_goals': ['출발', '도착']}
            if prompt.startswith('한국어 소설의 다음 장면'):
                return {'text': '가' * 5802, 'summary': '출발', 'facts': '도착'}
            if '압축한 전체 원고' in prompt:
                calls.append('compress')
                return {'text': '나' * 4900}
            if '삽입할 새 본문만' in prompt:
                self.assertIn('\n원고:\n'+'나' * 4900,prompt)
                calls.append('expand')
                return {'text': '다' * 598 if ready else ''}
            if '재작성한 전체 원고' in prompt:
                calls.append('rewrite')
                self.assertIn('기존 문장과 문단 배치를 고집하지', prompt)
                return {'text': ''}
            if prompt.startswith('REFERENCE_BACKFILL'):
                return {'findings': []}
            if prompt.startswith('이번 원고와 이전에 확정한'):
                return {'issues': [], 'findings': []}
            return {'summary': '출발', 'facts': '도착'}
        self.response = respond
        payload = {'request_id': 'write-length-recovery', 'text': '1화를 써줘'}
        first = self.c.post(self.base+'/chat', json=payload)
        self.assertIn('5,000~5,500자', first.json['error'])
        self.assertEqual(calls, ['compress', 'expand', 'rewrite'])
        ready = True
        retry = self.c.post(self.base+'/chat', json=payload)
        self.assertEqual(retry.status_code, 200, retry.json)
        self.assertEqual(calls, ['compress', 'expand', 'rewrite', 'expand'])
        self.assertEqual(len(self.state()['drafts']['episode:1']['text']), 5500)

    def test_short_target_caps_generated_manuscript_at_target_plus_500(self):
        self.assertEqual(self.c.post(self.base+'/length', json={
            'target_chars': 1000, 'total_episodes': 5,
        }).status_code, 200)

        def respond(prompt):
            if prompt.startswith('당신은'):
                return {'reply': '1화', 'updates': [], 'write_episode': 1, 'scene_goals': ['출발', '도착']}
            if prompt.startswith('한국어 소설의 다음 장면'):
                self.assertIn('회차 전체 목표 1000자, 최대 1500자', prompt)
                return {'text': '가' * 2000}
            if '압축한 전체 원고' in prompt:
                self.assertIn('공백 포함 1000~1500자', prompt)
                return {'text': '나' * 1500}
            if prompt.startswith('이번 원고와 이전에 확정한'):
                return {'issues': [], 'findings': [], 'summary': '출발', 'facts': '도착'}
            self.fail('예상하지 않은 AI 호출')

        self.response = respond
        response = self.c.post(self.base+'/chat', json={'request_id': 'write-short-target', 'text': '1화를 써줘'})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(len(self.state()['drafts']['episode:1']['text']), 1500)

    def test_length_retry_switches_to_another_saved_manuscript_passage(self):
        self.assertEqual(self.c.post(self.base+'/length', json={'target_chars': 3500, 'total_episodes': 5}).status_code, 200)
        first = '\n\n'.join(char * 600 for char in '가나다라')
        last = '\n\n'.join([*([char * 622 for char in '마바']), '사' * 624])
        scenes, windows = [], []
        ready = False

        def respond(prompt):
            if prompt.startswith('당신은'):
                return {'reply': '1화', 'updates': [], 'write_episode': 1, 'scene_goals': ['출발', '도착']}
            if prompt.startswith('한국어 소설의 다음 장면'):
                scenes.append(prompt)
                return {'text': first+'\n\n'+last}
            if '압축한 수정 구간만' in prompt:
                passage = prompt.split('\n수정 구간:\n', 1)[1].split('\n뒤 문맥:\n', 1)[0]
                windows.append(passage)
                return {'text': passage[:-380] if ready else passage}
            if '압축한 전체 원고' in prompt:
                return {'text': first + '\n\n' + last}
            if 'JSON {"summary"' in prompt:
                return {'summary': '출발과 도착', 'facts': '도착'}
            return {'issues': [], 'findings': []}

        self.response = respond
        payload = {'request_id': 'write-length-window-retry', 'text': '1화를 써줘'}
        self.assertIn('4,280자', self.c.post(self.base+'/chat', json=payload).json['error'])
        self.assertEqual(len(windows), 2)
        ready = True
        result = self.c.post(self.base+'/chat', json=payload)
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(len(scenes), 1)
        self.assertNotIn(windows[2], windows[:2])
        text = self.state()['drafts']['episode:1']['text']
        self.assertEqual(len(text), 3900)
        self.assertTrue(text.startswith('가' * 600))
        self.assertTrue(text.endswith('사' * 624))

    def test_organize_without_update_is_reported_as_not_applied(self):
        self.c.post(self.base+'/view',json={'selected':'characters','episode':1})
        self.response={'reply':'정리했습니다','updates':[]}
        response=self.c.post(self.base+'/chat',json={'request_id':'no-update-001','text':'등장인물을 정리해줘'})
        self.assertEqual(response.status_code,200)
        state=self.state();self.assertEqual(state['last_work_result']['status'],'not_applied')
        self.assertIn('실제 자료 변경은 반영되지 않았습니다',state['messages'][-1]['text'])
        self.assertEqual(state['last_work_result']['calls_this_run'],1)

    def test_guidelines_and_relevant_old_chapters_are_in_context(self):
        self.edit('guidelines:0','3인칭 시점. 비밀은 10화 이후 공개.');self.approve('guidelines:0')
        state=self.state();state['messages']=[{'role':'user','text':'검은열쇠 복선을 이어줘'}]
        state['official'].update({'episode:'+str(n):{'text':('검은열쇠가 등장' if n==2 else '일상 장면'),'summary':'회차 요약','facts':'기록'} for n in range(1,12)})
        context=json.loads(build_context(state))
        self.assertNotIn('guidelines:0',context['official'])
        self.assertEqual(context['official']['episode:2']['text'],'검은열쇠가 등장')
        self.assertIn(2,context['retrieved_episodes'])

if __name__=='__main__':unittest.main()
