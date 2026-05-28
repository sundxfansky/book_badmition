# Badminton Booker

一个简单的羽毛球场地预约辅助项目。它负责按配置轮询场地余量，在命中目标日期、时间段和场地后提交预约请求，并输出通知。

> 说明：本项目不包含验证码绕过、风控规避、账号批量注册等能力。实际接入时请遵守场馆平台的服务条款，只使用你本人账号和正常接口。

## 功能

- 按配置筛选日期、时间段、场地名称
- 支持定时轮询，命中可订场地后自动尝试预约
- provider 适配器模式，方便替换为真实场馆接口
- 默认内置 `demo` provider，可直接本地演示
- 支持从抓包导出的 `request.txt` 读取提交请求
- 支持控制台通知，预留 webhook 通知位置

## 快速开始

```bash
python3 -m badminton_booker --config config.example.json once
```

持续轮询：

```bash
python3 -m badminton_booker --config config.example.json watch
```

启动本地网页控制台：

```bash
python3 -m badminton_booker web --request-file request.txt
```

然后打开：

```text
http://127.0.0.1:8765
```

## Docker 部署

### 使用 GitHub 镜像

项目推送到 `main` 后会通过 GitHub Actions 自动构建镜像并推送到 GHCR：

```text
ghcr.io/sundxfansky/book_badmition:latest
```

拉取镜像：

```bash
docker pull ghcr.io/sundxfansky/book_badmition:latest
```

前台启动：

```bash
docker run --rm -p 8765:8765 ghcr.io/sundxfansky/book_badmition:latest
```

后台启动：

```bash
docker run -d \
  --name badminton-booker \
  --restart unless-stopped \
  -p 8765:8765 \
  ghcr.io/sundxfansky/book_badmition:latest
```

然后打开：

```text
http://127.0.0.1:8765
```

查看日志：

```bash
docker logs -f badminton-booker
```

停止并删除容器：

```bash
docker stop badminton-booker
docker rm badminton-booker
```

更新镜像并重启：

```bash
docker pull ghcr.io/sundxfansky/book_badmition:latest
docker stop badminton-booker
docker rm badminton-booker
docker run -d \
  --name badminton-booker \
  --restart unless-stopped \
  -p 8765:8765 \
  ghcr.io/sundxfansky/book_badmition:latest
```

### 使用本机 request.txt

镜像内已经包含仓库里的脱敏 `request.txt`。如果要使用本机新的抓包文件覆盖镜像里的 `request.txt`：

```bash
docker run -d \
  --name badminton-booker \
  --restart unless-stopped \
  -p 8765:8765 \
  -v "$PWD/request.txt:/app/request.txt:ro" \
  ghcr.io/sundxfansky/book_badmition:latest
```

### 本地构建镜像

如果需要从源码本地构建：

```bash
docker build -t badminton-booker .
```

启动本地构建的镜像：

```bash
docker run --rm -p 8765:8765 badminton-booker
```

使用抓包请求演练：

```bash
python3 -m badminton_booker --config config.request-file.example.json once
```

运行测试：

```bash
python3 -m unittest discover -s tests
```

## 配置

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
- `request_file.submit_path`: 提交预约的接口 path，例如 `/v2/reserve/submit?`
- `request_file.dry_run`: 是否只演练不真实提交，建议先保持 `true`

## 使用 request.txt

如果请求数据已经在抓包文件里，把配置改成：

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

当前项目会从 `req.base64` 解出提交 body，并读取其中的 `venues_date`、`venues_site_time` 作为候选场地。`dry_run` 为 `true` 时只验证解析结果，不会真的请求场馆接口。

确认日期、时间、场地都对以后，再把 `dry_run` 改为 `false` 才会真实提交请求。

## 网页控制台

网页控制台支持：

- 查看当前将要提交的请求
- 填写或覆盖 `wx-token`、`shop-id`、`brand-code`
- 手动添加多个日期，选择代码内固定的 7 个场地和 15 个时间段
- 查看运行状态和日志
- 导入/导出抢票配置 JSON
- dry-run 演练或真实提交
- 可开关 SSL 证书校验

为了减少不必要请求，网页会优先使用代码中固定的 7 个场地和 15 个时间段。开始抢票后不会再查询余位，而是按用户选择的日期、场地、时间段直接构造并轮询提交预约请求。实际提交仍使用抓包里的 `/v2/reserve/submit?` 请求模板。

建议先保持 `dry-run` 开启，确认“当前请求”里的 body 和 headers 正确后再关闭。

默认请求会关闭 SSL 证书校验，以避开本机 Python 证书链问题。需要开启时，可以勾选网页里的 `SSL 校验`，或在配置里设置：

```json
"verify_ssl": true
```

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

真实接入通常需要你先确认：

- 平台是否有公开 API 或浏览器请求接口
- 登录态如何合法保存和刷新
- 提交预约接口需要哪些字段
- 是否需要人工验证码或支付确认
