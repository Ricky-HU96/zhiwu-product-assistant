"""Visible browser reader. Only a human interacts with verification challenges.

All Playwright objects belong to one owner thread, including across HTTP jobs.
This module never reads cookies or exports browser storage.
"""
from concurrent.futures import Future, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
import importlib.util
import os
from pathlib import Path
from queue import Queue
import re
import threading
import time
from urllib.parse import urlsplit

from app.product import HOSTS, ProductError, canonical_url, extract_product, now
from app.variants import enrich_product
from app.images import attach_images

ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / ".browser-profile"
WAIT_SECONDS = 300
INCOMPLETE_SECONDS = 30

# Read only product regions, not account headers, cart contents or browser storage.
DOM_SNAPSHOT = r"""() => {
  const text = n => {
    if (!n) return '';
    const copy = n.cloneNode(true);
    copy.querySelectorAll('style, script, noscript').forEach(n => n.remove());
    return (copy.textContent || '').replace(/\s+/g, ' ').trim();
  };
  const first = selector => text(document.querySelector(selector));
  const title = first('#productTitle') || first('#title_feature_div h1') || first('h1#title');
  const facts = [];
  const add = (label, value) => { if (value && !facts.some(f => f.value === value) && facts.length < 28) facts.push({label, value: value.slice(0, 1500)}); };
  add('品牌', first('#bylineInfo'));
  const currentPrice = first('#corePrice_feature_div .a-price:not(.a-text-price) .a-offscreen') || first('#corePriceDisplay_desktop_feature_div .a-price:not(.a-text-price) .a-offscreen') || first('#priceblock_ourprice') || first('#priceblock_dealprice');
  add('页面品类', first('#wayfinding-breadcrumbs_feature_div'));
  document.querySelectorAll('#feature-bullets li, #productFactsDesktopExpander li').forEach(n => {
    const value = text(n); if (value.length >= 10 && !/Make sure this fits/.test(value)) add('功能描述', value);
  });
  [['#productDescription','商品说明'], ['#aplus_feature_div','图文详情说明（需核对型号范围）']].forEach(([selector,label]) => {
    const value = first(selector).slice(0,4800);
    if (value.replace(/Previous page|Next page|上一页|下一页|商品描述|产品描述|-?\d+:\d+/gi,'').trim().length<20) return;
    for (let offset=0; offset<value.length; offset+=1200) {
      const chunk = value.slice(offset,offset+1200); if (chunk.length>=20) add(label,chunk);
    }
  });
  document.querySelectorAll('#productDetails_techSpec_section_1 tr, #productDetails_detailBullets_sections1 tr, #productOverview_feature_div tr').forEach(row => {
    const cells = [...row.querySelectorAll('th, td')].map(text).filter(Boolean);
    if (cells.length >= 2) add(cells[0].slice(0, 80), cells.slice(1).join(' | '));
  });
  if (!facts.some(f => f.label === '功能描述')) {
    const description = first('#productDescription'); if (description.length >= 20) add('功能描述', description);
  }
  const variants = [], seen = new Set();
  const money = /(?:US\$|CA\$|USD|CAD|EUR|GBP|JPY|[$€£¥￥])\s*(?:\d{1,3}(?:[,.\u00a0 ]\d{3})+(?:[,.]\d{2})?|\d+(?:\s*[,.]\s*\d{2})?)/g;
  const starting = /\bfrom\b|起始价|起价|起\s*[:：]|\bab\s+|à partir|a partir/i;
  const unit = /^\s*\/\s*(?:oz|fl|ounce|count|kg|g|ml|l|液|盎司|件)/i;
  const firstPrice = value => [...value.matchAll(money)].find(m => !unit.test(value.slice(m.index + m[0].length)))?.[0] || '';
  const dimensions = {color_name:'颜色',size_name:'尺寸',style_name:'款式',pattern_name:'图案',item_package_quantity:'套装数量',number_of_items:'套装数量'};
  document.querySelectorAll('[id^="variation_"], [id^="inline-twister-row-"]').forEach(root => {
    const rawDimension = root.id.replace(/^variation_|^inline-twister-row-/, '');
    const dimension = dimensions[rawDimension] || first(`#${CSS.escape(root.id)} .a-form-label`).replace(/[:：].*$/, '') || rawDimension;
    root.querySelectorAll('li, option').forEach(node => {
      const image = node.querySelector('img');
      const raw = text(node);
      const explicit = image?.alt || node.getAttribute('data-value') || node.getAttribute('title') || raw;
      let label = explicit.replace(/^(?:Click to select|点击选择|选择)\s*[:：]?\s*/i, '').replace(money, '').replace(/\s+/g, ' ').trim();
      if (!label || label.length > 180 || seen.has(node)) return;
      const asin = node.getAttribute('data-asin') || node.getAttribute('data-defaultasin') || (node.getAttribute('data-dp-url') || '').match(/\/dp\/([A-Z0-9]{10})/)?.[1] || '';
      if (!asin && !image && node.tagName !== 'OPTION' && !node.querySelector('.a-button-text')) return;
      seen.add(node);
      const clean = node.cloneNode(true);
      clean.querySelectorAll('.a-text-price, del, s, [id*="unitPrice"], .a-price-unit, .a-price-per-unit').forEach(n => n.remove());
      const priced = [...clean.querySelectorAll('.a-price .a-offscreen, .a-color-price, .twister-plus-buying-options-price-data')]
        .map(text).map(firstPrice).find(Boolean);
      const cleanText = text(clean);
      const price = priced || firstPrice(cleanText);
      variants.push({dimension, label, asin, price, price_kind: starting.test(cleanText) ? 'from' : 'exact', evidence: raw || image?.alt || label,
        selected: node.matches('.swatchSelect, .a-button-selected, [aria-checked="true"], [selected]') || !!node.querySelector('.a-button-selected'),
        available: node.matches('.swatchUnavailable, [disabled], [aria-disabled="true"]') ? false : null});
    });
  });
  const images = [];
  document.querySelectorAll('#landingImage, #imgTagWrapperId img, #altImages img, #imageBlock img, #main-image-container img').forEach(node => {
    const tile = node.closest('li');
    if (/video|spin|360/i.test(tile?.className || '') || /video|360|视频/i.test(node.alt || '')) return;
    const caption = node.alt || '商品页面图片';
    [node.getAttribute('data-old-hires'), node.getAttribute('data-src'), node.currentSrc, node.src].filter(Boolean).forEach(url => images.push({url, caption}));
    try { Object.keys(JSON.parse(node.getAttribute('data-a-dynamic-image') || '{}')).forEach(url => images.push({url, caption})); } catch {}
  });
  document.querySelectorAll('script').forEach(node => {
    const source = node.textContent || '';
    if (/colorImages|ImageBlockATF/.test(source)) {
      for (const match of source.matchAll(/"(?:hiRes|large|mainUrl)"\s*:\s*"([^"\n]+)"/g)) images.push({url:match[1].replace(/\\\//g, '/'), caption:'商品图库图片（可能包含其他款式）'});
    }
  });
  const body = document.title + '\n' + (document.body?.innerText || '');
  return {title, facts, variants, images, current_price: currentPrice,
    verification: !title && /Robot Check|Enter the characters you see|verify you are a human|Click the button below to continue shopping|请完成验证|输入您看到的字符/i.test(body),
    not_found: !title && /Page Not Found|Sorry! We couldn.t find that page|Looking for something\?/i.test(body),
    loading: document.readyState !== 'complete'};
}"""


