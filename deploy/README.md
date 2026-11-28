# 私人服务器部署

这个部署方式面向一位所有者或可信的小团队，使用 Docker Compose 和带 HTTP Basic 登录的 Nginx 网关。不是公开注册、多租户服务；同一实例中的任务与生成配额共享。需要 Linux、Docker Compose，以及自己的 HTTPS 入口（例如 Cloudflare Tunnel）。

## 首次启动

在服务器的独立目录中取得仓库，限制目录访问权限，然后运行：

```sh
chmod 700 .
python3 deploy/init_private.py --host zhiwu.example.com --bind 127.0.0.1 --port 18765
docker compose up -d --build
python3 deploy/smoke_test.py
```

将示例域名换成自己的域名。Tunnel 连接器与应用在同一主机时，保持 `127.0.0.1`；在局域网另一台机器时，`--bind` 填应用服务器的固定私有 IPv4 地址。不要绑定所有网卡，也不要在路由器上把该端口转发到公网。LAN 访问使用 HTTP Basic，适用于可信局域网；公网访问必须走 HTTPS。

初始化会生成随机登录密码，默认账号为 `owner`，AI 保持未配置状态。**在自己的服务器终端**读取 `.secrets/initial-login.txt` 取得密码，不要将文件内容发到聊天、仓库或日志。初始化不会覆盖现有私有配置。

Cloudflare Tunnel 的“添加已发布应用程序”可按下表填写：

| 字段 | 填写内容 |
| --- | --- |
| 子域名 | `zhiwu`，也可自定 |
| 域 | 自己已接入 Cloudflare 的域名 |
| 路径 | 留空 |
| Service URL | `http://127.0.0.1:18765`；连接器在另一台机器时改为应用服务器的私有 IPv4 地址 |

浏览器通过 `https://zhiwu.自己的域名` 访问；首次弹出登录框。可以另加 Cloudflare Access 的邮箱允许名单。Tunnel 配置中的服务地址应指向网关端口，不能指向后端 8765。

参考：[Cloudflare Tunnel 设置](https://developers.cloudflare.com/tunnel/get-started/)、[Cloudflare Access](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/)。

## AI 配置与隐私

- 只在服务器 `.env` 中填写 `AI_BASE_URL`、`AI_API_KEY`、`AI_MODEL` 等字段，参照根目录 `.env.example`。不要将真实配置写进模板或前端。
- `.env` 权限为 `600`，`.secrets` 目录权限为 `700`。Docker 构建使用文件允许名单，不包含配置、登录信息、浏览器会话或 Git 历史；后端通过容器环境接收 AI 配置。只有后端拥有 AI 密钥，网关不接收 AI 环境变量。
- 宿主机管理员和具有 Docker 管理权限的用户仍能读取服务配置。此设计保护公网访问与仓库发布边界，不对服务器管理员隐藏密钥。
- 改完 `.env` 后运行 `docker compose up -d --force-recreate app gateway`。容器不挂载真实 `.env`，因此需要重建容器以载入修改后的环境。
- 登录网关保护页面和 API；后端不发布宿主机端口。网关拒绝未知 Host 和跨站 Origin，限制请求频率和体积。登录信息不会转发到应用或 AI 服务。
- 任务保存在进程内存，重启后消失。浏览器 SKU 缓存位于各标签页，见主 README。备份私有配置时也应限制访问。

AI 未配置时页面可打开，生成操作会明确提示尚未配置。当前精简镜像不安装 Playwright 或图形桌面，支持直接获取及粘贴资料；遇到 Amazon 验证码时需粘贴资料，不能在用户电脑上操控服务器浏览器。没有安装 Codex CLI，也不会读取开发者的本机登录状态。

## 检查与维护

```sh
docker compose ps
python3 deploy/smoke_test.py
# 配好 HTTPS 域名后，检查经公网入口的登录访问：
python3 deploy/smoke_test.py --public
docker compose logs --tail=30 app gateway
# 更新源文件后：
docker compose up -d --build
# 暂停：
docker compose stop
```

检查脚本只输出通过/失败，不输出密码、认证头、任务令牌或 AI 配置。正常结果包括：匿名页面/API 为 401、登录后页面可访问、私有文件不可下载、未知 Host 与跨站请求被拒绝。初始部署还会验证 AI 未启用；启用 AI 后，应调整相应预期再执行。

不要提交 `.env`、`.secrets/` 或实际部署日志；`scripts/audit_release.py` 会检查 Git 暂存区的私有目录与常见凭证格式。
