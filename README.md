# Twitter (X) 监控归档工具

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![GitHub Actions](https://img.shields.io/badge/GitHub%20Actions-Enabled-brightgreen)](https://github.com/features/actions)

这是一个基于 Playwright Stealth 的 Twitter/X 监控工具。它不再把结果推送到钉钉，而是把抓到的动态保存到仓库本地数据文件里，支持：

- 定时抓取并归档到 `data/tweets.jsonl`
- 自动记录每个订阅目标的最新推文 ID，避免重复保存
- 定时清理历史数据
- 在 GitHub Actions 里手动查询历史记录
- 动态订阅，不再依赖写死的 `TWITTER_USER` Secret

## 数据文件

- `data/subscriptions.json`: 订阅列表
- `data/last_id.json`: 每个目标最近一次保存的推文 ID
- `data/tweets.jsonl`: 历史归档，JSON Lines 格式
- `data/query_results/`: 查询结果输出目录
- `instances.json`: 健康 Nitter 实例缓存

## 运行方式

### 1. 定时监控

GitHub Actions 里的 `Twitter Monitor` 会按计划运行：

- 北京时间 08:00 - 22:00 每 30 分钟一次
- 其余时间每 2 小时一次

执行命令：

```bash
python twitter_monitor.py monitor
```

默认会在每次监控后自动清理，只保留最近 30 天、最多 2000 条记录。

### 2. 动态订阅

通过 `Manage Subscriptions And Query` workflow 管理订阅，不再需要配置 `TWITTER_USER` Secret。

支持的目标格式：

- 单个用户: `elonmusk`
- 多个用户: `elonmusk,OpenAI,AnthropicAI`
- 关键词搜索: `search:AI safety`
- 混合: `elonmusk,search:#ChatGPT`

本地命令也可以直接用：

```bash
python twitter_monitor.py subscribe add --targets "elonmusk,OpenAI"
python twitter_monitor.py subscribe remove --targets "OpenAI"
python twitter_monitor.py subscribe set --targets "elonmusk,search:AI safety"
python twitter_monitor.py subscribe list
```

### 3. 查询历史

可以在 GitHub Actions 里手动触发 `Manage Subscriptions And Query`，选择 `query` 动作并填写筛选条件。

本地命令示例：

```bash
python twitter_monitor.py query --target "elonmusk" --limit 10
python twitter_monitor.py query --keyword "grok" --since "2026-05-01T00:00:00+00:00"
python twitter_monitor.py query --keyword "OpenAI" --output data/query_results/query-result.json
```

查询结果会：

- 打印到 Actions 日志
- 输出为 `data/query_results/query-result.json`
- 在 GitHub Actions 中作为 artifact 上传，方便下载

### 4. 定时清理

`Cleanup Stored Tweets` workflow 每天运行一次，也支持手动指定：

- `retention_days`
- `max_records`

本地命令示例：

```bash
python twitter_monitor.py cleanup --retention-days 30 --max-records 2000
```

## GitHub Actions 工作流

### `Twitter Monitor`

定时抓取订阅目标，并将下列文件提交回仓库：

- `data/last_id.json`
- `data/tweets.jsonl`
- `data/subscriptions.json`

### `Manage Subscriptions And Query`

支持五类动作：

- `subscribe_add`
- `subscribe_remove`
- `subscribe_set`
- `subscribe_list`
- `query`

### `Cleanup Stored Tweets`

定时或手动清理 `data/tweets.jsonl`。

### `Update Nitter Instances`

定时刷新 `instances.json`。

## 首次部署

1. Fork 仓库
2. 打开仓库的 `Settings -> Actions -> General`
3. 将 `Workflow permissions` 设为 `Read and write permissions`
4. 在 `Actions` 页先手动运行一次：
   - `Update Nitter Instances`
   - `Manage Subscriptions And Query`，用 `subscribe_set` 配置你的初始订阅
   - `Twitter Monitor`

## 本地运行

```bash
pip install -r requirements.txt
playwright install chromium
python twitter_monitor.py subscribe set --targets "elonmusk,search:AI safety"
python twitter_monitor.py monitor
python twitter_monitor.py query --limit 5
```

## 兼容说明

如果仓库里之前还在使用：

- 根目录 `last_id.json`
- `TWITTER_USER` 环境变量

脚本会在首次运行时自动迁移到 `data/` 目录结构。

## 注意

- 仓库现在会把归档结果提交回 git 历史，适合轻量归档和查询。
- 如果后续数据量明显变大，更适合迁到 SQLite 或外部 KV/数据库。
- `query` 当前是查归档结果，不是实时在线搜索。

## 许可证

本项目采用 [MIT License](LICENSE) 开源。