class BrowserError(ProductError):
    pass


class BrowserCancelled(BrowserError):
    pass


@dataclass
class BrowserControl:
    cancel: threading.Event = field(default_factory=threading.Event)
    check: threading.Event = field(default_factory=threading.Event)
    focus: threading.Event = field(default_factory=threading.Event)


def browser_health():
    installed = importlib.util.find_spec("playwright") is not None
    return {"installed": installed, "channel": browser_channel(),
            "profile": "专用浏览器会话", "verification_timeout_seconds": WAIT_SECONDS}


def browser_channel():
    chosen = os.environ.get("PRODUCT_BROWSER", "").strip()
    if chosen in {"msedge", "chrome", "chromium"}:
        return chosen
    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles(x86)", ""), os.environ.get("ProgramFiles", ""), os.environ.get("LOCALAPPDATA", "")):
            if base and (Path(base) / "Microsoft/Edge/Application/msedge.exe").is_file():
                return "msedge"
    return "chromium"


def inspect_page(document, current_url, expected_url):
    """Return a product or a recoverable state, never data from another product."""
    expected_url = canonical_url(expected_url)
    parsed = urlsplit(current_url)
    if parsed.scheme != "https" or parsed.hostname not in HOSTS or parsed.port not in (None, 443) or parsed.username or parsed.password:
        return None, "wrong_page"
    # A challenge may live at /errors/validateCaptcha rather than the ASIN URL.
    has_title = bool(re.search(r'id=[\"\']productTitle[\"\']', document))
    if not has_title and re.search(r"validateCaptcha|Robot Check|automated access|Enter the characters you see|verify you are a human", document, re.I):
        return None, "verification"
    try:
        if canonical_url(current_url) != expected_url:
            return None, "wrong_page"
    except ProductError:
        return None, "wrong_page"
    try:
        product = extract_product(document, expected_url)
    except ProductError:
        if not has_title and re.search(r"Page Not Found|Sorry! We couldn.t find that page|Looking for something\?", document, re.I):
            return None, "not_found"
        return None, "incomplete"
    product["source_type"] = "browser"
    product["notice"] = "资料读取自专用浏览器中当前打开的商品页面。"
    for fact in product["facts"]:
        fact["source_type"] = "browser"
    return product, "ready"


