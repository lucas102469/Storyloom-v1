"""Existing-chapter character appearances: fake AI and isolated SQLite only."""
import copy,json,sqlite3,tempfile,unittest
from contextlib import closing
from pathlib import Path
from app import create_app
from tests import test_setting_history as fixtures
from setting_edits import edit_source
import story_additions

class CharacterAppearanceTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.path=str(Path(self.folder.name)/'appearance.sqlite3')
        self.calls=[];self.sent=False
        self.old='작센은 동굴 밖으로 나왔다.'
        self.new='작센의 연인 나나가 길가에서 작센과 잠시 눈을 마주쳤다.'
        self.patch={'patches':[{'old':self.old,'new':self.old+' '+self.new}]}
        self.first={'reply':'나나를 등장인물 자료에 추가했습니다.','updates':[{'kind':'characters','content':'## 나나\n역할: 조력자\n관계: 작센의 연인.'}]}
        def generate(prompt):
            self.calls.append(prompt)
            if prompt.startswith('STORY_ADDITION_PATCH'):return copy.deepcopy(self.patch)
            if not self.sent:self.sent=True;return copy.deepcopy(self.first)
            return {'summary':'작센과 나나가 만났다.','facts':'나나는 작센의 연인이다.','issues':[],
                    'findings':[{'kind':'characters','name':'나나','role':'조력자','field':'관계','fact':'나나는 작센의 연인이다.',
                                 'quote':self.new,'status':'clear','reason':''}],'updates':[],'items':[],'reply':''}
        self.app=create_app(self.path,generate,auth_required=False);self.c=self.app.test_client()
        self.pid=self.c.post('/api/projects',json={'title':'출현 검사'}).json['id'];self.base='/api/projects/'+self.pid
        fixtures.SettingHistoryTests.seed(self,chapters=True)
        self.request_text='작센의 연인 나나을 출현시켜줘 걍 가끔씩만 나올친구임'
        self.seed(draft_only=True)
    def tearDown(self):
        self.app.extensions['storyloom_workers'].shutdown(wait=True);self.folder.cleanup()
    def state(self):return self.c.get(self.base).json['state']
    def seed(self,draft_only):
        state=self.state();state['selected']='episode';state['episode']=1;state['drafts']={};state['records']={}
        state['settings']={'target_chars':1000,'total_episodes':10}
        state['official']['characters:0']['text']='## 작센 크라우드\n역할: 주인공\n설명: 검객.'
        state['official']['plot:0']['text']='## 전체 아웃라인\n시작: 출발\n중간: 여정\n결말: 귀환\n## 회차 구성\n1화: 작센이 출발한다.'
        body=(self.old+'\n\n'+('작센은 검을 쥔 채 길을 바라보았다. '*50)).strip()
        with closing(sqlite3.connect(self.path)) as con,con:
            content={'text':body,'summary':'출발','facts':'작센이 동굴 밖으로 나왔다.','impact':''}
            status='draft' if draft_only else 'confirmed'
            vid=con.execute('INSERT INTO versions(project,kind,episode,content,status,created) VALUES(?,?,?,?,?,?)',
                (self.pid,'episode',1,json.dumps(content,ensure_ascii=False),status,0)).lastrowid
            state['official'].pop('episode:1',None)
            state['drafts' if draft_only else 'official']['episode:1']={**content,'version':vid,'status':status}
            con.execute('UPDATE projects SET state=? WHERE id=?',(json.dumps(state,ensure_ascii=False),self.pid))
    def chat(self,rid='nana-appearance-001'):
        return self.c.post(self.base+'/chat',json={'request_id':rid,'text':self.request_text})
    def test_exact_request_on_first_draft_creates_scene_and_preserves_hero(self):
        before=self.state();response=self.chat();self.assertEqual(response.status_code,200,response.json)
        state=self.state()
        self.assertEqual(state['official'],before['official'])
        self.assertNotIn('characters:0',state['drafts'])
        self.assertIn(self.new,state['drafts']['episode:1']['text'])
        self.assertEqual(state['drafts']['episode:1']['text'],before['drafts']['episode:1']['text'].replace(self.old,self.old+' '+self.new))
        self.assertEqual(sum(p.startswith('STORY_ADDITION_PATCH') for p in self.calls),1)
    def test_approved_manuscript_exposes_nana_through_existing_settings_review(self):
        self.assertEqual(self.chat().status_code,200)
        draft=self.state()['drafts']['episode:1']
        for action in ('review','approve'):
            response=self.c.post(self.base+'/draft',json={'key':'episode:1','version':draft['version'],'action':action})
            self.assertEqual(response.status_code,200,response.json)
        state=self.state()
        response=self.c.post(self.base+'/episode-settings-review',json={'episode':1,'version':state['official']['episode:1']['version'],'action':'organize'})
        self.assertEqual(response.status_code,200,response.json)
        state=self.state();visible=edit_source(state,'characters:0')['text']
        self.assertIn('## 작센 크라우드',visible);self.assertIn('## 나나',visible)
        self.assertIn('나나는 작센의 연인이다.',visible)
        self.assertNotIn('characters:0',state['drafts'])
    def test_existing_confirmed_chapter_uses_same_appearance_path(self):
        self.seed(draft_only=False);before=self.state()
        self.assertEqual(self.chat().status_code,200)
        state=self.state();self.assertEqual(state['official'],before['official'])
        self.assertIn(self.new,state['drafts']['episode:1']['text']);self.assertNotIn('characters:0',state['drafts'])
    def test_empty_scene_repair_does_not_save_partial_character_sheet(self):
        self.patch={'patches':[]};before=self.state()
        self.assertEqual(self.chat().status_code,502)
        state=self.state();self.assertEqual(state['official'],before['official']);self.assertEqual(state['drafts'],before['drafts'])
    def test_repair_which_omits_named_nana_is_not_reported_as_complete(self):
        self.patch={'patches':[{'old':self.old,'new':'작센은 길을 따라 천천히 걸었다.'}]};before=self.state()
        self.assertEqual(self.chat().status_code,502)
        state=self.state();self.assertEqual(state['official'],before['official']);self.assertEqual(state['drafts'],before['drafts'])
    def test_valid_scene_response_needs_no_extra_scene_request(self):
        self.first['updates'].append({'kind':'episode','episode':1,'patches':self.patch['patches']})
        before=self.state()
        self.assertEqual(self.chat().status_code,200)
        state=self.state()
        self.assertIn(self.new,state['drafts']['episode:1']['text'])
        self.assertEqual(state['official'],before['official'])
        self.assertNotIn('characters:0',state['drafts'])
        self.assertFalse(any(p.startswith('STORY_ADDITION_PATCH') for p in self.calls))

    def test_named_appearance_verbs_route_without_generic_person_word(self):
        for verb in ('출현시켜줘','출연시켜줘','등장시켜줘'):
            with self.subTest(verb=verb):self.assertTrue(story_additions.requested(self.state(),'나나를 '+verb))
        self.assertFalse(story_additions.requested(self.state(),self.request_text,scope={'key':'characters:0'}))
        self.assertFalse(story_additions.requested(self.state(),self.request_text,operation='other'))
        self.assertFalse(story_additions.requested(self.state(),'맞춤법을 고쳐줘'))

if __name__=='__main__':unittest.main()
