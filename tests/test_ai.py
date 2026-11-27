import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

from app import ai
from app.config import AIConfig, ConfigError, load_config, read_env


def fixture():
    from tests.test_core import OutputTests
    test = OutputTests('test_valid_result_and_count')
    test.setUp()
    return test.product, copy.deepcopy(test.result)


CONFIG = AIConfig('https://models.invalid/v1', 'test-only-value', 'test-model')


class ConfigTests(unittest.TestCase):
    def test_dotenv_precedence_and_no_expansion(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '.env'
            path.write_text('AI_BASE_URL="https://models.invalid/v1" # root\nAI_API_KEY=local-test\nAI_MODEL=chosen\nIGNORED=$(no-execution)\n', encoding='utf-8')
            config = load_config(path, {'AI_MODEL': 'inherited', 'AI_API_KEY': 'inherited'})
            self.assertEqual(config.model, 'chosen')
            self.assertEqual(config.api_key, 'local-test')
            self.assertEqual(read_env(path)['IGNORED'], '$(no-execution)')
            self.assertNotIn('local-test', repr(config))
            self.assertNotIn('models.invalid', repr(config))

    def test_empty_file_value_does_not_reuse_inherited_secret(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '.env'
            path.write_text('AI_BASE_URL=https://models.invalid/v1\nAI_API_KEY=\nAI_MODEL=test\n', encoding='utf-8')
            with self.assertRaises(ConfigError):
                load_config(path, {'AI_API_KEY': 'inherited'})

    def test_remote_http_credentials_query_and_placeholder_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            for url in ['http://models.invalid/v1', 'https://user:private@models.invalid', 'https://models.invalid?secret=private', 'https://your-api-provider.example/v1']:
                with self.subTest(url=url), self.assertRaises(ConfigError) as caught:
                    load_config(Path(folder) / 'missing', {'AI_BASE_URL': url, 'AI_MODEL': 'test', 'AI_API_KEY': 'test-only-value'})
                self.assertNotIn('private', str(caught.exception))

    def test_local_model_can_run_without_key_and_missing_settings_fail(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'missing'
            config = load_config(path, {'AI_BASE_URL': 'http://127.0.0.1:11434/v1', 'AI_MODEL': 'local'})
            self.assertEqual(config.api_key, '')
            with self.assertRaises(ConfigError):
                load_config(path, {})

    def test_malformed_env_never_echoes_values(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '.env'
            path.write_text('AI_API_KEY="private-value\n', encoding='utf-8')
            with self.assertRaises(ConfigError) as caught:
                read_env(path)
            self.assertNotIn('private-value', str(caught.exception))


class AITests(unittest.TestCase):
    def test_health_contains_no_provider_details_or_secret(self):
        with patch('app.ai.load_config', return_value=CONFIG):
            result = ai.health()
        self.assertTrue(result['ready'])
        self.assertNotIn(CONFIG.api_key, json.dumps(result))
        self.assertNotIn(CONFIG.base_url, json.dumps(result))
        self.assertNotIn(CONFIG.model, json.dumps(result))

    def test_payload_protocols_and_fallback_formats(self):
        messages = [{'role': 'user', 'content': 'JSON'}]
        chat = ai.build_payload(CONFIG, messages)
        self.assertEqual(chat['response_format']['json_schema']['schema'], ai.SCHEMA)
        responses = ai.build_payload(AIConfig(CONFIG.base_url, CONFIG.api_key, CONFIG.model, 'responses'), messages)
        self.assertFalse(responses['store'])
        self.assertEqual(responses['text']['format']['type'], 'json_schema')
        fallback = ai.build_payload(AIConfig(CONFIG.base_url, '', 'test', response_format='text'), messages)
        self.assertNotIn('response_format', fallback)

    def test_vision_inputs_use_correct_protocol_and_no_local_file_path(self):
        for style, expected in [('chat_completions', 'image_url'), ('responses', 'input_image')]:
            product = {'facts': [], 'images': []}
            def prepare(product, folder):
                path = Path(folder) / 'I1.jpg'
                path.write_bytes(b'\xff\xd8\xfftest')
                product['vision_image_ids'] = ['I1']
                return [{'id': 'I1', 'path': path, 'mime': 'image/jpeg'}]
            with self.subTest(style=style), patch('app.ai.prepare_images', side_effect=prepare):
                content = ai.user_content(AIConfig(CONFIG.base_url, '', 'vision', style), product, '')
            self.assertEqual(content[-1]['type'], expected)
            self.assertIn('data:image/jpeg;base64,', json.dumps(content))
            self.assertEqual(product['vision_image_ids'], ['I1'])

    def test_text_only_setting_does_not_claim_image_input(self):
        product = {'facts': [], 'images': [], 'vision_image_ids': ['I1']}
        with patch('app.ai.prepare_images') as prepare:
            content = ai.user_content(AIConfig(CONFIG.base_url, '', 'text', vision_enabled=False), product, '')
        prepare.assert_not_called()
        self.assertEqual(product['vision_image_ids'], [])
        self.assertIsInstance(content, str)

    def test_user_opt_out_overrides_enabled_vision_config(self):
        product = {'facts': [], 'images': [], 'vision_image_ids': ['I1'], 'visual_check_enabled': False}
        with patch('app.ai.prepare_images') as prepare:
            content = ai.user_content(CONFIG, product, '')
        prepare.assert_not_called()
        self.assertEqual(product['vision_image_ids'], [])
        self.assertIsInstance(content, str)

    def test_chat_and_responses_extract_actual_output(self):
        for style, data in [
            ('chat_completions', {'choices': [{'message': {'content': '{"ok":true}'}, 'finish_reason': 'stop'}]}),
            ('responses', {'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': '{"ok":true}'}]}]})]:
            with self.subTest(style=style), patch('app.ai.urllib.request.build_opener') as opener:
                opener.return_value.open.return_value.__enter__.return_value.read.return_value = json.dumps(data).encode()
                value = ai.request_completion(AIConfig(CONFIG.base_url, CONFIG.api_key, CONFIG.model, style), [])
                self.assertTrue(json.loads(value)['ok'])
                request = opener.return_value.open.call_args.args[0]
                self.assertEqual(request.get_header('Authorization'), 'Bearer ' + CONFIG.api_key)
                self.assertTrue(request.full_url.endswith('/responses' if style == 'responses' else '/chat/completions'))

    def test_provider_error_does_not_echo_secret_or_private_endpoint(self):
        for code in [301, 400, 401, 429, 500]:
            error = urllib.error.HTTPError('https://private.invalid', code, CONFIG.api_key, {}, io.BytesIO(CONFIG.api_key.encode()))
            with self.subTest(code=code), patch('app.ai.urllib.request.build_opener') as opener:
                opener.return_value.open.side_effect = error
                with self.assertRaises(ai.AIError) as caught:
                    ai.request_completion(CONFIG, [])
                self.assertNotIn(CONFIG.api_key, str(caught.exception))
                self.assertNotIn('private.invalid', str(caught.exception))
        self.assertIsNone(ai.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.invalid'))

    def test_generation_validates_and_repairs_once(self):
        product, result = fixture()
        events = []
        with patch('app.ai.load_config', return_value=CONFIG), patch('app.ai.request_completion', side_effect=['not JSON', json.dumps(result)]) as request:
            actual = ai.generate(product, 'test audience', lambda status, message: events.append(status))
        self.assertEqual(request.call_count, 2)
        self.assertEqual(events, ['generating', 'generating', 'repairing'])
        self.assertEqual(len(actual['scripts']), 3)
        self.assertTrue(all(script['char_count'] <= 150 for script in actual['scripts']))

    def test_invalid_output_never_reaches_client_and_lock_is_released(self):
        product, _ = fixture()
        with patch('app.ai.load_config', return_value=CONFIG), patch('app.ai.request_completion', return_value=CONFIG.api_key):
            with self.assertRaises(ai.AIError) as caught:
                ai.generate(product)
        self.assertNotIn(CONFIG.api_key, str(caught.exception))
        self.assertFalse(ai.GENERATION_LOCK.locked())


if __name__ == '__main__':
    unittest.main()
