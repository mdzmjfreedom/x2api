# x2ding 服务端接口文档

本文档说明 `x2api` 服务端的整体架构、核心模块、鉴权方式、JSON 查询接口，以及 RSS 订阅接口。

适用场景：

- 客户端通过 `apiKey` 维护自己的订阅列表
- 客户端通过 JSON API 查询最近抓取结果
- RSS 阅读器或外部系统通过 RSS URL 消费聚合订阅流
- GitHub Actions 定时抓取，Vercel 对外提供查询与 RSS 服务

## 1. 整体架构

### 1.1 数据流

1. GitHub Actions 按计划触发采集任务
2. Python 采集器从数据库读取当前所有活跃订阅目标
3. 采集器通过 Nitter/X 相关来源抓取指定用户或关键词内容
4. 新内容写入 PostgreSQL
5. Vercel 上的 Next.js 服务读取 PostgreSQL，对外提供 JSON API 与 RSS
6. 客户端使用 `apiKey` 管理订阅、查询数据
7. RSS 使用 `feedToken` 生成专属订阅链接，供 RSS 阅读器或外部系统消费

### 1.2 功能模块

#### A. 采集模块 `collector/`

- 负责抓取 X/Twitter 相关内容
- 入口脚本：`collector/twitter_monitor.py`
- 支持：
  - 注册客户端
  - 管理订阅
  - 手动查询
  - 定时采集
  - 清理历史数据

#### B. 服务模块 `service/`

- 基于 Next.js App Router
- 提供：
  - 客户端注册接口
  - 订阅管理接口
  - 内容查询接口
  - RSS 输出接口

#### C. 数据库模块 `shared/schema.sql`

核心表：

- `clients`
  - 客户端身份
  - 保存 `api_key`、`feed_token`
- `targets`
  - 订阅目标
  - 支持两类：`user`、`keyword`
- `subscriptions`
  - 客户端与目标之间的订阅关系
- `items`
  - 抓取到的内容明细
- `crawl_state`
  - 每个目标的最近抓取状态

#### D. 调度模块 `.github/workflows/`

- `Twitter Monitor`
  - 每 6 分钟运行一次采集
- `Cleanup Stored Tweets`
  - 定期清理历史数据
- `Update Nitter Instances`
  - 定期更新可用实例列表
- `Manage Subscriptions And Query`
  - 手动执行注册、订阅管理、查询

## 2. 身份模型与鉴权

本系统当前没有传统用户系统，采用“匿名客户端”模型。

每个客户端在注册后会获得两种凭证：

- `apiKey`
  - 用于调用 JSON API
  - 代表客户端身份
- `feedToken`
  - 用于生成 RSS URL
  - RSS URL 本身就是访问凭证

### 2.1 JSON API 鉴权

支持两种传法：

#### 方式 A：`Authorization` 头

```http
Authorization: Bearer x2d_xxx
```

#### 方式 B：`x-api-key` 头

```http
x-api-key: x2d_xxx
```

说明：

- 两种方式任选其一
- 推荐优先使用 `Authorization: Bearer <apiKey>`

### 2.2 RSS 鉴权

RSS 不使用 `apiKey`。

RSS 通过 `feedToken` 直接访问，例如：

```text
/rss/feed_xxx.xml
```

因此：

- 拿到 RSS URL 即可访问
- 不需要额外请求头
- `feedToken` 应视为敏感凭证，不要公开泄漏

## 3. 订阅目标格式

系统支持两类订阅目标：

- 用户订阅：直接传用户名
- 关键词订阅：使用 `search:` 前缀

示例：

```text
OpenAI
elonmusk
search:特朗普
search:马斯克
search:黑料
```

规则：

- 普通字符串会被视为 `user`
- `search:关键词` 会被视为 `keyword`
- 目标会做大小写归一化去重
- 空字符串会报错

## 4. 服务端功能划分

从接入角度看，服务端可以分成 4 类能力：

### 4.1 客户端注册

- 创建匿名客户端
- 生成 `apiKey` 和 `feedToken`

### 4.2 订阅管理

- 查询当前订阅
- 整体覆盖订阅
- 增量新增订阅
- 增量删除订阅

### 4.3 内容查询

