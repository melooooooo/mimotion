# H5 + FastAPI 服务化版本

这个目录中的服务化实现保留原有 Zepp Life 登录和提交步数逻辑，新增了微信内置浏览器 H5 页面、公众号网页 OAuth 登录，以及兼容微信小程序 `web-view` 的后端 API。

## 本地启动

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

默认使用 SQLite：`mimotion_service.db`。浏览器访问开发入口：

```text
http://localhost:8000/dev-login?code=test-user
```

`dev-login` 会生成一次性 H5 ticket 并跳转到 `/app`，用于没有微信小程序资质时本地调试。生产环境不要开启开发登录。

## 关键环境变量

```bash
export DATABASE_URL='sqlite:///./mimotion_service.db'
export JWT_SECRET='please-change-this-secret'
export TOKEN_AES_KEY='1234567890abcdef'
export H5_BASE_URL='https://your-domain.com/app'
export ALLOW_DEV_LOGIN='false'
export WECHAT_APPID='your-miniapp-appid'
export WECHAT_SECRET='your-miniapp-secret'
export WECHAT_WEB_APPID='your-official-account-appid'
export WECHAT_WEB_SECRET='your-official-account-secret'
export WECHAT_OAUTH_REDIRECT_URI='https://your-domain.com/api/auth/wechat-oauth/callback'
```

`TOKEN_AES_KEY` 必须是 16 个字符，用于加密保存 Zepp token。服务不会保存 Zepp 明文密码。

PostgreSQL 示例：

```bash
export DATABASE_URL='postgresql+psycopg://user:password@host:5432/mimotion'
```

使用 PostgreSQL 时需要额外安装对应驱动，例如 `psycopg[binary]`。

## 微信内 H5 流程

公众号网页授权适用于用户把链接发到微信聊天、文件传输助手或公众号菜单后，在微信内置浏览器打开：

1. 用户访问 `https://your-domain.com/app/`。
2. H5 检测到微信环境但没有本地登录态时，跳转 `GET /wechat-login`。
3. 后端跳转到微信 OAuth：`https://open.weixin.qq.com/connect/oauth2/authorize`。
4. 微信回调 `GET /api/auth/wechat-oauth/callback?code=...&state=...`。
5. 后端用 `code` 换取 `openid`，生成一次性 ticket。
6. 后端跳回 `https://your-domain.com/app?ticket=...`。
7. H5 调用 `POST /api/auth/h5-exchange` 换取 JWT。
8. H5 后续调用绑定、提交、历史接口。

普通浏览器访问 `/app/` 只会显示“请在微信内打开”的门禁页；真正登录依赖后端微信 OAuth，不依赖前端 User-Agent 判断。

## 小程序 web-view 流程

1. 小程序调用 `wx.login()` 获取 `code`。
2. 小程序调用 `POST /api/auth/miniapp-login`，入参：`{"code":"..."}`。
3. 后端用 `jscode2session` 换取 `openid`，生成一次性 ticket。
4. 后端返回 `h5Url`，例如：`https://your-domain.com/app?ticket=...`。
5. 小程序 `web-view` 打开该 URL。
6. H5 调用 `POST /api/auth/h5-exchange` 换取 JWT。
7. H5 后续调用绑定、提交、历史接口。

## API

- `POST /api/auth/miniapp-login`
- `GET /wechat-login`
- `GET /api/auth/wechat-oauth/callback`
- `POST /api/auth/h5-exchange`
- `GET /api/me`
- `POST /api/zepp/bind`
- `DELETE /api/zepp/bind`
- `POST /api/steps/submit`
- `GET /api/steps/history`

所有业务接口都按后端 JWT 鉴权，数据按微信用户隔离。

## 生产注意事项

- H5 必须部署在 HTTPS 域名。
- 公众号 H5 模式需要在微信公众号后台配置网页授权域名。
- 小程序 web-view 模式需要在小程序后台配置 request 合法域名和 web-view 业务域名。
- 只靠 H5 User-Agent 判断不能作为安全鉴权，必须使用后端微信登录会话。
- Zepp token 失效且无法刷新时，用户需要重新绑定，因为服务不会保存 Zepp 密码。

## Dokploy 部署方案

当前服务可以部署到 Dokploy。推荐使用 `Application + Dockerfile Build Type + Supabase PostgreSQL + HTTPS 域名`，不要把传统 VPS 的 `systemd + Nginx` 方案原样照搬到 Dokploy。

推荐结构：

```text
微信内置浏览器 H5 / 小程序 web-view
  -> https://your-domain.com/app
  -> Dokploy / Traefik HTTPS
  -> 容器内部 8000 端口
  -> FastAPI
  -> Supabase PostgreSQL
```

