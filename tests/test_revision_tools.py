import unittest
from revision_tools import adjust_revision_length, apply_revision_patches, readable_manuscript, revision_timeline, repair_reviewed_manuscript


class RevisionToolsTests(unittest.TestCase):
    def test_recycled_insertion_is_rejected_without_rejecting_new_prose(self):
        from revision_tools import replays_existing_manuscript
        body='"연우는 문을 열었다." '+'가'*150+'\n\n시온이 차를 건넸다. '+'나'*150
        self.assertTrue(replays_existing_manuscript(body,body.replace('"','').replace('\n\n',' ')))
        self.assertTrue(replays_existing_manuscript(body,body+' 새로운 문장.'))
        self.assertFalse(replays_existing_manuscript(body,'연우는 찻잔을 돌려 쥐었다. 김이 손등에 닿았다.'))
        self.assertFalse(replays_existing_manuscript(body,'시온이 차를 건넸다.',allow_excerpt=True))

    def test_repetition_guard_requires_exact_long_sentence_runs(self):
        from revision_tools import repeated_passage_issues
        first=' '.join(['연우는 창가에 앉아 따뜻한 차가 식어 가는 모습을 말없이 지켜보았다']*3)+'.'
        second=' '.join(['시온은 서두르지 않고 낡은 나무 의자를 천천히 손질하며 기다렸다']*3)+'.'
        passage=first+' '+second
        self.assertEqual(len(repeated_passage_issues(passage+'\n\n'+passage)),1)
        self.assertEqual(repeated_passage_issues(passage+'\n\n'+passage.replace('시온은','시온도')),[])
        self.assertEqual(repeated_passage_issues('괜찮아. 기다릴게.\n\n괜찮아. 기다릴게.'),[])

    def test_review_repair_requires_actual_body_and_falls_back_to_complete_rewrite(self):
        for response in ({'reply':'고치세요.'},{'text':'원문.','patches':[]}):
            self.assertIsNone(repair_reviewed_manuscript('원문.',['문제'],'',{},lambda *a,**k:response))
        result=repair_reviewed_manuscript('원문.',['다음 회차 사건을 덜어내라.'],'확정 설정',{},
            lambda *a,**k:{'text':'현재 회차의 장면을 새로 구성했다.',
                          'patches':[{'old':'없는 구절','new':'잘못된 수정'}]})
        self.assertEqual(result,'현재 회차의 장면을 새로 구성했다.')

    def test_review_uses_matching_pending_neighbor_but_not_unrelated_draft(self):
        chapters={1:{'text':'서윤 출발'},2:{'text':'서윤 도착'},3:{'text':'공식 결말'}}
        drafts={'episode:2':{'text':'사라 도착','version':9},'episode:3':{'text':'미확정 결말','version':10}}
        results=[{'episode':2,'version':9}]
        self.assertEqual(revision_timeline(chapters,{1:'사라 출발'},drafts,results),
            {1:'사라 출발',2:'사라 도착',3:'공식 결말'})
        results[0]['decision']='kept'
        self.assertEqual(revision_timeline(chapters,{},drafts,results)[2],'서윤 도착')

    def test_long_paragraphs_get_breaks_without_word_changes(self):
        text=' '.join(['사라는 마을을 걸으며 도준을 기다렸다.']*40)
        result=readable_manuscript(text)
        self.assertIn('\n\n',result)
        self.assertEqual(result.split(),text.split())
        self.assertEqual(readable_manuscript(result),result)

    def test_rewrite_name_normalization_is_measured_before_accepting_length(self):
        result=adjust_revision_length('원문',10,15,'',lambda *a,**k:{'text':'서윤은 마을에 도착했다.'},
            lambda _:None,normalize=lambda text:text.replace('서윤은','사라는'))
        self.assertEqual(result,'사라는 마을에 도착했다.')

    def test_length_keeps_closest_full_revision_then_adds_before_ending(self):
        original = '가'*70+'\n\n결말.'
        closer = '나'*85+'\n\n결말.'
        calls=[]
        def generate(prompt, task):
            calls.append(prompt)
            return {'text': closer if len(calls)==1 else '대화가 이어졌다.'}
        result=adjust_revision_length(original,100,110,'확정 설정',generate,lambda _:None)
        self.assertTrue(100<=len(result)<=110)
        self.assertTrue(result.startswith('나'*85))
        self.assertTrue(result.endswith('결말.'))
        self.assertEqual(len(calls),2)
        self.assertIn(closer,calls[1])
        self.assertIn('삽입할 본문만',calls[1])

    def test_failed_expansion_switches_to_full_restructure_using_closest_candidate(self):
        outputs=iter(['가'*95, '나'*30, '다'*105])
        prompts=[]
        def generate(prompt,**kwargs):
            prompts.append(prompt)
            return {'text':next(outputs)}
        result=adjust_revision_length('가'*80,100,110,'',generate,lambda _:None)
        self.assertTrue(100<=len(result)<=110)
        self.assertIn('다'*105,result)
        self.assertNotIn('나'*30,result)
        self.assertIn('처음부터 다시 짜서',prompts[2])
        self.assertIn('가'*95,prompts[2])
        self.assertIn('핵심 사건, 인물 관계, 시점, 결말을 유지',prompts[2])

    def test_failure_never_truncates_or_fabricates_to_meet_count(self):
        self.assertIsNone(adjust_revision_length('원문.',100,110,'',lambda *a,**k:{'text':''},lambda _:None))

    def test_retry_resumes_saved_closer_candidate_and_checkpoints_progress(self):
        checkpoints=[]
        def generate(prompt, **kwargs):
            self.assertIn('삽입할 본문만',prompt)
            self.assertIn('나'*95,prompt)
            return {'text':'대화가 이어짐.'}
        result=adjust_revision_length('가'*70,100,110,'',generate,lambda _:None,
            saved='나'*95,checkpoint=checkpoints.append)
        self.assertTrue(100<=len(result)<=110)
        self.assertEqual(checkpoints,[result])

    def test_batch_patches_are_anchored_before_any_replacements(self):
        self.assertEqual(apply_revision_patches('A 문장. B 문장.',[
            {'old':'A 문장.','new':'B 문장.'},{'old':'B 문장.','new':'C 문장.'}]),'B 문장. C 문장.')
        self.assertEqual(apply_revision_patches('그는 말했다.\n\n문이 열렸다.',[
            {'old':'그는 말했다. 문이 열렸다.','new':'그는 문을 열었다.'}]),'그는 문을 열었다.')
        for original, patches in [
            ('같다. 같다.',[{'old':'같다.','new':'다르다.'}]),
            ('원래 문장.',[{'old':'다른 문장.','new':'수정.'}]),
            ('하나 둘 셋',[{'old':'하나 둘','new':'넷'},{'old':'둘 셋','new':'다섯'}])]:
            with self.assertRaises(ValueError):apply_revision_patches(original,patches)
