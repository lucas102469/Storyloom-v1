import copy
import hashlib
import json
import sqlite3
import unittest
from contextlib import closing
import branch_setup
from tests import test_alternative_stories as fixtures

class BranchSetupTests(unittest.TestCase):
    setUp=fixtures.AlternativeStoryTests.setUp
    tearDown=fixtures.AlternativeStoryTests.tearDown
    generate=fixtures.AlternativeStoryTests.generate
    publish=fixtures.AlternativeStoryTests.publish
    state=fixtures.AlternativeStoryTests.state
    enable=fixtures.AlternativeStoryTests.enable

    def create_branch(self):
        self.enable()
        with closing(sqlite3.connect(self.database)) as con,con:
            row=con.execute('SELECT payload FROM published_episode_settings WHERE work=? AND episode=2',(self.public_id,)).fetchone()
            payload=json.loads(row[0])
            payload['official']['plot:0']={'text':'## 전체 플롯\n시작: 원작 시작\n중간: ORIGINAL_FUTURE_SECRET\n결말: 원작 완결\n\n## 회차 구성\n1화: 원작 첫 사건\n2화: 원작 두 번째 사건\n3화: ORIGINAL_FUTURE_SECRET'}
            con.execute('UPDATE published_episode_settings SET payload=? WHERE work=? AND episode=2',(json.dumps(payload),self.public_id))
        result=self.reader.post(self.root+'/branch',json={'episode':2})
        self.assertEqual(result.status_code,200,result.json)
        pid=result.json['id']
        return pid,'/api/projects/'+pid

    def revise(self,pid,review='organize'):
        with closing(sqlite3.connect(self.database)) as con,con:
            state=self.state(pid)
            content={key:value for key,value in state['official']['episode:2'].items() if key not in ('version','status')}
            content.update(text='윤서는 눈물을 흘린 뒤 새로운 갈등과 마주하기로 결심했다.',summary='윤서가 새로운 갈등과 마주했다.',facts='새로운 갈등',metadata_stale=False)
            vid=con.execute('INSERT INTO versions(project,kind,episode,content,status,created) VALUES(?,?,?,?,?,0)',
                (pid,'episode',2,json.dumps(content),'confirmed')).lastrowid
            state['official']['episode:2']={**content,'version':vid,'status':'confirmed'}
            state['records']['2']={'version':vid,'summary':content['summary'],'facts':content['facts'],'metadata_stale':False,'setting_review':review,'findings':[]}
            con.execute('UPDATE projects SET state=? WHERE id=?',(json.dumps(state),pid))
        return vid

    def plan_result(self):
        return {'beginning':'수정한 사건을 이어간다.','middle':'새 갈등을 해결한다.','ending':'새 갈등을 마무리한다.',
                'episodes':[{'number':3,'event':'새로운 갈등을 조사한다.'},{'number':4,'event':'갈등을 해결한다.'}]}

    def prepare_plan(self,base):
        self.prepared=self.plan_result()
        return self.reader.post(base+'/branch-setup',json={'selection':{'target_chars':5000,'total_episodes':4}})

    def test_import_keeps_only_selected_chapters_and_completed_plot_without_future_outline(self):
        pid,base=self.create_branch()
        project=self.reader.get(base).json['state']
        self.assertEqual(branch_setup.events(project['official']['plot:0']['text']),{1:'원작 첫 사건',2:'원작 두 번째 사건'})
        self.assertNotIn('ORIGINAL_FUTURE_SECRET',project['official']['plot:0']['text'])
        self.assertEqual(project['branch_origin']['setup_stage'],'revise')
        self.assertNotIn('episode:3',project['official'])
        self.assertEqual(self.state(self.pid),self.original)
        self.assertEqual(self.prompts,[])

    def test_order_blocks_length_and_next_writing_until_review_and_plan_acceptance(self):
        pid,base=self.create_branch()
        self.assertEqual(self.prepare_plan(base).status_code,409)
        self.assertEqual(self.prompts,[])
        vid=self.revise(pid)
        self.assertEqual(self.prepare_plan(base).status_code,409)
        for route in ('length','initial-length','length-plan'):
            result=self.reader.post(base+'/'+route,json={'target_chars':5000,'total_episodes':4})
            self.assertEqual(result.status_code,409,result.json)
        self.prepared={'findings':[{'kind':'characters','name':'윤서','fact':'새로운 갈등과 마주하기로 결심했다.',
            'quote':'윤서는 눈물을 흘린 뒤 새로운 갈등과 마주하기로 결심했다.','status':'clear','reason':'clear'}]}
        reviewed=self.reader.post(base+'/episode-settings-review',json={'episode':2,'version':vid,'action':'organize'})
        self.assertEqual(reviewed.status_code,200,reviewed.json)
        state=self.reader.get(base).json['state']
        self.assertEqual(state['branch_origin']['setup_stage'],'length')
        self.assertEqual(state['records']['2']['findings'][0]['reason'],'')
        self.assertEqual(self.reader.post(base+'/episode-batch',json={'action':'start','count':2}).status_code,409)
        response=self.prepare_plan(base)
        self.assertEqual(response.status_code,200,response.json)
        state=self.reader.get(base).json['state']
        self.assertEqual(state['branch_origin']['setup_stage'],'plan')
        self.assertEqual(state['settings']['target_chars'],4000)
        self.assertEqual(sorted(branch_setup.events(state['drafts']['plot:0']['text'])),[1,2,3,4])
        self.assertNotIn('ORIGINAL_FUTURE_SECRET',self.prompts[-1])
        self.assertEqual(self.reader.post(base+'/episode-batch',json={'action':'start','count':2}).status_code,409)
        before={key:copy.deepcopy(value) for key,value in state['official'].items() if key.startswith('episode:')}
        version=state['drafts']['plot:0']['version']
        for action in ('review','approve'):
            accepted=self.reader.post(base+'/draft',json={'key':'plot:0','version':version,'action':action})
            self.assertEqual(accepted.status_code,200,accepted.json)
        state=self.reader.get(base).json['state']
        self.assertTrue(state['branch_origin']['setup_complete'])
        self.assertIsNone(branch_setup.error(state))
        self.assertEqual(state['settings'],{'target_chars':5000,'total_episodes':4})
        self.assertEqual({key:value for key,value in state['official'].items() if key.startswith('episode:')},before)
        self.assertNotIn('revision_impact',state)
        self.assertEqual(self.state(self.pid),self.original)

    def test_invalid_future_plan_does_not_change_existing_settings_or_chapters(self):
        pid,base=self.create_branch();self.revise(pid,'done')
        before=self.reader.get(base).json['state']
        self.prepared={**self.plan_result(),'episodes':[{'number':2,'event':'過去を書き換える'},{'number':3,'event':'重複'}]}
        result=self.reader.post(base+'/branch-setup',json={'selection':{'target_chars':5000,'total_episodes':4}})
        self.assertEqual(result.status_code,400,result.json)
        state=self.reader.get(base).json['state']
        self.assertEqual(state['official'],before['official'])
        self.assertEqual(state['settings'],before['settings'])
        self.assertNotIn('plot:0',state['drafts'])

    def test_pending_plan_survives_backup_and_cannot_be_accepted_after_source_change(self):
        pid,base=self.create_branch();self.revise(pid,'done')
        self.assertEqual(self.prepare_plan(base).status_code,200)
        backup=self.reader.get(base+'/export').json
        imported=self.reader.post('/api/projects/import',json=backup)
        self.assertEqual(imported.status_code,200,imported.json)
        restored='/api/projects/'+imported.json['id']
        state=self.reader.get(restored).json['state']
        self.assertEqual(state['branch_origin']['setup_stage'],'plan')
        version=state['drafts']['plot:0']['version']
        self.assertEqual(self.reader.post(restored+'/draft',json={'key':'plot:0','version':version,'action':'review'}).status_code,200)
        accepted=self.reader.post(restored+'/draft',json={'key':'plot:0','version':version,'action':'approve'})
        self.assertEqual(accepted.status_code,200,accepted.json)
        with closing(sqlite3.connect(self.database)) as con,con:
            current=self.state(pid)
            current['official']['world:0']['text']+='\n규칙: 새로운 규칙.'
            con.execute('UPDATE projects SET state=? WHERE id=?',(json.dumps(current),pid))
        draft=self.reader.get(base).json['state']['drafts']['plot:0']
        self.reader.post(base+'/draft',json={'key':'plot:0','version':draft['version'],'action':'review'})
        result=self.reader.post(base+'/draft',json={'key':'plot:0','version':draft['version'],'action':'approve'})
        self.assertEqual(result.status_code,409,result.json)
        self.assertFalse(self.reader.get(base).json['state']['branch_origin']['setup_complete'])

    def test_regular_projects_are_not_intercepted(self):
        result=self.owner.post('/api/projects/'+self.pid+'/branch-setup',json={'selection':{'target_chars':5000,'total_episodes':5}})
        self.assertEqual(result.status_code,409)
        self.assertEqual(self.prompts,[])
        self.assertEqual(self.state(self.pid),self.original)

    def test_reason_cleanup_is_exclusive_to_branch_and_preserves_real_review_reasons(self):
        state={'records':{'2':{'setting_review':'done','findings':[{'reason':'clear'},{'reason':'설정 충돌 확인 필요'}]}}}
        branch_setup.clean_findings(state)
        self.assertEqual(state['records']['2']['findings'][0]['reason'],'clear')
        state['branch_origin']={'episode':2,'setup_flow':1,'setup_complete':False}
        branch_setup.clean_findings(state)
        self.assertEqual(state['records']['2']['findings'][0]['reason'],'')
        self.assertEqual(state['records']['2']['findings'][1]['reason'],'설정 충돌 확인 필요')
        self.assertEqual(state['records']['2']['setting_review'],'review')

    def test_existing_unfinished_branch_migrates_once_and_keeps_version_history(self):
        pid,base=self.create_branch()
        with closing(sqlite3.connect(self.database)) as con,con:
            state=self.state(pid)
            state['branch_origin'].pop('setup_flow');state['branch_origin'].pop('setup_complete')
            state['official']['plot:0']['text']='## 전체 플롯\n결말: 옛 미래\n1화: 첫 사건\n2화: 둘째 사건\n3화: 옛 미래'
            con.execute('UPDATE projects SET state=? WHERE id=?',(json.dumps(state),pid))
        first=self.reader.get(base).json['state']
        self.assertEqual(sorted(branch_setup.events(first['official']['plot:0']['text'])),[1,2])
        version=first['official']['plot:0']['version']
        second=self.reader.get(base).json['state']
        self.assertEqual(second['official']['plot:0']['version'],version)
        self.assertEqual(second['branch_origin']['setup_stage'],'revise')

    def test_manual_plan_edit_cannot_rewrite_retained_chapters(self):
        pid,base=self.create_branch();self.revise(pid,'done')
        self.assertEqual(self.prepare_plan(base).status_code,200)
        draft=self.reader.get(base).json['state']['drafts']['plot:0']
        changed=draft['text'].replace('1화: 원작 첫 사건','1화: 다른 첫 사건')
        edited=self.reader.post(base+'/edit',json={'key':'plot:0','base_version':draft['version'],'text':changed})
        self.assertEqual(edited.status_code,200,edited.json)
        version=edited.json['version']
        self.reader.post(base+'/draft',json={'key':'plot:0','version':version,'action':'review'})
        accepted=self.reader.post(base+'/draft',json={'key':'plot:0','version':version,'action':'approve'})
        self.assertEqual(accepted.status_code,409,accepted.json)
        self.assertFalse(self.reader.get(base).json['state']['branch_origin']['setup_complete'])

    def test_future_plot_does_not_return_via_chat_before_branch_planning(self):
        pid,base=self.create_branch()
        self.prepared={'reply':'현재 회차 구성을 정리했어요.','updates':[{'kind':'plot','content':
            '## 전체 플롯\n시작: 이전 시작\n중간: 미래 계획\n결말: 미래 결말\n\n1화: 첫 사건\n2화: 바꾼 사건\n3화: FUTURE_SHOULD_NOT_RETURN'}]}
        result=self.reader.post(base+'/chat',json={'request_id':'branch-plot-no-future','text':'현재 플롯을 정리해줘'})
        self.assertEqual(result.status_code,200,result.json)
        state=self.reader.get(base).json['state']
        plot=state['drafts'].get('plot:0') or state['official']['plot:0']
        self.assertNotIn('FUTURE_SHOULD_NOT_RETURN',plot['text'])
        self.assertEqual(sorted(branch_setup.events(plot['text'])),[1,2])
        self.assertFalse(any(prompt.startswith('플롯 자동 정리') for prompt in self.prompts))

    def test_future_plot_cannot_be_added_manually_before_branch_planning(self):
        pid,base=self.create_branch()
        plot=self.reader.get(base).json['state']['official']['plot:0']
        result=self.reader.post(base+'/edit',json={'key':'plot:0','base_version':plot['version'],
            'text':plot['text']+'\n3화: 아직 정하면 안 되는 미래'})
        self.assertEqual(result.status_code,409,result.json)
        self.assertNotIn('plot:0',self.reader.get(base).json['state']['drafts'])

    def test_completed_branch_keeps_normal_review_status_behavior(self):
        state={'branch_origin':{'episode':2,'setup_flow':1,'setup_complete':True},
               'records':{'2':{'setting_review':'done','findings':[{'reason':'원작에서 보관한 후보'}]}}}
        branch_setup.clean_findings(state)
        self.assertEqual(state['records']['2']['setting_review'],'done')
        self.assertEqual(branch_setup.stage(state),'complete')

    def test_all_pending_setting_changes_are_reviewed_before_new_length(self):
        pid,base=self.create_branch();self.revise(pid,'done')
        for key in ('plan:0','plot:0','settings:0'):
            with self.subTest(key=key):
                with closing(sqlite3.connect(self.database)) as con,con:
                    state=self.state(pid)
                    state['drafts'][key]={'text':'검토할 설정 변경','version':999,'status':'draft'}
                    con.execute('UPDATE projects SET state=? WHERE id=?',(json.dumps(state),pid))
                result=self.prepare_plan(base)
                self.assertEqual(result.status_code,409,result.json)
                self.assertEqual(self.reader.get(base).json['state']['branch_origin']['setup_stage'],'review')
                with closing(sqlite3.connect(self.database)) as con,con:
                    state=self.state(pid);state['drafts'].pop(key)
                    con.execute('UPDATE projects SET state=? WHERE id=?',(json.dumps(state),pid))
        self.assertEqual(self.prompts,[])
