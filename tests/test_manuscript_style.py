import unittest
from manuscript_style import present
from workflow import normalize_manuscript_breaks

class ManuscriptStyleTests(unittest.TestCase):
    def test_direct_speech_gets_separate_paragraph_without_changing_words(self):
        text='남자는 검을 들었다. "너따위가?" 도하는 바라보았다.'
        self.assertEqual(present(text),'남자는 검을 들었다.\n\n"너따위가?"\n\n도하는 바라보았다.')
        self.assertEqual(present(present(text)),present(text))
    def test_curly_quotes_and_short_quoted_terms(self):
        self.assertEqual(present('그는 “최강의 검”을 들었다.'),'그는 “최강의 검”을 들었다.')
        self.assertEqual(present('그는 물었다. “괜찮아?”'),'그는 물었다.\n\n“괜찮아?”')
    def test_external_episode_label_removed_but_dialogue_reference_preserved(self):
        self.assertEqual(present('진동음과 함께 2화의 거대한 충돌은 막을 내렸다.'),'진동음과 함께 거대한 충돌은 막을 내렸다.')
        self.assertIn('2화의',present('그는 "그 소설 2화의 전투가 좋았어."라고 말했다.'))
    def test_unquoted_narrative_is_not_guessed_or_rewritten(self):
        text='남자는 떠나라고 말했다. 도하는 침묵했다.'
        self.assertEqual(present(text),text)
    def test_existing_newline_escape_normalization_still_works(self):
        self.assertEqual(normalize_manuscript_breaks('첫 문단.\\n\\n다음 문단.'),'첫 문단.\n\n다음 문단.')

if __name__=='__main__':unittest.main()