- 拉取客户端订阅对应的最新内容
- 支持按目标、关键词、时间筛选

### 4.4 RSS 输出

- 将客户端订阅结果输出为标准 RSS
- 适合外部 RSS 阅读器、自动化系统或消息桥接

## 5. API 基础信息

### 5.1 Base URL

部署后以你的 Vercel 域名为准，例如：

```text
https://x2api-service.vercel.app
```

### 5.2 内容类型

JSON 接口请求体统一使用：

```http
Content-Type: application/json
```

### 5.3 通用错误格式

接口失败时通常返回：

```json
{
  "error": "错误说明"
}
```

常见状态码：

- `200` 请求成功
- `201` 创建成功
- `400` 请求参数错误或业务错误
- `401` 缺少凭证或凭证无效
- `500` 服务端内部错误

## 6. 客户端注册接口

### `POST /api/client/register`

创建一个新的匿名客户端。

#### 请求头

无需鉴权。

#### 请求体

```json
{
  "label": "ios-ghostbrowser"
}
```

字段说明：

- `label`
  - 可选
  - 用于给客户端打标签，便于区分设备或来源

#### 成功响应 `201`

```json
{
  "id": "4dd5b3c3-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "label": "ios-ghostbrowser",
  "apiKey": "x2d_xxxxxxxxxxxxxxxxxxxxx",
  "apiKeyPreview": "x2d_xxx...xxxx",
  "feedToken": "feed_xxxxxxxxxxxxxxxxxxxxx",
  "feedUrlPath": "/rss/feed_xxxxxxxxxxxxxxxxxxxxx.xml",
  "createdAt": "2026-05-18T14:21:00.000Z"
}
```

#### 说明

- `apiKey` 用于后续所有 JSON API
- `feedToken` 用于拼接 RSS URL
- 当前实现允许公开调用注册接口
- 如果后续不希望任意注册，建议再加一层后台控制或管理密钥

#### cURL 示例

```bash
curl -sS -X POST "https://x2api-service.vercel.app/api/client/register" \
  -H "Content-Type: application/json" \
  --data '{"label":"ios-ghostbrowser"}'
```

## 7. 订阅管理接口

以下接口都需要 `apiKey`。

推荐先设置环境变量：

```bash
export X2API_BASE="https://x2api-service.vercel.app"
export X2API_KEY="x2d_your_api_key"
```

### 7.1 查询订阅

#### `GET /api/subscriptions`

返回当前客户端的订阅列表。

#### 请求示例

```bash
curl -sS "$X2API_BASE/api/subscriptions" \
  -H "Authorization: Bearer $X2API_KEY"
```

#### 成功响应 `200`

```json
{
  "subscriptions": [
    {
      "id": "sub-uuid",
      "targetId": "target-uuid",
      "target": "search:特朗普",
      "source": "twitter",
      "kind": "keyword",
      "value": "特朗普",
      "category": "news",
      "tags": ["特朗普", "美国政治"],
      "createdAt": "2026-05-18T14:30:00.000Z"
    },
    {
      "id": "sub-uuid-2",
      "targetId": "target-uuid-2",
      "target": "elonmusk",
      "source": "twitter",
      "kind": "user",
      "value": "elonmusk",
      "category": null,
      "tags": [],
      "createdAt": "2026-05-18T14:31:00.000Z"
    }
  ]
}
```

### 7.2 覆盖订阅列表

#### `PUT /api/subscriptions`

用新的订阅数组整体替换原有订阅。

#### 请求体

```json
{
  "targets": [
    {
      "target": "search:特朗普",
      "category": "news",
      "tags": ["特朗普", "美国政治"]
    },
    {
      "target": "search:AI coding",
      "category": "tech",
      "tags": ["AI", "编程", "Claude Code"]
    },
    {
      "source": "youtube",
      "kind": "channel",
      "target": "UCE_M8A5yxnLfW0KghEeajjw",
      "category": "tech",
      "tags": ["YouTube"]
    }
  ]
}
```

#### cURL 示例

