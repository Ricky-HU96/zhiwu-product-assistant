"""The browser must be able to load every application script, including cache."""
import re
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from server import Handler


class AssetTests(unittest.TestCase):
    def test_each_script_referenced_by_the_page_is_served_as_javascript(self):
        with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server, patch.object(Handler, 'log_message'):
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f'http://127.0.0.1:{server.server_port}'
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                page = opener.open(base, timeout=5).read().decode('utf-8')
                scripts = re.findall(r'<script src="([^"]+)"', page)
                self.assertIn('/sku-session.js', scripts)
                self.assertLess(scripts.index('/sku-session.js'), scripts.index('/app.js'))
                for script in scripts:
                    with self.subTest(script=script), opener.open(base + script, timeout=5) as response:
                        self.assertEqual(response.status, 200)
                        self.assertIn('javascript', response.headers['Content-Type'])
                        self.assertGreater(len(response.read()), 100)
            finally:
                server.shutdown()
                thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
