import unittest
from workflow import normalize_metadata, classify_document_updates

class DocumentFormatTests(unittest.TestCase):
    def test_metadata_accepts_lists_and_explicit_empty_facts(self):
        self.assertEqual(normalize_metadata({'summary':'1화 요약','facts':['인물이 등장','열쇠를 얻음']})['facts'],'인물이 등장\n열쇠를 얻음')
        self.assertEqual(normalize_metadata({'summary':'1화 요약','facts':[]})['facts'],'추가로 추출된 사실 없음')
        self.assertEqual(normalize_metadata({'metadata':{'summary':'1화 요약','facts':{'유진':'비밀을 알게 됨'}}})['facts'],'유진: 비밀을 알게 됨')
        with self.assertRaises(ValueError):normalize_metadata({'summary':'1화 요약'})
        with self.assertRaises(ValueError):normalize_metadata({'summary':'','facts':'사실'})
    def test_library_uses_explicit_genre_tags_and_logline(self):
        import tempfile
        from pathlib import Path
        from app import create_app
        with tempfile.TemporaryDirectory() as folder:
            app=create_app(str(Path(folder)/'cards.sqlite3'),lambda _: {})
            c=app.test_client();pid=c.post('/api/projects',json={'title':'별이 잠든 바다'}).json['id']
            c.post('/api/projects/'+pid+'/edit',json={'key':'plan:0','base_version':None,'text':'로그라인: 마지막 별을 찾는 여행.\n장르: 판타지\n키워드: 모험, 별, 등대'})
            card=c.get('/api/projects').json[0]
            self.assertEqual(card['genre'],'판타지')
            self.assertEqual(card['description'],'마지막 별을 찾는 여행.')
            self.assertEqual(card['tags'],['모험','별','등대'])
            app.extensions['storyloom_workers'].shutdown(wait=True)

    def test_plot_only_material_is_not_saved_as_a_plan(self):
        response=classify_document_updates({'reply':'정리','updates':[{'kind':'plan','content':'## 전체 플롯\n시작: 탄생\n## 회차 구성\n1화: 시작'}]})
        self.assertEqual(response['updates'][0]['kind'],'plot')
        self.assertIn('별도로',response['reply'])
        response=classify_document_updates({'reply':'정리','updates':[{'kind':'plan','content':'로그라인: 모험\n장르: 판타지\n## 시놉시스\n여행'}]})
        self.assertEqual(response['updates'][0]['kind'],'plan')

if __name__=='__main__':unittest.main()