```bash
curl -sS -X PUT "$X2API_BASE/api/subscriptions" \
  -H "Authorization: Bearer $X2API_KEY" \
  -H "Content-Type: application/json" \
  --data '{"targets":[{"target":"search:特朗普","category":"news","tags":["特朗普","美国政治"]},{"source":"youtube","kind":"channel","target":"UCE_M8A5yxnLfW0KghEeajjw","category":"tech","tags":["YouTube"]}]}'
```

#### 成功响应

返回替换后的完整订阅列表：

```json
{
  "subscriptions": [
    {
      "id": "sub-uuid",
      "targetId": "target-uuid",
      "target": "search:特朗普",
      "source": "twitter",
      "kind": "keyword",
      "value": "特朗普",
      "category": "news",
      "tags": ["特朗普", "美国政治"],
      "createdAt": "2026-05-18T14:30:00.000Z"
    }
  ]
}
```

#### 说明

- 如果传空数组，会清空当前订阅
- 服务端会自动去重
- 新对象格式中 `category` 必填，使用 `/api/videos/categories` 返回的 `slug`，也兼容分类中文名
- `source` 支持 `twitter` 和 `youtube`；旧字符串目标默认按 `twitter` 解析
- `kind` 支持 `user`、`keyword`、`channel`；`youtube` 目前只支持 `channel`
- `target` 支持 `"search:关键词"`、用户名、`"youtube:UC..."`；对象格式下 YouTube channel 推荐传纯 `UC...` channel ID
- `tags` 是用户自由输入标签，服务端会 trim、去重，并限制单个目标最多 12 个标签
- 为兼容脚本和旧调用，`targets` 里仍可传字符串，例如 `"search:AI safety"`；字符串格式不会写入分类和标签

### 7.3 增量新增订阅

#### `POST /api/subscriptions`

在现有订阅基础上新增目标。

#### 请求体

```json
{
  "targets": [
    {
      "target": "search:黑料",
      "category": "news",
      "tags": ["爆料"]
    },
    {
      "target": "realDonaldTrump",
      "category": "politics",
      "tags": ["美国政治"]
    }
  ]
}
```

#### cURL 示例

```bash
curl -sS -X POST "$X2API_BASE/api/subscriptions" \
  -H "Authorization: Bearer $X2API_KEY" \
  -H "Content-Type: application/json" \
  --data '{"targets":[{"target":"search:黑料","category":"news","tags":["爆料"]},{"target":"realDonaldTrump","category":"politics","tags":["美国政治"]}]}'
```

#### 说明

- 已存在的目标不会重复创建
- 返回值是新增后的完整订阅列表

### 7.4 增量删除订阅

#### `DELETE /api/subscriptions`

从当前订阅中移除指定目标。

#### 请求体

```json
{
  "targets": ["search:黑料", "realDonaldTrump"]
}
```

#### cURL 示例

```bash
curl -sS -X DELETE "$X2API_BASE/api/subscriptions" \
  -H "Authorization: Bearer $X2API_KEY" \
  -H "Content-Type: application/json" \
  --data '{"targets":["search:黑料","realDonaldTrump"]}'
```

#### 说明

- 删除不存在的目标时，接口仍会返回当前最新订阅列表

## 8. 内容查询接口

### `GET /api/items`

查询当前客户端订阅范围内的抓取内容。

#### 请求头

需要 `apiKey`。

#### 查询参数

- `limit`
  - 可选
  - 每次返回条数
  - 默认 `20`
  - 范围 `1-100`
- `cursor`
  - 可选
  - 上一页返回的游标
  - 不传时返回第一页
- `target`
  - 可选
  - 按目标精确过滤
  - 例如 `search:特朗普` 或 `elonmusk`
- `keyword`
  - 可选
  - 在内容、原文、翻译、作者字段里模糊搜索
- `tag`
  - 可选，可重复，也支持逗号分隔
  - 按标签过滤，例如 `tag=AI&tag=编程` 或 `tag=AI,编程`
- `category`
  - 可选，可重复，也支持逗号分隔
  - 按分类过滤，支持分类 `slug` 或显示名，例如 `category=war,finance`
- `since`
  - 可选
  - 只返回 `stored_at >= since` 的记录
  - 建议传 ISO8601 时间

#### 请求示例

```bash
curl -sS "$X2API_BASE/api/items?limit=10" \
  -H "Authorization: Bearer $X2API_KEY"
```

