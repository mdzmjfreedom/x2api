from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from psycopg import connect
from psycopg.rows import dict_row

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_LEXICON_URL = "https://raw.githubusercontent.com/M1Z2105a4/resource/refs/heads/main/feed_lexicon.json"


@dataclass(frozen=True)
class LexiconEntry:
    tag: str
    category: str | None
    keywords: tuple[str, ...]
    weight: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill tags for video items using target profiles and a lexicon bundle."
    )
    parser.add_argument("--apply", action="store_true", help="Write changes. Without this flag, only report matches.")
    parser.add_argument("--limit", type=int, default=500, help="Maximum untagged video items to scan.")
    parser.add_argument("--lexicon-url", default=DEFAULT_LEXICON_URL, help="Remote feed_lexicon.json URL.")
    parser.add_argument("--lexicon-file", help="Local feed_lexicon.json path. Overrides --lexicon-url.")
    parser.add_argument("--default-tag", default="未分类", help="Fallback tag when nothing matches.")
    parser.add_argument(
        "--min-rule-score",
        type=int,
        default=2,
        help="Minimum keyword match score required before rule tags are applied.",
    )
    return parser.parse_args()


def load_json(args: argparse.Namespace) -> dict[str, Any]:
    if args.lexicon_file:
        with open(args.lexicon_file, "r", encoding="utf-8") as file:
            return json.load(file)

    with urllib.request.urlopen(args.lexicon_url, timeout=30) as response:
        return json.load(response)


def parse_lexicon(bundle: dict[str, Any]) -> list[LexiconEntry]:
    entries: list[LexiconEntry] = []
    for resource in bundle.get("resources", []):
        if not isinstance(resource, dict) or resource.get("kind") != "lexicon":
            continue
        payload = resource.get("payload")
        if not isinstance(payload, dict):
            continue
        types = payload.get("types") or []
        if types and "video" not in types:
            continue
        tag = str(payload.get("tag") or "").strip()
        if not tag:
            continue
        keywords = tuple(
            str(keyword).strip().lower()
            for keyword in payload.get("keywords", [])
            if str(keyword).strip()
        )
        if not keywords:
            continue
        category = str(payload.get("category") or "").strip() or None
        weight = int(payload.get("weight") or 0)
        entries.append(LexiconEntry(tag=tag, category=category, keywords=keywords, weight=weight))

    entries.sort(key=lambda entry: max((len(keyword) for keyword in entry.keywords), default=0), reverse=True)
    return entries


def normalize_text(*parts: object) -> str:
    return "\n".join(str(part or "").lower() for part in parts)


def keyword_matches(text: str, keyword: str) -> bool:
    if not keyword:
        return False
    if re.fullmatch(r"[a-z0-9_][a-z0-9_\- ]*[a-z0-9_]", keyword):
        return re.search(rf"(?<![a-z0-9_]){re.escape(keyword)}(?![a-z0-9_])", text) is not None
    return keyword in text


def keyword_score(keyword: str) -> int:
    if re.fullmatch(r"[a-z0-9_][a-z0-9_\- ]*[a-z0-9_]", keyword):
        return 2 if len(keyword.replace(" ", "")) >= 4 else 1
    return 2 if len(keyword) >= 3 else 1


def match_lexicon(text: str, entries: list[LexiconEntry], min_rule_score: int) -> list[LexiconEntry]:
    matches: list[LexiconEntry] = []
    seen: set[str] = set()
    for entry in entries:
        if entry.tag in seen:
            continue
        score = sum(keyword_score(keyword) for keyword in entry.keywords if keyword_matches(text, keyword))
        if score >= min_rule_score:
            matches.append(entry)
            seen.add(entry.tag)
    return matches


def ensure_tag(cur, name: str, tag_type: str, weight: int) -> object:
    cur.execute(
        """
        INSERT INTO tags (name, type, weight)
        VALUES (%s, %s, %s)
        ON CONFLICT (name) DO UPDATE SET
          weight = GREATEST(tags.weight, EXCLUDED.weight)
        RETURNING id
        """,
        (name, tag_type, weight),
    )
    return cur.fetchone()["id"]


def main() -> int:
    args = parse_args()
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("Missing DATABASE_URL environment variable.")

    lexicon = parse_lexicon(load_json(args))
    scanned = 0
    tagged_items = 0
    created_relations = 0
    dry_run_samples: list[dict[str, Any]] = []

    with connect(database_url, row_factory=dict_row, prepare_threshold=None) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                  i.id,
                  i.title,
                  i.content,
                  i.author,
                  i.fullname,
                  t.kind,
                  t.value,
                  COALESCE(tp.tags, '[]'::jsonb) AS profile_tags,
                  tp.category AS profile_category,
                  tp.weight AS profile_weight
                FROM items i
                INNER JOIN targets t ON t.id = i.target_id
                LEFT JOIN target_profiles tp ON tp.target_id = t.id
                WHERE i.video_url IS NOT NULL
                  AND i.video_url <> ''
                  AND NOT EXISTS (
                    SELECT 1 FROM item_tags it WHERE it.item_id = i.id
                  )
                ORDER BY i.stored_at DESC
                LIMIT %s
                """,
                (max(args.limit, 1),),
            )
            rows = cur.fetchall()

        for row in rows:
            scanned += 1
            profile_tags = [str(tag).strip() for tag in (row["profile_tags"] or []) if str(tag).strip()]
            text = normalize_text(
                row["title"],
                row["content"],
                row["author"],
                row["fullname"],
                row["value"],
            )
            matches = match_lexicon(text, lexicon, max(args.min_rule_score, 1))
            tag_specs: list[tuple[str, str, str, int, float]] = []

            for tag in profile_tags:
                tag_specs.append((tag, "topic", "target", int(row["profile_weight"] or 0), 0.95))
            if row["profile_category"]:
                tag_specs.append((str(row["profile_category"]), "category", "target", int(row["profile_weight"] or 0), 0.95))
            for match in matches[:12]:
                tag_specs.append((match.tag, "topic", "rule", match.weight, 0.8))
                if match.category:
                    tag_specs.append((match.category, "category", "rule", match.weight, 0.75))
            if not tag_specs and args.default_tag:
                tag_specs.append((args.default_tag, "system", "rule", 0, 0.4))

            deduped: dict[str, tuple[str, str, int, float]] = {}
            for name, tag_type, source, weight, confidence in tag_specs:
                if name not in deduped or confidence > deduped[name][3]:
                    deduped[name] = (tag_type, source, weight, confidence)

            if not deduped:
                continue

            tagged_items += 1
            if not args.apply:
                if len(dry_run_samples) < 10:
                    dry_run_samples.append(
                        {
                            "item_id": str(row["id"]),
                            "target": f"search:{row['value']}" if row["kind"] == "keyword" else row["value"],
                            "tags": sorted(deduped.keys()),
                        }
                    )
                continue

            with conn.cursor() as cur:
                for name, (tag_type, source, weight, confidence) in deduped.items():
                    tag_id = ensure_tag(cur, name, tag_type, weight)
                    cur.execute(
                        """
                        INSERT INTO item_tags (item_id, tag_id, source, confidence)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (item_id, tag_id) DO NOTHING
                        """,
                        (row["id"], tag_id, source, confidence),
                    )
                    created_relations += cur.rowcount

        if args.apply:
            conn.commit()
        else:
            conn.rollback()

    print(
        json.dumps(
            {
                "apply": args.apply,
                "lexicon_entries": len(lexicon),
                "scanned": scanned,
                "tagged_items": tagged_items,
                "created_relations": created_relations,
                "samples": dry_run_samples,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
