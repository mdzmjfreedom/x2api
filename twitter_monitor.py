from __future__ import annotations

import argparse
import json
import os
import random
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, unquote, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LAST_ID_FILE = DATA_DIR / "last_id.json"
SUBSCRIPTIONS_FILE = DATA_DIR / "subscriptions.json"
TWEETS_FILE = DATA_DIR / "tweets.jsonl"
QUERY_RESULTS_DIR = DATA_DIR / "query_results"
INSTANCES_FILE = BASE_DIR / "instances.json"
LEGACY_LAST_ID_FILE = BASE_DIR / "last_id.json"

DEFAULT_RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "30"))
DEFAULT_MAX_RECORDS = int(os.environ.get("MAX_RECORDS", "2000"))
AUTO_TRANSLATE = os.environ.get("TRANSLATE_CONTENT", "false").lower() == "true"

# 兼容旧配置，首次迁移时仍可从 Secret 中读取订阅目标
LEGACY_USERS_ENV = os.environ.get("TWITTER_USER", "")

NITTER_INSTANCES = [
    "https://xcancel.com",
    "https://nitter.privacyredirect.com",
    "https://nitter.poast.org",
    "https://nitter.hu",
    "https://nitter.moomoo.me",
    "https://nitter.net",
]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat()


def parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def ensure_data_dirs() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    QUERY_RESULTS_DIR.mkdir(exist_ok=True)


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:
        print(f"[系统] 读取 {path.name} 失败: {exc}")
        return default


def save_json(path: Path, payload) -> None:
    ensure_data_dirs()
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def normalize_target(target: str) -> str:
    return target.strip()


def parse_targets(raw: str | list[str] | None) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        parts = raw
    else:
        parts = re.split(r"[\n,]+", raw)
    targets = []
    seen = set()
    for part in parts:
        target = normalize_target(part)
        if not target or target in seen:
            continue
        seen.add(target)
        targets.append(target)
    return targets


def load_subscriptions() -> list[str]:
    ensure_data_dirs()
    if SUBSCRIPTIONS_FILE.exists():
        payload = load_json(SUBSCRIPTIONS_FILE, default={})
        targets = parse_targets(payload.get("targets", [])) if isinstance(payload, dict) else parse_targets(payload)
        return targets

    legacy_targets = parse_targets(LEGACY_USERS_ENV)
    if legacy_targets:
        print("[系统] 发现旧版 TWITTER_USER 配置，已作为初始订阅导入")
        save_subscriptions(legacy_targets)
        return legacy_targets

    return []


def save_subscriptions(targets: list[str]) -> None:
    payload = {
        "targets": targets,
        "updated_at": now_iso(),
    }
    save_json(SUBSCRIPTIONS_FILE, payload)


def load_last_ids() -> dict[str, str]:
    ensure_data_dirs()
    data = load_json(LAST_ID_FILE, default={})
    if data:
        return data

    legacy = load_json(LEGACY_LAST_ID_FILE, default={})
    if legacy:
        print("[系统] 发现旧版 last_id.json，已迁移到 data/last_id.json")
        save_last_ids(legacy)
        return legacy

    return {}


def save_last_ids(last_ids: dict[str, str]) -> None:
    save_json(LAST_ID_FILE, last_ids)


def load_records() -> list[dict]:
    ensure_data_dirs()
    if not TWEETS_FILE.exists():
        return []

    records = []
    with TWEETS_FILE.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(f"[系统] 跳过损坏记录: {exc}")
    return records


def append_record(record: dict) -> None:
    ensure_data_dirs()
    with TWEETS_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def record_key(record: dict) -> str:
    return f"{record.get('target', '')}:{record.get('guid', '')}"