按目标过滤：

```bash
curl -sS "$X2API_BASE/api/items?target=search:%E7%89%B9%E6%9C%97%E6%99%AE&limit=10" \
  -H "Authorization: Bearer $X2API_KEY"
```

按关键词过滤：

```bash
curl -sS "$X2API_BASE/api/items?keyword=%E9%80%89%E4%B8%BE&limit=10" \
  -H "Authorization: Bearer $X2API_KEY"
```

按多个分类过滤：

```bash
curl -sS "$X2API_BASE/api/items?category=war,finance&limit=10" \
  -H "Authorization: Bearer $X2API_KEY"
```

按多个标签过滤：

```bash
curl -sS "$X2API_BASE/api/items?tag=AI&tag=%E7%BC%96%E7%A8%8B&limit=10" \
  -H "Authorization: Bearer $X2API_KEY"
```

按时间过滤：

```bash
curl -sS "$X2API_BASE/api/items?since=2026-05-18T00:00:00.000Z&limit=20" \
  -H "Authorization: Bearer $X2API_KEY"
```

使用上一页返回的游标继续读取：

```bash
curl -sS "$X2API_BASE/api/items?limit=10&cursor=eyJ2IjoxLCJwYXlsb2FkIjp7Li4ufX0" \
  -H "Authorization: Bearer $X2API_KEY"
```

#### 成功响应 `200`

```json
{
  "items": [
    {
      "id": "item-uuid",
      "target": "search:特朗普",
      "source": "twitter",
      "kind": "keyword",
      "category": "news",
      "isSensitive": false,
      "tags": ["特朗普", "美国政治"],
      "author": "some_author",
      "fullname": "Some Author",
      "title": null,
      "content": "抓取后的主内容",
      "rawContent": "原始内容",
      "translatedContent": "翻译内容",
      "link": "https://nitter.example/xxx",
      "xUrl": "https://x.com/xxx/status/123",
      "images": [
        "https://image.example/1.jpg"
      ],
      "videoUrl": null,
      "expiresAt": "2099-12-31T23:59:59.000Z",
      "videoUrlExpiresAt": "2099-12-31T23:59:59.000Z",
      "publishedAt": "2026-05-18T14:20:00.000Z",
      "storedAt": "2026-05-18T14:21:10.000Z",
      "guid": "1891234567890123456",
      "isRetweet": false
    }
  ],
  "pagination": {
    "limit": 10,
    "nextCursor": "eyJ2IjoxLCJwYXlsb2FkIjp7InNvcnRUaW1lIjoiMjAyNi0wNS0xOFQxNDoyMDowMC4wMDBaIiwic3RvcmVkQXQiOiIyMDI2LTA1LTE4VDE0OjIxOjEwLjAwMFoiLCJpZCI6Iml0ZW0tdXVpZCJ9fQ",
    "hasMore": true
  }
}
```

#### 字段说明

- `target`
  - 当前命中的订阅目标
- `kind`
  - `user`、`keyword` 或 `channel`
- `source`
  - `twitter` 或 `youtube`
- `category`
  - 目标分类 slug，来自 `target_profiles.category`
- `isSensitive`
  - 分类是否为敏感分类，来自 `categories.is_sensitive`
- `tags`
  - item 标签和目标画像标签的合并结果
- `author`
  - 作者账号名，通常是 `@username`
- `fullname`
  - 作者昵称/显示名
- `content`
  - 主内容
- `rawContent`
  - 原始内容
- `translatedContent`
  - 翻译内容
- `link`
  - 来源页链接，通常是镜像页
- `xUrl`
  - 原始 X 链接
- `images`
  - 图片 URL 数组
- `videoUrl`
  - 视频 URL
- `expiresAt`
  - 记录业务保留期限；YouTube item 通常为发布时间后 72 小时，Twitter 默认为长期有效
- `videoUrlExpiresAt`
  - 播放 URL 过期时间；客户端可用它判断当前播放链接是否需要友好跳过
- `publishedAt`
  - 原始发布时间
- `storedAt`
  - 写入数据库时间
- `guid`
  - 原始内容唯一标识
- `pagination.limit`
  - 当前请求的返回条数上限
