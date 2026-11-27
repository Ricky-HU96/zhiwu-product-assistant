import copy
import unittest
import urllib.request

from app.content import ContentError, validate_result
from app.product import AmazonRedirect, DEMO_TEXT, ProductError, canonical_url, extract_product, manual_product


class ProductTests(unittest.TestCase):
    def test_canonical_and_dirty_assignment_link(self):
        self.assertEqual(canonical_url('https://www.amazon.com/VTOY-Foldable-\x02\nCamping/dp/B0CXT9RSGQ?tag=tracking'),
                         'https://www.amazon.com/dp/B0CXT9RSGQ')
        self.assertEqual(canonical_url('https://amazon.de/gp/product/B0CXT9RSGQ/ref=abc'),
                         'https://www.amazon.de/dp/B0CXT9RSGQ')

    def test_url_allowlist(self):
        for url in ['http://www.amazon.com/dp/B0CXT9RSGQ', 'https://amazon.com.evil.test/dp/B0CXT9RSGQ',
                    'https://www.amazon.com@127.0.0.1/dp/B0CXT9RSGQ', 'https://www.amazon.com:8000/dp/B0CXT9RSGQ',
                    'https://www.amazon.com/dp/SHORT', 'https://www.amazon.com/dp/B0CXT9RSGQMORE',
                    'file:///etc/passwd', 'https://127.0.0.1/dp/B0CXT9RSGQ']:
            with self.subTest(url=url), self.assertRaises(ProductError):
                canonical_url(url)

    def test_block_external_redirect(self):
        request = urllib.request.Request('https://www.amazon.com/dp/B0CXT9RSGQ')
        with self.assertRaises(ProductError):
            AmazonRedirect().redirect_request(request, None, 302, 'Found', {}, 'https://127.0.0.1/private')

    def test_visible_product_fields_and_price_region(self):
        page = '''<html><span id="productTitle">Shade Chair</span><div class="a-price"><span class="a-offscreen">$999</span></div>
        <div id="corePrice_feature_div"><span class="a-offscreen">$79.99</span></div>
        <div id="feature-bullets"><ul><li>Folds into the included carry bag.</li><li>Built-in adjustable canopy.</li></ul></div>
        <table id="productDetails_techSpec_section_1"><tr><th>Weight</th><td>5.2 kg</td></tr></table></html>'''
        result = extract_product(page, 'https://www.amazon.com/dp/B0CXT9RSGQ')
        self.assertEqual(result['title'], 'Shade Chair')
        self.assertEqual(next(f['value'] for f in result['facts'] if f['label'] == '页面价格'), '$79.99')
        self.assertEqual(len(result['facts']), 6)
        self.assertIn('页面品类', result['missing'])
        self.assertTrue(all(f['source_type'] == 'page' for f in result['facts']))
        self.assertEqual(next(f['evidence'] for f in result['facts'] if f['label'] == 'Weight'), 'Weight | 5.2 kg')
        self.assertEqual(result['price_range']['min'], '79.99')

    def test_jsonld_graph_fallback(self):
        page = '''<script type="application/ld+json">{"@graph":[{"@type":"Product","name":"Chair","brand":{"name":"Example"},"description":"Foldable chair with a canopy.","offers":{"price":"50","priceCurrency":"USD"}}]}</script>'''
        result = extract_product(page, '')
        self.assertEqual(result['title'], 'Chair')
        self.assertTrue(any(f['value'] == 'USD 50' for f in result['facts']))

    def test_sku_price_skips_list_and_unit_prices(self):
        page = '''<span id="productTitle">Mascara</span>
        <div id="feature-bullets"><li>Washable mascara with a flexible brush.</li></div>
        <div id="corePrice_feature_div"><span class="a-text-price"><span class="a-offscreen">EUR 12.77</span></span><span class="a-offscreen">EUR 7.71</span></div>
        <div id="variation_color_name"><li data-asin="B08H3JPH74"><img alt="Black">
        <style>.test {price: 900}</style><s>EUR 12.77</s><span class="a-price-unit">EUR 32.14 / oz</span>
        <span class="a-price"><span class="a-offscreen">EUR 7.71</span></span></li>
        <li data-asin="B08H3JPH75"><img alt="Brown">2个选项，起始价：EUR 17.74</li></div>'''
        result = extract_product(page, 'https://www.amazon.com/dp/B08H3JPH74')
        self.assertEqual(result['variants'][0]['price']['amount'], '7.71')
        self.assertEqual(result['variants'][1]['price']['kind'], 'from')
        self.assertEqual(result['price_range']['display'], 'EUR 7.71')
        self.assertNotIn('.test', next(f['evidence'] for f in result['facts'] if f['label'] == 'SKU 选项'))

    def test_bot_page_and_insufficient_information(self):
        for page in ['<title>Robot Check</title><form action="validateCaptcha">', '<span id="productTitle">Chair</span>']:
            with self.subTest(page=page), self.assertRaises(ProductError):
                extract_product(page, '')

    def test_manual_source_and_demo_are_explicit(self):
        product = manual_product(DEMO_TEXT)
        self.assertEqual(product['source_type'], 'manual')
        self.assertIn('尚未', product['notice'])
        self.assertEqual(manual_product(DEMO_TEXT, demo=True)['source_type'], 'demo')
        self.assertEqual(''.join(f['value'] for f in manual_product('a' * 4000)['facts']), 'a' * 4000)


