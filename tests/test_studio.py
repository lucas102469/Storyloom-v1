"""UI/backend contract checks without credentials or external AI requests."""
import re
import tempfile
import unittest
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from app import create_app


class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids, self.assets = [], []

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if 'id' in attrs:
            self.ids.append(attrs['id'])
        if tag in ('script', 'img') and 'src' in attrs:
            self.assets.append(attrs['src'])
        if tag == 'link' and attrs.get('rel') == 'stylesheet':
            self.assets.append(attrs['href'])


class StudioTests(unittest.TestCase):
    def test_landing_has_single_preview_and_workspace_uses_chapter_actions(self):
        with tempfile.TemporaryDirectory() as folder:
            app = create_app(str(Path(folder) / 'tour.sqlite3'), lambda _: {})
            html = app.test_client().get('/').get_data(as_text=True)
            self.assertEqual(html.count('<iframe'), 1)
            self.assertIn('/workspace-preview?stage=episode', html)
            self.assertNotIn('data-tour-section=', html)
            self.assertNotIn('data-step=', html)
            self.assertIn('id="workProjectTitle"', html)
            self.assertNotIn('id="requestRevision"', html)
            self.assertIn('id="referenceTabs"', html)
            app.extensions['storyloom_workers'].shutdown(wait=True)

    def test_preview_reuses_template_without_nested_frames(self):
        with tempfile.TemporaryDirectory() as folder:
            app = create_app(str(Path(folder) / 'studio.sqlite3'), lambda _: {})
            client = app.test_client()
            html = client.get('/workspace-preview').get_data(as_text=True)
            self.assertIn('/static/studio-preview.js', html)
            self.assertNotIn('id="projectSwitcher"', html)
            self.assertIn('aria-label="작품 제목 편집"', html)
            self.assertIn('data-reference="plot"', html)
            self.assertNotIn('<iframe', html)
            self.assertNotIn('id="searchToggle"', html)
            app.extensions['storyloom_workers'].shutdown(wait=True)

    def test_compare_two_saved_versions_is_scoped_and_read_only(self):
        with tempfile.TemporaryDirectory() as folder:
            app = create_app(str(Path(folder) / 'studio.sqlite3'), lambda _: {})
            c = app.test_client()
            pid = c.post('/api/projects', json={'title': '버전 비교'}).json['id']
            base = '/api/projects/' + pid
            versions = []
            previous = None
            for text in ('이전 문장', '선택한 수정 문장', '현재 초안은 다른 내용'):
                previous = c.post(base + '/edit', json={'key': 'plan:0', 'text': text, 'base_version': previous}).json['version']
                versions.append(previous)
            before = c.get(base).json['state']
            result = c.get(base + '/compare?key=plan:0&version=' + str(versions[0]) + '&after_version=' + str(versions[1]))
            self.assertEqual(result.status_code, 200)
            self.assertEqual(''.join(x['after'] for x in result.json['changes']), '선택한 수정 문장')
            self.assertEqual(c.get(base).json['state'], before)
            world = c.post(base + '/edit', json={'key': 'world:0', 'text': '다른 자료', 'base_version': None}).json['version']
            self.assertEqual(c.get(base + '/compare?key=plan:0&after_version=' + str(world)).status_code, 400)
            app.extensions['storyloom_workers'].shutdown(wait=True)

    def test_lore_is_separate_from_world_and_requires_approval(self):
        with tempfile.TemporaryDirectory() as folder:
            app = create_app(str(Path(folder) / 'studio.sqlite3'), lambda _: {})
            client = app.test_client()
            pid = client.post('/api/projects', json={'title': '설정 분리 시험'}).json['id']
            base = '/api/projects/' + pid
            client.post(base + '/edit', json={'key': 'world:0', 'text': '배경은 섬이다.', 'base_version': None})
            result = client.post(base + '/edit', json={'key': 'lore:0', 'text': '별의 빛은 하루 한 번만 사용할 수 있다.', 'base_version': None})
            self.assertEqual(result.status_code, 200)
            version = result.json['version']
            self.assertEqual(client.post(base + '/view', json={'selected': 'lore', 'episode': 1}).status_code, 200)
            state = client.get(base).json['state']
            self.assertNotEqual(state['drafts']['world:0']['text'], state['drafts']['lore:0']['text'])
            self.assertFalse(state['official'])
            for action in ('review', 'approve'):
                self.assertEqual(client.post(base + '/draft', json={'action': action, 'key': 'lore:0', 'version': version}).status_code, 200)
            state = client.get(base).json['state']
            self.assertIn('lore:0', state['official'])
            self.assertNotIn('world:0', state['official'])
            app.extensions['storyloom_workers'].shutdown(wait=True)

    def test_review_state_matches_history_and_bookshelf(self):
        with tempfile.TemporaryDirectory() as folder:
            app = create_app(str(Path(folder) / 'studio.sqlite3'), lambda _: {})
            client = app.test_client()
            pid = client.post('/api/projects', json={'title': '상태 점검'}).json['id']
            base = '/api/projects/' + pid
            version = client.post(base + '/edit', json={
                'key': 'episode:1', 'text': '아직 승인하지 않은 원고', 'base_version': None}).json['version']
            self.assertIn('초안 작성중', client.get('/api/projects').json[0]['current_work'])
            client.post(base + '/draft', json={'action': 'review', 'key': 'episode:1', 'version': version})
            self.assertEqual(client.get(base + '/versions').json[0]['status'], 'review')
            self.assertIn('검토중', client.get('/api/projects').json[0]['current_work'])
            self.assertFalse(client.get(base).json['state']['official'])
            app.extensions['storyloom_workers'].shutdown(wait=True)

    def test_all_functional_controls_exist_once_and_assets_load(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            app = create_app(str(Path(folder) / 'studio.sqlite3'), lambda _: {})
            client = app.test_client()
            html = client.get('/').get_data(as_text=True)
            elements = Elements()
            elements.feed(html)
            self.assertFalse([key for key, count in Counter(elements.ids).items() if count > 1])
            for filename in ('workbench.js', 'studio-ui.js'):
                script = (root / 'static' / filename).read_text(encoding='utf-8')
                references = set(re.findall(r"\$\('([^']+)'\)", script))
                dynamic_ids = set(re.findall(r"\.id\s*=\s*'([^']+)'", script))
                self.assertFalse(references - set(elements.ids) - dynamic_ids, filename)
            for asset in elements.assets:
                self.assertTrue(asset.startswith('/static/'), asset)
                with client.get(asset) as response:
                    self.assertEqual(response.status_code, 200, asset)
            self.assertIn('<script data-studio', html)
            self.assertIn('시작하기 전에', html)
            app.extensions['storyloom_workers'].shutdown(wait=True)
