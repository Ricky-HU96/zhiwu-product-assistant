"""Read declared variation combinations as JSON data; never execute page scripts."""
import json
import re

KEYS = ('dimensions', 'variationValues', 'dimensionValuesDisplayData', 'dimensionToAsinMap', 'num_total_variations')
DIMENSIONS = {'color_name': '颜色', 'size_name': '尺寸', 'style_name': '款式', 'pattern_name': '图案',
              'item_package_quantity': '套装数量', 'number_of_items': '套装数量'}


def extract_catalog(sources):
    for source in sources:
        if not isinstance(source, str) or len(source) > 2_000_000 or 'dimensionValuesDisplayData' not in source and 'dimensionToAsinMap' not in source:
            continue
        data = {}
        for key in KEYS:
            match = re.search(r'["\']' + key + r'["\']\s*:\s*', source)
            if match:
                try:
                    data[key] = json.JSONDecoder().raw_decode(source[match.end():])[0]
                except (ValueError, RecursionError):
                    pass
        if data:
            return data
    return {}


def resolve_options(options, catalog, current_asin):
    """Resolve a change in one dimension while retaining every other selection.

    Missing combinations are only disabled when the declared total proves that
    the table is complete. An incomplete table never proves absence.
    """
    if not isinstance(catalog, dict):
        return options, None
    dimensions = catalog.get('dimensions')
    if not isinstance(dimensions, list) or not 1 <= len(dimensions) <= 6 or any(not isinstance(d, str) for d in dimensions) or len(set(dimensions)) != len(dimensions):
        return options, None
    rows = {}
    display = catalog.get('dimensionValuesDisplayData', {})
    if isinstance(display, dict):
        for asin, values in list(display.items())[:2000]:
            if re.fullmatch(r'[A-Z0-9]{10}', str(asin)) and isinstance(values, list) and len(values) == len(dimensions) and all(isinstance(v, str) and 0 < len(v) <= 180 for v in values):
                rows[asin] = dict(zip(dimensions, values))
    # Some page versions provide only index combinations and value dictionaries.
    if not rows:
        values = catalog.get('variationValues', {})
        mapping = catalog.get('dimensionToAsinMap', {})
        if isinstance(values, dict) and isinstance(mapping, dict):
            for key, asin in list(mapping.items())[:2000]:
                if not isinstance(key, str) or not re.fullmatch(r'[A-Z0-9]{10}', str(asin)):
                    continue
                indexes = key.split('_')
                try:
                    if len(indexes) == len(dimensions) and all(re.fullmatch(r'\d{1,4}', i) for i in indexes):
                        row = {d: values[d][int(i)] for d, i in zip(dimensions, indexes)}
                        if all(isinstance(v, str) and 0 < len(v) <= 180 for v in row.values()):
                            rows[asin] = row
                except (KeyError, IndexError, TypeError):
                    continue
    total = catalog.get('num_total_variations')
    complete = isinstance(total, int) and not isinstance(total, bool) and 0 < total == len(rows) <= 2000
    metadata = {'complete': complete, 'combination_count': len(rows), 'dimensions': [DIMENSIONS.get(d, d) for d in dimensions]}
    if not complete or current_asin not in rows:
        return options, metadata
    current = rows[current_asin]
    resolved = []
    for dimension in dimensions:
        translated = DIMENSIONS.get(dimension, dimension)
        declared = catalog.get('variationValues', {})
        declared = declared.get(dimension, []) if isinstance(declared, dict) else []
        declared = declared if isinstance(declared, list) else []
        labels = list(dict.fromkeys([v for v in declared[:160] if isinstance(v, str) and 0 < len(v) <= 180] + [row[dimension] for row in rows.values()]))
        for label in labels:
            target = next((asin for asin, row in rows.items() if row[dimension] == label and all(row[d] == current[d] for d in dimensions if d != dimension)), '')
            previous = next((o for o in options if o.get('dimension') == translated and o.get('asin') == target and target), {})
            selected = label == current[dimension]
            resolved.append({**previous, 'dimension': translated, 'label': label, 'asin': target,
                             'selected': selected, 'available': False if not target else previous.get('available'),
                             'disabled_reason': '当前其他规格下没有这个组合' if not target else '',
                             'evidence': f'页面实际组合表：{translated}：{label}；' + (f'ASIN {target}；保持其他规格不变' if target else '当前其他规格下无匹配组合')})
    resolved.extend(o for o in options if o.get('dimension') not in metadata['dimensions'])
    return resolved, metadata | {'selected_values': {DIMENSIONS.get(d, d): value for d, value in current.items()}}
