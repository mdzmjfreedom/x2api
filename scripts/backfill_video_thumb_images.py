from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from collector.twitter_monitor import get_original_image_url  # noqa: E402
from collector.opensearch_items import get_client, get_items_index, update_item_document as update_opensearch_item_document  # noqa: E402

try:
    from opensearchpy import helpers
except ModuleNotFoundError:  # pragma: no cover
    from opensearchpy import helpers  # type: ignore


VIDEO_THUMB_PATTERNS = (
    "%amplify_video_thumb%",
    "%ext_tw_video_thumb%",
    "%tweet_video_thumb%",
)
NITTER_VIDEO_THUMB_PREFIXES = (
    "https://nitter.privacyredirect.com/pic/amplify_video_thumb",
    "https://nitter.privacyredirect.com/pic/ext_tw_video_thumb",
    "https://nitter.privacyredirect.com/pic/tweet_video_thumb",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rewrite stored Nitter/X video thumbnail image URLs to pbs.twimg.com URLs."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Update rows. Without this flag the script only reports what would change.",
    )
    parser.add_argument(
        "--sample-limit",
        type=int,
        default=10,
        help="Number of changed URL samples to print.",
    )
    return parser.parse_args()


def is_nitter_video_thumb_url(image_url: str) -> bool:
    return image_url.startswith(NITTER_VIDEO_THUMB_PREFIXES)


def main() -> int:
    args = parse_args()
    sample_limit = max(args.sample_limit, 0)
    checked = 0
    changed_rows: list[tuple[str, list[str], str]] = []
    samples: list[dict[str, str]] = []
    client = get_client()
    if client is None:
        raise RuntimeError("Missing OPENSEARCH_URL environment variable.")

    query = {
        "_source": ["guid", "images"],
        "query": {
            "bool": {
                "filter": [
                    {
                        "wildcard": {
                            "images": {
                                "value": "*video_thumb*",
                            }
                        }
                    }
                ]
            }
        },
    }

    for hit in helpers.scan(client, index=get_items_index(), query=query, preserve_order=False):
        source = hit.get("_source") or {}
        item_id = str(hit.get("_id") or "")
        guid = str(source.get("guid") or "")
        images = source.get("images") or []
        if not item_id or not isinstance(images, list):
            continue
        checked += 1
        rewritten_images = [
            get_original_image_url(str(image_url))
            if is_nitter_video_thumb_url(str(image_url))
            else str(image_url)
            for image_url in images
        ]
        if rewritten_images == images:
            continue

        changed_rows.append((item_id, rewritten_images, guid))
        if len(samples) < sample_limit:
            for before, after in zip(images, rewritten_images):
                if before != after:
                    samples.append(
                        {
                            "guid": guid,
                            "before": str(before),
                            "after": after,
                        }
                    )
                    break

    if args.apply:
        for item_id, images, _guid in changed_rows:
            update_opensearch_item_document(item_id, images=images)

    print(
        {
            "mode": "apply" if args.apply else "dry-run",
            "checked": checked,
            "updated": len(changed_rows) if args.apply else 0,
            "would_update": len(changed_rows),
            "samples": samples,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
