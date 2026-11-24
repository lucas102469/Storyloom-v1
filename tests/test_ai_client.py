import json
import unittest
from unittest.mock import Mock, patch
import httpx
from google.genai import types
from ai_client import gemini, get_client, partial_text


class AIClientTests(unittest.TestCase):
    def test_continuation_uses_full_prose_contract_and_keeps_paragraphs(self):
        passages=['직전 장면에서 이어진다.\n\n대화한다.','이번 회차를 마친다.']
        response=types.GenerateContentResponse(candidates=[types.Candidate(finish_reason='STOP',
            content=types.Content(parts=[types.Part(text=json.dumps({'passages':passages,'title':'이어서'}))]))])
        client=Mock();client.models.generate_content.return_value=response
        with patch('ai_client.os.environ',{'GEMINI_API_KEY':'dummy-key'}),patch('ai_client.get_client',return_value=client),self.assertLogs('storyloom.ai'):
            result=gemini('scene_length:{"minimum":1000,"maximum":1500,"parts":2}\n직전 회차에서 이어서','continuation_scene')
        self.assertEqual(result['text'],'\n\n'.join(passages))
        self.assertEqual(result['title'],'이어서')
        self.assertEqual(client.models.generate_content.call_count,1)
        schema=client.models.generate_content.call_args.kwargs['config'].response_json_schema
        self.assertEqual(schema['properties']['passages']['minItems'],2)

    def test_first_scene_returns_all_passages_in_order_without_another_request(self):
        passages=['첫 구간.\n\n대화가 이어진다.','두 번째 구간.','세 번째 구간.']
        response=types.GenerateContentResponse(candidates=[types.Candidate(finish_reason='STOP',
            content=types.Content(parts=[types.Part(text=json.dumps({'passages':passages,'title':'첫 만남'}))]))])
        client=Mock();client.models.generate_content.return_value=response
        with patch('ai_client.os.environ',{'GEMINI_API_KEY':'dummy-key'}),patch('ai_client.get_client',return_value=client),self.assertLogs('storyloom.ai'):
            result=gemini('scene_length:{"minimum":1750,"parts":3}\n확정된 첫 장면','first_scene')
        self.assertEqual(result['text'],'\n\n'.join(passages))
        self.assertEqual(result['title'],'첫 만남')
        self.assertEqual(client.models.generate_content.call_count,1)
        contract=client.models.generate_content.call_args.kwargs['config'].response_json_schema
        self.assertEqual(contract['properties']['passages']['minItems'],3)
        self.assertGreaterEqual(contract['properties']['passages']['items']['minLength']*3+4,1750)

    def test_provider_status_is_classified_without_disclosing_details(self):
        from google.genai import errors
        for code, expected in [(503, '일시적으로 혼잡'), (429, '사용 한도'), (403, '접근 권한')]:
            client = Mock()
            client.models.generate_content.side_effect = errors.APIError(code, {'error': {'message': 'private-provider-detail'}})
            with self.subTest(code=code), patch('ai_client.os.environ', {'GEMINI_API_KEY': 'dummy-key'}), patch('ai_client.get_client', return_value=client), self.assertLogs('storyloom.ai'):
                with self.assertRaises(ValueError) as caught:
                    gemini('private manuscript')
                self.assertIn(expected, str(caught.exception))
                self.assertNotIn('private-provider-detail', str(caught.exception))
                self.assertNotIn('dummy-key', str(caught.exception))
                self.assertEqual(client.models.generate_content.call_count, 1)

    def test_task_specific_output_contracts(self):
        response = types.GenerateContentResponse(candidates=[types.Candidate(finish_reason='STOP', content=types.Content(parts=[types.Part(text='{"summary":"요약","facts":"사실"}')]))])
        for task, required in [('metadata',['summary','facts']),('continuity',['reply']),('references',['updates']),
                               ('episode_continuity',['issues']),('episode_findings',['findings']),
                               ('character_names',['names']), ('chat',['reply','updates']), ('scene',['text']),
                               ('home_settings',['items']), ('recover_findings',['episodes']),
                               ('episode_audit',['issues','summary','facts','findings']),
                               ('episode_review_metadata',['issues','summary','facts'])]:
            client=Mock();client.models.generate_content.return_value=response
            with self.subTest(task=task), patch('ai_client.os.environ', {'GEMINI_API_KEY':'dummy-key'}), patch('ai_client.get_client',return_value=client), self.assertLogs('storyloom.ai'):
                gemini('오프라인 형식 검사',task)
                config=client.models.generate_content.call_args.kwargs['config']
                self.assertEqual(config.response_json_schema['required'],required)

    def test_official_sdk_configuration(self):
        get_client.cache_clear()
        with patch('ai_client.genai.Client') as client:
            get_client('dummy-key')
            get_client('dummy-key')
            self.assertEqual(client.call_count, 1)
            options = client.call_args.kwargs['http_options']
            self.assertEqual(options.timeout, 180000)
            self.assertEqual(options.retry_options.attempts, 1)
            self.assertFalse(client.call_args.kwargs['vertexai'])
        get_client.cache_clear()

    def test_response_and_safe_logs(self):
        for task, level in [('chat', 'LOW'), ('scene', 'MEDIUM'), ('metadata', 'LOW')]:
            response = types.GenerateContentResponse(candidates=[types.Candidate(
                finish_reason='STOP', content=types.Content(parts=[types.Part(text='private thought', thought=True),
                    types.Part(text=json.dumps({'reply': 'ok', 'updates': []}))]))])
            client = Mock()
            client.models.generate_content.return_value = response
            with self.subTest(task=task), patch('ai_client.os.environ', {'GEMINI_API_KEY': 'dummy-key', 'STORYLOOM_MODEL': 'gemini-3.8-flash'}), \
                    patch('ai_client.get_client', return_value=client), self.assertLogs('storyloom.ai') as logs:
                self.assertEqual(gemini('private manuscript', task)['reply'], 'ok')
                config = client.models.generate_content.call_args.kwargs['config']
                self.assertEqual(config.thinking_config.thinking_level, level)
                self.assertNotIn('dummy-key', str(logs.output))
                self.assertNotIn('private manuscript', str(logs.output))

    def test_default_uses_verified_lite_without_thinking_override(self):
        response = types.GenerateContentResponse(candidates=[types.Candidate(
            finish_reason='STOP', content=types.Content(parts=[types.Part(text='{"reply":"ok"}')]))])
        client = Mock()
        client.models.generate_content.return_value = response
        with patch('ai_client.os.environ', {'GEMINI_API_KEY': 'dummy-key', 'GEMINI_MODEL': 'gemini-3.8-flash'}), \
                patch('ai_client.get_client', return_value=client), self.assertLogs('storyloom.ai'):
            gemini('test')
            arguments = client.models.generate_content.call_args.kwargs
            self.assertEqual(arguments['model'], 'gemini-3.5-flash-lite')
            self.assertIsNone(arguments['config'].thinking_config)

    def test_timeout_and_truncated_response_do_not_return_drafts(self):
        for response in [httpx.ReadTimeout('sensitive error'), types.GenerateContentResponse(
                candidates=[types.Candidate(finish_reason='MAX_TOKENS', content=types.Content(parts=[types.Part(text='{}')]))])]:
            client = Mock()
            if isinstance(response, Exception):
                client.models.generate_content.side_effect = response
            else:
                client.models.generate_content.return_value = response
            with patch('ai_client.os.environ', {'GEMINI_API_KEY': 'dummy-key'}), \
                    patch('ai_client.get_client', return_value=client), self.assertLogs('storyloom.ai') as logs:
                with self.assertRaises(ValueError):
                    gemini('private manuscript')
                self.assertNotIn('sensitive error', str(logs.output))

    def test_streaming_preview_and_completed_json(self):
        chunks = [types.GenerateContentResponse(candidates=[types.Candidate(
            content=types.Content(parts=[types.Part(text=part)]), finish_reason='STOP' if last else None)])
            for part, last in [('{"reply":"안녕', False), ('하세요","updates":[]}', True)]]
        client = Mock()
        client.models.generate_content_stream.return_value = iter(chunks)
        seen = []
        with patch('ai_client.os.environ', {'GEMINI_API_KEY':'dummy-key'}), patch('ai_client.get_client',return_value=client), self.assertLogs('storyloom.ai'):
            result=gemini('synthetic test',on_progress=lambda text,count:seen.append(text))
        self.assertEqual(result['reply'],'안녕하세요')
        self.assertEqual(seen,['안녕','안녕하세요'])
        self.assertFalse(client.models.generate_content.called)

    def test_partial_json_escapes_do_not_expose_raw_json(self):
        self.assertEqual(partial_text('{"reply":"첫줄\\n둘째줄"'),'첫줄\n둘째줄')
        self.assertEqual(partial_text('{"updates":['),'')
        self.assertEqual(partial_text('{"text":"인사\\uC5'),'인사')
