import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.images import attach_images, image_url, prepare_images, download_image
from app.product import extract_product
from app.browser import inspect_snapshot
from app.visual import validate_visual

URL = 'https://m.media-amazon.com/images/I/test-coat.jpg'
PRODUCT_URL = 'https://www.amazon.com/dp/B0CXT9RSGQ'


class ImageTests(unittest.TestCase):
    def test_only_approved_image_assets_can_be_used(self):
        self.assertEqual(image_url('https://m.media-amazon.com/images/I/test-coat._AC_SX40_.jpg?x=1'), URL)
        for url in ['http://m.media-amazon.com/images/I/coat.jpg', 'https://m.media-amazon.com.evil.invalid/images/I/coat.jpg',
                    'https://127.0.0.1/images/I/coat.jpg', 'file:///private.jpg', 'https://m.media-amazon.com/images/I/coat.svg',
                    'https://secret@m.media-amazon.com/images/I/coat.jpg', 'https://m.media-amazon.com:444/images/I/coat.jpg']:
            with self.subTest(url=url):
                self.assertIsNone(image_url(url))

    def test_gallery_deduplicates_resizes_and_limits_count(self):
        product = attach_images({}, [URL, URL.replace('.jpg', '._SX40_.jpg'), URL.replace('m.media-amazon.com', 'images-na.ssl-images-amazon.com')] + [URL.replace('test-coat', 'asset-' + str(i)) for i in range(20)])
        self.assertEqual(len(product['images']), 12)
        self.assertEqual(product['images'][0]['id'], 'I1')
        self.assertEqual(product['images'][1]['id'], 'I2')
        self.assertEqual(product['vision_image_ids'], [])

    def test_html_gallery_and_jsonld_images(self):
        page = '''<span id="productTitle">Raincoat</span><div id="feature-bullets"><li>Water resistant hooded raincoat for cycling.</li></div>
        <img id="landingImage" src="https://m.media-amazon.com/images/I/coat._SX40_.jpg" data-old-hires="https://m.media-amazon.com/images/I/coat.jpg">
        <div id="altImages"><img src="https://m.media-amazon.com/images/I/visor._SS40_.jpg"></div>
        <script type="application/ld+json">{"@type":"Product","image":["https://m.media-amazon.com/images/I/back.jpg"]}</script>'''
        product = extract_product(page, PRODUCT_URL)
        self.assertEqual(len(product['images']), 3)
        self.assertEqual(product['images'][1]['url'], 'https://m.media-amazon.com/images/I/visor.jpg')
        self.assertFalse(product['vision_image_ids'])

    def test_full_description_is_available_and_video_thumbnails_are_excluded(self):
        page = '''<span id="productTitle">Raincoat</span><div id="feature-bullets"><li>Water resistant hooded raincoat for cycling.</li></div>
        <div id="productDescription">The removable double visor is included with this raincoat.</div>
        <div id="aplus_feature_div">Product details for this model include a foldable hood.</div>
        <div id="altImages"><li class="videoThumbnail"><img src="https://m.media-amazon.com/images/I/video.jpg"></li><li><img src="https://m.media-amazon.com/images/I/coat.jpg"></li></div>'''
        product = extract_product(page, PRODUCT_URL)
        self.assertEqual(len(product['images']), 1)
        self.assertIn('double visor', next(f['value'] for f in product['facts'] if f['label'] == '商品说明'))
        self.assertTrue(any('图文详情' in f['label'] for f in product['facts']))
        empty = page.replace('Product details for this model include a foldable hood.', '商品描述 -0:00 Previous page Next page')
        self.assertFalse(any('图文详情' in f['label'] for f in extract_product(empty, PRODUCT_URL)['facts']))

    def test_rendered_gallery_is_collected_without_faking_visual_analysis(self):
        snapshot = {'title': 'Raincoat', 'facts': [{'label': '功能描述', 'value': '带帽防雨外套'}], 'images': [{'url': URL}]}
        product, state = inspect_snapshot(snapshot, PRODUCT_URL, PRODUCT_URL)
        self.assertEqual(state, 'ready')
        self.assertEqual(product['images'][0]['url'], URL)
        self.assertEqual(product['vision_image_ids'], [])

    def test_partial_download_only_marks_received_images_as_input(self):
        product = attach_images({}, [URL, URL.replace('coat', 'back')])
        with tempfile.TemporaryDirectory() as folder, patch('app.images.download_image', side_effect=lambda item, folder: {'id': item['id'], 'path': Path(folder) / 'image.jpg'} if item['id'] == 'I2' else None):
            result = prepare_images(product, folder)
        self.assertEqual(product['vision_image_ids'], ['I2'])
        self.assertEqual(len(result), 1)

    def test_image_response_type_and_magic_bytes_are_checked(self):
        with tempfile.TemporaryDirectory() as folder, patch('app.images.urllib.request.build_opener') as opener:
            response = opener.return_value.open.return_value.__enter__.return_value
            response.headers.get_content_type.return_value = 'image/jpeg'
            response.read.return_value = b'<html>challenge</html>'
            self.assertIsNone(download_image({'id': 'I1', 'url': URL}, folder))
            response.read.return_value = b'\xff\xd8\xfftest'
            result = download_image({'id': 'I1', 'url': URL}, folder)
            self.assertEqual(result['path'].suffix, '.jpg')
            self.assertIsNone(download_image({'id': '../private', 'url': URL}, folder))


class VisualTests(unittest.TestCase):
    def setUp(self):
        self.product = {'facts': [{'id': 'F1', 'value': '防雨衣，包装含衣服一件。'}], 'vision_image_ids': ['I1']}
        self.finding = {'kind': 'description_missing', 'title': '透明挡雨片是否随附待确认',
                        'observation': '图片头部附近似有两片透明片。', 'text_comparison': '已读描述未说明挡雨片。',
                        'impact': '可能影响骑行时的脸部遮挡预期。', 'action': '请向商家确认该SKU是否含两片挡雨片。',
                        'image_ids': ['I1'], 'text_evidence_ids': []}
        self.review = {'status': 'checked', 'summary': '仅核对所读页面，实物与配件需确认。', 'findings': [self.finding]}

    def test_missing_description_is_a_question_not_a_proven_conflict(self):
        validate_visual(self.review, self.product)
        changed = copy.deepcopy(self.review)
        changed['findings'][0]['kind'] = 'contradiction'
        with self.assertRaises(ValueError):
            validate_visual(changed, self.product)
        changed['findings'][0]['text_evidence_ids'] = ['F1']
        validate_visual(changed, self.product)

    def test_unseen_image_or_unknown_fact_cannot_be_cited(self):
        for field, value in [('image_ids', ['I2']), ('text_evidence_ids', ['F999'])]:
            changed = copy.deepcopy(self.review)
            changed['findings'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_visual(changed, self.product)

    def test_no_images_must_not_claim_checked_or_no_problems(self):
        self.product['vision_image_ids'] = []
        with self.assertRaises(ValueError):
            validate_visual(self.review, self.product)
        validate_visual({'status': 'unavailable', 'summary': '未取得图片，未核对。', 'findings': []}, self.product)

    def test_finding_requires_a_visible_image_and_explanation(self):
        changed = copy.deepcopy(self.review)
        changed['findings'][0]['image_ids'] = []
        with self.assertRaises(ValueError):
            validate_visual(changed, self.product)
        changed = copy.deepcopy(self.review)
        changed['findings'][0]['observation'] = ''
        with self.assertRaises(ValueError):
            validate_visual(changed, self.product)


if __name__ == '__main__':
    unittest.main()