def inspect_snapshot(snapshot, current_url, expected_url):
    """Validate a small rendered DOM snapshot, preserving field provenance."""
    expected_url = canonical_url(expected_url)
    parsed = urlsplit(current_url)
    if parsed.scheme != "https" or parsed.hostname not in HOSTS or parsed.port not in (None, 443) or parsed.username or parsed.password:
        return None, "wrong_page"
    if snapshot.get("verification"):
        return None, "verification"
    try:
        if canonical_url(current_url) != expected_url:
            return None, "wrong_page"
    except ProductError:
        return None, "wrong_page"
    if snapshot.get("not_found"):
        return None, "not_found"
    title = str(snapshot.get("title", "")).strip()
    if not title:
        return None, "incomplete"
    facts = [{"id": "F1", "label": "产品名称", "value": title[:1500], "evidence": title[:1500], "source_type": "browser"}]
    has_detail = False
    for field in snapshot.get("facts", []):
        label, value = str(field.get("label", "")).strip()[:80], str(field.get("value", "")).strip()[:1500]
        if not label or not value or any(f["value"] == value for f in facts):
            continue
        if label not in {"品牌", "页面价格", "页面品类", "产品名称"}:
            has_detail = True
        facts.append({"id": f"F{len(facts) + 1}", "label": label, "value": value, "evidence": value, "source_type": "browser"})
        if len(facts) >= 28:
            break
    if not has_detail:
        return None, "incomplete"
    product = {"title": title, "url": expected_url, "fetched_at": now(), "source_type": "browser", "facts": facts,
            "missing": [label for label in ("页面价格", "页面品类", "功能描述") if not any(f["label"] == label for f in facts)],
            "notice": "资料读取自专用浏览器中渲染后的商品区域。"}
    return attach_images(enrich_product(product, snapshot.get('variants', []), snapshot.get('current_price', '')), snapshot.get('images', [])), "ready"


def wait_for_product(page, url, control, progress, timeout=WAIT_SECONDS, incomplete_timeout=INCOMPLETE_SECONDS):
    """Read the page after user actions. Never click, type, or solve a challenge."""
    deadline = time.monotonic() + timeout
    previous_state = None
    incomplete_since = None
    errors_since = None
    while time.monotonic() < deadline:
        if control.cancel.is_set():
            raise BrowserCancelled("已结束浏览器读取，可以粘贴商品信息继续。")
        if page.is_closed():
            raise BrowserError("专用商品窗口已关闭。请重新分析以打开窗口，或粘贴商品信息。")
        if control.focus.is_set():
            control.focus.clear()
            page.bring_to_front()
        try:
            if hasattr(page, "evaluate"):
                snapshot = page.evaluate(DOM_SNAPSHOT)
                product, state = inspect_snapshot(snapshot, page.url, url)
            else:
                document = page.content()
                if len(document) > 8_000_000:
                    raise BrowserError("页面内容过大，无法可靠提取。请粘贴商品标题和功能描述。")
                product, state = inspect_page(document, page.url, url)
            errors_since = None
        except BrowserError:
            raise
        except Exception:
            # The human may be submitting verification or navigating at this instant.
            if page.is_closed():
                raise BrowserError("专用商品窗口已关闭，请重新分析或粘贴商品信息。") from None
            errors_since = errors_since or time.monotonic()
            if time.monotonic() - errors_since >= 15:
                raise BrowserError("浏览器连续 15 秒无法读取页面，已停止等待。请重新打开商品页或粘贴资料。") from None
            control.check.wait(min(1.5, max(0, deadline - time.monotonic())))
            control.check.clear()
            continue
        if product:
            if control.cancel.is_set():
                raise BrowserCancelled("已结束浏览器读取，可以粘贴商品信息继续。")
            return product
        if state == "not_found":
            raise BrowserError("浏览器显示商品页面不存在，请检查链接，或粘贴商品信息。")
        if state == "verification":
            incomplete_since = None
        else:
            if incomplete_since is None:
                incomplete_since = time.monotonic()
            if time.monotonic() - incomplete_since >= incomplete_timeout:
                reason = "当前窗口不是本次链接对应的商品页" if state == "wrong_page" else "未能提取商品名称、功能或规格"
                raise BrowserError(f"{reason}，已停止等待。请检查商品页面，或粘贴商品信息继续。")
        checked = control.check.is_set()
        control.check.clear()
        if state != previous_state or checked:
            if state == "verification":
                progress("waiting_verification", "请在专用浏览器窗口中完成验证；商品页面正常显示后会自动继续。")
            elif state == "wrong_page":
                progress("waiting_page", "请在专用窗口中返回本次商品页面。工具只读取本次链接对应的商品。")
            else:
                progress("waiting_page", "正在读取商品名称、功能和规格，最多等待 30 秒；提取失败后会停止并说明原因。")
            previous_state = state
        # Bounded polling also allows manual navigation and user verification to finish.
        control.check.wait(min(1.5, max(0, deadline - time.monotonic())))
    raise BrowserError("浏览器读取等待已超过 5 分钟。可以重新分析，或粘贴商品信息继续。")


