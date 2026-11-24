import json
import unittest
from workflow import build_episode_context, DEFAULT_SETTINGS


class EpisodeScopeTests(unittest.TestCase):
    def test_context_distinguishes_current_events_from_future_events(self):
        state={'messages':[],'selected':'episode','episode':1,'settings':DEFAULT_SETTINGS,
               'official':{'plot:0':{'text':'## 회차 구성\n1화: 상실과 복수 결심\n2화: 마을 출발\n3화: 수련 시작'}},
               'drafts':{},'records':{}}
        scope=json.loads(build_episode_context(state,1))['episode_scope']
        self.assertEqual(scope['current_events'],'상실과 복수 결심')
        self.assertEqual(scope['reserved_for_later'],{'2':'마을 출발','3':'수련 시작'})
        self.assertIn('분량이 부족해도',scope['instruction'])
        state['official']['plot:0']['text']='| 회차 | 사건 |\n|---|---|\n| 1화 | 상실 |\n| 2화 | 출발 |'
        self.assertEqual(json.loads(build_episode_context(state,1))['episode_scope']['current_events'],'상실')

