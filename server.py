"""Run with: python server.py (Python 3.10+, no pip dependencies)."""
import argparse
import json
import mimetypes
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from app.ai import AIError, generate, health
from app.browser import BROWSER, BrowserCancelled, BrowserControl, browser_health
from app.product import DEMO_TEXT, ProductError, canonical_url, fetch_product, manual_product
from app.images import IMAGE_HOSTS

ROOT = Path(__file__).resolve().parent
JOBS = {}
JOBS_LOCK = threading.Lock()
CONTROLS = {}
TOKEN = secrets.token_urlsafe(32)


def update_job(identifier, **values):
    with JOBS_LOCK:
        JOBS[identifier].update(values)


def analyze(identifier, payload):
    try:
        control = CONTROLS[identifier]
        demo = payload.get("demo") is True
        manual = payload.get("manual_text", "").strip()
        url = payload.get("url", "").strip()
        if demo:
            product = manual_product(DEMO_TEXT, demo=True)
        elif manual:
            product = manual_product(manual, url)
        else:
            mode = payload.get("fetch_mode", "auto")
            progress = lambda status, message: update_job(identifier, status=status, message=message)
            if mode == "browser":
                product = BROWSER.read(url, control, progress)
            else:
                try:
                    product = fetch_product(url)
                except ProductError as exc:
                    if mode == "direct":
                        raise
                    update_job(identifier, direct_error=str(exc))
                    product = BROWSER.read(url, control, progress)
            if control.cancel.is_set():
                raise BrowserCancelled("已结束浏览器读取，可以粘贴商品信息继续。")
        update_job(identifier, product=product, status="generating", message="商品信息已整理，正在分析购买理由")
        result = generate(product, payload.get("audience", ""), lambda status, message: update_job(identifier, status=status, message=message))
        update_job(identifier, result=result, status="done", message="分析完成")
    except BrowserCancelled as exc:
        update_job(identifier, status="cancelled", error=str(exc), message="已结束浏览器读取")
    except (ProductError, AIError) as exc:
        update_job(identifier, status="error", error=str(exc), message="任务未完成")
    except Exception as exc:
        # Do not return subprocess output, credentials, or submitted product text in errors.
        print(f"Analysis error: {type(exc).__name__}", flush=True)
        update_job(identifier, status="error", error="处理失败，请重试；如自动获取失败，可补充商品信息。", message="任务未完成")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        if self.command != "GET" or not self.path.startswith("/api/jobs/"):
            super().log_message(format, *args)

    def send_bytes(self, code, content, content_type):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        image_sources = ' '.join('https://' + host for host in sorted(IMAGE_HOSTS))
        self.send_header("Content-Security-Policy", f"default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data: {image_sources}; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        try:
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def send_json(self, code, value):
        self.send_bytes(code, json.dumps(value, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def valid_host(self):
        return self.headers.get("Host", "") in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}

    def do_GET(self):
        if not self.valid_host():
            return self.send_json(403, {"error": "仅允许通过本机地址访问。"})
        path = urlsplit(self.path).path
        if path == "/api/health":
            return self.send_json(200, {"ai": health(), "browser": browser_health(), "token": TOKEN})
        if path.startswith("/api/jobs/"):
            if self.headers.get("X-App-Token") != TOKEN:
                return self.send_json(403, {"error": "请刷新页面后重试。"})
            with JOBS_LOCK:
                value = dict(JOBS.get(path.rsplit("/", 1)[-1], {}))
            return self.send_json(200 if value else 404, value or {"error": "任务不存在或已过期。"})
        files = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/product.css": "product.css", "/favicon.svg": "favicon.svg"}
        if path not in files:
            return self.send_json(404, {"error": "Not found"})
        file = ROOT / "web" / files[path]
        return self.send_bytes(200, file.read_bytes(), (mimetypes.guess_type(file.name)[0] or "application/octet-stream") + "; charset=utf-8")

    def do_POST(self):
        if not self.valid_host() or self.headers.get("X-App-Token") != TOKEN:
            return self.send_json(403, {"error": "请求未通过验证，请刷新本机页面后重试。"})
        if self.headers.get("Origin") not in (None, f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"):
            return self.send_json(403, {"error": "不支持跨站请求。"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 80000:
                raise ValueError()
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                raise ValueError()
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError()
            route = self.path.strip("/").split("/")
            if len(route) == 4 and route[:2] == ["api", "jobs"] and route[3] in {"check", "focus", "cancel"}:
                with JOBS_LOCK:
                    job = JOBS.get(route[2])
                    control = CONTROLS.get(route[2])
                    if not job or not control:
                        return self.send_json(404, {"error": "任务不存在或已过期。"})
                    if job["status"] not in {"browser_starting", "browser_loading", "waiting_verification", "waiting_page"}:
                        return self.send_json(409, {"error": "任务已经离开浏览器读取阶段，请等待结果。"})
                    if route[3] == "focus":
                        control.focus.set()
                    elif route[3] == "cancel":
                        control.cancel.set()
                    control.check.set()
                return self.send_json(202, {"ok": True})
            if self.path != "/api/analyze":
                return self.send_json(404, {"error": "Not found"})
            if not isinstance(payload, dict) or any(not isinstance(payload.get(key, ""), str) for key in ("url", "manual_text", "audience")):
                raise ValueError()
            if len(payload.get("manual_text", "")) > 16000 or len(payload.get("audience", "")) > 500:
                return self.send_json(400, {"error": "补充信息最多 16000 字，受众说明最多 500 字。"})
            if payload.get("fetch_mode", "auto") not in ("auto", "browser", "direct"):
                return self.send_json(400, {"error": "不支持的商品读取方式。"})
            if payload.get("demo") is not True:
                if payload.get("url", "").strip():
                    canonical_url(payload["url"])
                if not payload.get("manual_text", "").strip():
                    canonical_url(payload.get("url", ""))
                elif len(payload["manual_text"].strip()) < 40:
                    return self.send_json(400, {"error": "请至少提供 40 字商品信息，包含名称与功能。"})
            if not health()["ready"]:
                return self.send_json(503, {"error": health()["message"]})
            with JOBS_LOCK:
                # Only one content-generation job may run at a time.
                if any(job["status"] not in {"done", "error", "cancelled"} for job in JOBS.values()):
                    return self.send_json(409, {"error": "已有任务运行中，请等待完成。"})
                expired = [key for key, job in JOBS.items() if time.time() - job["created"] > 3600]
                for key in expired:
                    JOBS.pop(key, None)
                    CONTROLS.pop(key, None)
                while len(JOBS) >= 20:
                    key = next(iter(JOBS))
                    JOBS.pop(key)
                    CONTROLS.pop(key, None)
                identifier = secrets.token_urlsafe(18)
                JOBS[identifier] = {"status": "fetching", "message": "正在获取商品信息", "created": time.time()}
                CONTROLS[identifier] = BrowserControl()
            threading.Thread(target=analyze, args=(identifier, payload), daemon=True).start()
            return self.send_json(202, {"id": identifier})
        except ProductError as exc:
            return self.send_json(400, {"error": str(exc)})
        except (ValueError, json.JSONDecodeError):
            return self.send_json(400, {"error": "请求格式无效。"})


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="商品理解与口播助手")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"\n商品理解与口播助手\nLocal: http://127.0.0.1:{server.server_port}\nAI：读取本地 .env 配置 · Ctrl+C 停止\n", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for control in CONTROLS.values():
            control.cancel.set()
            control.check.set()
        BROWSER.close()
        server.server_close()


if __name__ == "__main__":
    main()