- `pagination.nextCursor`
  - 下一页游标，没有更多数据时为 `null`
- `pagination.hasMore`
  - 是否还有下一页

#### 排序规则

按以下稳定顺序倒序返回：

1. `publishedAt`
2. `storedAt`
3. `id`

下一页会从上一页最后一条记录之后继续读取，不支持直接跳到任意页。

## 9. RSS 订阅接口

### `GET /rss/{feedToken}.xml`

返回当前客户端的聚合 RSS 订阅流。

#### 鉴权方式

- 不需要 `apiKey`
- `feedToken` 本身就是访问凭证

#### 查询参数

- `limit`
  - 可选
  - 默认 `50`
  - 范围 `1-100`

#### URL 示例

```text
https://x2api-service.vercel.app/rss/feed_xxxxxxxxxxxxxxxxxxxxx.xml
```

带条数限制：

```text
https://x2api-service.vercel.app/rss/feed_xxxxxxxxxxxxxxxxxxxxx.xml?limit=20
```

#### cURL 示例

```bash
curl -sS "https://x2api-service.vercel.app/rss/feed_xxxxxxxxxxxxxxxxxxxxx.xml?limit=20"
```

#### 响应头

```http
Content-Type: application/rss+xml; charset=utf-8
Cache-Control: s-maxage=300, stale-while-revalidate=300
```

#### RSS 输出说明

每条 `<item>` 主要包含：

- `<title>`
  - 优先使用抓取内容标题
  - 没有标题时使用 `{target} update`
- `<link>`
  - 优先使用 `xUrl`
  - 其次使用 `link`
- `<guid>`
  - 由 `target + guid` 组合
- `<pubDate>`
  - 使用 `publishedAt`，没有则退回 `storedAt`
- `<description>`
  - 包含作者、订阅目标和正文摘要

#### RSS 示例

```xml
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>x2ding feed feed_xxxxx</title>
    <description>x2ding aggregated subscription feed</description>
    <lastBuildDate>Mon, 18 May 2026 14:30:00 GMT</lastBuildDate>
    <generator>x2ding</generator>
    <item>
      <title>search:特朗普 update</title>
      <link>https://x.com/xxx/status/123</link>
      <guid isPermaLink="false">search:特朗普:1891234567890123456</guid>
      <pubDate>Mon, 18 May 2026 14:20:00 GMT</pubDate>
      <description>Author: some_author ...</description>
    </item>
  </channel>
</rss>
```

### 9.1 RSS 的接入建议

适合：

- RSS 阅读器
- 自动化通知桥接
- 第三方订阅系统
- 不需要复杂交互的只读消费端

不太适合：

- 需要增删订阅的 App 主交互
- 需要本地状态同步的客户端
- 需要精细筛选、分页、去重控制的原生应用

更推荐的方式：

- App 主业务走 JSON API
- RSS 作为导出能力或兼容能力

## 10. 推荐接入流程

### 10.1 App / 客户端

1. 调用 `POST /api/client/register`
2. 保存 `apiKey` 到安全存储
3. 保存 `feedToken` 或完整 RSS URL
4. 用 `PUT /api/subscriptions` 初始化订阅
5. 用 `GET /api/items` 拉取最新内容
6. 如果需要开放给 RSS 阅读器，再展示 RSS URL

### 10.2 第三方 RSS 阅读器

1. 后台先创建客户端
2. 后台写入订阅
3. 将 `/rss/{feedToken}.xml` 提供给 RSS 阅读器

## 11. 实际 cURL 样例

### 11.1 注册客户端

```bash
curl -sS -X POST "https://x2api-service.vercel.app/api/client/register" \
  -H "Content-Type: application/json" \
  --data '{"label":"demo-client"}'
```

### 11.2 设置订阅

```bash
curl -sS -X PUT "https://x2api-service.vercel.app/api/subscriptions" \
  -H "Authorization: Bearer x2d_your_api_key" \
  -H "Content-Type: application/json" \
  --data '{"targets":["search:特朗普","search:马斯克","search:黑料"]}'
```

### 11.3 查询内容

```bash
curl -sS "https://x2api-service.vercel.app/api/items?limit=10" \
  -H "Authorization: Bearer x2d_your_api_key"
```

### 11.4 查看 RSS