class OutputTests(unittest.TestCase):
    def setUp(self):
        self.product = manual_product(DEMO_TEXT, demo=True)
        angle = {'audience': '户外等候的家长', 'scenario': '球场边等候', 'pain': '日晒', 'feature': '带遮阳篷',
                 'benefit': '坐着休息时多一片遮挡', 'reason': '容易演示', 'evidence_ids': ['F3'], 'variant_ids': []}
        self.result = {'visual_review': {'status': 'unavailable', 'summary': '未提供商品图，未完成核对。', 'findings': []}, 'summary': '带遮阳篷的折叠椅',
                       'dimensions': [{'dimension': name, 'insight': '依据遮阳篷判断户外等候需求。', 'evidence_ids': ['F3']}
                                      for name in ['核心需求', 'SKU选择', '人群偏好', '场景', '价值', '购买顾虑']],
                       'sku_recommendations': [{'audience': '单人等候的家长', 'need': '一人使用', 'selection_reason': '单人装',
                                                'caveat': '需确认折叠尺寸', 'variant_ids': ['V1'],
                                                'evidence_ids': self.product['variants'][0]['evidence_ids']}],
                       'angles': [copy.deepcopy(angle) for _ in range(3)], 'limitations': ['防晒等级未提供', '折叠尺寸需确认'], 'scripts': []}
        for hook in ['球场边等孩子，怕晒？', '周末海边休息，怕晒？', '公园里坐一会儿，怕晒？']:
            body = '晴野带遮阳篷折叠露营椅自带遮阳篷，展开就能坐着休息。'
            self.result['scripts'].append({'title': hook, 'audience': '户外等候者', 'scenario': '等候', 'variant_ids': [],
                                           'product_identity': '晴野', 'identity_evidence_ids': ['F1'],
                                           'selling_points': [{'phrase': '自带遮阳篷', 'evidence_ids': ['F3']}],
                                           'hook': hook, 'script': hook + body, 'script_evidence_ids': ['F1', 'F3'], 'creator_notes': ['拍摄前核对实物遮阳篷结构。'],
                                           'shots': [{'visual': '等候场景', 'voiceover': hook, 'evidence_ids': []},
                                                     {'visual': '展开篷子', 'voiceover': body, 'evidence_ids': ['F3']}]})
        for index in range(2):
            self.result['scripts'][index]['variant_ids'] = [self.product['variants'][index]['id']]
            self.result['scripts'][index]['script_evidence_ids'] += self.product['variants'][index]['evidence_ids']

    def test_valid_result_and_count(self):
        result = validate_result(copy.deepcopy(self.result), self.product)
        self.assertEqual(result['scripts'][0]['char_count'], len(self.result['scripts'][0]['script']))

    def test_overlength_and_missing_evidence(self):
        invalid = copy.deepcopy(self.result)
        invalid['scripts'][1]['script'] = '字' * 151
        with self.assertRaises(ContentError):
            validate_result(invalid, self.product)
        invalid = copy.deepcopy(self.result)
        invalid['angles'][0]['evidence_ids'] = ['F999']
        with self.assertRaises(ContentError):
            validate_result(invalid, self.product)

    def test_hook_and_shots_must_match_script(self):
        for field in ['hook', 'shots']:
            invalid = copy.deepcopy(self.result)
            if field == 'hook':
                invalid['scripts'][2]['hook'] = '不在文案中的开头'
            else:
                invalid['scripts'][2]['shots'][1]['voiceover'] = '这是另一段文案。'
            with self.subTest(field=field), self.assertRaises(ContentError):
                validate_result(invalid, self.product)

    def test_sku_recommendation_cannot_invent_variant_or_wrong_evidence(self):
        for ids, refs in [(['V999'], ['F3']), (['V1'], ['F3'])]:
            invalid = copy.deepcopy(self.result)
            invalid['sku_recommendations'][0].update(variant_ids=ids, evidence_ids=refs)
            with self.assertRaises(ContentError):
                validate_result(invalid, self.product)

    def test_versions_cannot_duplicate_each_other(self):
        invalid = copy.deepcopy(self.result)
        invalid['scripts'][1] = copy.deepcopy(invalid['scripts'][0])
        with self.assertRaises(ContentError):
            validate_result(invalid, self.product)

    def test_multiple_skus_do_not_force_unsupported_variant_voiceover(self):
        invalid = copy.deepcopy(self.result)
        for script in invalid['scripts']:
            script['variant_ids'] = []
        self.assertEqual(len(validate_result(invalid, self.product)['scripts']), 3)

    def test_buyer_checklist_is_rejected_in_voiceover_but_allowed_in_creator_notes(self):
        invalid = copy.deepcopy(self.result)
        invalid['scripts'][0]['script'] = '看中了12V选项，先别急着下单。先问清供电接口，再决定是否购买。'
        with self.assertRaisesRegex(ContentError, 'creator_notes'):
            validate_result(invalid, self.product)
        self.result['scripts'][0]['creator_notes'] = ['拍摄前先问清供电接口与随货线缆。']
        self.assertEqual(validate_result(self.result, self.product)['scripts'][0]['creator_notes'][0], '拍摄前先问清供电接口与随货线缆。')

    def test_specific_product_identity_must_be_spoken_and_grounded(self):
        for identity, spoken in [('晴野', False), ('不存在的品牌', True)]:
            invalid = copy.deepcopy(self.result)
            script = invalid['scripts'][0]
            script['product_identity'] = identity
            script['script'] = script['script'].replace('晴野', identity if spoken else '这款')
            with self.subTest(identity=identity), self.assertRaises(ContentError):
                validate_result(invalid, self.product)

    def test_known_brand_cannot_be_replaced_with_category_identity(self):
        self.product['facts'].append({'id': 'F90', 'label': '品牌', 'value': '晴野', 'evidence': '晴野'})
        invalid = copy.deepcopy(self.result)
        invalid['scripts'][0].update(product_identity='折叠露营椅', identity_evidence_ids=['F1'])
        with self.assertRaisesRegex(ContentError, '商品标识'):
            validate_result(invalid, self.product)

    def test_selling_points_must_be_spoken_and_included_in_script_evidence(self):
        for point in [{'phrase': '防水等级IPX8', 'evidence_ids': ['F3']}, {'phrase': '自带遮阳篷', 'evidence_ids': ['F2']}]:
            invalid = copy.deepcopy(self.result)
            invalid['scripts'][0]['selling_points'] = [point]
            with self.subTest(point=point), self.assertRaises(ContentError):
                validate_result(invalid, self.product)


if __name__ == '__main__':
    unittest.main()
