"""Amazon page extraction using the Python standard library. No model facts."""
import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from app.variants import enrich_product
from app.images import attach_images, extract_images


class ProductError(ValueError):
    pass


HOSTS = {"amazon.com", "www.amazon.com", "amazon.co.uk", "www.amazon.co.uk",
         "amazon.de", "www.amazon.de", "amazon.co.jp", "www.amazon.co.jp",
         "amazon.ca", "www.amazon.ca", "amazon.fr", "www.amazon.fr",
         "amazon.it", "www.amazon.it", "amazon.es", "www.amazon.es"}


def canonical_url(value):
    value = re.sub(r"[\s\x00-\x1f\x7f\u200b-\u200f\u202a-\u202e\ufeff]", "", value)
    try:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme != "https" or parsed.hostname not in HOSTS or parsed.username or parsed.password or parsed.port not in (None, 443):
            raise ValueError()
        match = re.search(r"/(?:dp|gp/product|gp/aw/d)/([A-Za-z0-9]{10})(?:[/ ?]|$)", parsed.path)
        if not match:
            raise ValueError()
        host = parsed.hostname if parsed.hostname.startswith("www.") else "www." + parsed.hostname
        return f"https://{host}/dp/{match[1].upper()}"
    except (ValueError, TypeError):
        raise ProductError("请输入完整的 HTTPS Amazon 商品链接，链接中应包含 /dp/ 和 10 位商品编号。") from None


class AmazonRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        destination = urllib.parse.urlsplit(newurl)
        if destination.scheme != "https" or destination.hostname not in HOSTS or destination.port not in (None, 443) or destination.username or destination.password:
            raise ProductError("商品页面跳转到非支持地址，请粘贴商品信息继续。")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Node:
    def __init__(self, tag, attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def text(self):
        if self.tag in {"script", "style", "noscript"}:
            return ""
        return re.sub(r"\s+", " ", " ".join(c.text() if isinstance(c, Node) else c for c in self.children)).strip()

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()

    def sale_walk(self):
        classes = self.attrs.get('class', '').split()
        if self.tag in {'del', 's', 'script', 'style', 'noscript'} or any(c in classes for c in ('a-text-price', 'a-price-unit', 'a-price-per-unit')) or 'unitPrice' in self.attrs.get('id', ''):
            return
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.sale_walk()

    def sale_text(self):
        if next(self.sale_walk(), None) is None:
            return ''
        return re.sub(r'\s+', ' ', ' '.join(c.sale_text() if isinstance(c, Node) else c for c in self.children)).strip()


class PageParser(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("root")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        if len(self.stack) > 100:
            return
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if self.stack[-1].tag == tag:
            self.stack.pop()

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def now():
    return datetime.now(timezone.utc).isoformat()


def extract_product(document, url):
    parser = PageParser()
    parser.feed(document)
    nodes = list(parser.root.walk())
    ids = {n.attrs["id"]: n for n in nodes if n.attrs.get("id")}
    title = ids.get("productTitle")
    title = title.text() if title else ""
    if not title and re.search(r"validateCaptcha|Robot Check|automated access|Enter the characters you see", document, re.I):
        raise ProductError("Amazon 返回了验证码页面，未获取到商品信息。请展开补充信息，粘贴商品标题和功能描述继续。")
    facts = []

    def add(label, value, evidence=None):
        value = html.unescape(str(value or "")).strip()
        value = re.sub(r"\s+", " ", value)[:1500]
        if value and len(facts) < 28 and not any(f["value"] == value for f in facts):
            facts.append({"id": f"F{len(facts) + 1}", "label": label, "value": value,
                          "evidence": (evidence or value)[:1500], "source_type": "page"})

    # JSON-LD is preferred over guessing from unrelated visible page text.
    products = []
    def visit(value):
        if isinstance(value, dict):
            kind = value.get("@type", [])
            if kind == "Product" or isinstance(kind, list) and "Product" in kind:
                products.append(value)
            for key in ("@graph", "mainEntity"):
                if key in value:
                    visit(value[key])
        elif isinstance(value, list):
            for item in value:
                visit(item)
    for node in nodes:
        if node.tag == "script" and node.attrs.get("type") == "application/ld+json":
            try:
                visit(json.loads("".join(c for c in node.children if isinstance(c, str))))
            except (ValueError, TypeError):
                pass
    product = products[0] if products else {}
    title = title or product.get("name", "")
    if not isinstance(title, str) or not title.strip():
        raise ProductError("页面中未找到可靠的商品标题，无法继续自动分析。请粘贴商品信息。")
    add("产品名称", title)
    brand = product.get("brand", "")
    brand = brand.get("name", "") if isinstance(brand, dict) else brand
    brand = brand if isinstance(brand, str) else ""
    if not brand and "bylineInfo" in ids:
        brand = ids["bylineInfo"].text()
    add("品牌", brand)
    price = ""
    for identity in ("priceblock_ourprice", "priceblock_dealprice", "price_inside_buybox", "corePrice_feature_div", "corePriceDisplay_desktop_feature_div"):
        if identity in ids:
            parent = ids[identity]
            offscreen = next((n for n in parent.sale_walk() if "a-offscreen" in n.attrs.get("class", "").split()), None)
            price = offscreen.text() if offscreen else parent.text() if identity.startswith("priceblock") or identity == "price_inside_buybox" else ""
            if price:
                break
    offers = product.get("offers", {})
    offers = offers[0] if isinstance(offers, list) and offers else offers
    if not price and isinstance(offers, dict) and offers.get("price") is not None:
        price = f"{offers.get('priceCurrency', '')} {offers['price']}".strip()
    add("页面价格", price)
    if "wayfinding-breadcrumbs_feature_div" in ids:
        add("页面品类", ids["wayfinding-breadcrumbs_feature_div"].text())
    else:
        category = product.get("category")
        add("页面品类", category if isinstance(category, str) else "")
    if "feature-bullets" in ids:
        for node in ids["feature-bullets"].walk():
            if node.tag == "li":
                text = node.text()
                if len(text) > 15 and "Make sure this fits" not in text:
                    add("功能描述", text)
    for identity, label in [('productDescription', '商品说明'), ('aplus_feature_div', '图文详情说明（需核对型号范围）')]:
        if identity in ids:
            description = ids[identity].text()[:4800]
            meaningful = re.sub(r'Previous page|Next page|上一页|下一页|商品描述|产品描述|-?\d+:\d+', '', description, flags=re.I).strip()
            if len(meaningful) < 20:
                continue
            for offset in range(0, len(description), 1200):
                if len(description[offset:offset + 1200]) >= 20:
                    add(label, description[offset:offset + 1200])
    for identity in ("productDetails_techSpec_section_1", "productDetails_detailBullets_sections1", "productOverview_feature_div"):
        if identity in ids:
            for row in ids[identity].walk():
                if row.tag == "tr":
                    cells = [n.text() for n in row.children if isinstance(n, Node) and n.tag in {"th", "td"}]
                    if len(cells) >= 2 and cells[0] and cells[1]:
                        add(cells[0][:80], " | ".join(cells[1:]), " | ".join(cells))
    if len(facts) < 3 and isinstance(product.get("description"), str):
        description = re.sub(r"<[^>]+>", " ", product["description"])
        add("功能描述", description)
    if not any(f["label"] == "功能描述" for f in facts) and len(facts) < 3:
        raise ProductError("仅获取到标题，缺少足够的功能或规格信息。请粘贴商品介绍后继续。")
    product_data = {"title": title, "url": url, "fetched_at": now(), "source_type": "page", "facts": facts,
                    "missing": [label for label in ("页面价格", "页面品类", "功能描述") if not any(f["label"] == label for f in facts)]}
    options = []
    dimensions = {'color_name': '颜色', 'size_name': '尺寸', 'style_name': '款式', 'item_package_quantity': '套装数量'}
    for identity, group in ids.items():
        if not identity.startswith('variation_'):
            continue
        dimension = dimensions.get(identity[10:], identity[10:])
        for node in group.walk():
            if node.tag not in {'li', 'option'}:
                continue
            image = next((n for n in node.walk() if n.tag == 'img'), None)
            asin = node.attrs.get('data-asin') or node.attrs.get('data-defaultasin') or ''
            if not image and not asin and node.tag != 'option':
                continue
            raw = node.text()
            price_node = next((n for n in node.sale_walk() if any(c in n.attrs.get('class', '').split() for c in ('a-offscreen', 'a-color-price', 'twister-plus-buying-options-price-data'))), None)
            label = image.attrs.get('alt', '') if image else node.attrs.get('title', raw)
            options.append({'dimension': dimension, 'label': label, 'asin': asin, 'price': price_node.text() if price_node else node.sale_text(),
                            'evidence': raw or label, 'selected': 'swatchSelect' in node.attrs.get('class', '') or 'selected' in node.attrs,
                            'available': False if 'swatchUnavailable' in node.attrs.get('class', '') else None})
    return attach_images(enrich_product(product_data, options, price), extract_images(nodes, ids, product))


def fetch_product(value):
    url = canonical_url(value)
    request = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9", "Accept": "text/html,application/xhtml+xml"})
    try:
        with urllib.request.build_opener(AmazonRedirect()).open(request, timeout=22) as response:
            document = response.read(2_500_000).decode(response.headers.get_content_charset() or "utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise ProductError("该商品链接返回 404，商品可能已下架或链接有误。请检查链接，或粘贴商品信息继续。") from None
        raise ProductError(f"Amazon 暂时无法读取（HTTP {exc.code}）。请稍后重试，或粘贴商品信息。") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ProductError("连接 Amazon 失败或超时，请检查网络，或粘贴商品信息继续。") from None
    return extract_product(document, url)


DEMO_TEXT = """产品名称：晴野带遮阳篷折叠露营椅（虚构演示商品）
品类：户外折叠椅
功能：椅子自带可展开的遮阳篷。
功能：椅身可折叠，附收纳袋。
规格：重量 5.2 kg，最大承重 120 kg。
规格：座面宽度 52 cm。
价格：未提供。
限制：未提供防晒等级、防雨性能及折叠后尺寸。"""
DEMO_TEXT += "\nSKU：橄榄绿单人装；价格：USD 49.99\nSKU：粉色单人装；价格：USD 59.99\nSKU：橄榄绿双人装；价格：USD 89.99"


def manual_product(text, url="", demo=False):
    if not isinstance(text, str) or len(text.strip()) < 40 or len(text) > 16000:
        raise ProductError("请提供 40～16000 字的商品信息，建议包含名称、功能和规格。")
    url = canonical_url(url) if url else ""
    lines = [line.strip(" \t•·-") for line in text.splitlines() if line.strip()]
    # Keep all submitted material, including when the user pastes a single long paragraph.
    chunks = [line[start:start + 1300] for line in lines for start in range(0, len(line), 1300)]
    facts = [{"id": f"F{i + 1}", "label": "演示资料" if demo else "用户提供", "value": line,
              "evidence": line, "source_type": "demo" if demo else "manual"} for i, line in enumerate(chunks)]
    title = re.sub(r"^(产品名称|商品名称|标题)\s*[:：]\s*", "", lines[0])[:160]
    product_data = {"title": title, "url": url, "fetched_at": now(), "source_type": "demo" if demo else "manual",
                    "facts": facts, "missing": [], "notice": "虚构演示资料，不对应真实商品。" if demo else "以下信息由用户提供，尚未通过 Amazon 页面核验。"}
    options = []
    for line in lines:
        match = re.match(r'^(SKU|颜色|尺寸|款式|套装)\s*[:：]\s*(.+)', line, re.I)
        if match:
            label = re.split(r'[；;]\s*(?:价格|price)\s*[:：]', match[2], flags=re.I)[0]
            options.append({'dimension': '款式/套装' if match[1].upper() == 'SKU' else match[1],
                            'label': label, 'price': line, 'evidence': line})
    return attach_images(enrich_product(product_data, options))