```bash
curl -sS "https://x2api-service.vercel.app/rss/feed_xxxxxxxxxxxxxxxxxxxxx.xml"
```

## 12. 当前实现边界

### 12.1 优点

- 架构简单
- 易部署
- API key 模式适合匿名客户端
- RSS 天然兼容外部生态

### 12.2 当前限制

- 采集稳定性依赖第三方 Nitter/X 来源
- 当前没有传统登录、权限分级和配额体系
- `/api/items` 当前使用 cursor 分页，不提供总页数，也不支持直接跳到任意页
- RSS URL 若泄漏，任何拿到链接的人都可以读取该客户端订阅流
- `POST /api/client/register` 当前默认开放

## 13. 建议的后续增强

- 增加客户端禁用、轮换 `apiKey`、轮换 `feedToken`
- 增加管理后台或管理员接口
- 为更多列表接口复用统一 cursor 分页能力
- 增加 Webhook / Push 推送
- 增加订阅分组与标签
- 为开放注册增加简单风控或管理令牌

## 14. 代码对应位置

- 服务端路由：
  - `service/src/app/api/client/register/route.ts`
  - `service/src/app/api/subscriptions/route.ts`
  - `service/src/app/api/items/route.ts`
  - `service/src/app/rss/[feedToken]/route.ts`
- 鉴权：
  - `service/src/lib/auth.ts`
- 订阅逻辑：
  - `service/src/lib/subscription-service.ts`
- 查询逻辑：
  - `service/src/lib/item-service.ts`
- RSS 生成：
  - `service/src/lib/rss.ts`
- 数据库结构：
  - `shared/schema.sql`

## 12. 视频 Feed 相关接口

视频 Feed 是旁路能力，不改变现有订阅、JSON 查询和 RSS 接口。它复用 `items.video_url` 中已有的视频内容，并通过标签、公共池和行为事件提供刷视频体验。

### 12.1 数据来源

- 用户订阅内容：来自当前客户端订阅目标下的 `items`。
- 公共视频池：来自 `target_profiles.is_public_pool = true` 的系统目标。
- 标签：来自 `item_tags`，也可继承 `target_profiles.tags`。
- 分类：来自 `categories` 受控分类表，也可通过 `target_profiles.category` 关联到目标。

### 12.2 `GET /api/videos/feed`

返回视频流。

请求头：

```http
Authorization: Bearer x2d_xxx
```

查询参数：

- `limit`：默认 `10`，最大 `20`
- `cursor`：上一页返回的游标
- `tag`：按标签过滤，可重复，也支持逗号分隔
- `category`：按分类过滤，可重复，也支持逗号分隔；支持分类 `slug` 或显示名
- `source`：`mixed`、`user`、`public`，默认 `mixed`

响应：

```json
{
  "items": [
    {
      "id": "item_uuid",
      "videoKey": "https://video.twimg.com/ext_tw_video/.../vid/avc1/720x1280/video.mp4",
      "videoUrl": "https://video.twimg.com/...",
      "videoUrlExpiresAt": "2099-12-31T23:59:59.000Z",
      "expiresAt": "2099-12-31T23:59:59.000Z",
      "coverUrl": "https://pbs.twimg.com/...",
      "title": "...",
      "caption": "...",
      "author": "@user",
      "fullname": "User",
      "xUrl": "https://x.com/...",
      "link": "https://nitter...",
      "publishedAt": "2026-05-21T00:00:00.000Z",
      "storedAt": "2026-05-21T00:01:00.000Z",
      "source": "twitter",
      "target": "search:AI video",
      "kind": "keyword",
      "category": "tech",
      "tags": ["AI", "科技"],
      "stats": {
        "impressions": 0,
        "plays": 0,
        "finishes": 0,
        "likes": 0,
        "dislikes": 0,
        "skips": 0,
        "shares": 0,
        "score": 0
      }
    }
  ],
  "pagination": {
    "limit": 10,
    "nextCursor": null,
    "hasMore": false
  }
}
```

说明：

