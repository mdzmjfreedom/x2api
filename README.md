# x2ding

一个面向 X/Twitter 监控的订阅系统：

- `collector/` 用 Python + Playwright 抓取 Nitter/X 内容
- `service/` 用 Next.js 提供 API key 管理接口和 RSS 输出
- `shared/schema.sql` 定义统一 PostgreSQL 结构

## 架构

- GitHub Actions 定时执行 `collector/twitter_monitor.py monitor`
- 采集结果写入 PostgreSQL
- Vercel 部署 `service`
- 客户端通过 `apiKey` 管理订阅，通过 `feedToken` 消费 RSS
- `Twitter Monitor` 支持按分片并行采集，单节点时也复用同一套逻辑

## 目录

```text
collector/   Python 抓取器与 Actions 入口
service/     Next.js API + RSS 服务
shared/      数据库 schema
data/        本地查询产物与临时文件
```

## 数据库初始化

先准备 PostgreSQL，然后执行：

```bash
psql "$DATABASE_URL" -f shared/schema.sql
```

如果你使用 Neon / Supabase，把 `DATABASE_URL` 分别配置到：

- GitHub Actions secret: `DATABASE_URL`
- GitHub Actions secret: `IMGBB_API_KEY`
- Vercel project env: `DATABASE_URL`

如果你使用 Supabase：

- Vercel 和 GitHub Actions 优先使用 `Transaction pooler` 连接串
- 不要在这些 serverless / CI 场景里使用 `db.<project-ref>.supabase.co:5432` 这种 direct connection

## 本地运行

### 1. 安装 service 依赖

```bash
npm install
```

### 2. 安装 collector 依赖

```bash
python3 -m pip install -r collector/requirements.txt
playwright install chromium
```

### 3. 注册一个客户端

```bash
DATABASE_URL=... python3 collector/twitter_monitor.py register-client --label "local"
```

返回结果里会包含：

- `apiKey`
- `feedToken`
- `feedUrlPath`

### 4. 给这个客户端设置订阅

```bash
DATABASE_URL=... python3 collector/twitter_monitor.py subscribe set \
  --api-key "x2d_xxx" \
  --targets "OpenAI,search:AI safety"
```

### 5. 运行采集

```bash
DATABASE_URL=... python3 collector/twitter_monitor.py monitor
```

如果要本地模拟多分片采集，也可以执行：

```bash
DATABASE_URL=... python3 collector/twitter_monitor.py monitor --shard-index 0 --shard-count 3
```

### 6. 启动服务

```bash
DATABASE_URL=... npm run dev --workspace service
```

## Vercel 部署

按 Vercel 官方 monorepo 方式，把这个仓库作为一个 monorepo 导入，并把项目的 `Root Directory` 设为 `service`。文档见 [Using Monorepos](https://vercel.com/docs/monorepos/) 和 [General settings](https://vercel.com/docs/project-configuration/general-settings)。

## API

更完整的接口说明见：[docs/SERVICE_API.md](docs/SERVICE_API.md)

### `POST /api/client/register`

创建一个匿名客户端：

```json
{
  "label": "my-device"
}
```

### `GET /api/subscriptions`

Header:

```text
Authorization: Bearer x2d_xxx
```

### `PUT /api/subscriptions`

覆盖订阅：

```json
{
  "targets": ["OpenAI", "search:AI safety"]
}
```

### `POST /api/subscriptions`

增量添加订阅。

### `DELETE /api/subscriptions`

增量删除订阅。

### `GET /api/items`

支持参数：

- `limit`
- `cursor`
- `target`
- `keyword`
- `since`

响应包含 `items` 和 `pagination`，其中 `pagination.nextCursor` 用于继续拉下一页。

## GitHub Actions 分片

`Twitter Monitor` 的 `workflow_dispatch` 支持输入 `shard_count`：

- `1`
  - 单节点模式，行为和现在一致
- `2` 或更大
  - 自动生成多个并行分片 job
  - 每个目标会稳定落到某一个分片里，不会重复抓取

### `GET /rss/:feedToken.xml`

返回该客户端的聚合 RSS。

## GitHub Actions

- `Twitter Monitor`: 定时抓取
- `Manage Subscriptions And Query`: 手动注册客户端、管理订阅、导出查询
- `Cleanup Stored Tweets`: 清理历史数据
- `Update Nitter Instances`: 刷新实例列表

## 当前取舍

- 采集仍依赖第三方 Nitter/X 镜像，稳定性取决于目标站点和实例可用性
- 没有登录系统，`apiKey` 就是匿名客户端身份
- RSS 用 `feedToken` 暴露，不直接使用 `apiKey`