### 部署前需要准备

Dokploy 上建议使用 Dockerfile 构建。项目需要补充 PostgreSQL 驱动：

```text
psycopg[binary]==3.2.3
```

如果服务连接 Supabase，`DATABASE_URL` 使用 `postgresql+psycopg://...` 格式。Supabase 只作为后端数据库使用，不要把 Supabase `anon key` 或 `service role key` 放到 H5 前端。

建议数据库引擎增加连接保活参数，减少 Supabase 连接闲置断开后的问题：

```python
pool_pre_ping=True
pool_recycle=1800
```

### Dockerfile 示例

在项目根目录新增 `Dockerfile`：

```dockerfile
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY util ./util
COPY web ./web

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

注意：容器内必须监听 `0.0.0.0`，不能监听 `127.0.0.1`，否则 Dokploy/Traefik 无法从容器外转发请求。

### Dokploy Application 配置

在 Dokploy 中创建 Application：

- Source：GitHub/Git 仓库
- Build Type：`Dockerfile`
- Dockerfile Path：`Dockerfile`
- Docker Context Path：`.`
- Domain Container Port：`8000`

通常不需要在 Advanced -> Ports 暴露端口。Dokploy 的 Domain Container Port 是给 Traefik 内部路由使用的，和直接暴露 `IP:port` 不是一回事。

### 环境变量

在 Dokploy Application 的 Environment 中配置：

```env
DATABASE_URL=postgresql+psycopg://postgres.xxxxx:你的数据库密码@aws-0-区域.pooler.supabase.com:5432/postgres?sslmode=require
JWT_SECRET=至少32位随机字符串
TOKEN_AES_KEY=16位字符密钥
H5_BASE_URL=https://你的域名/app
ALLOW_DEV_LOGIN=false
WECHAT_APPID=你的小程序appid
WECHAT_SECRET=你的小程序secret
WECHAT_WEB_APPID=你的公众号appid
WECHAT_WEB_SECRET=你的公众号appsecret
WECHAT_OAUTH_REDIRECT_URI=https://你的域名/api/auth/wechat-oauth/callback
```

生产环境必须保持：

```env
ALLOW_DEV_LOGIN=false
```

`TOKEN_AES_KEY` 必须正好 16 个字符。这个密钥用于解密已经保存的 Zepp token，丢失后旧 token 无法恢复，只能让用户重新绑定。

### Supabase 连接选择

如果 Dokploy 所在服务器支持 IPv6，可以使用 Supabase Direct Connection：

```env
DATABASE_URL=postgresql+psycopg://postgres:数据库密码@db.xxxxx.supabase.co:5432/postgres?sslmode=require
```

如果是普通 IPv4 VPS，优先使用 Supabase Session Pooler：

```env
DATABASE_URL=postgresql+psycopg://postgres.xxxxx:数据库密码@aws-0-区域.pooler.supabase.com:5432/postgres?sslmode=require
```

不建议默认使用 Transaction Pooler `6543`，因为当前服务是常驻 FastAPI 后端，不是 serverless/edge function。

### 域名和 HTTPS

在 Dokploy Application -> Domains 中添加域名：

- Host：`你的域名`
- Path：`/`
- Container Port：`8000`
- HTTPS：启用 Let's Encrypt

DNS 中需要把域名解析到 Dokploy 服务器 IP。部署后访问：

```text
https://你的域名/app/
```

### 部署后验证

首次部署可以临时打开开发登录：

```env
ALLOW_DEV_LOGIN=true
```

重新部署后访问：

```text
https://你的域名/dev-login?code=test-user
```

确认 H5 能打开、Supabase 表能自动创建后，立刻改回：

```env
ALLOW_DEV_LOGIN=false
```

再重新部署一次。

也可以用 curl 检查：

```bash
curl -I https://你的域名/app/
curl -I https://你的域名/wechat-login
```

`/wechat-login` 应该返回跳转到 `open.weixin.qq.com` 的响应。真实 OAuth 回调必须在微信内完成。

### 公众号后台配置

如果使用“微信内打开 H5”模式，微信公众号后台需要配置：

- 网页授权域名：`你的域名`
- JS 接口安全域名：`你的域名`，仅在后续需要微信 JS-SDK 能力时必需

公众号网页授权使用的是公众号 `appid/appsecret`，不是小程序 `appid/secret`。

### 小程序后台配置

微信小程序后台需要配置：

- request 合法域名：`https://你的域名`
- web-view 业务域名：`https://你的域名`

小程序侧流程：

```text
wx.login()
  -> POST https://你的域名/api/auth/miniapp-login
  -> 后端返回 h5Url
  -> web-view 打开 h5Url
```
