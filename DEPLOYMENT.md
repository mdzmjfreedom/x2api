# GitHub Actions 部署指南

这个版本已经不再依赖钉钉，也不再要求把订阅目标写死在 `TWITTER_USER` Secret 里。现在的方式是：

- 定时抓取并保存到仓库本地数据文件
- 通过 GitHub Actions 手动管理订阅
- 通过 GitHub Actions 手动查询历史
- 通过定时任务清理历史

## 第一步：Fork 仓库

先把仓库 Fork 到你自己的 GitHub 账号下。

## 第二步：开启 Actions 写权限

进入：

`Settings -> Actions -> General -> Workflow permissions`

选择：

`Read and write permissions`

这是必须的，因为监控结果、订阅列表、清理后的数据都会自动提交回仓库。

## 第三步：首次初始化

进入 `Actions` 页面，按顺序手动运行：

1. `Update Nitter Instances`
2. `Manage Subscriptions And Query`
3. `Twitter Monitor`

### 3.1 配置订阅

手动运行 `Manage Subscriptions And Query` 时：

- `action`: 选 `subscribe_set`
- `targets`: 填你的订阅目标

示例：

```text
elonmusk,OpenAI,AnthropicAI
```

或者：

```text
elonmusk,search:AI safety,search:#ChatGPT
```

支持格式：

- 单个用户：`elonmusk`
- 多个用户：`elonmusk,OpenAI,AnthropicAI`
- 关键词搜索：`search:AI safety`
- 混合：`elonmusk,search:#ChatGPT`

### 3.2 验证监控

运行 `Twitter Monitor`，它会：

- 读取 `data/subscriptions.json`
- 抓取每个订阅目标的最新动态
- 保存到 `data/tweets.jsonl`
- 更新 `data/last_id.json`
- 自动提交到仓库

## 第四步：查询历史

手动运行 `Manage Subscriptions And Query`，将：

- `action` 设为 `query`

可选输入：

- `target`: 精确查询某个订阅目标
- `keyword`: 按内容关键字查
- `since`: 起始时间，ISO 8601
- `until`: 结束时间，ISO 8601
- `limit`: 返回条数

示例：

### 查询某个账号最近 10 条

- `action`: `query`
- `target`: `OpenAI`
- `limit`: `10`

### 查询包含关键词的记录

- `action`: `query`
- `keyword`: `GPT-5`

### 按时间范围查询

- `action`: `query`
- `since`: `2026-05-01T00:00:00+00:00`
- `until`: `2026-05-18T23:59:59+00:00`

运行后你会看到：

- 日志里打印结果摘要
- 一个名为 `query-result` 的 artifact，可直接下载 JSON

## 第五步：清理历史

`Cleanup Stored Tweets` 会每天自动运行一次，也可以手动触发。

参数：

- `retention_days`: 保留最近多少天
- `max_records`: 最多保留多少条

例如：

- 保留 15 天
- 最多保留 500 条

就填：

- `retention_days`: `15`
- `max_records`: `500`

## 常见操作

### 新增订阅

运行 `Manage Subscriptions And Query`：

- `action`: `subscribe_add`
- `targets`: `vercel,search:Next.js`

### 删除订阅

- `action`: `subscribe_remove`
- `targets`: `vercel`

### 全量覆盖订阅

- `action`: `subscribe_set`
- `targets`: `OpenAI,AnthropicAI,search:AI safety`

### 查看当前订阅

- `action`: `subscribe_list`

## 旧配置兼容

如果你之前配置过：

- `TWITTER_USER`
- 根目录 `last_id.json`

脚本会自动迁移，但后续建议全部改用：

- `data/subscriptions.json`
- `data/last_id.json`

## 不再需要的 Secret

这次改造后，这几个都不是必需项了：

- `TWITTER_USER`
- `DINGTALK_WEBHOOK`
- `CLOUDFLARE_PROXY`
- `IMGBB_API_KEY`

除非你后面想继续扩展图片代理或消息推送，否则现在可以不配。
