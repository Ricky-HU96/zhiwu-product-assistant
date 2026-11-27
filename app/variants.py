"""Observed SKU options and price range; never infer unobserved combinations."""
from decimal import Decimal, InvalidOperation
import re
from urllib.parse import urlsplit

NUMBER = r'(?:\d{1,3}(?:[,.\u00a0 ]\d{3})+(?:[,.]\d{2})?|\d+(?:\s*[,.]\s*\d{2})?)'
PRICE = re.compile(r'(?P<before>US\$|CA\$|USD|CAD|EUR|GBP|JPY|[$€£¥￥])\s*(?P<number>' + NUMBER + r')|(?P<suffix_number>' + NUMBER + r')\s*(?P<suffix>USD|CAD|EUR|GBP|JPY|€|£)', re.I)
FROM_PRICE = re.compile(r'\bfrom\b|起始价|起价|起\s*[:：]|\bab\s+|à partir|a partir', re.I)


def parse_price(value, url=''):
    value = str(value or '').replace('\xa0', ' ')
    match = PRICE.search(value)
    if not match:
        return None
    if re.match(r'\s*/\s*(?:oz|fl|ounce|count|kg|g|ml|l|液|盎司|件)', value[match.end():], re.I):
        return None
    symbol = (match['before'] or match['suffix'] or '').upper()
    currency = {'€': 'EUR', '£': 'GBP', '¥': 'JPY', '￥': 'JPY', 'US$': 'USD', 'CA$': 'CAD'}.get(symbol, symbol)
    if currency == '$':
        currency = 'CAD' if urlsplit(url).hostname in {'amazon.ca', 'www.amazon.ca'} else 'USD'
    number = re.sub(r'\s+', '', match['number'] or match['suffix_number'])
    if ',' in number and '.' in number:
        decimal_mark = ',' if number.rfind(',') > number.rfind('.') else '.'
        number = number.replace('.' if decimal_mark == ',' else ',', '').replace(decimal_mark, '.')
    elif ',' in number:
        number = number.replace(',', '.') if re.search(r',\d{2}$', number) and currency != 'JPY' else number.replace(',', '')
    elif re.fullmatch(r'\d{1,3}(?:\.\d{3})+', number):
        number = number.replace('.', '')
    try:
        amount = Decimal(number)
        if amount <= 0 or amount > 10000000:
            return None
        kind = 'from' if FROM_PRICE.search(value) else 'exact'
        return {'amount': format(amount, 'f'), 'currency': currency, 'text': value.strip()[:500],
                'kind': kind, 'display': f'{currency} {amount:,.2f}' + (' 起' if kind == 'from' else '')}
    except InvalidOperation:
        return None


