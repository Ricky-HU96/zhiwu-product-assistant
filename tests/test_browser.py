import threading
import unittest
from unittest.mock import patch

from app.browser import BrowserCancelled, BrowserControl, BrowserError, BrowserReader, inspect_page, inspect_snapshot, wait_for_product
from app.product import ProductError
import server

URL = 'https://www.amazon.com/dp/B0CXT9RSGQ'
PRODUCT = '<span id="productTitle">Shade Chair</span><div id="feature-bullets"><ul><li>Built-in canopy for sitting outdoors.</li></ul></div>'
CHALLENGE = '<title>Robot Check</title><form action="/errors/validateCaptcha">Enter the characters you see</form>'


class FakePage:
    def __init__(self, documents, url=URL):
        self.documents = iter(documents)
        self.url = url
        self.last = ''
        self.focused = False

    def is_closed(self):
        return False

    def content(self):
        self.last = next(self.documents, self.last)
        return self.last

    def goto(self, *args, **kwargs):
        pass

    def bring_to_front(self):
        self.focused = True


class BrowserTests(unittest.TestCase):
    def test_challenge_and_wrong_product_have_no_facts(self):
        self.assertEqual(inspect_page(CHALLENGE, 'https://www.amazon.com/errors/validateCaptcha', URL), (None, 'verification'))
        self.assertEqual(inspect_page(PRODUCT, 'https://www.amazon.com/dp/B000000000', URL), (None, 'wrong_page'))
        self.assertEqual(inspect_page(PRODUCT, 'https://example.com/dp/B0CXT9RSGQ', URL), (None, 'wrong_page'))

    def test_browser_source_provenance(self):
        product, state = inspect_page(PRODUCT, URL, URL)
        self.assertEqual(state, 'ready')
        self.assertEqual(product['source_type'], 'browser')
        self.assertTrue(all(f['source_type'] == 'browser' for f in product['facts']))

    def test_rendered_dom_extraction_and_wrong_asin(self):
        snapshot = {'title': '露营椅', 'facts': [{'label': '功能描述', 'value': '自带遮阳篷，可折叠收纳。'}]}
        product, state = inspect_snapshot(snapshot, URL, URL)
        self.assertEqual(state, 'ready')
        self.assertEqual(product['facts'][1]['value'], '自带遮阳篷，可折叠收纳。')
        self.assertIn('页面价格', product['missing'])
        self.assertEqual(inspect_snapshot(snapshot, 'https://www.amazon.com/dp/B000000000', URL), (None, 'wrong_page'))

    def test_incomplete_page_stops_without_waiting_five_minutes(self):
        with self.assertRaisesRegex(BrowserError, '已停止等待'):
            wait_for_product(FakePage(['<h1>Page loaded but no product</h1>']), URL,
                             BrowserControl(), lambda *args: None, incomplete_timeout=0)

    def test_verification_still_waits_for_human_even_with_short_field_timeout(self):
        control = BrowserControl()
        def progress(*args):
            control.check.set()
        product = wait_for_product(FakePage([CHALLENGE, PRODUCT]), URL, control, progress,
                                   timeout=2, incomplete_timeout=0)
        self.assertEqual(product['title'], 'Shade Chair')

    def test_resumes_after_page_changes_without_clicking_verification(self):
        control = BrowserControl()
        events = []
        def progress(status, message):
            events.append(status)
            control.check.set()  # Test wakeup, not interaction with a challenge.
        product = wait_for_product(FakePage([CHALLENGE, PRODUCT]), URL, control, progress, timeout=2)
        self.assertEqual(product['title'], 'Shade Chair')
        self.assertEqual(events, ['waiting_verification'])

    def test_cancel_and_timeout(self):
        control = BrowserControl()
        control.cancel.set()
        with self.assertRaises(BrowserCancelled):
            wait_for_product(FakePage([CHALLENGE]), URL, control, lambda *args: None)
        with self.assertRaises(BrowserError):
            wait_for_product(FakePage([CHALLENGE]), URL, BrowserControl(), lambda *args: None, timeout=0)

    def test_closed_window_and_not_found(self):
        page = FakePage([PRODUCT])
        page.is_closed = lambda: True
        with self.assertRaises(BrowserError):
            wait_for_product(page, URL, BrowserControl(), lambda *args: None)
        with self.assertRaises(BrowserError):
            wait_for_product(FakePage(['<title>Page Not Found</title>']), URL, BrowserControl(), lambda *args: None)

    def test_owner_thread_reused_across_jobs(self):
        reader = BrowserReader()
        owners = []
        def open_page():
            owners.append(threading.get_ident())
            return FakePage([PRODUCT])
        try:
            with patch('app.browser.browser_health', return_value={'installed': True}), patch.object(reader, '_open', side_effect=open_page):
                first = reader.read(URL, BrowserControl(), lambda *args: None)
                second = reader.read(URL, BrowserControl(), lambda *args: None)
                self.assertEqual(first['title'], second['title'])
                self.assertEqual(len(set(owners)), 1)
                self.assertNotEqual(owners[0], threading.get_ident())
        finally:
            reader.close()


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        server.JOBS['test'] = {'status': 'fetching'}
        server.CONTROLS['test'] = BrowserControl()

    def tearDown(self):
        server.JOBS.pop('test')
        server.CONTROLS.pop('test')

    def test_failed_http_falls_back_then_generates(self):
        product = inspect_page(PRODUCT, URL, URL)[0]
        with patch('server.fetch_product', side_effect=ProductError('验证码')), patch.object(server.BROWSER, 'read', return_value=product) as browser, patch('server.generate', return_value={'script': '口播'}) as model:
            server.analyze('test', {'url': URL})
        browser.assert_called_once()
        model.assert_called_once()
        self.assertEqual(server.JOBS['test']['status'], 'done')
        self.assertEqual(server.JOBS['test']['direct_error'], '验证码')

    def test_direct_mode_does_not_open_window(self):
        with patch('server.fetch_product', side_effect=ProductError('404')), patch.object(server.BROWSER, 'read') as browser:
            server.analyze('test', {'url': URL, 'fetch_mode': 'direct'})
        browser.assert_not_called()
        self.assertEqual(server.JOBS['test']['status'], 'error')

    def test_cancel_releases_workflow_without_calling_model(self):
        with patch.object(server.BROWSER, 'read', side_effect=BrowserCancelled('已取消')), patch('server.generate') as model:
            server.analyze('test', {'url': URL, 'fetch_mode': 'browser'})
        model.assert_not_called()
        self.assertEqual(server.JOBS['test']['status'], 'cancelled')

    def test_sku_inspection_does_not_call_model_or_reuse_previous_check(self):
        product = inspect_page(PRODUCT, URL, URL)[0]
        product['vision_image_ids'] = ['I1']
        with patch.object(server.BROWSER, 'read', return_value=product), patch('server.generate') as model:
            server.analyze('test', {'url': URL, 'fetch_mode': 'browser', 'inspect_only': True, 'visual_check': False})
        model.assert_not_called()
        job = server.JOBS['test']
        self.assertEqual(job['status'], 'done')
        self.assertIsNone(job['result'])
        self.assertFalse(job['product']['visual_check_enabled'])
        self.assertEqual(job['product']['vision_image_ids'], [])


if __name__ == '__main__':
    unittest.main()
