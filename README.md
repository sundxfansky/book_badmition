# Badminton Booker

一个简单的羽毛球场地预约辅助项目。它负责按配置轮询场地余量，在命中目标日期、时间段和场地后提交预约请求，并输出通知。

> 说明：本项目不包含验证码绕过、风控规避、账号批量注册等能力。实际接入时请遵守场馆平台的服务条款，只使用你本人账号和正常接口。

## 功能

- 按配置筛选日期、时间段、场地名称
- 支持定时轮询，命中可订场地后自动尝试预约
- 支持从抓包导出的 `request.txt` 读取提交请求
- 支持控制台通知 + 企业微信机器人通知
- 监听模式：监控已约场地释放后自动下单
- 网页控制台：可视化管理抢票参数
- Admin 管理面板：多任务管理

## 部署方式

本项目支持三种部署方式：

| 方式 | 适合场景 | 需要服务器 |
|------|---------|-----------|
| Cloudflare Workers | 推荐，免运维，全球加速 | 否 |
| 本地运行 | 开发调试 | 否 |
| VPS / Docker | 自有服务器长期运行 | 是 |

---

## 方式一：Cloudflare Workers 部署（推荐）

完全 serverless，无需服务器。使用 Durable Objects 实现持久状态和定时轮询。

### 前置条件

- [Cloudflare 账号](https://dash.cloudflare.com/sign-up)（需要 Workers Paid 计划，$5/月）
- Node.js 18+
- 已抓包获得 `request.txt`

### 部署步骤

#### 1. 克隆仓库并进入 Workers 目录

```bash
git clone https://github.com/sundxfansky/book_badmition.git
cd book_badmition
git checkout cf
cd workers
```

#### 2. 安装依赖

```bash
npm install
```

#### 3. 登录 Cloudflare

```bash
npx wrangler login
```

#### 4. 创建 KV 命名空间

```bash
npx wrangler kv namespace create REQUEST_STORE
npx wrangler kv namespace create ADMIN_KV
```

将输出的 `id` 填入 `wrangler.toml` 中对应的 `[[kv_namespaces]]` 配置。

#### 5. 配置企业微信通知（可选）

编辑 `wrangler.toml`，将 `WECHAT_BOT_WEBHOOK` 改为你的企业微信机器人 webhook 地址：

```toml
[vars]
WECHAT_BOT_WEBHOOK = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=你的key"
```

#### 6. 上传 request.txt 到 KV

```bash
npx wrangler kv key put --binding=REQUEST_STORE "request_template:default" --path=../request.txt
```

#### 7. 部署

```bash
npx wrangler deploy
```

部署成功后会输出 Worker URL，例如 `https://badminton-booker.your-subdomain.workers.dev`。

#### 8. 使用

打开 Worker URL 即可使用网页控制台。首次访问 `/sundx` 管理面板时需要设置管理员密码。

### 更新 request.txt

抓包获得新的 `request.txt` 后，重新上传：

```bash
npx wrangler kv key put --binding=REQUEST_STORE "request_template:default" --path=../request.txt
```

或通过 Admin 面板的"上传 request.txt"功能上传。

### 本地开发

```bash
cd workers
npm install
npx wrangler dev
```

打开 `http://localhost:8787` 即可本地调试。

---

## 方式二：本地运行

适合开发调试或临时使用。

### 前置条件

- Python 3.10+

### 运行

```bash
# 启动网页控制台
python3 -m badminton_booker web --request-file request.txt

# 或指定端口
python3 -m badminton_booker --host 0.0.0.0 --port 8765 web --request-file request.txt
```

打开 `http://127.0.0.1:8765` 使用网页控制台。

### 其他命令

```bash
# 单次运行（使用配置文件）
python3 -m badminton_booker --config config.json once

# 持续轮询
python3 -m badminton_booker --config config.json watch
```

### 运行测试

```bash
python3 -m unittest discover -s tests
```

---

## 方式三：VPS / Docker 部署

适合有自己服务器、需要长期稳定运行的场景。

### 使用 GitHub 镜像

项目推送到 `main` 后会通过 GitHub Actions 自动构建镜像：

```bash
docker pull ghcr.io/sundxfansky/book_badmition:latest
```

### 启动容器

```bash
docker run -d \
  --name badminton-booker \
  --restart unless-stopped \
  -p 8765:8765 \
  ghcr.io/sundxfansky/book_badmition:latest
```

打开 `http://你的服务器IP:8765` 使用。

### 使用本机 request.txt

```bash
docker run -d \
  --name badminton-booker \
  --restart unless-stopped \
  -p 8765:8765 \
  -v "$PWD/request.txt:/app/request.txt:ro" \
  ghcr.io/sundxfansky/book_badmition:latest
```

### 查看日志

```bash
docker logs -f badminton-booker
```

### 更新

```bash
docker pull ghcr.io/sundxfansky/book_badmition:latest
docker stop badminton-booker && docker rm badminton-booker
docker run -d \
  --name badminton-booker \
  --restart unless-stopped \
  -p 8765:8765 \
  ghcr.io/sundxfansky/book_badmition:latest
```

### 本地构建镜像

```bash
docker build -t badminton-booker .
docker run --rm -p 8765:8765 badminton-booker
```

---

## 配置说明

复制示例配置：

```bash
cp config.example.json config.json
```

常用字段：

- `provider`: 使用的场馆适配器，目前内置 `demo` 和 `request_file`
- `poll_interval_seconds`: 轮询间隔
- `max_attempts`: 最大轮询次数，`0` 表示不限次数
- `target.dates`: 目标日期，格式 `YYYY-MM-DD`
- `target.time_ranges`: 目标时间段
- `target.court_names`: 目标场地名，为空表示不限
- `booking.user_name`: 预约人姓名
- `booking.phone`: 预约手机号
- `notification.webhook_url`: 预留 webhook 地址，可不填
- `request_file.path`: 抓包导出的请求文件
- `request_file.submit_path`: 提交预约的接口 path
- `request_file.dry_run`: 是否只演练不真实提交

## 使用 request.txt

把配置改成：

```json
{
  "provider": "request_file",
  "request_file": {
    "path": "request.txt",
    "submit_path": "/v2/reserve/submit?",
    "dry_run": true
  }
}
```

确认日期、时间、场地都对以后，再把 `dry_run` 改为 `false` 才会真实提交请求。

## 网页控制台

网页控制台支持：

- 查看当前将要提交的请求
- 填写或覆盖 `wx-token`、`shop-id`、`brand-code`
- 手动添加多个日期，选择场地和时间段
- 查看运行状态和日志
- 导入/导出抢票参数 JSON
- dry-run 演练或真实提交
- 可开关 SSL 证书校验（本地/VPS 模式）

## 接入真实场馆

在 `badminton_booker/providers/` 下新增一个 provider，实现：

```python
class YourProvider(BookingProvider):
    def list_slots(self, target):
        ...

    def reserve(self, slot, booking):
        ...
```

然后在 `badminton_booker/providers/__init__.py` 注册名称即可。
