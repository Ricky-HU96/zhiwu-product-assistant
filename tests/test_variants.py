import unittest
from app.variants import enrich_product, parse_price
from app.product import manual_product, DEMO_TEXT
from app.browser import inspect_snapshot


class VariantTests(unittest.TestCase):
    def product(self):
        return {'url': 'https://www.amazon.com/dp/B0CXT9RSGQ', 'source_type': 'browser', 'facts': [], 'missing': ['页面价格']}

    def test_price_formats_and_no_symbol_guess(self):
        self.assertEqual(parse_price('$1,249.99')['amount'], '1249.99')
        self.assertEqual(parse_price('EUR 1.299,99')['amount'], '1299.99')
        self.assertEqual(parse_price('EUR 1.299')['amount'], '1299')
        self.assertEqual(parse_price('￥1,299')['amount'], '1299')
        self.assertEqual(parse_price('CA$59.99')['currency'], 'CAD')
        self.assertIsNone(parse_price('2 pack 120 kg'))

    def test_range_from_option_prices_excludes_unavailable(self):
        product = enrich_product(self.product(), [
            {'label': '黑色', 'dimension': '颜色', 'price': '$79.99'},
            {'label': '粉色', 'dimension': '颜色', 'price': '$89.99'},
            {'label': '售罄', 'dimension': '颜色', 'price': '$9.99', 'available': False}])
        self.assertEqual(product['price_range']['min'], '79.99')
        self.assertEqual(product['price_range']['max'], '89.99')
        self.assertEqual(product['price_range']['priced_variants'], 2)
        self.assertIn('尚未加载', product['price_range']['scope'])
        self.assertNotIn('页面价格', product['missing'])

    def test_mixed_currency_is_not_combined(self):
        product = enrich_product(self.product(), [{'label': '黑色', 'price': 'EUR 10'}, {'label': '粉色', 'price': 'USD 100'}], '$90')
        self.assertEqual(product['price_range']['currency'], 'USD')
        self.assertEqual(product['price_range']['min'], '90')
        self.assertEqual(product['price_range']['max'], '100')

    def test_single_quote_and_missing_price_are_not_invented(self):
        product = enrich_product(self.product(), [{'label': '粉色', 'price': '$59.99'}, {'label': '黑色'}])
        self.assertEqual(product['price_range']['min'], product['price_range']['max'])
        self.assertEqual(product['price_range']['quote_count'], 1)
        self.assertIsNone(enrich_product(self.product(), [{'label': '黑色'}])['price_range'])

    def test_snapshot_sku_options_get_traceable_facts(self):
        snapshot = {'title': '露营椅', 'facts': [{'label': '功能描述', 'value': '带遮阳篷，可折叠'}],
                    'variants': [{'label': '粉色单人装', 'dimension': '款式', 'price': '$59.99'}]}
        product, _ = inspect_snapshot(snapshot, 'https://www.amazon.com/dp/B0CXT9RSGQ', 'https://www.amazon.com/dp/B0CXT9RSGQ')
        identity = product['variants'][0]['evidence_ids'][0]
        self.assertTrue(any(f['id'] == identity and '粉色' in f['value'] for f in product['facts']))

    def test_manual_demo_has_realistic_explicit_options(self):
        product = manual_product(DEMO_TEXT, demo=True)
        self.assertEqual(len(product['variants']), 3)
        self.assertEqual(product['price_range']['display'], 'USD 49.99–89.99')

    def test_starting_prices_are_attached_but_not_exact_range_endpoints(self):
        product = enrich_product(self.product(), [
            {'label': '黑色', 'price': 'EUR 7.71'},
            {'label': '粉色', 'price': 'EUR 9.88'},
            {'label': '绿色', 'price': 'EUR 5.41', 'evidence': '2个选项，起始价：EUR 5.41'},
            {'label': '棕色', 'price': 'From EUR 17.74'},
            {'label': '未知'}])
        self.assertEqual(product['price_range']['display'], 'EUR 7.71–9.88')
        self.assertEqual(product['price_range']['from_count'], 2)
        self.assertEqual(product['variants'][2]['price']['display'], 'EUR 5.41 起')
        self.assertIsNone(product['variants'][4]['price'])
        self.assertIn('起价', product['facts'][2]['value'])

    def test_only_starting_prices_have_no_upper_bound(self):
        product = enrich_product(self.product(), [{'label': 'a', 'price': 'From $10'}, {'label': 'b', 'price': '$20', 'price_kind': 'from'}])
        self.assertEqual(product['price_range']['display'], 'USD 10.00 起')
        self.assertIsNone(product['price_range']['max'])
        self.assertEqual(product['price_range']['kind'], 'from')

    def test_price_number_does_not_consume_following_option_count(self):
        self.assertEqual(parse_price('EUR 7.71 2 个选项')['amount'], '7.71')
        self.assertEqual(parse_price('EUR7.71每 液体盎司 件')['amount'], '7.71')
        self.assertIsNone(parse_price('EUR 32.14 / 液体盎司'))

    def test_current_price_fallback_applies_only_to_selected_option(self):
        product = enrich_product(self.product(), [{'label': '黑色', 'selected': True}, {'label': '粉色'}], 'EUR 7.71')
        self.assertEqual(product['variants'][0]['price']['amount'], '7.71')
        self.assertIn('当前', product['variants'][0]['price']['context'])
        self.assertIsNone(product['variants'][1]['price'])
        self.assertEqual(product['price_range']['quote_count'], 1)


if __name__ == '__main__':
    unittest.main()
