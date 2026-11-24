import unittest
from first_chapter_length import plan_length_edits, apply_length_edits


class FirstChapterLengthTests(unittest.TestCase):
    def test_allocated_edits_hit_target_without_touching_other_prose(self):
        body='\n\n'.join(char*200 for char in '가나다라마바사아자차')
        for lower in (1000,2500):
            with self.subTest(lower=lower):
                windows=plan_length_edits(body,lower,lower+500)
                self.assertGreaterEqual(len(windows),2)
                edits={'edits':[{'id':item['id'],'text':'새'*item['target_chars']} for item in windows]}
                result=apply_length_edits(body,windows,edits)
                self.assertEqual(len(result),lower+250)
                self.assertTrue(result.startswith('가'*200+'\n\n'))
                self.assertTrue(result.endswith('\n\n'+'차'*200))
                expected=body
                for item in reversed(windows):
                    expected=expected[:item['start']]+'새'*item['target_chars']+expected[item['end']:]
                self.assertEqual(result,expected)

    def test_bad_edits_are_not_applied(self):
        body='\n\n'.join(char*200 for char in '가나다라마바사아자차')
        windows=plan_length_edits(body,2500,3000)
        good=[{'id':item['id'],'text':'새'*item['target_chars']} for item in windows]
        for bad in (good[:-1],[good[0]]*len(good),[{**item,'id':99} for item in good],
                    [{**item,'text':body} for item in good]):
            with self.subTest(bad=bad[0].get('id') if bad else None),self.assertRaises(ValueError):
                apply_length_edits(body,windows,{'edits':bad})

    def test_no_forced_edits_without_suitable_interior_prose(self):
        self.assertEqual(plan_length_edits('도입\n\n끝맺음',1000,1500),[])
        self.assertEqual(plan_length_edits('가'*1100,1000,1500),[])
