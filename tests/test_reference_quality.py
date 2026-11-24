import tempfile
import unittest
from pathlib import Path
from app import create_app
from reference_quality import empty_reference_cards, fill_reference_descriptions, normalize_reference_topics, normalize_character_layout


class ReferenceQualityTests(unittest.TestCase):
    def test_unlisted_attributes_under_described_titles_are_not_promoted(self):
        for label in ('특기','새항목명','잠버릇','선호 음식'):
            text='## 유진의 원룸\n설명: 개인 공간.\n'+label+': 임의의 속성값\n## 오피스\n설명: 일터.'
            self.assertEqual(normalize_reference_topics(text),text)
    def test_environment_and_danger_stay_attributes_without_changing_independent_topics(self):
        for name in ('환경','위험도','기후','등급','재질'):
            original='## 시련의 투기장\n설명: 거대한 장벽의 공간.\n'+name+': 최고 등급'
            self.assertEqual(normalize_reference_topics(original),original)
            split='## 시련의 투기장\n설명: 거대한 장벽의 공간.\n## '+name+'\n설명: 최고 등급'
            self.assertEqual(normalize_reference_topics(split),original)
        independent='## 투기장\n설명: 싸우는 공간.\n## 바다\n설명: 배가 다닌다.'
        self.assertEqual(normalize_reference_topics(independent),independent)
    def test_inline_character_cards_keep_their_descriptions_without_an_ai_repair(self):
        original = ('## 강진혁 역할: 주인공, 길거리 싸움꾼 성격: 냉소적이고 무뚝뚝함 '
                    '목표: 강자를 만나는 것 관계: 서태준의 제안에 반응함 설명: 패배를 모르는 싸움의 천재. '
                    '## 서태준 역할: 복싱부 주장 성격: 열정적이고 끈기 있음 목표: 전국 대회 우승 '
                    '관계: 강진혁을 영입하려 함 설명: 엘리트 복싱 선수 출신.')
        normalized = normalize_character_layout(original)
        self.assertEqual(empty_reference_cards(normalized), [])
        self.assertIn('## 강진혁\n역할:', normalized)
        self.assertIn('\n## 서태준\n역할:', normalized)
        self.assertIn('설명: 패배를 모르는 싸움의 천재.', normalized)
        self.assertEqual(normalize_character_layout(normalized), normalized)
        self.assertEqual(' '.join(normalized.split()), ' '.join(original.split()))
        calls = []
        def ai(prompt):
            calls.append(prompt)
            if prompt.startswith('설명 누락 보완'):
                raise AssertionError('이미 설명이 있는 카드에 AI 보완을 요청하면 안 됩니다.')
            return {'reply': '등장인물', 'updates': [{'kind': 'characters', 'content': original}]}
        with tempfile.TemporaryDirectory() as folder:
            app = create_app(str(Path(folder)/'test.sqlite3'), ai)
            try:
                c = app.test_client()
                pid = c.post('/api/projects', json={'title': '링 위의 포식자'}).json['id']
                response = c.post('/api/projects/'+pid+'/chat', json={
                    'request_id': 'inline-characters-test', 'text': '등장인물을 정리해줘.'})
                self.assertEqual(response.status_code, 200, response.json)
                saved = c.get('/api/projects/'+pid).json['state']['drafts']['characters:0']['text']
                self.assertEqual(saved, normalized)
                self.assertEqual(len(calls), 1)
            finally:
                app.extensions['storyloom_workers'].shutdown(wait=True)

    def test_character_layout_preserves_normal_cards_prose_and_real_missing_descriptions(self):
        for text in ('## 강진혁\n역할: 주인공\n설명: 싸움의 천재.\n\n## 서태준\n설명: 복싱 선수.',
                     '그는 역할: 주인공이라고 적힌 쪽지를 읽었다.', '## 강진혁\n\n## 서태준'):
            self.assertEqual(normalize_character_layout(text), text)
        self.assertEqual([n for n, _ in empty_reference_cards(normalize_character_layout('## 강진혁\n\n## 서태준'))],
                         ['강진혁', '서태준'])

    def test_generic_attributes_stay_under_the_described_owner(self):
        for suffix in ('핵심 규칙: 외부 세계의 시간이 흐르지 않는다.',
                       '## 핵심 규칙\n설명: 외부 세계의 시간이 흐르지 않는다.',
                       '### 핵심 규칙\n외부 세계의 시간이 흐르지 않는다.'):
            text='## 안식의 틈새\n배경: 현실의 시공간 틈새에 있는 차원 공간.\n'+suffix
            result=normalize_reference_topics(text)
            self.assertEqual(result,'## 안식의 틈새\n배경: 현실의 시공간 틈새에 있는 차원 공간.\n규칙: 외부 세계의 시간이 흐르지 않는다.')
            self.assertEqual(normalize_reference_topics(result),result)
            self.assertEqual(empty_reference_cards(result),[])

    def test_global_and_ambiguous_topics_remain_independent(self):
        for text in (
            '## 안식의 틈새\n배경: 차원 공간.\n## 세계관 핵심 규칙\n설명: 모든 세계에 마법이 있다.',
            '## 안식의 틈새\n배경: 차원 공간.\n## 핵심 규칙\n설명: 세계 전체에 마법이 있다.',
            '## 안식의 틈새\n## 핵심 규칙\n설명: 시간이 흐르지 않는다.',
            '## 신아\n설명: 금융 도시.\n## 다른 차원\n설명: 별개의 장소.'):
            self.assertEqual(normalize_reference_topics(text),text)

    def test_inline_topic_does_not_receive_character_name(self):
        characters = '## 무영\n역할: 주인공\n## 지수\n역할: 조력자'
        for text in ('## 무영\n세계관 핵심 규칙: 요괴는 인간의 감정을 배운다.',
                     '##\n세계관 핵심 규칙: 요괴는 인간의 감정을 배운다.',
                     '세계관 핵심 규칙: 요괴는 인간의 감정을 배운다.'):
            result = normalize_reference_topics(text, characters)
            self.assertEqual(result, '## 세계관 핵심 규칙\n설명: 요괴는 인간의 감정을 배운다.')
            self.assertEqual(empty_reference_cards(result), [])
        city = '## 신아\n도시의 핵심 규칙: 돈이 곧 법이다.'
        self.assertIn('## 신아', normalize_reference_topics(city, characters))
        self.assertEqual([n for n, _ in empty_reference_cards(normalize_reference_topics(city, characters))], ['신아'])

    def test_bare_name_is_not_completed_by_a_separate_inline_topic(self):
        text='## 신아\n도시의 핵심 규칙: 돈이 곧 법이며 권력은 자본의 흐름에서 나온다.'
        self.assertEqual([name for name, _ in empty_reference_cards(text)], ['신아'])
        sources='신아는 부채 등급에 따라 거주지가 나뉘는 금융 도시다.'
        filled=fill_reference_descriptions(text,[{
            'name':'신아','description':'신아는 부채 등급에 따라 거주지가 나뉘는 금융 도시다.',
            'evidence':sources}],sources)
        self.assertEqual(empty_reference_cards(filled),[])
        self.assertIn('도시의 핵심 규칙: 돈이 곧 법',filled)
        self.assertIn('## 신아\n설명: 신아는 부채',filled)
        self.assertEqual(fill_reference_descriptions(text,[{
            'name':'신아','description':'거대한 제국이다.','evidence':'원문에 없는 새로운 설정을 지어낸다.'}],sources),text)

    def test_existing_description_and_attribute_subheadings_are_not_flagged(self):
        self.assertEqual(empty_reference_cards('## 서린\n역할: 항해사\n\n## 바다\n### 특징\n파도가 거세다.'),[])
        self.assertEqual([name for name,_ in empty_reference_cards('## 바다\n설명: 미정')],['바다'])

    def test_generated_bare_reference_is_completed_before_saving(self):
        source='신아는 부채 등급에 따라 거주지가 나뉘는 금융 도시다.'
        calls=[]
        def ai(prompt):
            calls.append(prompt)
            if prompt.startswith('설명 누락 보완'):
                return {'items':[{'name':'신아','description':source,'evidence':source}]}
            return {'reply':'세계관을 정리했어요.','updates':[{'kind':'world','content':'## 신아\n'}]}
        with tempfile.TemporaryDirectory() as folder:
            app=create_app(str(Path(folder)/'test.sqlite3'),ai)
            try:
                c=app.test_client()
                pid=c.post('/api/projects',json={'title':'설명 검사'}).json['id']
                response=c.post('/api/projects/'+pid+'/chat',json={
                    'request_id':'reference-description-1','text':'세계관을 정리해줘. '+source})
                self.assertEqual(response.status_code,200,response.json)
                state=c.get('/api/projects/'+pid).json['state']
                self.assertIn('설명: '+source,state['drafts']['world:0']['text'])
                self.assertNotIn('world:0',state['official'])
                self.assertEqual(sum(prompt.startswith('설명 누락 보완') for prompt in calls),1)
            finally:
                app.extensions['storyloom_workers'].shutdown(wait=True)

    def test_existing_rule_title_is_saved_without_ai_entity_naming(self):
        calls = []
        def ai(prompt):
            calls.append(prompt)
            return {'reply': '세계관 정리', 'updates': [{'kind': 'world',
                'content': '세계관 핵심 규칙: 요괴는 인간의 감정을 배워야 인간으로 살아간다.'}]}
        with tempfile.TemporaryDirectory() as folder:
            app = create_app(str(Path(folder)/'test.sqlite3'), ai)
            try:
                c = app.test_client()
                pid = c.post('/api/projects', json={'title': '규칙 제목 검사'}).json['id']
                response = c.post('/api/projects/'+pid+'/chat', json={
                    'request_id': 'rule-heading-test', 'text': '세계관을 정리해줘.'})
                self.assertEqual(response.status_code, 200, response.json)
                result = c.get('/api/projects/'+pid).json['state']['drafts']['world:0']['text']
                self.assertEqual(result, '## 세계관 핵심 규칙\n설명: 요괴는 인간의 감정을 배워야 인간으로 살아간다.')
                self.assertFalse(any('이름이 미정인 항목' in prompt for prompt in calls))
            finally:
                app.extensions['storyloom_workers'].shutdown(wait=True)

    def test_future_ai_generation_saves_attributes_in_their_owner(self):
        generated='## 안식의 틈새\n배경: 현실 밖의 차원 공간.\n## 핵심 규칙\n설명: 이곳에서는 외부 시간이 흐르지 않는다.'
        with tempfile.TemporaryDirectory() as folder:
            app=create_app(str(Path(folder)/'test.sqlite3'), lambda prompt:{
                'reply':'세계관 정리','updates':[{'kind':'world','content':generated}]})
            try:
                c=app.test_client();pid=c.post('/api/projects',json={'title':'속성 소속 검사'}).json['id']
                response=c.post('/api/projects/'+pid+'/chat',json={
                    'request_id':'reference-owner-test','text':'세계관을 정리해줘.'})
                self.assertEqual(response.status_code,200,response.json)
                state=c.get('/api/projects/'+pid).json['state']
                self.assertEqual(state['drafts']['world:0']['text'],
                    '## 안식의 틈새\n배경: 현실 밖의 차원 공간.\n규칙: 이곳에서는 외부 시간이 흐르지 않는다.')
                self.assertNotIn('world:0',state['official'])
            finally:
                app.extensions['storyloom_workers'].shutdown(wait=True)

    def test_missing_evidence_does_not_save_empty_or_invented_reference(self):
        def ai(prompt):
            if prompt.startswith('설명 누락 보완'):
                return {'items':[]}
            return {'reply':'세계관','updates':[{'kind':'world','content':'## 신아\n'}]}
        with tempfile.TemporaryDirectory() as folder:
            app=create_app(str(Path(folder)/'test.sqlite3'),ai)
            try:
                c=app.test_client();pid=c.post('/api/projects',json={'title':'미정 설정'}).json['id']
                response=c.post('/api/projects/'+pid+'/chat',json={
                    'request_id':'reference-description-2','text':'세계관을 정리해줘.'})
                self.assertEqual(response.status_code,502,response.json)
                self.assertIn('설정 설명을 보완하지 못했어요',response.json['error'])
                self.assertNotIn('world:0',c.get('/api/projects/'+pid).json['state']['drafts'])
            finally:
                app.extensions['storyloom_workers'].shutdown(wait=True)