class BrowserReader:
    def __init__(self):
        self._queue = Queue()
        self._thread = None
        self._lock = threading.Lock()
        self._context = None
        self._playwright = None

    def read(self, url, control, progress):
        url = canonical_url(url)
        if not browser_health()["installed"]:
            raise BrowserError("浏览器读取组件未安装。请使用项目 .venv 启动服务，或粘贴商品信息继续。")
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="product-browser", daemon=True)
                self._thread.start()
        future = Future()
        self._queue.put((url, control, progress, future))
        try:
            return future.result(timeout=WAIT_SECONDS + 110)
        except FutureTimeout:
            control.cancel.set()
            control.check.set()
            raise BrowserError("浏览器响应超时，请关闭专用窗口后重试，或粘贴商品信息。") from None

    def _open(self):
        from playwright.sync_api import sync_playwright
        if self._playwright is None:
            self._playwright = sync_playwright().start()
        if self._context is not None:
            try:
                pages = self._context.pages
                if pages and not pages[0].is_closed():
                    return pages[0]
                self._context.close()
            except Exception:
                pass
            self._context = None
        PROFILE.mkdir(exist_ok=True)
        channel = browser_channel()
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE), channel=None if channel == "chromium" else channel,
            headless=False, chromium_sandbox=True, viewport=None, timeout=45000,
            accept_downloads=False)
        return self._context.pages[0] if self._context.pages else self._context.new_page()

    def _run(self):
        try:
            while True:
                task = self._queue.get()
                if task is None:
                    return
                url, control, progress, future = task
                try:
                    if control.cancel.is_set():
                        raise BrowserCancelled("已结束浏览器读取，可以粘贴商品信息继续。")
                    progress("browser_starting", "正在打开专用商品浏览器窗口")
                    page = self._open()
                    progress("browser_loading", "正在通过浏览器加载商品页面")
                    page.bring_to_front()
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    except Exception as exc:
                        from playwright.sync_api import TimeoutError as PlaywrightTimeout
                        if not isinstance(exc, PlaywrightTimeout):
                            raise BrowserError("浏览器无法打开商品页，请检查网络或代理配置，或粘贴商品信息。") from None
                    product = wait_for_product(page, url, control, progress)
                    future.set_result(product)
                except BrowserError as exc:
                    future.set_exception(exc)
                except Exception:
                    # Never expose browser internals, profile storage, or page content in errors.
                    future.set_exception(BrowserError("专用浏览器无法启动或连接已断开。请检查 Edge 安装，关闭本工具的旧商品窗口后重试；也可粘贴资料。"))
        finally:
            if self._context:
                try:
                    self._context.close()
                except Exception:
                    pass
            if self._playwright:
                try:
                    self._playwright.stop()
                except Exception:
                    pass

    def close(self):
        self._queue.put(None)
        if self._thread:
            self._thread.join(timeout=5)


BROWSER = BrowserReader()
