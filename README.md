# 知物 · 商品理解与口播助手

面向电商内容人员：输入 Amazon 商品链接，整理商品事实与 SKU 价格，分析消费者的使用需求，把有依据的购买理由变成可拍摄的中文短视频口播。

## 功能

- 商品概览：已确认售价区间、基本信息、按颜色/尺寸分组的选项及各自价格、功能和规格。
- 价格区分：确定售价、选项起价、价格未展示分别标注；排除划线价和单位价格，不把起价当作确定区间的上限。
- 产品分析：核心需求、SKU 选择、人群偏好、使用场景、价格与价值、购买顾虑等维度；保留可定位的事实依据。
- 视频口播：三版独立受众、场景和 SKU 的中文口播，每版不超过 150 字，含开场钩子及镜头建议；支持编辑、复制和导出。
- 获取失败时可使用浏览器辅助读取或粘贴资料。演示使用明确标记的虚构商品资料，仍会调用使用者配置的 AI。

## 快速开始

需要 Python 3.10+。Windows 浏览器辅助读取默认使用已安装的 Edge。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

编辑本地 `.env`，填写自己的配置：

```dotenv
AI_BASE_URL=https://your-api-provider.example/v1
AI_API_KEY=
AI_MODEL=
AI_API_STYLE=chat_completions
AI_RESPONSE_FORMAT=json_schema
AI_TIMEOUT_SECONDS=180
```

上面的地址是占位示例，必须替换。不要将真实密钥写入 README、`.env.example` 或前端代码。

```powershell
.\.venv\Scripts\python.exe server.py
# 或在 Windows 中运行：.\start.ps1
```

访问 <http://127.0.0.1:8765/>。未配置时页面会提示配置 AI，不会尝试复用其他应用的登录状态。基础页面与直接请求使用 Python 标准库；若不需要浏览器辅助读取，可跳过依赖安装。

macOS/Linux：使用 `.venv/bin/python`，以 `cp .env.example .env` 复制模板，并在安装依赖后运行 `python -m playwright install chromium`。可通过进程环境变量 `PRODUCT_BROWSER` 选择 `msedge`、`chrome` 或 `chromium`。

## AI 配置

| 变量 | 用途 |
| --- | --- |
| `AI_BASE_URL` | 供应商的 API 根地址，包含其要求的 `/v1` 等前缀；不要追加具体接口路径。远程服务要求 HTTPS。 |
| `AI_API_KEY` | 自己的 API 密钥，仅在后端使用。本机 HTTP 模型服务可留空。 |
| `AI_MODEL` | 供应商实际支持的模型 ID，没有内置默认模型。 |
| `AI_API_STYLE` | `chat_completions` 调用 `/chat/completions`；`responses` 调用 `/responses`。 |
| `AI_RESPONSE_FORMAT` | `json_schema`（默认）、`json_object` 或 `text`。模型不支持结构化输出时可选择后两者；所有模式均进行本地结果校验。 |
| `AI_TIMEOUT_SECONDS` | 单次模型请求超时，10～300 秒，默认 180。格式/内容校验失败最多修订一次。 |

支持提供以上兼容协议的服务，并非支持所有供应商的专有接口。可参考 [Chat Completions 接口](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)及 [Responses 接口](https://developers.openai.com/api/reference/resources/responses/methods/create)确认兼容格式。

`.env` 中的同名配置优先于进程环境变量，包括空值；不执行命令、不展开变量。改完后下一次分析会读取新配置。提供程序的地址、模型和密钥不通过健康检查返回网页；“已配置”表示本地配置检查通过，不表示已完成远程连通性验证。

## 使用与边界

1. 输入商品链接，可补充想面向的人群，选择读取方式并开始分析。
2. 默认自动请求，失败后打开专用商品浏览器。验证码由使用者手动完成，工具不会点击或填写验证控件。
3. 商品字段就绪后显示概览，再生成分析与口播。原文依据默认收起，点击分析中的 F 编号自动展开定位。
4. 切换三版口播，分别编辑、复制，或导出全部内容。发布前核对原商品描述。

- 支持 Amazon.com、.de、.co.uk、.co.jp、.ca、.fr、.it、.es 的商品详情链接，仅允许规定站点的 HTTPS 跳转。
- 只读取页面实际提供的选项，不主动遍历购买、不将独立颜色与尺寸组合成未经验证的 SKU。价格可能随时间、地区、规格和供货变化，已读取区间不保证覆盖全部 SKU。当前选择价回填选项时会标明规格组合限制。
- 消费者人群、场景及收益属于 AI 推断。颜色不代表性别上的必然或排他适配；当前款式规格不自动套用到全部变体。
- 每版口播按 Unicode 码点计数，包含标点和空格，最多 150 字。开头最多 30 字作为约前 5 秒的写作约束，实际时长由语速决定。
- 验证状态最长等待 5 分钟，普通商品字段缺失最多等待 30 秒；可随时取消浏览器读取并粘贴资料。

## 隐私与运行

- `.env`、浏览器会话、运行临时文件、截图、日志和本地备份均不提交。仓库只提供无密钥的 `.env.example`。
- 密钥仅由后端作为认证头发送给配置的服务；不跟随模型服务重定向，不返回供应商原始报错、响应或凭证到前端，也不打印请求正文。
- 商品资料、受众说明和生成结果会发送给使用者自己配置的 AI 服务，适用该服务的数据政策。本机运行不代表模型处理离线进行。Responses 请求使用 `store=false`；供应商的实际留存政策需自行确认。
- 专用商品会话位于 `.browser-profile/`，可能含登录/地区信息。不要打包分享该目录或真实 `.env`。导出的简报由使用者自行保管。
- 服务仅监听 `127.0.0.1`，检查同源请求，生成任务串行执行。内存中最多保留最近 20 个任务，创建新任务时清理超过 1 小时的历史，重启清空。刷新同一标签页可恢复仍在内存中的任务。
- 本项目是本地工具。若部署到公网，需要额外实现身份认证、访问限制、配额和浏览器会话隔离，不能直接当作静态网站部署。

## 开发检查

```powershell
python -m unittest discover -s tests -v
node --check web/app.js
```

测试覆盖商品解析、浏览器恢复/取消/超时、SKU 与价格、口播结构及证据校验、配置优先级、两种模型协议和错误信息脱敏；模型测试使用模拟响应，不依赖真实密钥或付费调用。
