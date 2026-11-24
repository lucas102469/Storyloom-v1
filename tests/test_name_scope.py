import unittest
from workflow import name_only_changes, linked_entity_renames, replace_linked_names


class NameScopeTests(unittest.TestCase):
    def test_real_story_change_is_not_treated_as_rename(self):
        changes = {'characters:0': {'before': '## 진혁\n능력: 한 기업을 감시한다.',
                                    'after': '## 라라리\n능력: 도시 전체를 통제한다.'}}
        self.assertFalse(name_only_changes(changes, {'진혁': '라라리'}))

    def test_plan_and_plot_name_changes_remain_narrow(self):
        changes = {key: {'before': '진혁은 반격을 준비한다.', 'after': '라라리는 반격을 준비한다.'}
                   for key in ('characters:0', 'plan:0', 'plot:0')}
        self.assertTrue(name_only_changes(changes, {'진혁': '라라리'}))
        changes['settings:0'] = {'before': '분량 1000', 'after': '분량 2000'}
        self.assertFalse(name_only_changes(changes, {'진혁': '라라리'}))

    def test_unambiguous_short_name_and_particles_follow_new_full_name(self):
        before = '## 강진혁\n역할: 주인공\n설명: 반격을 준비한다.'
        renames = linked_entity_renames(before, before.replace('강진혁', '소리케'), characters=True)
        self.assertEqual(replace_linked_names('강진혁은 진혁과 떠났다.', renames), '소리케는 소리케와 떠났다.')

    def test_short_name_that_matches_another_card_is_not_replaced(self):
        before = '## 강진혁\n설명: 주인공이다.\n\n## 진혁\n설명: 별개의 사람이다.'
        renames = linked_entity_renames(before, before.replace('## 강진혁', '## 소리케'), characters=True)
        self.assertNotIn('진혁', renames)