def enrich_product(product, options=(), current_price=''):
    """Add SKU facts and same-currency observed range; attach traceable fact IDs."""
    facts = product['facts']
    source = product['source_type']
    variants, seen = [], set()

    def add_fact(label, value, evidence=None):
        fact = {'id': f'F{len(facts) + 1}', 'label': label, 'value': value,
                'evidence': evidence or value, 'source_type': source}
        facts.append(fact)
        return fact['id']

    quotes = []
    current = parse_price(current_price, product['url'])
    current_assigned = False
    current_asin = product['url'].rstrip('/').rsplit('/', 1)[-1]
    for option in options:
        label = re.sub(r'\s+', ' ', str(option.get('label', ''))).strip()[:180]
        dimension = str(option.get('dimension', '款式')).strip()[:50]
        if dimension == '颜色' and re.fullmatch(r'\d+(?:\.\d+)?\s*[vV](?:\s*(?:DC|USB))?', label, re.I):
            dimension = '供电规格'
        asin = str(option.get('asin', '')).upper()
        if asin and not re.fullmatch(r'[A-Z0-9]{10}', asin):
            asin = ''
        if not label or label in {'选择', 'Select', 'Choose an option'}:
            continue
        key = (dimension, asin, label)
        if key in seen or len(variants) >= 32:
            continue
        seen.add(key)
        identity = f'V{len(variants) + 1}'
        raw = str(option.get('evidence', label))[:1500]
        quote = parse_price(option.get('price', ''), product['url'])
        selected = option.get('selected') is True or bool(asin and asin == current_asin)
        if quote:
            if option.get('price_kind') == 'from' or FROM_PRICE.search(raw):
                quote['kind'] = 'from'
                quote['display'] = f"{quote['currency']} {Decimal(quote['amount']):,.2f} 起"
        if selected and current and current['kind'] == 'exact' and (not quote or quote['kind'] == 'from'):
            quote = current.copy()
            quote['context'] = '当前选择的其他规格组合'
        if selected and current and quote and quote['kind'] == 'exact' and quote['currency'] == current['currency'] and quote['amount'] == current['amount']:
            current_assigned = True
        value = f'{dimension}：{label}' + (f"；{'起价' if quote['kind'] == 'from' else '售价'}：{quote['display']}" if quote else '；价格未展示')
        fact_id = add_fact('SKU 选项', value, raw)
        if quote:
            quote['evidence_ids'] = [fact_id]
        host = urlsplit(product['url']).hostname
        variant_url = f'https://{host}/dp/{asin}' if asin and host else ''
        variants.append({'id': identity, 'label': label, 'dimension': dimension, 'asin': asin, 'url': variant_url,
                         'selected': selected, 'price': quote,
                         'available': option.get('available') if isinstance(option.get('available'), bool) else None, 'evidence_ids': [fact_id]})
        if quote and option.get('available') is not False:
            quotes.append(quote | {'variant_id': identity, 'source': f'{dimension}：{label}'})

    product['variants'] = variants
    selected_options = [v for v in variants if v['selected']]
    product['selected_sku'] = {'asin': current_asin if re.fullmatch(r'[A-Z0-9]{10}', current_asin) else '',
                               'label': ' · '.join(f"{v['dimension']}：{v['label']}" for v in selected_options) or '页面当前配置（未取得完整规格标签）',
                               'variant_ids': [v['id'] for v in selected_options]}
    product['current_price'] = current
    if current and not current_assigned:
        quotes.append(current | {'variant_id': '', 'source': '当前商品报价'})
    # A single product cannot be shown as a synthetic range across currencies.
    by_currency = {}
    for quote in quotes:
        by_currency.setdefault(quote['currency'], []).append(quote)
    product['price_range'] = None
    if by_currency:
        preferred = current['currency'] if current else max(by_currency, key=lambda currency: len(by_currency[currency]))
        all_quotes = by_currency[preferred]
        group = [q for q in all_quotes if q['kind'] == 'exact']
        starting = [q for q in all_quotes if q['kind'] == 'from']
        amounts = [Decimal(q['amount']) for q in (group or starting)]
        low, high = min(amounts), max(amounts) if group else None
        priced_variants = len({q['variant_id'] for q in group if q['variant_id']})
        display = f'{preferred} {low:,.2f} 起' if not group else f'{preferred} {low:,.2f}' if low == high else f'{preferred} {low:,.2f}–{high:,.2f}'
        evidence = '；'.join(f"{q['source']}：{q['text']}" for q in (group or starting))[:3000]
        price_id = add_fact('已确认售价区间' if group else '仅获取起价', display, evidence)
        scope = (f'基于 {len(group)} 个确定报价。另有 {len(starting)} 个选项仅显示起价，未纳入区间；尚未加载选项不保证覆盖。' if group
                 else f'仅获取 {len(starting)} 个选项起价，不能据此确认完整最低价和最高售价。')
        product['price_range'] = {'min': str(low), 'max': str(high) if high is not None else None, 'currency': preferred,
                                  'kind': 'exact' if group else 'from', 'from_count': len(starting),
                                  'display': display, 'quote_count': len(group), 'priced_variants': priced_variants,
                                  'variant_count': len(variants), 'evidence_ids': [price_id],
                                  'scope': scope}
        product['missing'] = [label for label in product.get('missing', []) if label != '页面价格']
    product['variant_notice'] = ('以下为页面实际提供的选项，不将独立选项排列组合成未经验证的 SKU。' if variants else '本次未读取到 SKU 选项；不会根据标题或常识补造颜色、尺寸或套装。')
    return product
