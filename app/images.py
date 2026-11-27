"""Product gallery metadata and bounded downloads from Amazon image CDNs only."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import urllib.request
from urllib.parse import urlsplit, urlunsplit

IMAGE_HOSTS = {'m.media-amazon.com', 'images-na.ssl-images-amazon.com', 'images-eu.ssl-images-amazon.com', 'images-fe.ssl-images-amazon.com'}
MAX_IMAGES = 12
VISION_LIMIT = 6
MAX_IMAGE_BYTES = 5_000_000


def image_url(value):
    try:
        url = urlsplit(str(value or ''))
        if url.scheme != 'https' or url.hostname not in IMAGE_HOSTS or url.port not in (None, 443) or url.username or url.password or not re.fullmatch(r'/images/I/[A-Za-z0-9+_%.,-]+\.(?:jpg|jpeg|png|webp)', url.path, re.I):
            return None
        # Thumbnails and high-resolution versions share an Amazon asset ID.
        path = re.sub(r'\._[^/]+_\.(jpg|jpeg|png|webp)$', r'.\1', url.path, flags=re.I)
        return urlunsplit(('https', url.hostname, path, '', ''))
    except (ValueError, TypeError):
        return None


def attach_images(product, candidates=()):
    images, seen = [], set()
    for candidate in candidates:
        item = {'url': candidate} if isinstance(candidate, str) else candidate
        if not isinstance(item, dict):
            continue
        url = image_url(item.get('url'))
        asset = urlsplit(url).path if url else ''
        if not url or asset in seen:
            continue
        seen.add(asset)
        sku = product.get('selected_sku', {})
        images.append({'id': f'I{len(images) + 1}', 'url': url, 'caption': str(item.get('caption') or '商品页面图片')[:180],
                       'scope': f"来源：{sku.get('label', '页面当前配置')}" + (f" · ASIN {sku['asin']}" if sku.get('asin') else '') + '；通用示意图仍需核实'})
        if len(images) >= MAX_IMAGES:
            break
    product['images'] = images
    product['vision_image_ids'] = []
    return product


def extract_images(nodes, ids, structured_product):
    candidates = []
    roots = [ids[key] for key in ('landingImage', 'imgTagWrapperId', 'altImages', 'imageBlock', 'main-image-container') if key in ids]
    for root in roots:
        excluded = {id(child) for parent in root.walk() if re.search(r'video|spin|360', parent.attrs.get('class', ''), re.I) for child in parent.walk()}
        for node in root.walk():
            if node.tag != 'img' or id(node) in excluded:
                continue
            attrs = node.attrs
            caption = attrs.get('alt', '')
            if re.search(r'video|360|视频', caption, re.I):
                continue
            for name in ('data-old-hires', 'data-src', 'src'):
                if attrs.get(name):
                    candidates.append({'url': attrs[name], 'caption': caption})
            try:
                dynamic = json.loads(attrs.get('data-a-dynamic-image', '{}'))
                if isinstance(dynamic, dict):
                    candidates.extend({'url': key, 'caption': caption} for key in dynamic)
            except (ValueError, TypeError):
                pass
    # Do not merge all variant galleries from page scripts into the selected SKU.
    structured = structured_product.get('image', [])
    if not isinstance(structured, list):
        structured = [structured]
    for value in structured:
        candidates.append({'url': value.get('url', '') if isinstance(value, dict) else value})
    return candidates


class NoImageRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def download_image(item, folder):
    url = image_url(item.get('url'))
    if not url:
        return None
    try:
        request = urllib.request.Request(url, headers={'User-Agent': 'ProductGallery/1.0', 'Accept': 'image/jpeg,image/png,image/webp'})
        with urllib.request.build_opener(NoImageRedirect()).open(request, timeout=12) as response:
            kind = response.headers.get_content_type()
            if kind not in {'image/jpeg', 'image/png', 'image/webp'}:
                return None
            data = response.read(MAX_IMAGE_BYTES + 1)
        valid = (kind == 'image/jpeg' and data.startswith(b'\xff\xd8\xff') or
                 kind == 'image/png' and data.startswith(b'\x89PNG\r\n\x1a\n') or
                 kind == 'image/webp' and data.startswith(b'RIFF') and data[8:12] == b'WEBP')
        if not valid or len(data) > MAX_IMAGE_BYTES:
            return None
        extension = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp'}[kind]
        # IDs from the collector only; never use a page-controlled filename.
        if not re.fullmatch(r'I\d{1,2}', str(item.get('id', ''))):
            return None
        path = Path(folder) / (item['id'] + extension)
        path.write_bytes(data)
        return {'id': item['id'], 'path': path, 'mime': kind}
    except (OSError, ValueError):
        return None


def prepare_images(product, folder):
    images = product.get('images', [])[:VISION_LIMIT]
    with ThreadPoolExecutor(max_workers=3) as pool:
        downloaded = list(pool.map(lambda item: download_image(item, folder), images))
    prepared = [item for item in downloaded if item]
    product['vision_image_ids'] = [item['id'] for item in prepared]
    return prepared
