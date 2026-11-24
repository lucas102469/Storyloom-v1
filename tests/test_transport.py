import json
import subprocess
import unittest
from unittest.mock import patch
from gemini_transport import curl_request, TransportFailure


class TransportTests(unittest.TestCase):
    def test_credentials_use_stdin_only_and_unicode_is_preserved(self):
        done = subprocess.CompletedProcess([], 0, b'{"candidates": []}\nSTORYLOOM_HTTP:200', b'')
        with patch('gemini_transport.shutil.which', return_value='curl.exe'), \
                patch('gemini_transport.subprocess.run', return_value=done) as run:
            curl_request('https://example.test', {'text': '안녕'}, 'secret-test-key')
            self.assertNotIn('secret-test-key', str(run.call_args.args))
            config = run.call_args.kwargs['input'].decode('utf-8')
            self.assertIn('secret-test-key', config)
            data_line = next(line for line in config.splitlines() if line.startswith('data-binary'))
            self.assertEqual(json.loads(json.loads(data_line.split(' = ', 1)[1])), {'text': '안녕'})

    def test_errors_do_not_echo_external_data(self):
        cases = [(28, b'', b'secret-test-key', 'TIMEOUT', None),
                 (0, b'sensitive body\nSTORYLOOM_HTTP:403', b'', 'HTTP_ERROR', 403)]
        for code, stdout, stderr, category, status in cases:
            with self.subTest(category=category), patch('gemini_transport.shutil.which', return_value='curl.exe'), \
                    patch('gemini_transport.subprocess.run', return_value=subprocess.CompletedProcess([], code, stdout, stderr)):
                with self.assertRaises(TransportFailure) as caught:
                    curl_request('https://example.test', {}, 'secret-test-key')
                self.assertEqual(caught.exception.category, category)
                self.assertEqual(caught.exception.status, status)
                self.assertNotIn('secret-test-key', str(caught.exception))
                self.assertNotIn('sensitive body', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
