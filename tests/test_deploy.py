import contextlib
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from deploy.init_private import initialize


class DeploymentTests(unittest.TestCase):
    def test_login_uses_stdin_and_configuration_stays_private(self):
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return SimpleNamespace(returncode=0, stdout='owner:$2y$10$' + 'A' * 53 + '\n')

        with tempfile.TemporaryDirectory() as folder:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = initialize(folder, 'product.example.com', runner=runner)
            root = Path(folder)
            password = (root / '.secrets/initial-login.txt').read_text().split('password: ', 1)[1].strip()
            self.assertGreaterEqual(len(password), 24)
            self.assertNotIn(password, output.getvalue())
            self.assertNotIn(password, ' '.join(calls[0][0]))
            self.assertEqual(calls[0][1]['input'], password + '\n')
            self.assertIn('-niB', calls[0][0])
            self.assertFalse(result['ai_configured'])
            self.assertIn('AI_API_KEY=\n', (root / '.env').read_text())
            self.assertNotIn(password, (root / '.env').read_text())
            with self.assertRaises(ValueError):
                initialize(folder, 'product.example.com', runner=runner)
            self.assertEqual(len(calls), 1)

    def test_unsafe_addresses_fail_before_creating_files(self):
        with tempfile.TemporaryDirectory() as folder:
            for bind in ('0.0.0.0', '8.8.8.8', '224.0.0.1'):
                with self.assertRaises(ValueError):
                    initialize(folder, 'product.example.com', bind)
            for host in ('https://product.example.com', 'product.example.com/path', 'a..example.com', 'example.com;quit'):
                with self.assertRaises(ValueError):
                    initialize(folder, host)
            self.assertFalse(list(Path(folder).iterdir()))


if __name__ == '__main__':
    unittest.main()
