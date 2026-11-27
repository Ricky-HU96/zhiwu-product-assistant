import json
import unittest

from app.catalog import extract_catalog
from app.variants import enrich_product
from app.browser import inspect_snapshot
from app.product import extract_product

ASIN = 'B000000001'
URL = 'https://www.amazon.com/dp/' + ASIN
CATALOG = {'dimensions': ['color_name', 'size_name'], 'num_total_variations': 3,
           'dimensionValuesDisplayData': {ASIN: ['Black', '42 inch'], 'B000000002': ['Black', '36 inch'], 'B000000003': ['White', '36 inch']}}


class CatalogTests(unittest.TestCase):
    def product(self, asin=ASIN):
        return {'url': 'https://www.amazon.com/dp/' + asin, 'source_type': 'page', 'facts': [], 'missing': []}

    def test_missing_color_at_current_size_is_disabled_not_redirected_to_another_size(self):
        # The page option links to White/36; clicking it must not silently change 42.
        options = [{'dimension': '颜色', 'label': 'White', 'asin': 'B000000003', 'price': '$20'}]
        p = enrich_product(self.product(), options, '$30', CATALOG)
        white = next(v for v in p['variants'] if v['label'] == 'White')
        self.assertIs(white['available'], False)
        self.assertEqual(white['url'], '')
        self.assertIsNone(white['price'])
        self.assertIn('组合', white['disabled_reason'])
        smaller = next(v for v in p['variants'] if v['label'] == '36 inch')
        self.assertTrue(smaller['url'].endswith('B000000002'))
        self.assertEqual(p['selected_sku']['label'], '颜色：Black · 尺寸：42 inch')

    def test_availability_changes_after_selecting_a_compatible_size(self):
        p = enrich_product(self.product('B000000002'), [], '$25', CATALOG)
        white = next(v for v in p['variants'] if v['label'] == 'White')
        self.assertNotEqual(white['available'], False)
        self.assertTrue(white['url'].endswith('B000000003'))
        p = enrich_product(self.product('B000000003'), [], '$20', CATALOG)
        larger = next(v for v in p['variants'] if v['label'] == '42 inch')
        self.assertIs(larger['available'], False)

    def test_declared_option_without_any_combination_is_visible_but_disabled(self):
        catalog = {**CATALOG, 'variationValues': {'color_name': ['Black', 'White', 'Blue']}}
        p = enrich_product(self.product(), [], '', catalog)
        blue = next(v for v in p['variants'] if v['label'] == 'Blue')
        self.assertIs(blue['available'], False)
        self.assertFalse(blue['url'])

    def test_partial_or_ambiguous_catalog_never_proves_a_combination_missing(self):
        option = {'dimension': '颜色', 'label': 'White', 'asin': 'B000000003'}
        for catalog in [{**CATALOG, 'num_total_variations': 4}, {**CATALOG, 'num_total_variations': None}, {**CATALOG, 'dimensions': ['color_name', 'color_name']}]:
            p = enrich_product(self.product(), [option], '', catalog)
            self.assertNotEqual(p['variants'][0]['available'], False)
            self.assertTrue(p['variants'][0]['url'])

    def test_index_map_is_resolved_in_declared_dimension_order(self):
        data = {'dimensions': ['color_name', 'size_name'], 'num_total_variations': 2,
                'variationValues': {'size_name': ['36 inch', '42 inch'], 'color_name': ['Black', 'White']},
                'dimensionToAsinMap': {'0_1': ASIN, '1_0': 'B000000003'}}
        p = enrich_product(self.product(), [], '', data)
        self.assertTrue(p['sku_catalog']['complete'])
        self.assertIs(next(v for v in p['variants'] if v['label'] == 'White')['available'], False)

    def test_json_extraction_does_not_execute_script_and_preserves_string_braces(self):
        source = 'arbitraryCall(); var data = ' + json.dumps(CATALOG) + '; dangerousCall();'
        self.assertEqual(extract_catalog([source]), CATALOG)
        self.assertEqual(extract_catalog(['"dimensionValuesDisplayData": runCode()']), {})

    def test_browser_and_static_extraction_use_the_same_combination_rules(self):
        snapshot = {'title': 'Ceiling fan', 'facts': [{'label': '功能描述', 'value': 'Adjustable fan with remote control'}], 'variation_catalog': CATALOG}
        browser, state = inspect_snapshot(snapshot, URL, URL)
        html = '<h1 id="productTitle">Ceiling fan</h1><div id="feature-bullets"><li>Adjustable fan with remote control</li></div><script>var data=' + json.dumps(CATALOG) + ';</script>'
        static = extract_product(html, URL)
        self.assertEqual(state, 'ready')
        for p in (browser, static):
            self.assertTrue(p['sku_catalog']['complete'])
            self.assertIs(next(v for v in p['variants'] if v['label'] == 'White')['available'], False)


if __name__ == '__main__':
    unittest.main()