- `videoKey` 是稳定视频身份。YouTube 使用 `youtube:<videoId>`，客户端应用它做缓存、去重和已看状态，不应使用会刷新的 `videoUrl` 做身份。
- `expiresAt` 是业务数据保留期限；`videoUrlExpiresAt` 是播放 URL 过期时间。
- YouTube 视频只有 `expiresAt > now` 且 `videoUrlExpiresAt > now + 10 minutes` 时才会返回；客户端如果回到已过期的旧数据，应提示并自动跳过。

### 12.3 `POST /api/videos/events`

上报刷视频行为，并同步聚合到 `video_stats`。

请求体：

```json
{
  "itemId": "item_uuid",
  "eventType": "play",
  "watchMs": 3200,
  "metadata": {}
}
```

`eventType` 支持：

- `impression`
- `play`
- `finish`
- `like`
- `dislike`
- `skip`
- `share`

响应：

```json
{
  "ok": true
}
```

### 12.4 `GET /api/videos/tags`

返回可用标签。

```json
{
  "tags": [
    {
      "name": "AI",
      "type": "topic",
      "weight": 10
    }
  ]
}
```

### 12.5 `GET /api/videos/categories`

返回视频分类。分类由服务端维护，标签仍可由用户自由输入。

```json
{
  "categories": [
    {
      "slug": "tech",
      "name": "科技",
      "weight": 240,
      "isSensitive": false,
      "defaultHidden": false
    },
    {
      "slug": "war",
      "name": "军事",
      "weight": 210,
      "isSensitive": false,
      "defaultHidden": false
    },
    {
      "slug": "finance",
      "name": "金融",
      "weight": 200,
      "isSensitive": false,
      "defaultHidden": false
    },
    {
      "slug": "adult",
      "name": "成人",
      "weight": 20,
      "isSensitive": true,
      "defaultHidden": true
    }
  ]
}
```

### 12.6 标签脚本

`scripts/backfill_video_tags.py` 使用远程词库给历史视频打标签，默认词库为：

```text
https://raw.githubusercontent.com/M1Z2105a4/resource/main/ttt_lexicon.json
```

试运行：

```bash
DATABASE_URL=... python3 scripts/backfill_video_tags.py --limit 100
```

写入：

```bash
DATABASE_URL=... python3 scripts/backfill_video_tags.py --apply --limit 100
```

### 12.6 清理脚本

视频 Feed 清理采用分层保留策略，默认规则：

- `feed_events` 明细事件保留 7 天
- 非视频 `items` 保留 14 天
- 低分视频保留 7 天，默认 `score <= -5`
- 普通视频保留 30 天
- 公共池视频保留 60 天
- 高分视频保留 90 天，默认 `score >= 20`
- 主内容表超量保护默认保留 100000 条，并优先保留高分视频、公共池视频和普通视频

删除 `items` 时，关联的 `item_tags`、`video_stats`、`feed_events` 会通过外键级联清理。

试运行：

```bash
DATABASE_URL=... python3 scripts/cleanup_video_feed_data.py
```

写入删除：

```bash
DATABASE_URL=... python3 scripts/cleanup_video_feed_data.py --apply
```

可调整阈值：

```bash
DATABASE_URL=... python3 scripts/cleanup_video_feed_data.py \
  --apply \
  --event-days 7 \
  --non-video-days 14 \
  --low-score-video-days 7 \
  --video-days 30 \
  --public-video-days 60 \
  --high-score-video-days 90 \
  --low-score-threshold -5 \
  --high-score-threshold 20
```

## 13. YouTube Collector 命令

- `python collector/twitter_monitor.py monitor` 只抓取 Twitter/X 目标，不处理 YouTube，避免 YouTube resolver 影响 Twitter 抓取。
- `python collector/twitter_monitor.py monitor-youtube` 单独抓取 `source = youtube` 的 channel RSS，并将 72 小时内的新视频写入解析队列。
- `python collector/twitter_monitor.py refresh-youtube-playback-urls --limit 30 --refresh-window-minutes 90 --critical-window-minutes 15` 单独刷新即将过期的 YouTube 播放 URL 并补处理未解析队列。
- 对应 workflow：`youtube-monitor.yml` 负责 RSS 抓取，`youtube-playback-refresh.yml` 负责播放 URL 保鲜；当前两者都配置为每 10 分钟运行一次，也支持手动 `workflow_dispatch`。