def cleanup_records(retention_days: int, max_records: int) -> dict[str, int]:
    records = load_records()
    before_count = len(records)
    if before_count == 0:
        return {"before": 0, "after": 0, "deleted": 0}

    threshold = now_utc() - timedelta(days=retention_days)
    kept = []
    for record in records:
        stored_at = parse_datetime(record.get("stored_at", ""))
        if stored_at is None or stored_at >= threshold:
            kept.append(record)

    kept.sort(
        key=lambda item: parse_datetime(item.get("stored_at", "")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    if max_records > 0:
        kept = kept[:max_records]
    kept.sort(
        key=lambda item: parse_datetime(item.get("stored_at", "")) or datetime.min.replace(tzinfo=timezone.utc)
    )

    ensure_data_dirs()
    if kept:
        with TWEETS_FILE.open("w", encoding="utf-8") as fh:
            for record in kept:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    elif TWEETS_FILE.exists():
        TWEETS_FILE.unlink()

    return {"before": before_count, "after": len(kept), "deleted": before_count - len(kept)}


def get_random_user_agent():
    ua_list = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/121.0.0.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    ]
    return random.choice(ua_list)


def load_instances():
    if INSTANCES_FILE.exists():
        try:
            with INSTANCES_FILE.open("r", encoding="utf-8") as fh:
                instances = json.load(fh)
            if instances and isinstance(instances, list):
                print(f"[系统] 成功从本地缓存加载 {len(instances)} 个实例")
                return instances
        except Exception as exc:
            print(f"[系统] 加载实例缓存失败: {exc}")

    print("[系统] 缓存不存在或损坏，采用内置兜底实例列表")
    return NITTER_INSTANCES


def get_original_image_url(nitter_url: str) -> str:
    try:
        if "pbs.twimg.com" in nitter_url:
            return nitter_url

        if "/pic/enc/" in nitter_url:
            enc_part = nitter_url.split("/pic/enc/")[-1].split("?")[0]
            try:
                decoded = bytes.fromhex(enc_part).decode("utf-8")
                if "pbs.twimg.com" in decoded:
                    return decoded
            except Exception:
                pass

        path = unquote(nitter_url)
        if "/media/" in path:
            media_part = path.split("/media/")[-1].split("?")[0]
            if "." in media_part:
                media_id, ext = media_part.rsplit(".", 1)
                ext = ext.split("&")[0].split("?")[0]
                return f"https://pbs.twimg.com/media/{media_id}?format={ext}&name=large"

        match = re.search(r"(pbs\.twimg\.com/media/[^?&]+)", path)
        if match:
            return "https://" + match.group(1)
    except Exception as exc:
        print(f"[图片解析] 还原 URL 失败 {nitter_url}: {exc}")

    return nitter_url


def translate_text(text: str, target_lang: str = "zh-CN") -> str | None:
    if not text or not text.strip():
        return None

    try:
        resp = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={
                "client": "gtx",
                "sl": "auto",
                "tl": target_lang,
                "dt": "t",
                "q": text,
            },
            headers={"User-Agent": get_random_user_agent()},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if data and data[0]:
            return "".join(part[0] for part in data[0] if part and part[0])
    except Exception as exc:
        print(f"[翻译] 失败: {exc}")
    return None


def nitter_to_x_url(nitter_url: str) -> str:
    if not nitter_url:
        return ""
    parsed = urlparse(nitter_url)
    return urlunparse(("https", "x.com", parsed.path, "", parsed.query, ""))


def scrape_nitter_with_playwright(target: str, dynamic_instances: list[str] | None = None) -> list[dict]:
    try:
        from playwright.sync_api import sync_playwright
        from playwright_stealth import stealth_sync
    except ModuleNotFoundError as exc:
        print(f"[{target}] 缺少抓取依赖: {exc}")
        return []

    is_search = target.startswith("search:")
    keyword = target[7:] if is_search else target

    instances = list(dynamic_instances or NITTER_INSTANCES)
    if len(instances) > 5:
        top_5 = instances[:5]
        random.shuffle(top_5)
        others = instances[5:]
        random.shuffle(others)
        instances = top_5 + others
    else:
        random.shuffle(instances)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for instance in instances:
            context = None
            try:
                context = browser.new_context(
                    user_agent=get_random_user_agent(),
                    viewport={"width": 1280, "height": 720},
                )
                page = context.new_page()
                stealth_sync(page)

                if is_search:
                    url = f"{instance.rstrip('/')}/search?f=tweets&q={quote(keyword)}"
                else:
                    url = f"{instance.rstrip('/')}/{keyword}"

                print(f"[{target}] 正在加载: {url}")
                try:
                    response = page.goto(url, wait_until="networkidle", timeout=45000)
                    if response and response.status == 403:
                        print(f"[{target}] 访问 {instance} 被拒 (403 Forbidden)")
                        context.close()
                        context = None
                        continue
                except Exception as exc:
                    print(f"[{target}] 加载 {instance} 超时或失败: {exc}")
                    context.close()
                    context = None
                    continue

                challenge_keywords = ["Verifying your browser", "Just a moment", "Checking your browser"]
                for _ in range(5):
                    content = page.content()
                    if any(keyword in content for keyword in challenge_keywords):
                        page.wait_for_timeout(5000)
                    else:
                        break

                soup = BeautifulSoup(page.content(), "html.parser")
                items = soup.select(".timeline-item")
                if not items:
                    print(f"[{target}] 在实例 {instance} 上未发现推文内容")
                    context.close()
                    context = None
                    continue

                valid_tweets = []
                for item in items[:20]:
                    if item.select_one(".pinned") is not None:
                        print(f"[{target}] 发现置顶推文，跳过")
                        continue

                    is_retweet = item.select_one(".retweet-header") is not None
                    images = []
                    for img in item.select(".attachment.image img, .tweet-image img, .still-image img, .attachments img"):
                        if any(cls in str(img.parent.get("class", [])) for cls in ["avatar", "profile"]):
                            continue
                        src = img.get("src", "")
                        if not src or "emoji" in src.lower() or "hashtag_click" in src:
                            continue

                        if src.startswith("//"):
                            full_src = "https:" + src
                        elif src.startswith("/"):
                            full_src = instance.rstrip("/") + src
                        else:
                            full_src = src
                        images.append(get_original_image_url(full_src))

                    video_url = None
                    try:
                        video_el = item.select_one("video source") or item.select_one("video")
                        if video_el:
                            poster_el = item.select_one("video")
                            if poster_el:
                                poster = poster_el.get("poster", "")
                                if poster:
                                    if poster.startswith("//"):
                                        full_poster = "https:" + poster
                                    elif poster.startswith("/"):
                                        full_poster = instance.rstrip("/") + poster
                                    else:
                                        full_poster = poster
                                    full_poster = get_original_image_url(full_poster)
                                    if full_poster not in images:
                                        images.append(full_poster)

                            v_src = video_el.get("src", "")
                            if v_src:
                                if v_src.startswith("//"):
                                    video_url = "https:" + v_src
                                elif v_src.startswith("/"):
                                    video_url = instance.rstrip("/") + v_src
                                else:
                                    video_url = v_src
                    except Exception as exc:
                        print(f"[{target}] 视频提取异常: {exc}")

                    content_el = item.select_one(".tweet-content")
                    link_el = item.select_one(".tweet-link")
                    date_el = item.select_one(".tweet-date a")
                    author_el = item.select_one(".username")
                    if not content_el or not link_el:
                        continue

                    link_href = link_el.get("href", "")
                    tweet_id = link_href.split("/status/")[-1].split("#")[0] if "/status/" in link_href else link_href
                    nitter_link = instance.rstrip("/") + link_href
                    raw_content = content_el.get_text(strip=True)
                    clean_content = raw_content.replace("€∋", "").strip()

                    tweet = {
                        "target": target,
                        "target_type": "search" if is_search else "user",
                        "target_value": keyword,
                        "content": clean_content,
                        "raw_content": raw_content,
                        "translated_content": translate_text(clean_content) if AUTO_TRANSLATE else None,
                        "link": nitter_link,
                        "x_url": nitter_to_x_url(nitter_link),
                        "published": date_el.get("title", "") if date_el else "Unknown Time",
                        "author": author_el.get_text(strip=True) if author_el else keyword,
                        "guid": tweet_id,
                        "is_retweet": is_retweet,
                        "images": images,
                        "video_url": video_url,
                        "stored_at": now_iso(),
                        "source_instance": instance,
                    }
                    valid_tweets.append(tweet)

                if valid_tweets:
                    newest_id = valid_tweets[0]["guid"]
                    print(f"[{target}] 成功从 {instance} 抓取 {len(valid_tweets)} 条候选推文，最新 ID: {newest_id}")
                    context.close()
                    browser.close()
                    return valid_tweets

                print(f"[{target}] {instance} 页面上未找到符合条件的非置顶推文")
                context.close()
                context = None
            except Exception as exc:
                print(f"[{target}] 访问 {instance} 出错: {exc}")
            finally:
                if context is not None:
                    try:
                        context.close()
                    except Exception:
                        pass

        browser.close()
    return []


def print_record(record: dict, index: int | None = None) -> None:
    prefix = f"{index}. " if index is not None else ""
    print(f"{prefix}[{record.get('stored_at', '-')}] {record.get('target', '-')}")
    print(f"   作者: {record.get('author', '-')}")
    print(f"   ID: {record.get('guid', '-')}")
    print(f"   内容: {record.get('content', '').strip()}")
    if record.get("translated_content"):
        print(f"   翻译: {record['translated_content']}")
    print(f"   Nitter: {record.get('link', '-')}")
    if record.get("x_url"):
        print(f"   X: {record['x_url']}")
    if record.get("images"):
        print(f"   图片数: {len(record['images'])}")
    if record.get("video_url"):
        print(f"   视频: {record['video_url']}")
    print("")


def command_monitor(args) -> int:
    targets = parse_targets(args.targets) if args.targets else load_subscriptions()
    if not targets:
        print("[系统] 当前没有订阅目标，跳过本轮监控")
        return 0

    retention_days = args.retention_days if args.retention_days is not None else DEFAULT_RETENTION_DAYS
    max_records = args.max_records if args.max_records is not None else DEFAULT_MAX_RECORDS
    last_ids = load_last_ids()
    existing_records = load_records()
    existing_keys = {record_key(record) for record in existing_records}
    instances = load_instances()

    print(f"[{datetime.now()}] 开始监控，共 {len(targets)} 个目标")
    new_records = 0
    for target in targets:
        try:
            tweets = scrape_nitter_with_playwright(target, instances)
            if not tweets:
                continue

            current_id = tweets[0]["guid"]
            previous_id = last_ids.get(target)
            if previous_id == current_id:
                print(f"[{target}] 无更新")
                continue

            pending_records = []
            for tweet in tweets:
                if previous_id and tweet["guid"] == previous_id:
                    break

                key = record_key(tweet)
                if key in existing_keys:
                    continue

                pending_records.append(tweet)

            for tweet in reversed(pending_records):
                append_record(tweet)
                existing_keys.add(record_key(tweet))

            last_ids[target] = current_id
            new_records += len(pending_records)
            print(f"[{target}] 已保存 {len(pending_records)} 条新记录到本地存储")
        except Exception as exc:
            print(f"[{target}] 处理异常: {exc}")

    save_last_ids(last_ids)
    print(f"[系统] 本轮新增 {new_records} 条记录")

    if not args.skip_cleanup:
        stats = cleanup_records(retention_days, max_records)
        print(
            f"[系统] 清理完成: 保留 {stats['after']} 条，删除 {stats['deleted']} 条 "
            f"(retention_days={retention_days}, max_records={max_records})"
        )
    return 0


def command_subscribe(args) -> int:
    current = load_subscriptions()
    current_set = set(current)

    if args.action == "list":
        if not current:
            print("[系统] 当前没有订阅目标")
            return 0
        print("[系统] 当前订阅列表:")
        for idx, target in enumerate(current, start=1):
            print(f"{idx}. {target}")
        return 0

    raw_targets = args.targets
    targets = parse_targets(raw_targets)
    if args.action not in {"list", "set"} and not targets:
        print("[系统] 请通过 --targets 提供目标")
        return 1
    if args.action == "set" and raw_targets is None:
        print("[系统] set 动作需要显式提供 --targets，可传空字符串清空订阅")
        return 1

    if args.action == "add":
        updated = current[:]
        for target in targets:
            if target not in current_set:
                updated.append(target)
                current_set.add(target)
        save_subscriptions(updated)
        print(f"[系统] 已新增 {len(updated) - len(current)} 个订阅目标")
    elif args.action == "remove":
        updated = [target for target in current if target not in set(targets)]
        save_subscriptions(updated)
        print(f"[系统] 已移除 {len(current) - len(updated)} 个订阅目标")
    elif args.action == "set":
        save_subscriptions(targets)
        print(f"[系统] 已重置订阅列表，共 {len(targets)} 个目标")
    else:
        print(f"[系统] 未知订阅动作: {args.action}")
        return 1

    return 0


def record_matches(record: dict, args) -> bool:
    if args.target and record.get("target") != args.target:
        return False

    if args.keyword:
        haystacks = [
            record.get("content", ""),
            record.get("raw_content", ""),
            record.get("translated_content", "") or "",
            record.get("author", ""),
        ]
        merged = "\n".join(haystacks).lower()
        if args.keyword.lower() not in merged:
            return False

    stored_at = parse_datetime(record.get("stored_at", ""))
    if args.since:
        since_dt = parse_datetime(args.since)
        if since_dt and stored_at and stored_at < since_dt:
            return False
    if args.until:
        until_dt = parse_datetime(args.until)
        if until_dt and stored_at and stored_at > until_dt:
            return False
    return True


def command_query(args) -> int:
    records = load_records()
    filtered = [record for record in records if record_matches(record, args)]
    filtered.sort(
        key=lambda item: parse_datetime(item.get("stored_at", "")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    if args.limit > 0:
        filtered = filtered[: args.limit]

    print(f"[系统] 查询结果 {len(filtered)} 条")
    for idx, record in enumerate(filtered, start=1):
        print_record(record, idx)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as fh:
            json.dump(filtered, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        print(f"[系统] 查询结果已写入 {output_path}")

    return 0


def command_cleanup(args) -> int:
    retention_days = args.retention_days if args.retention_days is not None else DEFAULT_RETENTION_DAYS
    max_records = args.max_records if args.max_records is not None else DEFAULT_MAX_RECORDS
    stats = cleanup_records(retention_days, max_records)
    print(
        f"[系统] 清理完成: 处理前 {stats['before']} 条，处理后 {stats['after']} 条，"
        f"删除 {stats['deleted']} 条"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Twitter/X 监控与本地存储工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    monitor_parser = subparsers.add_parser("monitor", help="抓取订阅目标并保存最新推文")
    monitor_parser.add_argument("--targets", help="覆盖订阅列表，逗号或换行分隔")
    monitor_parser.add_argument("--retention-days", type=int, default=None, help="保留天数")
    monitor_parser.add_argument("--max-records", type=int, default=None, help="最大保留记录数")
    monitor_parser.add_argument("--skip-cleanup", action="store_true", help="本轮监控后不执行清理")
    monitor_parser.set_defaults(func=command_monitor)

    subscribe_parser = subparsers.add_parser("subscribe", help="管理订阅列表")
    subscribe_parser.add_argument("action", choices=["add", "remove", "set", "list"], help="订阅动作")
    subscribe_parser.add_argument("--targets", help="目标列表，逗号或换行分隔")
    subscribe_parser.set_defaults(func=command_subscribe)

    query_parser = subparsers.add_parser("query", help="查询历史保存结果")
    query_parser.add_argument("--target", help="按订阅目标精确过滤")
    query_parser.add_argument("--keyword", help="按内容关键字过滤")
    query_parser.add_argument("--since", help="起始时间，ISO 8601，例如 2026-05-01T00:00:00+00:00")
    query_parser.add_argument("--until", help="结束时间，ISO 8601，例如 2026-05-31T23:59:59+00:00")
    query_parser.add_argument("--limit", type=int, default=20, help="最大返回条数")
    query_parser.add_argument("--output", help="将查询结果写入 JSON 文件")
    query_parser.set_defaults(func=command_query)

    cleanup_parser = subparsers.add_parser("cleanup", help="清理历史记录")
    cleanup_parser.add_argument("--retention-days", type=int, default=None, help="保留天数")
    cleanup_parser.add_argument("--max-records", type=int, default=None, help="最大保留记录数")
    cleanup_parser.set_defaults(func=command_cleanup)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
