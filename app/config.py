"""Server-only AI settings. No credentials or provider details reach the browser."""
from dataclasses import dataclass, field
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent


class ConfigError(ValueError):
    pass


def read_env(path):
    """Read simple KEY=value lines without executing code or expanding variables."""
    try:
        lines = Path(path).read_text(encoding='utf-8-sig').splitlines()
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError):
        raise ConfigError('无法读取 .env，请检查文件权限和 UTF-8 编码。') from None
    values = {}
    for index, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:].lstrip()
        name, separator, value = line.partition('=')
        name, value = name.strip(), value.strip()
        if not separator or not re.fullmatch(r'[A-Z_][A-Z0-9_]*', name):
            raise ConfigError(f'.env 第 {index} 行格式错误，应为 KEY=value。')
        if value.startswith(('"', "'")):
            quote = value[0]
            end = value.find(quote, 1)
            if end < 0 or (value[end + 1:].strip() and not value[end + 1:].lstrip().startswith('#')):
                raise ConfigError(f'.env 第 {index} 行引号格式错误。')
            value = value[1:end]
        else:
            value = re.split(r'\s+#', value, maxsplit=1)[0].rstrip()
        values[name] = value
    return values


@dataclass(frozen=True)
class AIConfig:
    base_url: str = field(repr=False)
    api_key: str = field(repr=False)
    model: str = field(repr=False)
    api_style: str = 'chat_completions'
    response_format: str = 'json_schema'
    timeout: int = 180
    vision_enabled: bool = True


def load_config(path=None, environ=None):
    environment = os.environ if environ is None else environ
    values = {name: value for name, value in environment.items() if name.startswith('AI_')}
    # Explicit local settings take precedence, including blank values.
    values.update(read_env(ROOT / '.env' if path is None else path))
    missing = [name for name in ('AI_BASE_URL', 'AI_MODEL') if not values.get(name, '').strip()]
    if missing:
        raise ConfigError('请复制 .env.example 为 .env 并填写：' + '、'.join(missing) + '。')
    base_url = values['AI_BASE_URL'].strip().rstrip('/')
    try:
        url = urlsplit(base_url)
        port = url.port
    except ValueError:
        raise ConfigError('AI_BASE_URL 地址格式错误。') from None
    local = url.hostname in {'localhost', '127.0.0.1', '::1'}
    if not url.hostname or url.username or url.password or url.query or url.fragment or (url.scheme != 'https' and not (url.scheme == 'http' and local)):
        raise ConfigError('AI_BASE_URL 应为 HTTPS API 根地址；本机服务可用 HTTP，地址不能包含凭证或查询参数。')
    if url.hostname.endswith('.example'):
        raise ConfigError('请把 AI_BASE_URL 示例地址替换为自己的模型服务地址。')
    key = values.get('AI_API_KEY', '').strip()
    if not key and not local:
        raise ConfigError('请在 .env 中填写 AI_API_KEY。')
    if any(c in key for c in '\r\n'):
        raise ConfigError('AI_API_KEY 不能包含换行。')
    style = values.get('AI_API_STYLE', 'chat_completions')
    output = values.get('AI_RESPONSE_FORMAT', 'json_schema')
    if style not in {'chat_completions', 'responses'}:
        raise ConfigError('AI_API_STYLE 仅支持 chat_completions 或 responses。')
    if output not in {'json_schema', 'json_object', 'text'}:
        raise ConfigError('AI_RESPONSE_FORMAT 仅支持 json_schema、json_object 或 text。')
    try:
        timeout = int(values.get('AI_TIMEOUT_SECONDS', '180'))
        if not 10 <= timeout <= 300:
            raise ValueError()
    except ValueError:
        raise ConfigError('AI_TIMEOUT_SECONDS 应为 10～300 的整数。') from None
    vision = values.get('AI_VISION_ENABLED', 'true').lower()
    if vision not in {'true', 'false'}:
        raise ConfigError('AI_VISION_ENABLED 应为 true 或 false。')
    return AIConfig(base_url, key, values['AI_MODEL'].strip(), style, output, timeout, vision == 'true')
