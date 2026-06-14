from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import hmac
from html import unescape as html_unescape
import json
import os
import random
import re
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode, urljoin, urlparse, urlunparse
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
from psycopg import connect
from psycopg.errors import OperationalError
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

try:
    from collector.avgood_source import (
        AVGOOD_CRITICAL_WINDOW_MINUTES,
        AVGOOD_DEFAULT_BASE_URL,
        AVGOOD_KIND,
        AVGOOD_REFRESH_WINDOW_MINUTES,
        AVGOOD_RETENTION_HOURS,
        AVGOOD_SITE_NAME,
        AVGOOD_SOURCE,
        is_avgood_target_url,
        monitor_site as monitor_avgood_site,
        normalize_avgood_target_value,
        refresh_playback_urls as refresh_avgood_playback_urls,
        upsert_video_item as upsert_avgood_video_item,
    )
    from collector.badnews_source import (
        BADNEWS_CRITICAL_WINDOW_MINUTES,
        BADNEWS_DEFAULT_BASE_URL,
        BADNEWS_KIND,
        BADNEWS_REFRESH_WINDOW_MINUTES,
        BADNEWS_RETENTION_HOURS,
        BADNEWS_SITE_NAME,
        BADNEWS_SOURCE,
        is_badnews_target_url,
        monitor_site as monitor_badnews_site,
        normalize_badnews_target_value,
        refresh_playback_urls as refresh_badnews_playback_urls,
        upsert_video_item as upsert_badnews_video_item,
    )
    from collector.baoliao51_refresh import refresh_playback_urls as refresh_baoliao51_playback_urls
    from collector.baoliao51_source import (
        BAOLIAO51_CRITICAL_WINDOW_MINUTES,
        BAOLIAO51_DEFAULT_BASE_URL,
        BAOLIAO51_KIND,
        BAOLIAO51_REFRESH_WINDOW_MINUTES,
        BAOLIAO51_RETENTION_HOURS,
        BAOLIAO51_SITE_NAME,
        BAOLIAO51_SOURCE,
        is_baoliao51_target_url,
        monitor_site as monitor_baoliao51_site,
        normalize_baoliao51_target_value,
        upsert_baoliao51_video_item,
    )
    from collector.bdrq_source import (
        BDRQ_CRITICAL_WINDOW_MINUTES,
        BDRQ_DEFAULT_BASE_URL,
        BDRQ_KIND,
        BDRQ_REFRESH_WINDOW_MINUTES,
        BDRQ_RETENTION_HOURS,
        BDRQ_SITE_NAME,
        BDRQ_SOURCE,
        is_bdrq_target_url,
        monitor_site as monitor_bdrq_site,
        normalize_bdrq_target_value,
        refresh_playback_urls as refresh_bdrq_playback_urls,
        upsert_video_item as upsert_bdrq_video_item,
    )
    from collector.affair_refresh import refresh_playback_urls as refresh_affair_playback_urls
    from collector.affair_source import (
        AFFAIR_CRITICAL_WINDOW_MINUTES,
        AFFAIR_DEFAULT_BASE_URL,
        AFFAIR_KIND,
        AFFAIR_REFRESH_WINDOW_MINUTES,
        AFFAIR_RETENTION_HOURS,
        AFFAIR_SITE_NAME,
        AFFAIR_SOURCE,
        is_affair_target_url,
        monitor_site as monitor_affair_site,
        normalize_affair_target_value,
    )
    from collector.attach_refresh import refresh_playback_urls as refresh_attach_playback_urls
    from collector.attach_source import (
        ATTACH_CRITICAL_WINDOW_MINUTES,
        ATTACH_DEFAULT_BASE_URL,
        ATTACH_KIND,
        ATTACH_REFRESH_WINDOW_MINUTES,
        ATTACH_RETENTION_HOURS,
        ATTACH_SITE_NAME,
        ATTACH_SOURCE,
        is_attach_target_url,
        monitor_site as monitor_attach_site,
        normalize_attach_target_value,
    )
    from collector.dirtyship_refresh import refresh_playback_urls as refresh_dirtyship_playback_urls
    from collector.dirtyship_source import (
        DIRTYSHIP_CRITICAL_WINDOW_MINUTES,
        DIRTYSHIP_DEFAULT_BASE_URL,
        DIRTYSHIP_KIND,
        DIRTYSHIP_REFRESH_WINDOW_MINUTES,
        DIRTYSHIP_RETENTION_HOURS,
        DIRTYSHIP_SITE_NAME,
        DIRTYSHIP_SOURCE,
        is_dirtyship_target_url,
        monitor_site as monitor_dirtyship_site,
        normalize_dirtyship_target_value,
    )
    from collector.hs705_source import (
        HS705_CRITICAL_WINDOW_MINUTES,
        HS705_DEFAULT_BASE_URL,
        HS705_KIND,
        HS705_REFRESH_WINDOW_MINUTES,
        HS705_RETENTION_HOURS,
        HS705_SITE_NAME,
        HS705_SOURCE,
        is_hs705_target_url,
        monitor_site as monitor_hs705_site,
        normalize_hs705_target_value,
        refresh_playback_urls as refresh_hs705_playback_urls,
        upsert_video_item as upsert_hs705_video_item,
    )
    from collector.influencersgonewild_source import (
        INFLUENCERSGONEWILD_CRITICAL_WINDOW_MINUTES,
        INFLUENCERSGONEWILD_DEFAULT_BASE_URL,
        INFLUENCERSGONEWILD_KIND,
        INFLUENCERSGONEWILD_REFRESH_WINDOW_MINUTES,
        INFLUENCERSGONEWILD_RETENTION_HOURS,
        INFLUENCERSGONEWILD_SITE_NAME,
        INFLUENCERSGONEWILD_SOURCE,
        is_influencersgonewild_target_url,
        monitor_site as monitor_influencersgonewild_site,
        normalize_influencersgonewild_target_value,
        refresh_playback_urls as refresh_influencersgonewild_playback_urls,
        upsert_video_item as upsert_influencersgonewild_video_item,
    )
    from collector.missav_refresh import refresh_playback_urls as refresh_missav_playback_urls
    from collector.missav_source import (
        MISSAV_CRITICAL_WINDOW_MINUTES,
        MISSAV_DEFAULT_BASE_URL,
        MISSAV_KIND,
        MISSAV_REFRESH_WINDOW_MINUTES,
        MISSAV_RETENTION_HOURS,
        MISSAV_SITE_NAME,
        MISSAV_SOURCE,
        is_missav_target_url,
        monitor_site as monitor_missav_site,
        normalize_missav_target_value,
    )
    from collector.dadaafa_source import (
        DADAAFA_CRITICAL_WINDOW_MINUTES,
        DADAAFA_DEFAULT_BASE_URL,
        DADAAFA_KIND,
        DADAAFA_REFRESH_WINDOW_MINUTES,
        DADAAFA_RETENTION_HOURS,
        DADAAFA_SITE_NAME,
        DADAAFA_SOURCE,
        is_dadaafa_target_url,
        monitor_site as monitor_dadaafa_site,
        normalize_dadaafa_target_value,
        refresh_playback_urls as refresh_dadaafa_playback_urls,
        upsert_video_item as upsert_dadaafa_video_item,
    )
    from collector.j18_source import (
        J18_CRITICAL_WINDOW_MINUTES,
        J18_DEFAULT_BASE_URL,
        J18_KIND,
        J18_REFRESH_WINDOW_MINUTES,
        J18_RETENTION_HOURS,
        J18_SITE_NAME,
        J18_SOURCE,
        is_18j_target_url,
        monitor_site as monitor_18j_site,
        normalize_18j_target_value,
        refresh_playback_urls as refresh_18j_playback_urls,
        upsert_video_item as upsert_18j_video_item,
    )
    from collector.mh18_source import (
        MH18_DEFAULT_BASE_URL,
        MH18_CRITICAL_WINDOW_MINUTES,
        MH18_KIND,
        MH18_REFRESH_WINDOW_MINUTES,
        MH18_RETENTION_HOURS,
        MH18_SITE_NAME,
        MH18_SOURCE,
        is_18mh_target_url,
        monitor_site as monitor_18mh_site,
        normalize_18mh_target_value,
        refresh_playback_urls as refresh_18mh_playback_urls,
        upsert_video_item as upsert_18mh_video_item,
    )
    from collector.mtif_source import (
        MTIF_CRITICAL_WINDOW_MINUTES,
        MTIF_DEFAULT_BASE_URL,
        MTIF_KIND,
        MTIF_REFRESH_WINDOW_MINUTES,
        MTIF_RETENTION_HOURS,
        MTIF_SITE_NAME,
        MTIF_SOURCE,
        is_mtif_target_url,
        monitor_site as monitor_mtif_site,
        normalize_mtif_target_value,
        refresh_playback_urls as refresh_mtif_playback_urls,
        upsert_video_item as upsert_mtif_video_item,
    )
    from collector.porna91_source import (
        PORNA91_CRITICAL_WINDOW_MINUTES,
        PORNA91_DEFAULT_BASE_URL,
        PORNA91_KIND,
        PORNA91_REFRESH_WINDOW_MINUTES,
        PORNA91_RETENTION_HOURS,
        PORNA91_SITE_NAME,
        PORNA91_SOURCE,
        is_porna91_target_url,
        monitor_site as monitor_porna91_site,
        normalize_porna91_target_value,
        refresh_playback_urls as refresh_porna91_playback_urls,
    )
    from collector.porn91_source import (
        PORN91_CRITICAL_WINDOW_MINUTES,
        PORN91_DEFAULT_BASE_URL,
        PORN91_KIND,
        PORN91_REFRESH_WINDOW_MINUTES,
        PORN91_RETENTION_HOURS,
        PORN91_SITE_NAME,
        PORN91_SOURCE,
        is_91porn_target_url,
        monitor_site as monitor_91porn_site,
        normalize_91porn_target_value,
        refresh_playback_urls as refresh_91porn_playback_urls,
        upsert_video_item as upsert_91porn_video_item,
    )
    from collector.rb91_refresh import refresh_playback_urls as refresh_91rb_playback_urls
    from collector.rb91_source import (
        RB91_CRITICAL_WINDOW_MINUTES,
        RB91_DEFAULT_BASE_URL,
        RB91_KIND,
        RB91_REFRESH_WINDOW_MINUTES,
        RB91_RETENTION_HOURS,
        RB91_SITE_NAME,
        RB91_SOURCE,
        is_91rb_target_url,
        monitor_site as monitor_91rb_site,
        normalize_91rb_target_value,
    )
    from collector.rou_source import (
        ROU_CRITICAL_WINDOW_MINUTES,
        ROU_DEFAULT_BASE_URL,
        ROU_KIND,
        ROU_REFRESH_WINDOW_MINUTES,
        ROU_RETENTION_HOURS,
        ROU_SITE_NAME,
        ROU_SOURCE,
        is_rou_target_url,
        monitor_site as monitor_rou_site,
        normalize_rou_target_value,
        refresh_playback_urls as refresh_rou_playback_urls,
    )
    from collector.tikporn_refresh import refresh_playback_urls as refresh_tikporn_playback_urls
    from collector.tikporn_source import (
        TIKPORN_CRITICAL_WINDOW_MINUTES,
        TIKPORN_DEFAULT_BASE_URL,
        TIKPORN_KIND,
        TIKPORN_REFRESH_WINDOW_MINUTES,
        TIKPORN_RETENTION_HOURS,
        TIKPORN_SITE_NAME,
        TIKPORN_SOURCE,
        is_tikporn_target_url,
        monitor_site as monitor_tikporn_site,
        normalize_tikporn_target_value,
    )
    from collector.xxxtik_source import (
        XXXTIK_CRITICAL_WINDOW_MINUTES,
        XXXTIK_DEFAULT_BASE_URL,
        XXXTIK_KIND,
        XXXTIK_REFRESH_WINDOW_MINUTES,
        XXXTIK_RETENTION_HOURS,
        XXXTIK_SITE_NAME,
        XXXTIK_SOURCE,
        is_xxxtik_target_url,
        monitor_site as monitor_xxxtik_site,
        normalize_xxxtik_target_value,
        refresh_playback_urls as refresh_xxxtik_playback_urls,
        upsert_video_item as upsert_xxxtik_video_item,
    )
except ModuleNotFoundError:
    from avgood_source import (
        AVGOOD_CRITICAL_WINDOW_MINUTES,
        AVGOOD_DEFAULT_BASE_URL,
        AVGOOD_KIND,
        AVGOOD_REFRESH_WINDOW_MINUTES,
        AVGOOD_RETENTION_HOURS,
        AVGOOD_SITE_NAME,
        AVGOOD_SOURCE,
        is_avgood_target_url,
        monitor_site as monitor_avgood_site,
        normalize_avgood_target_value,
        refresh_playback_urls as refresh_avgood_playback_urls,
        upsert_video_item as upsert_avgood_video_item,
    )
    from badnews_source import (
        BADNEWS_CRITICAL_WINDOW_MINUTES,
        BADNEWS_DEFAULT_BASE_URL,
        BADNEWS_KIND,
        BADNEWS_REFRESH_WINDOW_MINUTES,
        BADNEWS_RETENTION_HOURS,
        BADNEWS_SITE_NAME,
        BADNEWS_SOURCE,
        is_badnews_target_url,
        monitor_site as monitor_badnews_site,
        normalize_badnews_target_value,
        refresh_playback_urls as refresh_badnews_playback_urls,
        upsert_video_item as upsert_badnews_video_item,
    )
    from baoliao51_refresh import refresh_playback_urls as refresh_baoliao51_playback_urls
    from baoliao51_source import (
        BAOLIAO51_CRITICAL_WINDOW_MINUTES,
        BAOLIAO51_DEFAULT_BASE_URL,
        BAOLIAO51_KIND,
        BAOLIAO51_REFRESH_WINDOW_MINUTES,
        BAOLIAO51_RETENTION_HOURS,
        BAOLIAO51_SITE_NAME,
        BAOLIAO51_SOURCE,
        is_baoliao51_target_url,
        monitor_site as monitor_baoliao51_site,
        normalize_baoliao51_target_value,
        upsert_baoliao51_video_item,
    )
    from bdrq_source import (
        BDRQ_CRITICAL_WINDOW_MINUTES,
        BDRQ_DEFAULT_BASE_URL,
        BDRQ_KIND,
        BDRQ_REFRESH_WINDOW_MINUTES,
        BDRQ_RETENTION_HOURS,
        BDRQ_SITE_NAME,
        BDRQ_SOURCE,
        is_bdrq_target_url,
        monitor_site as monitor_bdrq_site,
        normalize_bdrq_target_value,
        refresh_playback_urls as refresh_bdrq_playback_urls,
        upsert_video_item as upsert_bdrq_video_item,
    )
    from affair_refresh import refresh_playback_urls as refresh_affair_playback_urls
    from affair_source import (
        AFFAIR_CRITICAL_WINDOW_MINUTES,
        AFFAIR_DEFAULT_BASE_URL,
        AFFAIR_KIND,
        AFFAIR_REFRESH_WINDOW_MINUTES,
        AFFAIR_RETENTION_HOURS,
        AFFAIR_SITE_NAME,
        AFFAIR_SOURCE,
        is_affair_target_url,
        monitor_site as monitor_affair_site,
        normalize_affair_target_value,
    )
    from attach_refresh import refresh_playback_urls as refresh_attach_playback_urls
    from attach_source import (
        ATTACH_CRITICAL_WINDOW_MINUTES,
        ATTACH_DEFAULT_BASE_URL,
        ATTACH_KIND,
        ATTACH_REFRESH_WINDOW_MINUTES,
        ATTACH_RETENTION_HOURS,
        ATTACH_SITE_NAME,
        ATTACH_SOURCE,
        is_attach_target_url,
        monitor_site as monitor_attach_site,
        normalize_attach_target_value,
    )
    from dirtyship_refresh import refresh_playback_urls as refresh_dirtyship_playback_urls
    from dirtyship_source import (
        DIRTYSHIP_CRITICAL_WINDOW_MINUTES,
        DIRTYSHIP_DEFAULT_BASE_URL,
        DIRTYSHIP_KIND,
        DIRTYSHIP_REFRESH_WINDOW_MINUTES,
        DIRTYSHIP_RETENTION_HOURS,
        DIRTYSHIP_SITE_NAME,
        DIRTYSHIP_SOURCE,
        is_dirtyship_target_url,
        monitor_site as monitor_dirtyship_site,
        normalize_dirtyship_target_value,
    )
    from hs705_source import (
        HS705_CRITICAL_WINDOW_MINUTES,
        HS705_DEFAULT_BASE_URL,
        HS705_KIND,
        HS705_REFRESH_WINDOW_MINUTES,
        HS705_RETENTION_HOURS,
        HS705_SITE_NAME,
        HS705_SOURCE,
        is_hs705_target_url,
        monitor_site as monitor_hs705_site,
        normalize_hs705_target_value,
        refresh_playback_urls as refresh_hs705_playback_urls,
        upsert_video_item as upsert_hs705_video_item,
    )
    from influencersgonewild_source import (
        INFLUENCERSGONEWILD_CRITICAL_WINDOW_MINUTES,
        INFLUENCERSGONEWILD_DEFAULT_BASE_URL,
        INFLUENCERSGONEWILD_KIND,
        INFLUENCERSGONEWILD_REFRESH_WINDOW_MINUTES,
        INFLUENCERSGONEWILD_RETENTION_HOURS,
        INFLUENCERSGONEWILD_SITE_NAME,
        INFLUENCERSGONEWILD_SOURCE,
        is_influencersgonewild_target_url,
        monitor_site as monitor_influencersgonewild_site,
        normalize_influencersgonewild_target_value,
        refresh_playback_urls as refresh_influencersgonewild_playback_urls,
        upsert_video_item as upsert_influencersgonewild_video_item,
    )
    from missav_refresh import refresh_playback_urls as refresh_missav_playback_urls
    from missav_source import (
        MISSAV_CRITICAL_WINDOW_MINUTES,
        MISSAV_DEFAULT_BASE_URL,
        MISSAV_KIND,
        MISSAV_REFRESH_WINDOW_MINUTES,
        MISSAV_RETENTION_HOURS,
        MISSAV_SITE_NAME,
        MISSAV_SOURCE,
        is_missav_target_url,
        monitor_site as monitor_missav_site,
        normalize_missav_target_value,
    )
    from dadaafa_source import (
        DADAAFA_CRITICAL_WINDOW_MINUTES,
        DADAAFA_DEFAULT_BASE_URL,
        DADAAFA_KIND,
        DADAAFA_REFRESH_WINDOW_MINUTES,
        DADAAFA_RETENTION_HOURS,
        DADAAFA_SITE_NAME,
        DADAAFA_SOURCE,
        is_dadaafa_target_url,
        monitor_site as monitor_dadaafa_site,
        normalize_dadaafa_target_value,
        refresh_playback_urls as refresh_dadaafa_playback_urls,
        upsert_video_item as upsert_dadaafa_video_item,
    )
    from j18_source import (
        J18_CRITICAL_WINDOW_MINUTES,
        J18_DEFAULT_BASE_URL,
        J18_KIND,
        J18_REFRESH_WINDOW_MINUTES,
        J18_RETENTION_HOURS,
        J18_SITE_NAME,
        J18_SOURCE,
        is_18j_target_url,
        monitor_site as monitor_18j_site,
        normalize_18j_target_value,
        refresh_playback_urls as refresh_18j_playback_urls,
        upsert_video_item as upsert_18j_video_item,
    )
    from mh18_source import (
        MH18_DEFAULT_BASE_URL,
        MH18_CRITICAL_WINDOW_MINUTES,
        MH18_KIND,
        MH18_REFRESH_WINDOW_MINUTES,
        MH18_RETENTION_HOURS,
        MH18_SITE_NAME,
        MH18_SOURCE,
        is_18mh_target_url,
        monitor_site as monitor_18mh_site,
        normalize_18mh_target_value,
        refresh_playback_urls as refresh_18mh_playback_urls,
        upsert_video_item as upsert_18mh_video_item,
    )
    from mtif_source import (
        MTIF_CRITICAL_WINDOW_MINUTES,
        MTIF_DEFAULT_BASE_URL,
        MTIF_KIND,
        MTIF_REFRESH_WINDOW_MINUTES,
        MTIF_RETENTION_HOURS,
        MTIF_SITE_NAME,
        MTIF_SOURCE,
        is_mtif_target_url,
        monitor_site as monitor_mtif_site,
        normalize_mtif_target_value,
        refresh_playback_urls as refresh_mtif_playback_urls,
        upsert_video_item as upsert_mtif_video_item,
    )
    from porna91_source import (
        PORNA91_CRITICAL_WINDOW_MINUTES,
        PORNA91_DEFAULT_BASE_URL,
        PORNA91_KIND,
        PORNA91_REFRESH_WINDOW_MINUTES,
        PORNA91_RETENTION_HOURS,
        PORNA91_SITE_NAME,
        PORNA91_SOURCE,
        is_porna91_target_url,
        monitor_site as monitor_porna91_site,
        normalize_porna91_target_value,
        refresh_playback_urls as refresh_porna91_playback_urls,
    )
    from porn91_source import (
        PORN91_CRITICAL_WINDOW_MINUTES,
        PORN91_DEFAULT_BASE_URL,
        PORN91_KIND,
        PORN91_REFRESH_WINDOW_MINUTES,
        PORN91_RETENTION_HOURS,
        PORN91_SITE_NAME,
        PORN91_SOURCE,
        is_91porn_target_url,
        monitor_site as monitor_91porn_site,
        normalize_91porn_target_value,
        refresh_playback_urls as refresh_91porn_playback_urls,
        upsert_video_item as upsert_91porn_video_item,
    )
    from rb91_refresh import refresh_playback_urls as refresh_91rb_playback_urls
    from rb91_source import (
        RB91_CRITICAL_WINDOW_MINUTES,
        RB91_DEFAULT_BASE_URL,
        RB91_KIND,
        RB91_REFRESH_WINDOW_MINUTES,
        RB91_RETENTION_HOURS,
        RB91_SITE_NAME,
        RB91_SOURCE,
        is_91rb_target_url,
        monitor_site as monitor_91rb_site,
        normalize_91rb_target_value,
    )
    from rou_source import (
        ROU_CRITICAL_WINDOW_MINUTES,
        ROU_DEFAULT_BASE_URL,
        ROU_KIND,
        ROU_REFRESH_WINDOW_MINUTES,
        ROU_RETENTION_HOURS,
        ROU_SITE_NAME,
        ROU_SOURCE,
        is_rou_target_url,
        monitor_site as monitor_rou_site,
        normalize_rou_target_value,
        refresh_playback_urls as refresh_rou_playback_urls,
    )
    from tikporn_refresh import refresh_playback_urls as refresh_tikporn_playback_urls
    from tikporn_source import (
        TIKPORN_CRITICAL_WINDOW_MINUTES,
        TIKPORN_DEFAULT_BASE_URL,
        TIKPORN_KIND,
        TIKPORN_REFRESH_WINDOW_MINUTES,
        TIKPORN_RETENTION_HOURS,
        TIKPORN_SITE_NAME,
        TIKPORN_SOURCE,
        is_tikporn_target_url,
        monitor_site as monitor_tikporn_site,
        normalize_tikporn_target_value,
    )
    from xxxtik_source import (
        XXXTIK_CRITICAL_WINDOW_MINUTES,
        XXXTIK_DEFAULT_BASE_URL,
        XXXTIK_KIND,
        XXXTIK_REFRESH_WINDOW_MINUTES,
        XXXTIK_RETENTION_HOURS,
        XXXTIK_SITE_NAME,
        XXXTIK_SOURCE,
        is_xxxtik_target_url,
        monitor_site as monitor_xxxtik_site,
        normalize_xxxtik_target_value,
        refresh_playback_urls as refresh_xxxtik_playback_urls,
        upsert_video_item as upsert_xxxtik_video_item,
    )


BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
QUERY_RESULTS_DIR = DATA_DIR / "query_results"
INSTANCES_FILE = PROJECT_ROOT / "instances.json"

DEFAULT_RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "30"))
DEFAULT_MAX_RECORDS = int(os.environ.get("MAX_RECORDS", "100000"))
AUTO_TRANSLATE = os.environ.get("TRANSLATE_CONTENT", "false").lower() == "true"
IMGBB_API_KEY = os.environ.get("IMGBB_API_KEY", "").strip()
YOUTUBE_RETENTION_HOURS = 72
YOUTUBE_RSS_TIMEOUT_SECONDS = 20
YOUTUBE_VIDEOS_PAGE_TIMEOUT_SECONDS = 20
YOUTUBE_PLAYBACK_RESOLVER_TIMEOUT_SECONDS = 30
HEILIAO_SITE_NAME = "黑料不打烊"
HEILIAO_SOURCE = "heiliao"
HEILIAO_KIND = "site"
HEILIAO_DEFAULT_BASE_URL = os.environ.get("HEILIAO_BASE_URL", "https://among.uvsoskqus.cc").strip().rstrip("/")
HEILIAO_RETENTION_HOURS = int(os.environ.get("HEILIAO_RETENTION_HOURS", "84"))
HEILIAO_REQUEST_TIMEOUT_SECONDS = 30
HEILIAO_REFRESH_WINDOW_MINUTES = 90
HEILIAO_CRITICAL_WINDOW_MINUTES = 15
CG91_SITE_NAME = "91吃瓜网"
CG91_SOURCE = "cg91"
CG91_KIND = "site"
CG91_DEFAULT_BASE_URL = os.environ.get("CG91_BASE_URL", "https://www.91cg1.com").strip().rstrip("/")
CG91_RETENTION_HOURS = int(os.environ.get("CG91_RETENTION_HOURS", "84"))
DOUYIN_SITE_NAME = "抖阴"
DOUYIN_SOURCE = "douyin"
DOUYIN_KIND = "site"
DOUYIN_DEFAULT_BASE_URL = os.environ.get("DOUYIN_BASE_URL", "https://xygrfrfb3g.b2h7y8w.com").strip().rstrip("/")
DOUYIN_RETENTION_HOURS = int(os.environ.get("DOUYIN_RETENTION_HOURS", "84"))
DOUYIN_REQUEST_TIMEOUT_SECONDS = 30
DOUYIN_API_SECRET = "x3t8rvtaescfe38s"
DOUYIN_TIMEZONE = timezone(timedelta(hours=8))
DOUYIN_EMPTY_DESCRIPTIONS = {"当前暂无简介"}
DETAIL_LINK_PROFILE_SOURCES = {
    HEILIAO_SOURCE,
    CG91_SOURCE,
    BAOLIAO51_SOURCE,
    DOUYIN_SOURCE,
    MH18_SOURCE,
    ROU_SOURCE,
    DADAAFA_SOURCE,
    MTIF_SOURCE,
    BADNEWS_SOURCE,
    TIKPORN_SOURCE,
    PORNA91_SOURCE,
    PORN91_SOURCE,
    RB91_SOURCE,
    J18_SOURCE,
    BDRQ_SOURCE,
    AVGOOD_SOURCE,
    HS705_SOURCE,
    XXXTIK_SOURCE,
    AFFAIR_SOURCE,
    ATTACH_SOURCE,
    DIRTYSHIP_SOURCE,
    INFLUENCERSGONEWILD_SOURCE,
    MISSAV_SOURCE,
}

DATABASE_URL = os.environ.get("DATABASE_URL", "")
VIDEO_THUMB_PREFIXES = (
    "amplify_video_thumb",
    "ext_tw_video_thumb",
    "tweet_video_thumb",
)

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


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.strip()

    try:
        return datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        pass

    for fmt in (
        "%b %d, %Y · %I:%M %p UTC",
        "%b %d, %Y %I:%M %p UTC",
    ):
        try:
            return datetime.strptime(normalized, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue

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


def require_database_url() -> str:
    if not DATABASE_URL:
        raise RuntimeError("Missing DATABASE_URL environment variable.")
    return DATABASE_URL


def get_db_connection():
    # Supabase transaction pooler is the safest fit for short-lived jobs such
    # as GitHub Actions, but it doesn't support prepared statements.
    return connect(require_database_url(), row_factory=dict_row, prepare_threshold=None)


LOCK_KEYS = {
    "admin": 0x6B4D5F3141444D4E,
    "cleanup": 0x6B4D5F434C45414E,
    "manage": 0x6B4D5F4D414E4147,
    "twitter": 0x6B4D5F5457495454,
    "youtube": 0x6B4D5F5954554245,
    "heiliao": 0x6B4D5F4845494C49,
    "cg91": 0x6B4D5F434739315F,
    "baoliao51": 0x6B4D5F42414F3531,
    "douyin": 0x6B4D5F444F55594E,
    "18mh": 0x6B4D5F31384D485F,
    "rou": 0x6B4D5F524F555F5F,
    "dadaafa": 0x6B4D5F4441444146,
    "18j": 0x6B4D5F31384A5F5F,
    "1mtif": 0x6B4D5F314D544946,
    "91porna": 0x6B4D5F39315041,
    "91porn": 0x6B4D5F3931504F52,
    "91rb": 0x6B4D5F393152425F,
    "avgood": 0x6B4D5F4156474F4F,
    "705hs": 0x6B4D5F3730354853,
    "xxxtik": 0x6B4D5F5858585449,
    "affair": 0x6B4D5F4146464149,
    "attach": 0x6B4D5F4154544143,
    "dirtyship": 0x6B4D5F4449525459,
    "influencersgonewild": 0x6B4D5F494E464C47,
    "missav": 0x6B4D5F4D49535341,
    "badnews": 0x6B4D5F4241444E45,
    "bdrq": 0x6B4D5F424452515F,
    "tikporn": 0x6B4D5F54494B504F,
    "other": 0x6B4D5F4F54484552,
}

DB_SLOT_LOCK_BASE = 0x6B4D5F534C4F5400


def lock_key_for_source(source: str | None) -> int:
    key = LOCK_KEYS.get((source or "").lower(), LOCK_KEYS["other"])
    return key - (1 << 64) if key >= 1 << 63 else key


def advisory_lock_key(value: int) -> int:
    return value - (1 << 64) if value >= 1 << 63 else value


def db_lock_max_writers() -> int:
    return max(1, int(os.environ.get("DB_LOCK_MAX_WRITERS", "4")))


def release_db_lock(conn, lock_name: str) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(%s)", (lock_key_for_source(lock_name),))


def try_acquire_db_lock(conn, lock_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s) AS locked", (lock_key_for_source(lock_name),))
        row = cur.fetchone()
    return bool(row and row["locked"])


def slot_lock_key(slot: int) -> int:
    return advisory_lock_key(DB_SLOT_LOCK_BASE + slot)


def try_acquire_db_slot(conn) -> int | None:
    with conn.cursor() as cur:
        for slot in range(db_lock_max_writers()):
            cur.execute("SELECT pg_try_advisory_lock(%s) AS locked", (slot_lock_key(slot),))
            row = cur.fetchone()
            if row and row["locked"]:
                return slot
    return None


def release_db_slot(conn, slot: int) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(%s)", (slot_lock_key(slot),))


def wait_for_db_lock(lock_name: str):
    timeout_seconds = int(os.environ.get("DB_LOCK_WAIT_TIMEOUT_SECONDS", "1800"))
    poll_seconds = max(1, int(os.environ.get("DB_LOCK_POLL_SECONDS", "5")))
    deadline = time.monotonic() + timeout_seconds
    attempt = 0
    while True:
        attempt += 1
        conn = get_db_connection()
        slot = None
        try:
            slot = try_acquire_db_slot(conn)
            if slot is not None:
                if try_acquire_db_lock(conn, lock_name):
                    return conn, slot
                release_db_slot(conn, slot)
        except OperationalError:
            conn.close()
            raise
        conn.close()
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Timed out waiting for DB lock: {lock_name}")
        if attempt == 1 or attempt % 12 == 0:
            print(f"[db-lock] still waiting lock={lock_name} attempts={attempt} max_writers={db_lock_max_writers()}")
        time.sleep(poll_seconds)


def lock_name_for_command(func_name: str, args) -> str | None:
    if func_name == "command_monitor":
        return "twitter"
    if func_name in {"command_register_client", "command_seed_system_targets"}:
        return "admin"
    if func_name == "command_cleanup":
        return "cleanup"
    if func_name == "command_subscribe":
        if getattr(args, "action", None) in {"add", "remove", "set"}:
            return "manage"
        return None
    match = re.match(r"^command_(?:monitor|refresh)_(.+?)(?:_playback_urls)?$", func_name)
    if match:
        return match.group(1)
    return None


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


def parse_target_value(target: str) -> dict[str, str]:
    normalized = normalize_target(target)
    if normalized.lower().startswith("avgood:"):
        value = normalize_avgood_target_value(normalized[len("avgood:") :].strip())
        return {"source": AVGOOD_SOURCE, "kind": AVGOOD_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if normalized.lower().startswith("avgood.com:"):
        value = normalize_avgood_target_value(normalized[len("avgood.com:") :].strip())
        return {"source": AVGOOD_SOURCE, "kind": AVGOOD_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if is_avgood_target_url(normalized):
        value = normalize_avgood_target_value(normalized)
        return {"source": AVGOOD_SOURCE, "kind": AVGOOD_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if normalized.lower().startswith("705hs:"):
        value = normalize_hs705_target_value(normalized[len("705hs:") :].strip())
        return {"source": HS705_SOURCE, "kind": HS705_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if normalized.lower().startswith("705hs.com:"):
        value = normalize_hs705_target_value(normalized[len("705hs.com:") :].strip())
        return {"source": HS705_SOURCE, "kind": HS705_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if normalized.lower().startswith("992kp:"):
        value = normalize_hs705_target_value(normalized[len("992kp:") :].strip())
        return {"source": HS705_SOURCE, "kind": HS705_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if normalized.lower().startswith("992kp.com:"):
        value = normalize_hs705_target_value(normalized[len("992kp.com:") :].strip())
        return {"source": HS705_SOURCE, "kind": HS705_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if is_hs705_target_url(normalized):
        value = normalize_hs705_target_value(normalized)
        return {"source": HS705_SOURCE, "kind": HS705_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if normalized.lower().startswith("xxxtik:"):
        value = normalize_xxxtik_target_value(normalized[len("xxxtik:") :].strip())
        return {"source": XXXTIK_SOURCE, "kind": XXXTIK_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if normalized.lower().startswith("xxxtik.com:"):
        value = normalize_xxxtik_target_value(normalized[len("xxxtik.com:") :].strip())
        return {"source": XXXTIK_SOURCE, "kind": XXXTIK_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if is_xxxtik_target_url(normalized):
        value = normalize_xxxtik_target_value(normalized)
        return {"source": XXXTIK_SOURCE, "kind": XXXTIK_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if normalized.lower().startswith("affair:"):
        value = normalize_affair_target_value(normalized[len("affair:") :].strip())
        return {"source": AFFAIR_SOURCE, "kind": AFFAIR_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if normalized.lower().startswith("911bl:"):
        value = normalize_affair_target_value(normalized[len("911bl:") :].strip())
        return {"source": AFFAIR_SOURCE, "kind": AFFAIR_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if normalized.lower().startswith("911bl.com:"):
        value = normalize_affair_target_value(normalized[len("911bl.com:") :].strip())
        return {"source": AFFAIR_SOURCE, "kind": AFFAIR_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if is_affair_target_url(normalized):
        value = normalize_affair_target_value(normalized)
        return {"source": AFFAIR_SOURCE, "kind": AFFAIR_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if normalized.lower().startswith("attach:"):
        value = normalize_attach_target_value(normalized[len("attach:") :].strip())
        return {"source": ATTACH_SOURCE, "kind": ATTACH_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if normalized.lower().startswith("hlcgw:"):
        value = normalize_attach_target_value(normalized[len("hlcgw:") :].strip())
        return {"source": ATTACH_SOURCE, "kind": ATTACH_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if normalized.lower().startswith("attach.bslqmdvk.cc:"):
        value = normalize_attach_target_value(normalized[len("attach.bslqmdvk.cc:") :].strip())
        return {"source": ATTACH_SOURCE, "kind": ATTACH_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if is_attach_target_url(normalized):
        value = normalize_attach_target_value(normalized)
        return {"source": ATTACH_SOURCE, "kind": ATTACH_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if normalized.lower().startswith("dirtyship:"):
        value = normalize_dirtyship_target_value(normalized[len("dirtyship:") :].strip())
        return {"source": DIRTYSHIP_SOURCE, "kind": DIRTYSHIP_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if normalized.lower().startswith("dirtyship.com:"):
        value = normalize_dirtyship_target_value(normalized[len("dirtyship.com:") :].strip())
        return {"source": DIRTYSHIP_SOURCE, "kind": DIRTYSHIP_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if is_dirtyship_target_url(normalized):
        value = normalize_dirtyship_target_value(normalized)
        return {"source": DIRTYSHIP_SOURCE, "kind": DIRTYSHIP_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if normalized.lower().startswith("influencersgonewild:"):
        value = normalize_influencersgonewild_target_value(normalized[len("influencersgonewild:") :].strip())
        return {"source": INFLUENCERSGONEWILD_SOURCE, "kind": INFLUENCERSGONEWILD_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if normalized.lower().startswith("igw:"):
        value = normalize_influencersgonewild_target_value(normalized[len("igw:") :].strip())
        return {"source": INFLUENCERSGONEWILD_SOURCE, "kind": INFLUENCERSGONEWILD_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if normalized.lower().startswith("influencersgonewild.com:"):
        value = normalize_influencersgonewild_target_value(normalized[len("influencersgonewild.com:") :].strip())
        return {"source": INFLUENCERSGONEWILD_SOURCE, "kind": INFLUENCERSGONEWILD_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if is_influencersgonewild_target_url(normalized):
        value = normalize_influencersgonewild_target_value(normalized)
        return {"source": INFLUENCERSGONEWILD_SOURCE, "kind": INFLUENCERSGONEWILD_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if normalized.lower().startswith("missav:"):
        value = normalize_missav_target_value(normalized[len("missav:") :].strip())
        return {"source": MISSAV_SOURCE, "kind": MISSAV_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if normalized.lower().startswith("missav.app:"):
        value = normalize_missav_target_value(normalized[len("missav.app:") :].strip())
        return {"source": MISSAV_SOURCE, "kind": MISSAV_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if is_missav_target_url(normalized):
        value = normalize_missav_target_value(normalized)
        return {"source": MISSAV_SOURCE, "kind": MISSAV_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if normalized.lower().startswith("bdrq:"):
        value = normalize_bdrq_target_value(normalized[len("bdrq:") :].strip())
        return {"source": BDRQ_SOURCE, "kind": BDRQ_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if normalized.lower().startswith("bdrq45:"):
        value = normalize_bdrq_target_value(normalized[len("bdrq45:") :].strip())
        return {"source": BDRQ_SOURCE, "kind": BDRQ_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if normalized.lower().startswith("bdrq12:"):
        value = normalize_bdrq_target_value(normalized[len("bdrq12:") :].strip())
        return {"source": BDRQ_SOURCE, "kind": BDRQ_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if is_bdrq_target_url(normalized):
        value = normalize_bdrq_target_value(normalized)
        return {"source": BDRQ_SOURCE, "kind": BDRQ_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if normalized.lower().startswith("1mtif:"):
        value = normalize_mtif_target_value(normalized[len("1mtif:") :].strip())
        return {"source": MTIF_SOURCE, "kind": MTIF_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if normalized.lower().startswith("mtif:"):
        value = normalize_mtif_target_value(normalized[len("mtif:") :].strip())
        return {"source": MTIF_SOURCE, "kind": MTIF_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if is_mtif_target_url(normalized):
        value = normalize_mtif_target_value(normalized)
        return {"source": MTIF_SOURCE, "kind": MTIF_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if normalized.lower().startswith("badnews:"):
        value = normalize_badnews_target_value(normalized[len("badnews:") :].strip())
        return {"source": BADNEWS_SOURCE, "kind": BADNEWS_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if normalized.lower().startswith("bad.news:"):
        value = normalize_badnews_target_value(normalized[len("bad.news:") :].strip())
        return {"source": BADNEWS_SOURCE, "kind": BADNEWS_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if is_badnews_target_url(normalized):
        value = normalize_badnews_target_value(normalized)
        return {"source": BADNEWS_SOURCE, "kind": BADNEWS_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if normalized.lower().startswith("91porn:"):
        value = normalize_91porn_target_value(normalized[len("91porn:") :].strip())
        return {"source": PORN91_SOURCE, "kind": PORN91_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if is_91porn_target_url(normalized):
        value = normalize_91porn_target_value(normalized)
        return {"source": PORN91_SOURCE, "kind": PORN91_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if normalized.lower().startswith("91rb:"):
        value = normalize_91rb_target_value(normalized[len("91rb:") :].strip())
        return {"source": RB91_SOURCE, "kind": RB91_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if normalized.lower().startswith("rb91:"):
        value = normalize_91rb_target_value(normalized[len("rb91:") :].strip())
        return {"source": RB91_SOURCE, "kind": RB91_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if is_91rb_target_url(normalized):
        value = normalize_91rb_target_value(normalized)
        return {"source": RB91_SOURCE, "kind": RB91_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if normalized.lower().startswith("91porna:"):
        value = normalize_porna91_target_value(normalized[len("91porna:") :].strip())
        return {"source": PORNA91_SOURCE, "kind": PORNA91_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if normalized.lower().startswith("porna91:"):
        value = normalize_porna91_target_value(normalized[len("porna91:") :].strip())
        return {"source": PORNA91_SOURCE, "kind": PORNA91_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if is_porna91_target_url(normalized):
        value = normalize_porna91_target_value(normalized)
        return {"source": PORNA91_SOURCE, "kind": PORNA91_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if normalized.lower().startswith("tikporn:"):
        value = normalize_tikporn_target_value(normalized[len("tikporn:") :].strip())
        return {"source": TIKPORN_SOURCE, "kind": TIKPORN_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if normalized.lower().startswith("tik.porn:"):
        value = normalize_tikporn_target_value(normalized[len("tik.porn:") :].strip())
        return {"source": TIKPORN_SOURCE, "kind": TIKPORN_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if is_tikporn_target_url(normalized):
        value = normalize_tikporn_target_value(normalized)
        return {"source": TIKPORN_SOURCE, "kind": TIKPORN_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if normalized.lower().startswith("rou:"):
        value = normalize_rou_target_value(normalized[len("rou:") :].strip())
        return {"source": ROU_SOURCE, "kind": ROU_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if is_rou_target_url(normalized):
        value = normalize_rou_target_value(normalized)
        return {"source": ROU_SOURCE, "kind": ROU_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if normalized.lower().startswith("dadaafa:"):
        value = normalize_dadaafa_target_value(normalized[len("dadaafa:") :].strip())
        return {"source": DADAAFA_SOURCE, "kind": DADAAFA_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if is_dadaafa_target_url(normalized):
        value = normalize_dadaafa_target_value(normalized)
        return {"source": DADAAFA_SOURCE, "kind": DADAAFA_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if normalized.lower().startswith("18j:"):
        value = normalize_18j_target_value(normalized[len("18j:") :].strip())
        return {"source": J18_SOURCE, "kind": J18_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if is_18j_target_url(normalized):
        value = normalize_18j_target_value(normalized)
        return {"source": J18_SOURCE, "kind": J18_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if normalized.lower().startswith("douyin:"):
        value = normalize_douyin_target_value(normalized[len("douyin:") :].strip())
        return {"source": DOUYIN_SOURCE, "kind": DOUYIN_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if is_douyin_target_url(normalized):
        value = normalize_douyin_target_value(normalized)
        return {"source": DOUYIN_SOURCE, "kind": DOUYIN_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if normalized.lower().startswith("18mh:"):
        value = normalize_18mh_target_value(normalized[len("18mh:") :].strip())
        return {"source": MH18_SOURCE, "kind": MH18_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if is_18mh_target_url(normalized):
        value = normalize_18mh_target_value(normalized)
        return {"source": MH18_SOURCE, "kind": MH18_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if normalized.lower().startswith("baoliao51:"):
        value = normalize_baoliao51_target_value(normalized[len("baoliao51:") :].strip())
        return {"source": BAOLIAO51_SOURCE, "kind": BAOLIAO51_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if is_baoliao51_target_url(normalized):
        value = normalize_baoliao51_target_value(normalized)
        return {"source": BAOLIAO51_SOURCE, "kind": BAOLIAO51_KIND, "value": value, "normalized_value": normalize_site_target_key(value)}

    if normalized.lower().startswith("cg91:"):
        value = normalize_cg91_target_value(normalized[len("cg91:") :].strip())
        return {
            "source": CG91_SOURCE,
            "kind": CG91_KIND,
            "value": value,
            "normalized_value": normalize_site_target_key(value),
        }

    if is_cg91_target_url(normalized):
        value = normalize_cg91_target_value(normalized)
        return {
            "source": CG91_SOURCE,
            "kind": CG91_KIND,
            "value": value,
            "normalized_value": normalize_site_target_key(value),
        }

    if normalized.lower().startswith("heiliao:"):
        value = normalize_heiliao_target_value(normalized[len("heiliao:") :].strip())
        return {
            "source": HEILIAO_SOURCE,
            "kind": HEILIAO_KIND,
            "value": value,
            "normalized_value": normalize_heiliao_target_key(value),
        }

    if is_heiliao_target_url(normalized):
        value = normalize_heiliao_target_value(normalized)
        return {
            "source": HEILIAO_SOURCE,
            "kind": HEILIAO_KIND,
            "value": value,
            "normalized_value": normalize_heiliao_target_key(value),
        }

    if normalized.lower().startswith("youtube:"):
        value = normalize_youtube_target_value(normalized[8:].strip())
        return {
            "source": "youtube",
            "kind": "channel",
            "value": value,
            "normalized_value": value.lower(),
        }

    if is_youtube_target_url(normalized):
        value = normalize_youtube_target_value(normalized)
        return {
            "source": "youtube",
            "kind": "channel",
            "value": value,
            "normalized_value": value.lower(),
        }

    if normalized.startswith("search:"):
        keyword = normalized[7:].strip()
        if not keyword:
            raise ValueError("Keyword target cannot be empty.")
        return {
            "source": "twitter",
            "kind": "keyword",
            "value": keyword,
            "normalized_value": keyword.lower(),
        }

    if not normalized:
        raise ValueError("Target cannot be empty.")

    return {
        "source": "twitter",
        "kind": "user",
        "value": normalized,
        "normalized_value": normalized.lower(),
    }


def format_target(kind: str, value: str) -> str:
    return f"search:{value}" if kind == "keyword" else value


def format_target_row(target_row: dict) -> str:
    if target_row.get("source") == AVGOOD_SOURCE:
        return f"avgood:{target_row['value']}"
    if target_row.get("source") == HS705_SOURCE:
        return f"705hs:{target_row['value']}"
    if target_row.get("source") == XXXTIK_SOURCE:
        return f"xxxtik:{target_row['value']}"
    if target_row.get("source") == AFFAIR_SOURCE:
        return f"affair:{target_row['value']}"
    if target_row.get("source") == ATTACH_SOURCE:
        return f"attach:{target_row['value']}"
    if target_row.get("source") == DIRTYSHIP_SOURCE:
        return f"dirtyship:{target_row['value']}"
    if target_row.get("source") == INFLUENCERSGONEWILD_SOURCE:
        return f"influencersgonewild:{target_row['value']}"
    if target_row.get("source") == MISSAV_SOURCE:
        return f"missav:{target_row['value']}"
    if target_row.get("source") == BDRQ_SOURCE:
        return f"bdrq:{target_row['value']}"
    if target_row.get("source") == MTIF_SOURCE:
        return f"1mtif:{target_row['value']}"
    if target_row.get("source") == BADNEWS_SOURCE:
        return f"badnews:{target_row['value']}"
    if target_row.get("source") == PORN91_SOURCE:
        return f"91porn:{target_row['value']}"
    if target_row.get("source") == RB91_SOURCE:
        return f"91rb:{target_row['value']}"
    if target_row.get("source") == PORNA91_SOURCE:
        return f"91porna:{target_row['value']}"
    if target_row.get("source") == TIKPORN_SOURCE:
        return f"tikporn:{target_row['value']}"
    if target_row.get("source") == ROU_SOURCE:
        return f"rou:{target_row['value']}"
    if target_row.get("source") == DADAAFA_SOURCE:
        return f"dadaafa:{target_row['value']}"
    if target_row.get("source") == J18_SOURCE:
        return f"18j:{target_row['value']}"
    if target_row.get("source") == DOUYIN_SOURCE:
        return f"douyin:{target_row['value']}"
    if target_row.get("source") == MH18_SOURCE:
        return f"18mh:{target_row['value']}"
    if target_row.get("source") == BAOLIAO51_SOURCE:
        return f"baoliao51:{target_row['value']}"
    if target_row.get("source") == CG91_SOURCE:
        return f"cg91:{target_row['value']}"
    if target_row.get("source") == HEILIAO_SOURCE:
        return f"heiliao:{target_row['value']}"
    if target_row.get("source") == "youtube":
        return f"youtube:{target_row['value']}"
    return format_target(target_row["kind"], target_row["value"])


def non_empty(value) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def normalized_presentation_source(source: str | None) -> str:
    source_key = (source or "").strip().lower()
    if source_key in {"x", "twitter"}:
        return "twitter"
    if source_key in {"yt", "youtube"}:
        return "youtube"
    if source_key in {"avgood", "avgood.com"}:
        return AVGOOD_SOURCE
    if source_key in {"705hs", "705hs.com", "992kp", "992kp.com"}:
        return HS705_SOURCE
    if source_key in {"xxxtik", "xxxtik.com"}:
        return XXXTIK_SOURCE
    if source_key in {"affair", "911bl", "911bl.com", "affair.zhkrsawaw.cc"}:
        return AFFAIR_SOURCE
    if source_key in {"attach", "attach.bslqmdvk.cc", "hlcgw", "hlcgw.com"}:
        return ATTACH_SOURCE
    if source_key in {"dirtyship", "dirtyship.com"}:
        return DIRTYSHIP_SOURCE
    if source_key in {"influencersgonewild", "influencersgonewild.com", "igw"}:
        return INFLUENCERSGONEWILD_SOURCE
    if source_key in {"missav", "missav.app"}:
        return MISSAV_SOURCE
    if source_key in {"91", "cg91"}:
        return CG91_SOURCE
    if source_key in {"51", "baoliao51"}:
        return BAOLIAO51_SOURCE
    if source_key in {"18mh", "mh18"}:
        return MH18_SOURCE
    if source_key in {"rou", "rouvideo", "rou.video"}:
        return ROU_SOURCE
    if source_key in {"dadaafa", "dada", "dadaafa.cc"}:
        return DADAAFA_SOURCE
    if source_key in {"18j", "18j.tv", "j18"}:
        return J18_SOURCE
    if source_key in {"1mtif", "mtif", "1mtif.sbs"}:
        return MTIF_SOURCE
    if source_key in {"tikporn", "tik", "tik.porn"}:
        return TIKPORN_SOURCE
    if source_key in {"badnews", "bad.news"}:
        return BADNEWS_SOURCE
    if source_key in {"bdrq", "bdrq45", "bdrq45.cc", "bdrq12", "bdrq12.cc"}:
        return BDRQ_SOURCE
    if source_key in {"91porn", "91porn.com"}:
        return PORN91_SOURCE
    if source_key in {"91rb", "91rb.com", "rb91"}:
        return RB91_SOURCE
    if source_key in {"91porna", "porna91", "91porna.com"}:
        return PORNA91_SOURCE
    return source_key


def source_display_name(source: str | None) -> str:
    source_key = normalized_presentation_source(source)
    return {
        "twitter": "X",
        "youtube": "YouTube",
        HEILIAO_SOURCE: "黑料",
        CG91_SOURCE: "91吃瓜",
        BAOLIAO51_SOURCE: "51爆料",
        DOUYIN_SOURCE: "抖阴",
        MH18_SOURCE: MH18_SITE_NAME,
        ROU_SOURCE: ROU_SITE_NAME,
        DADAAFA_SOURCE: DADAAFA_SITE_NAME,
        AVGOOD_SOURCE: AVGOOD_SITE_NAME,
        HS705_SOURCE: HS705_SITE_NAME,
        XXXTIK_SOURCE: XXXTIK_SITE_NAME,
        AFFAIR_SOURCE: AFFAIR_SITE_NAME,
        ATTACH_SOURCE: ATTACH_SITE_NAME,
        DIRTYSHIP_SOURCE: DIRTYSHIP_SITE_NAME,
        INFLUENCERSGONEWILD_SOURCE: INFLUENCERSGONEWILD_SITE_NAME,
        MISSAV_SOURCE: MISSAV_SITE_NAME,
        J18_SOURCE: J18_SITE_NAME,
        MTIF_SOURCE: MTIF_SITE_NAME,
        TIKPORN_SOURCE: TIKPORN_SITE_NAME,
        BADNEWS_SOURCE: BADNEWS_SITE_NAME,
        BDRQ_SOURCE: BDRQ_SITE_NAME,
        PORN91_SOURCE: PORN91_SITE_NAME,
        RB91_SOURCE: RB91_SITE_NAME,
        PORNA91_SOURCE: PORNA91_SITE_NAME,
    }.get(source_key, source_key or "X")


def display_target(value: str | None) -> str | None:
    target = non_empty(value)
    if not target:
        return None
    separator_index = target.find(":")
    if separator_index > 0:
        target = target[separator_index + 1 :]
    return non_empty(target)


def twitter_username(value: str | None) -> str | None:
    username = non_empty(value)
    if not username:
        return None

    parsed = urlparse(username)
    if parsed.netloc.lower() in {"twitter.com", "www.twitter.com", "x.com", "www.x.com"}:
        parts = [part for part in parsed.path.split("/") if part]
        username = parts[0] if parts else ""
    else:
        username = username.lstrip("@").strip()
        slash_index = username.find("/")
        if slash_index >= 0:
            username = username[:slash_index]

    username = username.strip("@/ ")
    return username if re.fullmatch(r"[A-Za-z0-9_]{1,15}", username) else None


def youtube_profile_url(value: str | None) -> str | None:
    raw = non_empty(value)
    if not raw:
        return None
    if raw.lower().startswith("youtube:"):
        raw = raw[len("youtube:") :].strip()
    if re.fullmatch(r"UC[A-Za-z0-9_-]+", raw):
        return f"https://www.youtube.com/channel/{raw}"
    if raw.startswith("@"):
        return f"https://www.youtube.com/{raw}"

    parsed = urlparse(raw)
    if parsed.netloc.lower() not in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        return None

    query = parse_qs(parsed.query)
    user = non_empty((query.get("user") or [""])[0])
    if user:
        return f"https://www.youtube.com/user/{user}"
    playlist_id = non_empty((query.get("playlist_id") or [""])[0])
    if playlist_id:
        return f"https://www.youtube.com/playlist?list={playlist_id}"
    channel_id = non_empty((query.get("channel_id") or [""])[0])
    if channel_id:
        return f"https://www.youtube.com/channel/{channel_id}"

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0].lower() in {"channel", "user", "c"}:
        return f"https://www.youtube.com/{parts[0]}/{parts[1]}"
    if parts and parts[0].startswith("@"):
        return f"https://www.youtube.com/{parts[0]}"
    return None


def http_url(value: str | None) -> str | None:
    raw = non_empty(value)
    if not raw or re.search(r"\s", raw):
        return None

    parsed = urlparse(raw)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    return raw


def build_author_presentation(
    *,
    source: str | None,
    target: str | None,
    author: str | None,
    fullname: str | None,
    x_url: str | None,
    link: str | None,
) -> dict[str, str | None]:
    source_key = normalized_presentation_source(source)
    display_author = non_empty(fullname) or non_empty(author) or display_target(target) or source_display_name(source_key)
    display_handle = None
    author_profile_url = None
    author_profile_platform = None

    if source_key == "twitter":
        username = twitter_username(author) or twitter_username(target) or twitter_username(x_url) or twitter_username(link)
        display_handle = f"@{username}" if username and non_empty(fullname) else None
        author_profile_url = f"https://x.com/{username}" if username else None
        author_profile_platform = "X" if username else None
    elif source_key == "youtube":
        author_profile_url = youtube_profile_url(target) or youtube_profile_url(link)
        author_profile_platform = "YouTube" if author_profile_url else None
    elif source_key in DETAIL_LINK_PROFILE_SOURCES:
        author_profile_url = http_url(link)
        author_profile_platform = source_display_name(source_key) if author_profile_url else None

    return {
        "display_author": display_author,
        "display_handle": display_handle,
        "author_profile_url": author_profile_url,
        "author_profile_platform": author_profile_platform,
    }


def build_item_author_presentation(target_row: dict, *, author: str | None, fullname: str | None, x_url: str | None, link: str | None) -> dict[str, str | None]:
    return build_author_presentation(
        source=target_row.get("source"),
        target=format_target_row(target_row),
        author=author,
        fullname=fullname,
        x_url=x_url,
        link=link,
    )


def normalize_heiliao_target_value(raw: str) -> str:
    value = (raw or HEILIAO_DEFAULT_BASE_URL).strip().rstrip("/")
    if not value:
        value = HEILIAO_DEFAULT_BASE_URL
    parsed = urlparse(value if "://" in value else f"https://{value}")
    if not parsed.netloc:
        raise ValueError("Heiliao target must be a URL or host.")
    return urlunparse((parsed.scheme or "https", parsed.netloc.lower(), "", "", "", ""))


def normalize_heiliao_target_key(value: str) -> str:
    return normalize_site_target_key(value)


def normalize_site_target_key(value: str) -> str:
    parsed = urlparse(value)
    return parsed.netloc.lower() or value.lower().rstrip("/")


def is_heiliao_target_url(raw: str) -> bool:
    value = raw.strip().lower()
    if not value:
        return False
    try:
        parsed = urlparse(value if "://" in value else f"https://{value}")
    except Exception:
        return False
    host = parsed.netloc.lower()
    return host == "among.uvsoskqus.cc" or host.endswith(".uvsoskqus.cc")


def normalize_cg91_target_value(raw: str) -> str:
    value = (raw or CG91_DEFAULT_BASE_URL).strip().rstrip("/")
    if not value:
        value = CG91_DEFAULT_BASE_URL
    parsed = urlparse(value if "://" in value else f"https://{value}")
    if not parsed.netloc:
        raise ValueError("91cg target must be a URL or host.")
    return urlunparse((parsed.scheme or "https", parsed.netloc.lower(), "", "", "", ""))


def is_cg91_target_url(raw: str) -> bool:
    value = raw.strip().lower()
    if not value:
        return False
    try:
        parsed = urlparse(value if "://" in value else f"https://{value}")
    except Exception:
        return False
    host = parsed.netloc.lower()
    return host in {"91cg1.com", "www.91cg1.com"}


def normalize_douyin_target_value(raw: str) -> str:
    value = (raw or DOUYIN_DEFAULT_BASE_URL).strip().rstrip("/")
    if not value:
        value = DOUYIN_DEFAULT_BASE_URL
    parsed = urlparse(value if "://" in value else f"https://{value}")
    if not parsed.netloc:
        raise ValueError("Douyin target must be a URL or host.")
    return urlunparse((parsed.scheme or "https", parsed.netloc.lower(), "", "", "", ""))


def is_douyin_target_url(raw: str) -> bool:
    value = raw.strip().lower()
    if not value:
        return False
    try:
        parsed = urlparse(value if "://" in value else f"https://{value}")
    except Exception:
        return False
    host = parsed.netloc.lower()
    default_host = urlparse(DOUYIN_DEFAULT_BASE_URL).netloc.lower()
    return bool(default_host and host == default_host)


def normalize_youtube_channel_id(raw: str) -> str:
    value = raw.strip()
    if not value:
        raise ValueError("YouTube channel target cannot be empty.")
    channel_id = value
    parsed = urlparse(value)
    if parsed.netloc.lower() in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        query = parse_qs(parsed.query)
        feed_channel_id = (query.get("channel_id") or [""])[0].strip()
        if feed_channel_id:
            channel_id = feed_channel_id
        else:
            parts = [part for part in parsed.path.split("/") if part]
            if len(parts) >= 2 and parts[0].lower() == "channel":
                channel_id = parts[1]
    elif value.lower().startswith("/channel/"):
        parts = [part for part in value.split("/") if part]
        if len(parts) >= 2:
            channel_id = parts[1]
    if not re.fullmatch(r"UC[A-Za-z0-9_-]{20,}", channel_id):
        raise ValueError("YouTube channel target must be a channel ID, /channel/UC... URL, or feeds/videos.xml?channel_id=UC... URL.")
    return channel_id


def normalize_youtube_feed_url(raw: str) -> str:
    value = raw.strip()
    if not value:
        raise ValueError("YouTube feed target cannot be empty.")

    parsed = urlparse(value)
    host = parsed.netloc.lower()
    if host not in {"youtube.com", "www.youtube.com", "m.youtube.com"} or parsed.path != "/feeds/videos.xml":
        raise ValueError("YouTube feed target must be a YouTube feed URL.")

    query = parse_qs(parsed.query)
    channel_id = (query.get("channel_id") or [""])[0].strip()
    if channel_id and re.fullmatch(r"UC[A-Za-z0-9_-]{20,}", channel_id):
        return channel_id

    user = (query.get("user") or [""])[0].strip()
    if user:
        return urlunparse(("https", "www.youtube.com", "/feeds/videos.xml", "", urlencode({"user": user}), ""))

    playlist_id = (query.get("playlist_id") or [""])[0].strip()
    if playlist_id:
        return urlunparse(("https", "www.youtube.com", "/feeds/videos.xml", "", urlencode({"playlist_id": playlist_id}), ""))

    raise ValueError("YouTube feed target must include channel_id, user, or playlist_id.")


def is_youtube_target_url(raw: str) -> bool:
    value = raw.strip()
    if not value:
        return False
    try:
        parsed = urlparse(value)
    except ValueError:
        return value.lower().startswith(("/channel/", "/feeds/videos.xml"))
    host = parsed.netloc.lower()
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        return parsed.path == "/feeds/videos.xml" or parsed.path.startswith("/channel/")
    return value.lower().startswith(("/channel/", "/feeds/videos.xml"))


def normalize_youtube_target_value(raw: str) -> str:
    value = raw.strip()
    if not value:
        raise ValueError("YouTube target cannot be empty.")

    if value.lower().startswith("/channel/"):
        parts = [part for part in value.split("/") if part]
        if len(parts) >= 2:
            return normalize_youtube_channel_id(parts[1])
        raise ValueError("YouTube channel target cannot be empty.")

    if value.lower().startswith("/feeds/videos.xml") or "youtube.com/feeds/videos.xml" in value.lower():
        return normalize_youtube_feed_url(value)

    if "youtube.com" in value.lower():
        parsed = urlparse(value)
        if parsed.path == "/feeds/videos.xml":
            return normalize_youtube_feed_url(value)
        return normalize_youtube_channel_id(value)

    return normalize_youtube_channel_id(value)


def create_opaque_token(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(24)}"


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


def normalize_instance_config(instances: list[object]) -> list[dict[str, object]]:
    normalized_instances: list[dict[str, object]] = []
    for item in instances:
        if isinstance(item, str):
            url = item.rstrip("/")
            if not url:
                continue
            normalized_instances.append({"url": url, "priority": 0})
            continue

        if isinstance(item, dict):
            raw_url = item.get("url")
            if not isinstance(raw_url, str):
                continue
            url = raw_url.rstrip("/")
            if not url:
                continue

            raw_priority = item.get("priority", 0)
            try:
                priority = int(raw_priority)
            except (TypeError, ValueError):
                priority = 0

            normalized_instances.append({"url": url, "priority": priority})

    return normalized_instances


def order_instances_for_attempts(
    instances: list[object],
    runtime_penalties: dict[str, int] | None = None,
) -> list[str]:
    runtime_penalties = runtime_penalties or {}
    normalized_instances = normalize_instance_config(instances)

    # Lower score wins: explicit priority first, then runtime 403 penalties.
    for item in normalized_instances:
        url = str(item["url"])
        item["sort_score"] = int(item["priority"]) + runtime_penalties.get(url, 0)
        item["shuffle_key"] = random.random()

    normalized_instances.sort(key=lambda item: (int(item["sort_score"]), float(item["shuffle_key"])))
    return [str(item["url"]) for item in normalized_instances]


def select_targets_for_shard(target_rows: list[dict], shard_index: int, shard_count: int) -> list[dict]:
    if shard_count <= 1:
        return target_rows

    selected_targets: list[dict] = []
    for target_row in target_rows:
        target_key = format_target_row(target_row).lower()
        digest = hashlib.sha256(target_key.encode("utf-8")).hexdigest()
        bucket = int(digest[:8], 16) % shard_count
        if bucket == shard_index:
            selected_targets.append(target_row)

    return selected_targets


def get_random_user_agent():
    ua_list = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/121.0.0.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    ]
    return random.choice(ua_list)


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

        for prefix in VIDEO_THUMB_PREFIXES:
            match = re.search(rf"(?:/pic/)?({prefix}/[^#]+)", path)
            if match:
                return f"https://pbs.twimg.com/{match.group(1)}"

        match = re.search(r"(pbs\.twimg\.com/media/[^?&]+)", path)
        if match:
            return "https://" + match.group(1)
    except Exception as exc:
        print(f"[图片解析] 还原 URL 失败 {nitter_url}: {exc}")

    return nitter_url


def get_original_video_url(video_url: str, instance: str) -> str:
    if not video_url:
        return ""

    try:
        if video_url.startswith("//"):
            absolute_url = "https:" + video_url
        elif video_url.startswith("/"):
            absolute_url = instance.rstrip("/") + video_url
        else:
            absolute_url = video_url

        parsed = urlparse(absolute_url)
        decoded_path = unquote(parsed.path)

        if decoded_path.startswith("/video/"):
            parts = decoded_path.split("/", 3)
            if len(parts) == 4:
                decoded_target = unquote(parts[3])
                if decoded_target.startswith("http://") or decoded_target.startswith("https://"):
                    return decoded_target
                if decoded_target.startswith("//"):
                    return "https:" + decoded_target
                if decoded_target.startswith("video.twimg.com/"):
                    return "https://" + decoded_target

        if decoded_path.startswith("/pic/video.twimg.com/"):
            suffix = decoded_path[len("/pic/") :]
            return "https://" + suffix

        if "video.twimg.com" in decoded_path:
            match = re.search(r"(video\.twimg\.com/[^?#\"'<>\\s]+(?:\\?[^#\"'<>\\s]+)?)", decoded_path)
            if match:
                return "https://" + match.group(1)

        return absolute_url
    except Exception as exc:
        print(f"[视频解析] 还原 URL 失败 {video_url}: {exc}")
        return video_url


def upload_to_imgbb(image_url: str) -> str | None:
    if not IMGBB_API_KEY:
        return None

    original_url = image_url
    image_url = get_original_image_url(image_url)
    if image_url != original_url:
        print(f"[图床] 已还原图片地址: {image_url}")

    try:
        print(f"[图床] 正在从 {image_url} 下载图片...")
        img_response = requests.get(
            image_url,
            timeout=30,
            headers={
                "User-Agent": get_random_user_agent(),
                "Referer": "https://twitter.com/",
            },
        )
        img_response.raise_for_status()
        content_type = img_response.headers.get("Content-Type", "")
        if not content_type.lower().startswith("image/"):
            print(f"[图床] 下载结果不是图片，跳过上传: {content_type or 'unknown'}")
            return None

        image_base64 = base64.b64encode(img_response.content).decode("utf-8")
        print("[图床] 正在上传到 ImgBB...")
        upload_response = requests.post(
            "https://api.imgbb.com/1/upload",
            data={
                "key": IMGBB_API_KEY,
                "image": image_base64,
            },
            timeout=30,
        )
        upload_response.raise_for_status()
        payload = upload_response.json()
        if payload.get("success") and payload.get("data", {}).get("url"):
            uploaded_url = payload["data"]["url"]
            print(f"[图床] ImgBB 上传成功: {uploaded_url}")
            return uploaded_url

        print(f"[图床] ImgBB 上传失败: {payload}")
    except Exception as exc:
        print(f"[图床] ImgBB 上传异常: {exc}")

    return None


def rewrite_images_with_imgbb(tweets: list[dict]) -> None:
    if not IMGBB_API_KEY:
        return

    uploaded_cache: dict[str, str] = {}
    for tweet in tweets:
        images = tweet.get("images") or []
        if not images:
            continue

        rewritten_images: list[str] = []
        for image_url in images:
            cached = uploaded_cache.get(image_url)
            if cached:
                rewritten_images.append(cached)
                continue

            uploaded = upload_to_imgbb(image_url)
            final_url = uploaded or image_url
            uploaded_cache[image_url] = final_url
            rewritten_images.append(final_url)

        tweet["images"] = rewritten_images


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


def scrape_nitter_with_playwright(
    target: str,
    dynamic_instances: list[str] | None = None,
    runtime_penalties: dict[str, int] | None = None,
) -> list[dict]:
    try:
        from playwright.sync_api import sync_playwright
        from playwright_stealth import stealth_sync
    except ModuleNotFoundError as exc:
        print(f"[{target}] 缺少抓取依赖: {exc}")
        return []

    is_search = target.startswith("search:")
    keyword = target[7:] if is_search else target

    instances = list(dynamic_instances or order_instances_for_attempts(list(dynamic_instances or NITTER_INSTANCES)))

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
                        if runtime_penalties is not None:
                            runtime_penalties[instance] = runtime_penalties.get(instance, 0) + 100
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
                    video_poster_url = None
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
                                    video_poster_url = get_original_image_url(full_poster)
                                    if video_poster_url not in images:
                                        images.append(video_poster_url)

                            v_src = (
                                video_el.get("src", "")
                                or video_el.get("data-url", "")
                                or video_el.get("data-src", "")
                            )
                            if v_src:
                                video_url = get_original_video_url(v_src, instance)
                    except Exception as exc:
                        print(f"[{target}] 视频提取异常: {exc}")

                    content_el = item.select_one(".tweet-content")
                    link_el = item.select_one(".tweet-link")
                    date_el = item.select_one(".tweet-date a")
                    author_el = item.select_one(".username")
                    fullname_el = item.select_one(".fullname")
                    if not content_el or not link_el:
                        continue

                    link_href = link_el.get("href", "")
                    tweet_id = link_href.split("/status/")[-1].split("#")[0] if "/status/" in link_href else link_href
                    nitter_link = instance.rstrip("/") + link_href
                    raw_content = content_el.get_text(strip=True)
                    clean_content = raw_content.replace("€∋", "").strip()
                    published = date_el.get("title", "") if date_el else ""

                    tweet = {
                        "target": target,
                        "target_type": "keyword" if is_search else "user",
                        "target_value": keyword,
                        "content": clean_content,
                        "raw_content": raw_content,
                        "translated_content": translate_text(clean_content) if AUTO_TRANSLATE else None,
                        "link": nitter_link,
                        "x_url": nitter_to_x_url(nitter_link),
                        "published": published,
                        "author": author_el.get_text(strip=True) if author_el else keyword,
                        "fullname": fullname_el.get_text(" ", strip=True) if fullname_el else None,
                        "guid": tweet_id,
                        "is_retweet": is_retweet,
                        "images": images,
                        "video_url": video_url,
                        "video_poster_url": video_poster_url,
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


def upsert_target(conn, target: str) -> dict:
    parsed = parse_target_value(target)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO targets (source, kind, value, normalized_value)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (source, kind, normalized_value)
            DO UPDATE SET value = EXCLUDED.value
            RETURNING id, source, kind, value, normalized_value
            """,
            (parsed["source"], parsed["kind"], parsed["value"], parsed["normalized_value"]),
        )
        return cur.fetchone()


def load_active_targets(conn, source: str = "twitter") -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                t.id,
                t.source,
                t.kind,
                t.value,
                t.normalized_value,
                cs.last_guid
            FROM targets t
            LEFT JOIN crawl_state cs ON cs.target_id = t.id
            WHERE t.source = %s
              AND EXISTS (
                SELECT 1
                FROM subscriptions s
                INNER JOIN clients c ON c.id = s.client_id
                WHERE s.target_id = t.id
                  AND c.status = 'active'
            )
            ORDER BY t.source, t.kind, LOWER(t.value)
            """,
            (source,),
        )
        return cur.fetchall()


def load_youtube_targets(conn) -> list[dict]:
    return load_active_targets(conn, "youtube")


def resolve_client(conn, api_key: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, api_key, feed_token, label, status
            FROM clients
            WHERE api_key = %s
            LIMIT 1
            """,
            (api_key,),
        )
        return cur.fetchone()


def list_subscriptions(conn, client_id: str) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.source, t.kind, t.value
            FROM subscriptions s
            INNER JOIN targets t ON t.id = s.target_id
            WHERE s.client_id = %s
            ORDER BY t.source, t.kind, LOWER(t.value)
            """,
            (client_id,),
        )
        rows = cur.fetchall()
    return [format_target_row(row) for row in rows]


def replace_subscriptions(conn, client_id: str, targets: list[str]) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM subscriptions WHERE client_id = %s", (client_id,))
        for target in targets:
            target_row = upsert_target(conn, target)
            cur.execute(
                """
                INSERT INTO subscriptions (client_id, target_id)
                VALUES (%s, %s)
                ON CONFLICT (client_id, target_id) DO NOTHING
                """,
                (client_id, target_row["id"]),
            )


def add_subscriptions(conn, client_id: str, targets: list[str]) -> None:
    with conn.cursor() as cur:
        for target in targets:
            target_row = upsert_target(conn, target)
            cur.execute(
                """
                INSERT INTO subscriptions (client_id, target_id)
                VALUES (%s, %s)
                ON CONFLICT (client_id, target_id) DO NOTHING
                """,
                (client_id, target_row["id"]),
            )


def remove_subscriptions(conn, client_id: str, targets: list[str]) -> None:
    with conn.cursor() as cur:
        for target in targets:
            parsed = parse_target_value(target)
            cur.execute(
                """
                DELETE FROM subscriptions
                WHERE client_id = %s
                  AND target_id IN (
                    SELECT id
                    FROM targets
                    WHERE source = %s AND kind = %s AND normalized_value = %s
                  )
                """,
                (client_id, parsed["source"], parsed["kind"], parsed["normalized_value"]),
            )


DEFAULT_SYSTEM_TARGETS = [
    {"target": "search:AI video", "category": "科技", "tags": ["AI", "科技"], "weight": 10},
    {"target": "search:robot demo", "category": "科技", "tags": ["机器人", "科技"], "weight": 8},
    {"target": "search:funny video", "category": "搞笑", "tags": ["搞笑"], "weight": 8},
    {"target": "search:cat video", "category": "宠物", "tags": ["猫", "宠物"], "weight": 7},
    {"target": "search:dog video", "category": "宠物", "tags": ["狗", "宠物"], "weight": 7},
    {"target": "search:NBA highlights", "category": "体育", "tags": ["NBA", "篮球", "体育"], "weight": 8},
    {"target": "search:football highlights", "category": "体育", "tags": ["足球", "体育"], "weight": 7},
    {"target": "search:movie trailer", "category": "影视", "tags": ["电影", "预告片", "影视"], "weight": 6},
    {"target": "search:game trailer", "category": "游戏", "tags": ["游戏", "预告片"], "weight": 6},
    {"target": "search:music video", "category": "音乐", "tags": ["音乐"], "weight": 6},
]


def parse_system_targets_file(path: str | None) -> list[dict]:
    if not path:
        return DEFAULT_SYSTEM_TARGETS

    with open(path, "r", encoding="utf-8") as file:
        payload = json.load(file)

    if not isinstance(payload, list):
        raise ValueError("System targets file must contain a JSON array.")

    targets = []
    for item in payload:
        if isinstance(item, str):
            targets.append({"target": item, "category": None, "tags": [], "weight": 0})
            continue
        if not isinstance(item, dict) or not str(item.get("target") or "").strip():
            raise ValueError("Each system target must be a string or object with target.")
        tags = item.get("tags") or []
        if not isinstance(tags, list):
            raise ValueError("System target tags must be a list.")
        targets.append(
            {
                "target": str(item["target"]).strip(),
                "category": str(item.get("category") or "").strip() or None,
                "tags": [str(tag).strip() for tag in tags if str(tag).strip()],
                "weight": int(item.get("weight") or 0),
            }
        )
    return targets


def seed_system_targets(conn, target_configs: list[dict]) -> dict[str, int]:
    upserted = 0
    with conn.cursor() as cur:
        for config in target_configs:
            target_row = upsert_target(conn, config["target"])
            cur.execute(
                """
                INSERT INTO target_profiles (target_id, scope, tags, category, weight, is_public_pool)
                VALUES (%s, 'system', %s, %s, %s, TRUE)
                ON CONFLICT (target_id) DO UPDATE SET
                    scope = 'system',
                    tags = EXCLUDED.tags,
                    category = EXCLUDED.category,
                    weight = EXCLUDED.weight,
                    is_public_pool = TRUE,
                    updated_at = NOW()
                """,
                (target_row["id"], Jsonb(config.get("tags") or []), config.get("category"), int(config.get("weight") or 0)),
            )
            upserted += 1
    return {"upserted": upserted}


def load_system_targets(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                t.id,
                t.source,
                t.kind,
                t.value,
                t.normalized_value,
                cs.last_guid
            FROM targets t
            INNER JOIN target_profiles tp ON tp.target_id = t.id
            LEFT JOIN crawl_state cs ON cs.target_id = t.id
            WHERE t.source = 'twitter'
              AND tp.scope = 'system'
              AND tp.is_public_pool = TRUE
            ORDER BY tp.weight DESC, t.source, t.kind, LOWER(t.value)
            """
        )
        return cur.fetchall()


def register_client(conn, label: str | None) -> dict:
    api_key = create_opaque_token("x2d")
    feed_token = create_opaque_token("feed")
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO clients (api_key, feed_token, label)
            VALUES (%s, %s, %s)
            RETURNING id, api_key, feed_token, label, created_at
            """,
            (api_key, feed_token, label),
        )
        return cur.fetchone()


def upsert_crawl_state(conn, target_id: str, *, last_guid: str | None, last_error: str | None, success: bool) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO crawl_state (target_id, last_guid, last_checked_at, last_success_at, last_error)
            VALUES (%s, %s, NOW(), CASE WHEN %s THEN NOW() ELSE NULL END, %s)
            ON CONFLICT (target_id)
            DO UPDATE SET
                last_guid = COALESCE(EXCLUDED.last_guid, crawl_state.last_guid),
                last_checked_at = NOW(),
                last_success_at = CASE WHEN %s THEN NOW() ELSE crawl_state.last_success_at END,
                last_error = %s,
                updated_at = NOW()
            """,
            (target_id, last_guid, success, last_error, success, last_error),
        )


def insert_items(conn, target_row: dict, tweets: list[dict], previous_id: str | None) -> int:
    pending_records = []
    for tweet in tweets:
        if previous_id and tweet["guid"] == previous_id:
            break
        pending_records.append(tweet)

    if pending_records:
        rewrite_images_with_imgbb(pending_records)

    inserted = 0
    with conn.cursor() as cur:
        for tweet in reversed(pending_records):
            published_at = parse_datetime(tweet.get("published"))
            title = tweet.get("content", "").strip()
            if len(title) > 140:
                title = title[:137] + "..."
            presentation = build_item_author_presentation(
                target_row,
                author=tweet.get("author"),
                fullname=tweet.get("fullname"),
                x_url=tweet.get("x_url"),
                link=tweet.get("link"),
            )

            metadata = {
                "target": tweet.get("target"),
                "target_type": tweet.get("target_type"),
                "target_value": tweet.get("target_value"),
                "published_raw": tweet.get("published"),
                "source_instance": tweet.get("source_instance"),
                "video_poster_url": tweet.get("video_poster_url"),
            }

            cur.execute(
                """
                INSERT INTO items (
                    target_id,
                    guid,
                    author,
                    fullname,
                    display_author,
                    display_handle,
                    author_profile_url,
                    author_profile_platform,
                    title,
                    content,
                    raw_content,
                    translated_content,
                    link,
                    x_url,
                    images,
                    video_url,
                    published_at,
                    stored_at,
                    is_retweet,
                    metadata
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (target_id, guid) DO NOTHING
                """,
                (
                    target_row["id"],
                    tweet.get("guid"),
                    tweet.get("author"),
                    tweet.get("fullname"),
                    presentation["display_author"],
                    presentation["display_handle"],
                    presentation["author_profile_url"],
                    presentation["author_profile_platform"],
                    title or None,
                    tweet.get("content"),
                    tweet.get("raw_content"),
                    tweet.get("translated_content"),
                    tweet.get("link"),
                    tweet.get("x_url"),
                    Jsonb(tweet.get("images", [])),
                    tweet.get("video_url"),
                    published_at,
                    parse_datetime(tweet.get("stored_at")) or now_utc(),
                    bool(tweet.get("is_retweet")),
                    Jsonb(metadata),
                ),
            )
            inserted += cur.rowcount

    return inserted


def youtube_entry_value(entry: dict, *keys: str) -> str | None:
    for key in keys:
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def extract_youtube_video_id(entry: dict) -> str | None:
    direct = youtube_entry_value(entry, "yt_videoid", "yt:videoId", "videoId")
    if direct:
        return direct
    entry_id = youtube_entry_value(entry, "id", "guid")
    if entry_id and entry_id.startswith("yt:video:"):
        return entry_id.removeprefix("yt:video:")
    link = youtube_entry_value(entry, "link")
    if link:
        parsed = urlparse(link)
        query_video_id = parse_qs(parsed.query).get("v", [None])[0]
        if query_video_id:
            return query_video_id
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[0] == "shorts":
            return parts[1]
    media_content = entry.get("media_content") or []
    if isinstance(media_content, list):
        for media in media_content:
            if isinstance(media, dict):
                url = media.get("url")
                if isinstance(url, str):
                    parsed = urlparse(url)
                    query_video_id = parse_qs(parsed.query).get("v", [None])[0]
                    if query_video_id:
                        return query_video_id
    return None


def extract_youtube_thumbnail(entry: dict, video_id: str) -> str:
    thumbnails = entry.get("media_thumbnail") or []
    if isinstance(thumbnails, list):
        for thumbnail in thumbnails:
            if isinstance(thumbnail, dict):
                url = thumbnail.get("url")
                if isinstance(url, str) and url.strip():
                    return url.strip()
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


def make_youtube_queue_payload(target_row: dict, entry: dict, fetched_at: datetime, channel_id: str | None) -> dict | None:
    video_id = extract_youtube_video_id(entry)
    if not video_id:
        return None
    published_at = parse_datetime(youtube_entry_value(entry, "published", "updated")) or fetched_at
    if published_at < fetched_at - timedelta(hours=YOUTUBE_RETENTION_HOURS):
        return None
    guid = f"yt:video:{video_id}"
    link = youtube_entry_value(entry, "link") or f"https://www.youtube.com/watch?v={video_id}"
    title = youtube_entry_value(entry, "title", "media_title") or "YouTube video"
    description = youtube_entry_value(entry, "media_description", "summary", "description") or ""
    author = youtube_entry_value(entry, "author", "name") or target_row["value"]
    thumbnail = extract_youtube_thumbnail(entry, video_id)
    expires_at = published_at + timedelta(hours=YOUTUBE_RETENTION_HOURS)
    resolved_channel_id = channel_id or extract_youtube_channel_id(entry) or target_row["value"]
    return {
        "source": "youtube",
        "target_id": str(target_row["id"]),
        "channel_id": resolved_channel_id,
        "guid": guid,
        "provider_video_id": video_id,
        "title": title,
        "content": description,
        "raw_content": description,
        "author": author,
        "fullname": author,
        "link": link,
        "x_url": None,
        "images": [thumbnail],
        "video_poster_url": thumbnail,
        "published_at": published_at.isoformat(),
        "expires_at": expires_at.isoformat(),
    }


def youtube_web_text(value) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if not isinstance(value, dict):
        return None

    for key in ("content", "simpleText", "text"):
        raw = value.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()

    runs = value.get("runs")
    if isinstance(runs, list):
        text = "".join(youtube_web_text(run) or "" for run in runs).strip()
        return text or None

    return None


def parse_youtube_relative_datetime(value: str | None, reference: datetime) -> datetime | None:
    if not value:
        return None

    normalized = value.strip().lower()
    if not normalized:
        return None
    normalized = re.sub(r"\s+", " ", normalized)
    compact = normalized.replace(" ", "")

    if normalized in {"now", "just now", "streamed moments ago"} or compact in {"刚刚", "剛剛", "たった今", "今"}:
        return reference

    unit_aliases: list[tuple[str, tuple[str, ...], timedelta]] = [
        ("years", ("years", "year", "yrs", "yr", "y", "年前", "年"), timedelta(days=365)),
        ("months", ("months", "month", "mos", "mo", "か月前", "ヶ月前", "個月前", "个月前", "月前", "か月", "ヶ月", "個月", "个月", "月"), timedelta(days=30)),
        ("weeks", ("weeks", "week", "wks", "wk", "w", "週間前", "週前", "周前", "週間", "週", "周"), timedelta(weeks=1)),
        ("days", ("days", "day", "d", "日前", "天前", "日", "天"), timedelta(days=1)),
        ("hours", ("hours", "hour", "hrs", "hr", "h", "時間前", "小时前", "小時前", "時間", "小时", "小時"), timedelta(hours=1)),
        ("minutes", ("minutes", "minute", "mins", "min", "m", "分前", "分钟前", "分鐘前", "分", "分钟", "分鐘"), timedelta(minutes=1)),
        ("seconds", ("seconds", "second", "secs", "sec", "s", "秒前", "秒"), timedelta(seconds=1)),
    ]

    for _unit, aliases, delta in unit_aliases:
        for alias in sorted(aliases, key=len, reverse=True):
            if re.fullmatch(r"[a-z]+", alias):
                pattern = rf"(\d+)\s*{re.escape(alias)}(?:\s+ago)?"
                match = re.search(pattern, normalized)
            else:
                pattern = rf"(\d+)\s*{re.escape(alias)}"
                match = re.search(pattern, compact)
            if match:
                amount = int(match.group(1))
                return reference - (delta * amount)

    return None


def extract_youtube_initial_data(html: str) -> dict:
    patterns = (
        r"var\s+ytInitialData\s*=\s*(\{.*?\});</script>",
        r"window\[['\"]ytInitialData['\"]\]\s*=\s*(\{.*?\});</script>",
    )
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.DOTALL)
        if match:
            return json.loads(match.group(1))
    raise RuntimeError("YouTube videos page initial data not found.")


def iter_youtube_lockup_view_models(value):
    if isinstance(value, dict):
        lockup = value.get("lockupViewModel")
        if isinstance(lockup, dict):
            yield lockup
        for child in value.values():
            yield from iter_youtube_lockup_view_models(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_youtube_lockup_view_models(child)


def youtube_lockup_thumbnail(lockup: dict, video_id: str) -> str:
    sources = (
        lockup.get("contentImage", {})
        .get("thumbnailViewModel", {})
        .get("image", {})
        .get("sources", [])
    )
    if isinstance(sources, list):
        candidates: list[dict] = [source for source in sources if isinstance(source, dict) and isinstance(source.get("url"), str)]
        if candidates:
            selected = max(candidates, key=lambda item: int(item.get("width") or 0) * int(item.get("height") or 0))
            return selected["url"].strip()
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


def youtube_lockup_published_at(lockup: dict, reference: datetime) -> datetime | None:
    metadata_rows = (
        lockup.get("metadata", {})
        .get("lockupMetadataViewModel", {})
        .get("metadata", {})
        .get("contentMetadataViewModel", {})
        .get("metadataRows", [])
    )
    if not isinstance(metadata_rows, list):
        return None

    for row in metadata_rows:
        if not isinstance(row, dict):
            continue
        metadata_parts = row.get("metadataParts", [])
        if not isinstance(metadata_parts, list):
            continue
        for part in metadata_parts:
            if not isinstance(part, dict):
                continue
            candidates = [
                youtube_web_text(part.get("text")),
                part.get("accessibilityLabel") if isinstance(part.get("accessibilityLabel"), str) else None,
            ]
            for candidate in candidates:
                published_at = parse_youtube_relative_datetime(candidate, reference)
                if published_at:
                    return published_at
    return None


def youtube_channel_title_from_page(html: str, channel_id: str) -> str:
    match = re.search(r"<title>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return channel_id
    title = html_unescape(match.group(1)).strip()
    title = re.sub(r"\s*-\s*YouTube\s*$", "", title).strip()
    return title or channel_id


def youtube_lockup_entry(lockup: dict, *, channel_title: str, fetched_at: datetime) -> dict | None:
    video_id = str(lockup.get("contentId") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        return None

    content_type = str(lockup.get("contentType") or "").strip()
    if content_type and content_type != "LOCKUP_CONTENT_TYPE_VIDEO":
        return None

    published_at = youtube_lockup_published_at(lockup, fetched_at)
    if published_at is None:
        return None

    metadata = lockup.get("metadata", {}).get("lockupMetadataViewModel", {})
    title = youtube_web_text(metadata.get("title")) or "YouTube video"
    thumbnail = youtube_lockup_thumbnail(lockup, video_id)
    link = f"https://www.youtube.com/watch?v={video_id}"
    return {
        "id": f"yt:video:{video_id}",
        "guid": f"yt:video:{video_id}",
        "yt_videoid": video_id,
        "videoId": video_id,
        "title": title,
        "author": channel_title,
        "name": channel_title,
        "link": link,
        "published": published_at.isoformat(),
        "updated": published_at.isoformat(),
        "media_thumbnail": [{"url": thumbnail}],
        "summary": "",
        "description": "",
        "media_description": "",
    }


def fetch_youtube_videos_page_entries(channel_id: str, fetched_at: datetime) -> list[dict]:
    url = f"https://www.youtube.com/channel/{quote(channel_id, safe='')}/videos"
    response = requests.get(
        url,
        params={"hl": "en", "gl": "US"},
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; x2api-youtube-videos/1.0)",
            "Accept-Language": "en-US,en;q=0.9",
        },
        timeout=YOUTUBE_VIDEOS_PAGE_TIMEOUT_SECONDS,
    )
    response.raise_for_status()

    channel_title = youtube_channel_title_from_page(response.text, channel_id)
    initial_data = extract_youtube_initial_data(response.text)
    entries: list[dict] = []
    seen: set[str] = set()
    for lockup in iter_youtube_lockup_view_models(initial_data):
        entry = youtube_lockup_entry(lockup, channel_title=channel_title, fetched_at=fetched_at)
        if not entry:
            continue
        video_id = entry["yt_videoid"]
        if video_id in seen:
            continue
        seen.add(video_id)
        entries.append(entry)
    return entries


def enqueue_youtube_payload(conn, payload: dict) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO video_resolution_queue (
                source, target_id, guid, provider_video_id, payload, status, attempts, next_attempt_at, expires_at
            )
            VALUES ('youtube', %s, %s, %s, %s, 'pending', 0, NOW(), %s)
            ON CONFLICT (target_id, guid) DO UPDATE SET
                payload = EXCLUDED.payload,
                next_attempt_at = LEAST(video_resolution_queue.next_attempt_at, NOW()),
                expires_at = EXCLUDED.expires_at,
                updated_at = NOW()
            RETURNING (xmax = 0) AS inserted
            """,
            (payload["target_id"], payload["guid"], payload["provider_video_id"], Jsonb(payload), parse_datetime(payload["expires_at"])),
        )
        row = cur.fetchone()
        return bool(row and row.get("inserted"))


def item_exists_for_guid(conn, target_id: str, guid: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM items WHERE target_id = %s AND guid = %s LIMIT 1", (target_id, guid))
        return cur.fetchone() is not None


def collect_media_candidates(value) -> list[dict]:
    candidates: list[dict] = []
    if isinstance(value, dict):
        if isinstance(value.get("url"), str):
            candidates.append(value)
        for child in value.values():
            candidates.extend(collect_media_candidates(child))
    elif isinstance(value, list):
        for child in value:
            candidates.extend(collect_media_candidates(child))
    return candidates


def parse_url_expire(video_url: str) -> datetime | None:
    expire_value = parse_qs(urlparse(video_url).query).get("expire", [None])[0]
    if not expire_value:
        return None
    try:
        return datetime.fromtimestamp(int(expire_value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def youtube_resolver_url_variants(watch_url: str) -> list[str]:
    variants = [watch_url]
    parsed = urlparse(watch_url)
    video_id = parse_qs(parsed.query).get("v", [None])[0]
    parts = [part for part in parsed.path.split("/") if part]
    if not video_id and len(parts) >= 2 and parts[0] in {"shorts", "v"}:
        video_id = parts[1]
    if not video_id and parsed.netloc.lower() == "youtu.be" and parts:
        video_id = parts[0]
    if video_id:
        variants.extend(
            [
                f"https://www.youtube.com/v/{video_id}?version=3",
                f"https://www.youtube.com/watch?v={video_id}",
                f"https://youtu.be/{video_id}",
            ]
        )
    seen: set[str] = set()
    return [url for url in variants if url and not (url in seen or seen.add(url))]


def fetch_youtube_resolver_payload(resolver_url: str) -> dict:
    response = requests.post(
        "https://www.clipto.com/api/youtube",
        headers={
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": "https://www.clipto.com",
            "Referer": "https://www.clipto.com/zh-TW/media-downloader/youtube-downloader?via=ytb",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.6 Safari/605.1.15",
        },
        json={"url": resolver_url},
        timeout=YOUTUBE_PLAYBACK_RESOLVER_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


def resolve_youtube_playback_url(watch_url: str) -> dict:
    errors: list[str] = []
    payload = None
    candidates: list[dict] = []
    for resolver_url in youtube_resolver_url_variants(watch_url):
        try:
            payload = fetch_youtube_resolver_payload(resolver_url)
            candidates = collect_media_candidates(payload)
            if candidates:
                break
            errors.append(f"{resolver_url}: empty media list")
        except Exception as exc:
            errors.append(f"{resolver_url}: {exc}")
    if payload is None:
        raise RuntimeError("; ".join(errors) or "YouTube resolver returned no payload.")
    progressive = []
    for candidate in candidates:
        url = candidate.get("url")
        if not isinstance(url, str) or not url.startswith("http"):
            continue
        format_id = str(candidate.get("formatId") or candidate.get("format_id") or candidate.get("itag") or "")
        mime = str(candidate.get("mimeType") or candidate.get("mime") or candidate.get("type") or "").lower()
        quality = str(candidate.get("quality") or candidate.get("qualityLabel") or "")
        has_audio = candidate.get("hasAudio") is True or candidate.get("audio") is not False or "audio" in mime
        video_only = candidate.get("hasAudio") is False or "video/webm" in mime and "audio" not in mime
        if format_id == "18" or (("mp4" in mime or ".mp4" in url) and has_audio and not video_only):
            progressive.append((0 if format_id == "18" else 1, quality, candidate))
    if not progressive:
        raise RuntimeError("No progressive MP4 candidate returned by YouTube resolver. " + "; ".join(errors))
    progressive.sort(key=lambda item: (item[0], item[1]))
    selected = progressive[0][2]
    video_url = selected["url"]
    expires_at = parse_url_expire(video_url)
    if not expires_at:
        raise RuntimeError("Resolved YouTube playback URL does not include an expire query parameter.")
    return {
        "video_url": video_url,
        "video_url_expires_at": expires_at,
        "format_id": str(selected.get("formatId") or selected.get("format_id") or selected.get("itag") or ""),
        "duration_seconds": selected.get("duration") or selected.get("durationSeconds"),
        "raw": payload,
    }


def upsert_resolved_youtube_item(conn, queue_row: dict, resolved: dict) -> str:
    payload = queue_row["payload"] or {}
    presentation = build_author_presentation(
        source="youtube",
        target=f"youtube:{payload['channel_id']}",
        author=payload.get("author"),
        fullname=payload.get("fullname"),
        x_url=None,
        link=payload.get("link"),
    )
    metadata = {
        "target": f"youtube:{payload['channel_id']}",
        "target_type": "channel",
        "target_value": payload["channel_id"],
        "youtube_video_id": payload["provider_video_id"],
        "youtube_channel_id": payload["channel_id"],
        "watch_url": payload["link"],
        "resolver": "clipto",
        "resolved_at": now_iso(),
        "format_id": resolved.get("format_id"),
        "duration_seconds": resolved.get("duration_seconds"),
        "video_poster_url": payload.get("video_poster_url"),
        "video_url_expires_at": resolved["video_url_expires_at"].isoformat(),
    }
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO items (
                target_id, guid, author, fullname,
                display_author, display_handle, author_profile_url, author_profile_platform,
                title, content, raw_content, translated_content,
                link, x_url, images, video_url, expires_at, video_url_expires_at,
                published_at, stored_at, is_retweet, metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, %s, NULL, %s, %s, %s, %s, %s, NOW(), FALSE, %s)
            ON CONFLICT (target_id, guid) DO UPDATE SET
                display_author = EXCLUDED.display_author,
                display_handle = EXCLUDED.display_handle,
                author_profile_url = EXCLUDED.author_profile_url,
                author_profile_platform = EXCLUDED.author_profile_platform,
                video_url = EXCLUDED.video_url,
                video_url_expires_at = EXCLUDED.video_url_expires_at,
                expires_at = EXCLUDED.expires_at,
                metadata = items.metadata || EXCLUDED.metadata
            RETURNING id
            """,
            (
                queue_row["target_id"],
                payload["guid"],
                payload.get("author"),
                payload.get("fullname"),
                presentation["display_author"],
                presentation["display_handle"],
                presentation["author_profile_url"],
                presentation["author_profile_platform"],
                payload.get("title"),
                payload.get("content"),
                payload.get("raw_content"),
                payload.get("link"),
                Jsonb(payload.get("images") or []),
                resolved["video_url"],
                parse_datetime(payload["expires_at"]),
                resolved["video_url_expires_at"],
                parse_datetime(payload["published_at"]),
                Jsonb(metadata),
            ),
        )
        return str(cur.fetchone()["id"])


def resolve_youtube_queue_row(conn, queue_row: dict) -> bool:
    payload = queue_row["payload"] or {}
    try:
        resolved = resolve_youtube_playback_url(payload["link"])
        item_id = upsert_resolved_youtube_item(conn, queue_row, resolved)
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE video_resolution_queue
                SET status = 'resolved', attempts = attempts + 1, last_error = NULL,
                    resolved_item_id = %s, updated_at = NOW()
                WHERE id = %s
                """,
                (item_id, queue_row["id"]),
            )
        return True
    except Exception as exc:
        attempts = int(queue_row.get("attempts") or 0) + 1
        delay_minutes = min(180, 5 * (2 ** min(attempts, 5)))
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE video_resolution_queue
                SET status = 'failed', attempts = attempts + 1, last_error = %s,
                    next_attempt_at = NOW() + (%s || ' minutes')::interval,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (str(exc)[:500], delay_minutes, queue_row["id"]),
            )
        print(f"[YouTube] resolve failed for {payload.get('guid')}: {exc}")
        return False


def extract_youtube_channel_id(entry: dict) -> str | None:
    return youtube_entry_value(entry, "yt_channelid", "yt:channelId", "channelId")


def extract_youtube_channel_id_from_xml(xml: bytes) -> str | None:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    channel_id = root.findtext("yt:channelId", namespaces={"yt": "http://www.youtube.com/xml/schemas/2015"})
    if channel_id and channel_id.strip():
        return channel_id.strip()
    return None


def fetch_youtube_rss_entries(youtube_ref: str, fetched_at: datetime | None = None) -> tuple[list[dict], str | None]:
    import feedparser

    reference = fetched_at or now_utc()
    channel_id = None
    raw_target = youtube_ref.strip()
    if raw_target.lower().startswith(("http://", "https://")):
        url = raw_target
    else:
        channel_id = normalize_youtube_channel_id(raw_target)
        url = "https://www.youtube.com/feeds/videos.xml?" + urlencode({"channel_id": channel_id})

    response = requests.get(
        url,
        headers={"User-Agent": "x2api-youtube-rss/1.0"},
        timeout=YOUTUBE_RSS_TIMEOUT_SECONDS,
    )
    if response.status_code == 404:
        if channel_id:
            print(f"[YouTube] RSS 404 for {channel_id}; fallback to /videos page")
            return fetch_youtube_videos_page_entries(channel_id, reference), channel_id
        raise RuntimeError(f"YouTube RSS 404 for {url}")
    response.raise_for_status()
    parsed = feedparser.parse(response.content)
    if parsed.bozo:
        raise RuntimeError(f"YouTube RSS parse failed: {parsed.bozo_exception}")
    entries = [dict(entry) for entry in parsed.entries]
    resolved_channel_id = channel_id or extract_youtube_channel_id_from_xml(response.content) or next((extract_youtube_channel_id(entry) for entry in entries if extract_youtube_channel_id(entry)), None)
    if not entries:
        if resolved_channel_id:
            print(f"[YouTube] RSS empty for {resolved_channel_id}; fallback to /videos page")
            return fetch_youtube_videos_page_entries(resolved_channel_id, reference), resolved_channel_id
        raise RuntimeError(f"YouTube RSS empty for {url}")
    return entries, resolved_channel_id


def monitor_youtube_target(conn, target_row: dict) -> int:
    fetched_at = now_utc()
    entries, channel_id = fetch_youtube_rss_entries(target_row["value"], fetched_at=fetched_at)
    if not entries:
        upsert_crawl_state(conn, target_row["id"], last_guid=target_row.get("last_guid"), last_error="No YouTube RSS entries returned.", success=False)
        print(f"[{format_target_row(target_row)}] fetched=0 eligible=0 skipped_existing=0 queue_inserted=0 queue_checked=0 resolved=0")
        return 0
    latest_guid: str | None = target_row.get("last_guid")
    latest_published_at: datetime | None = None
    eligible_count = 0
    skipped_existing_count = 0
    enqueue_inserted_count = 0
    fetch_count = len(entries)
    for entry in entries:
        payload = make_youtube_queue_payload(target_row, entry, fetched_at, channel_id)
        if payload is None:
            continue
        eligible_count += 1
        payload_published_at = parse_datetime(payload.get("published_at"))
        if payload_published_at is not None and (latest_published_at is None or payload_published_at > latest_published_at):
            latest_published_at = payload_published_at
            latest_guid = payload["guid"]
        if item_exists_for_guid(conn, target_row["id"], payload["guid"]):
            skipped_existing_count += 1
            continue
        if enqueue_youtube_payload(conn, payload):
            enqueue_inserted_count += 1
    conn.commit()

    resolved_count = 0
    checked_queue_count = 0
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT *
            FROM video_resolution_queue
            WHERE target_id = %s
              AND status IN ('pending', 'failed')
              AND next_attempt_at <= NOW()
              AND expires_at > NOW()
            ORDER BY attempts ASC, payload->>'published_at' DESC
            LIMIT 5
            """,
            (target_row["id"],),
        )
        rows = cur.fetchall()
        checked_queue_count = len(rows)
    for row in rows:
        if resolve_youtube_queue_row(conn, row):
            resolved_count += 1
        conn.commit()

    upsert_crawl_state(conn, target_row["id"], last_guid=latest_guid or target_row.get("last_guid"), last_error=None, success=True)
    print(
        f"[{format_target_row(target_row)}] fetched={fetch_count} eligible={eligible_count} "
        f"skipped_existing={skipped_existing_count} queue_inserted={enqueue_inserted_count} "
        f"queue_checked={checked_queue_count} resolved={resolved_count}"
    )
    return resolved_count


def refresh_youtube_playback_urls(conn, limit: int, refresh_window_minutes: int, critical_window_minutes: int) -> dict[str, int]:
    processed = 0
    resolved = 0
    refreshed = 0

    def remaining() -> int:
        return max(0, limit - processed)

    def fetch_queue_rows(where_sql: str, params: tuple = ()) -> list[dict]:
        if remaining() <= 0:
            return []
        with conn.cursor() as cur:
            cur.execute(where_sql, (*params, remaining()))
            return cur.fetchall()

    def refresh_item(row: dict) -> bool:
        payload = {
            "guid": row["guid"],
            "provider_video_id": row["metadata"].get("youtube_video_id"),
            "channel_id": row["metadata"].get("youtube_channel_id"),
            "link": row["metadata"].get("watch_url") or row["link"],
            "expires_at": row["expires_at"].isoformat(),
            "published_at": row["published_at"].isoformat() if row.get("published_at") else row["stored_at"].isoformat(),
            "title": row.get("title"),
            "content": row.get("content"),
            "raw_content": row.get("raw_content"),
            "author": row.get("author"),
            "fullname": row.get("fullname"),
            "images": row.get("images") or [],
            "video_poster_url": row["metadata"].get("video_poster_url"),
        }
        try:
            resolved_payload = resolve_youtube_playback_url(payload["link"])
            metadata = row["metadata"] | {
                "resolver": "clipto",
                "resolved_at": now_iso(),
                "format_id": resolved_payload.get("format_id"),
                "duration_seconds": resolved_payload.get("duration_seconds"),
                "video_url_expires_at": resolved_payload["video_url_expires_at"].isoformat(),
            }
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE items
                    SET video_url = %s, video_url_expires_at = %s, metadata = %s, stored_at = stored_at
                    WHERE id = %s
                    """,
                    (resolved_payload["video_url"], resolved_payload["video_url_expires_at"], Jsonb(metadata), row["id"]),
                )
            return True
        except Exception as exc:
            print(f"[YouTube] refresh failed for {row['guid']}: {exc}")
            return False

    item_queries = [
        (
            """
            SELECT i.*, t.value AS channel_id
            FROM items i INNER JOIN targets t ON t.id = i.target_id
            WHERE t.source = 'youtube' AND i.expires_at > NOW()
              AND i.video_url_expires_at <= NOW() + (%s || ' minutes')::interval
            ORDER BY i.video_url_expires_at ASC
            LIMIT %s
            """,
            (critical_window_minutes,),
        ),
        (
            """
            SELECT i.*, t.value AS channel_id
            FROM items i INNER JOIN targets t ON t.id = i.target_id
            WHERE t.source = 'youtube' AND i.expires_at > NOW()
              AND i.video_url_expires_at <= NOW() + (%s || ' minutes')::interval
            ORDER BY i.video_url_expires_at ASC, i.published_at DESC
            LIMIT %s
            """,
            (refresh_window_minutes,),
        ),
    ]
    queue_queries = [
        (
            """
            SELECT * FROM video_resolution_queue
            WHERE source = 'youtube' AND status = 'pending' AND expires_at > NOW()
            ORDER BY payload->>'published_at' DESC
            LIMIT %s
            """,
            (),
        ),
        (
            """
            SELECT * FROM video_resolution_queue
            WHERE source = 'youtube' AND status = 'failed' AND next_attempt_at <= NOW() AND expires_at > NOW()
            ORDER BY attempts ASC, payload->>'published_at' DESC
            LIMIT %s
            """,
            (),
        ),
    ]

    for query, params in [item_queries[0], queue_queries[0], item_queries[1], queue_queries[1]]:
        rows = fetch_queue_rows(query, params)
        for row in rows:
            processed += 1
            if "payload" in row:
                if resolve_youtube_queue_row(conn, row):
                    resolved += 1
            elif refresh_item(row):
                refreshed += 1
            conn.commit()
            if remaining() <= 0:
                break
    return {"processed": processed, "resolved": resolved, "refreshed": refreshed}


def heiliao_headers(referer: str | None = None) -> dict[str, str]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if referer:
        headers["Referer"] = referer
    return headers


def fetch_heiliao_html(url: str, referer: str | None = None) -> str:
    response = requests.get(url, headers=heiliao_headers(referer), timeout=HEILIAO_REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.text


def parse_chinese_date(value: str | None, default_time: str = "00:00:00") -> datetime | None:
    if not value:
        return None
    match = re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", value)
    if not match:
        return parse_datetime(value)
    year, month, day = (int(part) for part in match.groups())
    hour, minute, second = (int(part) for part in default_time.split(":"))
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


def extract_heiliao_json_ld(soup: BeautifulSoup) -> dict:
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.get_text("", strip=True))
        except json.JSONDecodeError:
            continue
        entity = payload.get("mainEntity") if isinstance(payload, dict) else None
        if isinstance(entity, dict):
            return entity
    return {}


def extract_heiliao_page_id(url: str) -> str:
    match = re.search(r"/archives/(\d+)\.html", urlparse(url).path)
    if not match:
        return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
    return match.group(1)


def parse_heiliao_list_page(base_url: str, page_url: str) -> tuple[list[dict], str | None]:
    soup = BeautifulSoup(fetch_heiliao_html(page_url), "html.parser")
    items: list[dict] = []
    seen: set[str] = set()
    for article in soup.select("article"):
        link = article.select_one('a[href*="/archives/"]')
        heading = article.select_one("h2,h1,h3")
        if not link or not link.get("href") or not heading:
            continue
        detail_url = urlunparse(urlparse(urljoin(page_url, link["href"]))._replace(fragment=""))
        if detail_url in seen:
            continue
        seen.add(detail_url)
        text = article.get_text(" ", strip=True)
        items.append(
            {
                "url": detail_url,
                "page_id": extract_heiliao_page_id(detail_url),
                "title": heading.get_text(" ", strip=True),
                "published_at": parse_chinese_date(text),
                "raw_meta": text[:500],
            }
        )

    next_url = None
    for selector in ('link[rel="next"]', 'a[rel="next"]', 'a.next', '.page-navigator a.next'):
        next_link = soup.select_one(selector)
        href = next_link.get("href") if next_link else None
        if href:
            next_url = urljoin(page_url, href)
            break
    if not next_url:
        for link in soup.find_all("a", href=True):
            label = link.get_text(" ", strip=True).lower()
            if label in {"下一页", "next", "›", "»"} or "下一页" in label:
                next_url = urljoin(page_url, link["href"])
                break
    if next_url:
        parsed_base = urlparse(base_url)
        parsed_next = urlparse(next_url)
        if parsed_next.netloc and parsed_next.netloc.lower() != parsed_base.netloc.lower():
            next_url = None
    return items, next_url


def parse_heiliao_auth_key_expiry(video_url: str) -> datetime | None:
    auth_key = parse_qs(urlparse(video_url).query).get("auth_key", [None])[0]
    if not auth_key:
        return None
    first_part = auth_key.split("-", 1)[0]
    try:
        return datetime.fromtimestamp(int(first_part), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def heiliao_video_url_expires_at(video_url: str) -> datetime:
    parsed = parse_heiliao_auth_key_expiry(video_url)
    fallback = now_utc() + timedelta(minutes=HEILIAO_REFRESH_WINDOW_MINUTES)
    if not parsed or parsed <= now_utc() + timedelta(minutes=HEILIAO_CRITICAL_WINDOW_MINUTES):
        return fallback
    return parsed


def verify_heiliao_hls_url(video_url: str, referer: str) -> dict:
    parsed = urlparse(video_url)
    if parsed.scheme not in {"http", "https"} or not parsed.path.endswith(".m3u8"):
        raise ValueError("Heiliao video URL must be an HLS .m3u8 URL.")
    response = requests.get(video_url, headers=heiliao_headers(referer), timeout=HEILIAO_REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    playlist = response.text
    if "#EXTM3U" not in playlist or "#EXTINF" not in playlist:
        raise ValueError("Heiliao HLS playlist is not playable media.")
    media_urls = re.findall(r'https?://[^\s"\']+', playlist)
    if not media_urls:
        raise ValueError("Heiliao HLS playlist has no absolute media URLs.")

    checked_key = False
    checked_segment = False
    for media_url in media_urls:
        media_path = urlparse(media_url).path.lower()
        if not checked_key and media_path.endswith(".key"):
            media_response = requests.get(media_url, headers=heiliao_headers(referer), timeout=HEILIAO_REQUEST_TIMEOUT_SECONDS, stream=True)
            media_response.raise_for_status()
            chunk = next(media_response.iter_content(16), b"")
            if len(chunk) != 16:
                raise ValueError("Heiliao HLS key is not readable.")
            checked_key = True
        if not checked_segment and media_path.endswith(".ts"):
            media_response = requests.get(media_url, headers=heiliao_headers(referer), timeout=HEILIAO_REQUEST_TIMEOUT_SECONDS, stream=True)
            media_response.raise_for_status()
            chunk = next(media_response.iter_content(64), b"")
            if not chunk:
                raise ValueError("Heiliao HLS segment is not readable.")
            checked_segment = True
        if checked_key and checked_segment:
            break
    if not checked_segment:
        raise ValueError("Heiliao HLS playlist has no readable segment.")
    return {
        "video_url": video_url,
        "video_url_expires_at": heiliao_video_url_expires_at(video_url),
        "playlist_bytes": len(playlist.encode("utf-8")),
        "media_url_count": len(media_urls),
    }


def parse_heiliao_detail_page(detail_url: str, list_item: dict | None = None) -> dict:
    soup = BeautifulSoup(fetch_heiliao_html(detail_url), "html.parser")
    entity = extract_heiliao_json_ld(soup)
    title_el = soup.select_one("h1.post-title") or soup.find("h1")
    title = (title_el.get_text(" ", strip=True) if title_el else None) or (list_item or {}).get("title") or "黑料不打烊视频"
    published_at = parse_datetime(entity.get("datePublished")) or (list_item or {}).get("published_at") or now_utc()
    modified_at = parse_datetime(entity.get("dateModified"))
    description = extract_meta_description(soup, entity) or extract_site_post_body(soup)
    image = entity.get("image") if isinstance(entity.get("image"), str) else None
    page_id = extract_heiliao_page_id(detail_url)
    content_scope = soup.select_one("article.post") or soup
    players = []
    for index, player in enumerate(content_scope.select("div.dplayer[data-config]"), start=1):
        try:
            config = json.loads(player["data-config"])
        except (KeyError, json.JSONDecodeError):
            continue
        video_config = config.get("video") if isinstance(config, dict) else None
        video_url = video_config.get("url") if isinstance(video_config, dict) else None
        video_type = video_config.get("type") if isinstance(video_config, dict) else None
        if not isinstance(video_url, str) or not video_url.strip():
            continue
        if not isinstance(video_type, str) or video_type.lower() != "hls":
            continue
        video_id = (player.get("data-video_id") or f"{page_id}{index:03d}").strip()
        video_title = (player.get("data-video_title") or f"{title}{index:03d}").strip()
        tags = [tag.strip() for tag in (player.get("data-video_tag_name") or "").split(",") if tag.strip()]
        players.append(
            {
                "guid": f"heiliao:{page_id}:{video_id}",
                "page_id": page_id,
                "player_index": index,
                "video_id": video_id,
                "video_title": video_title,
                "video_url": video_url.strip(),
                "video_type": video_type.lower(),
                "tags": tags,
            }
        )
    return {
        "url": detail_url,
        "page_id": page_id,
        "title": title,
        "description": description,
        "image": image,
        "published_at": published_at,
        "modified_at": modified_at,
        "players": players,
    }


def ensure_heiliao_target(conn, base_url: str, *, public_pool: bool = True) -> dict:
    target_row = upsert_target(conn, f"heiliao:{base_url}")
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO target_profiles (target_id, scope, tags, category, weight, is_public_pool)
            VALUES (%s, 'system', %s, 'adult', 45, %s)
            ON CONFLICT (target_id) DO UPDATE SET
                scope = EXCLUDED.scope,
                tags = EXCLUDED.tags,
                category = EXCLUDED.category,
                weight = EXCLUDED.weight,
                is_public_pool = EXCLUDED.is_public_pool,
                updated_at = NOW()
            """,
            (target_row["id"], Jsonb([HEILIAO_SITE_NAME, "黑料", "视频"]), public_pool),
        )
    return target_row


def upsert_heiliao_video_item(conn, target_row: dict, detail: dict, player: dict, verified: dict, retention_hours: int) -> bool:
    published_at = detail.get("published_at") or now_utc()
    expires_at = published_at + timedelta(hours=retention_hours)
    content = detail.get("description") or detail.get("title") or player.get("video_title")
    images = [detail["image"]] if detail.get("image") else []
    presentation = build_item_author_presentation(
        target_row,
        author=HEILIAO_SITE_NAME,
        fullname=HEILIAO_SITE_NAME,
        x_url=None,
        link=detail["url"],
    )
    metadata = {
        "target": format_target_row(target_row),
        "target_type": HEILIAO_KIND,
        "target_value": target_row["value"],
        "site_name": HEILIAO_SITE_NAME,
        "source_url": detail["url"],
        "page_id": detail["page_id"],
        "player_index": player["player_index"],
        "page_video_count": len(detail.get("players") or []),
        "heiliao_video_id": player["video_id"],
        "video_type": player["video_type"],
        "tags": player.get("tags") or [],
        "date_modified": detail.get("modified_at").isoformat() if detail.get("modified_at") else None,
        "resolver": "heiliao-dplayer",
        "resolved_at": now_iso(),
        "video_url_expires_at": verified["video_url_expires_at"].isoformat(),
    }
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO items (
                target_id, guid, author, fullname,
                display_author, display_handle, author_profile_url, author_profile_platform,
                title, content, raw_content, translated_content,
                link, x_url, images, video_url, expires_at, video_url_expires_at,
                published_at, stored_at, is_retweet, metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, %s, NULL, %s, %s, %s, %s, %s, NOW(), FALSE, %s)
            ON CONFLICT (target_id, guid) DO UPDATE SET
                display_author = EXCLUDED.display_author,
                display_handle = EXCLUDED.display_handle,
                author_profile_url = EXCLUDED.author_profile_url,
                author_profile_platform = EXCLUDED.author_profile_platform,
                title = EXCLUDED.title,
                content = EXCLUDED.content,
                raw_content = EXCLUDED.raw_content,
                images = EXCLUDED.images,
                video_url = EXCLUDED.video_url,
                expires_at = EXCLUDED.expires_at,
                video_url_expires_at = EXCLUDED.video_url_expires_at,
                published_at = COALESCE(items.published_at, EXCLUDED.published_at),
                metadata = items.metadata || EXCLUDED.metadata
            RETURNING (xmax = 0) AS inserted
            """,
            (
                target_row["id"],
                player["guid"],
                HEILIAO_SITE_NAME,
                HEILIAO_SITE_NAME,
                presentation["display_author"],
                presentation["display_handle"],
                presentation["author_profile_url"],
                presentation["author_profile_platform"],
                player.get("video_title") or detail.get("title"),
                content,
                detail.get("title"),
                detail["url"],
                Jsonb(images),
                verified["video_url"],
                expires_at,
                verified["video_url_expires_at"],
                published_at,
                Jsonb(metadata),
            ),
        )
        row = cur.fetchone()
    return bool(row and row.get("inserted"))


def monitor_heiliao_site(
    conn,
    *,
    base_url: str,
    max_pages: int,
    retention_hours: int,
    public_pool: bool,
    dry_run: bool = False,
) -> dict[str, int | list]:
    base_url = normalize_heiliao_target_value(base_url)
    target_row = None if dry_run else ensure_heiliao_target(conn, base_url, public_pool=public_pool)
    page_url = base_url + "/"
    cutoff = now_utc() - timedelta(hours=retention_hours)
    inserted = 0
    updated = 0
    verified_count = 0
    skipped_existing = 0
    skipped_unverified = 0
    pages = 0
    samples = []
    latest_guid = None

    for _ in range(max_pages):
        pages += 1
        list_items, next_url = parse_heiliao_list_page(base_url, page_url)
        page_inserted = 0
        page_existing = 0
        page_old = 0
        if not list_items:
            break
        for list_item in list_items:
            if list_item.get("published_at") and list_item["published_at"] < cutoff:
                page_old += 1
                continue
            detail = parse_heiliao_detail_page(list_item["url"], list_item)
            if not detail["players"]:
                continue
            for player in detail["players"]:
                latest_guid = latest_guid or player["guid"]
                if target_row and item_exists_for_guid(conn, str(target_row["id"]), player["guid"]):
                    page_existing += 1
                    skipped_existing += 1
                    continue
                try:
                    verified = verify_heiliao_hls_url(player["video_url"], detail["url"])
                except Exception as exc:
                    skipped_unverified += 1
                    print(f"[Heiliao] skip unverified {player['guid']}: {exc}")
                    continue
                verified_count += 1
                if dry_run:
                    samples.append(
                        {
                            "guid": player["guid"],
                            "title": player.get("video_title"),
                            "link": detail["url"],
                            "published_at": detail["published_at"].isoformat() if detail.get("published_at") else None,
                            "video_url": verified["video_url"],
                            "video_url_expires_at": verified["video_url_expires_at"].isoformat(),
                        }
                    )
                    continue
                if upsert_heiliao_video_item(conn, target_row, detail, player, verified, retention_hours):
                    inserted += 1
                    page_inserted += 1
                else:
                    updated += 1
        if target_row:
            upsert_crawl_state(conn, target_row["id"], last_guid=latest_guid, last_error=None, success=True)
        if not next_url:
            break
        if page_inserted == 0 and (page_existing > 0 or page_old == len(list_items)):
            break
        page_url = next_url

    return {
        "pages": pages,
        "verified": verified_count,
        "inserted": inserted,
        "updated": updated,
        "skipped_existing": skipped_existing,
        "skipped_unverified": skipped_unverified,
        "samples": samples[:10],
    }


def refresh_heiliao_playback_urls(conn, limit: int, refresh_window_minutes: int, critical_window_minutes: int) -> dict[str, int]:
    processed = 0
    refreshed = 0
    failed = 0
    queries = [
        (
            """
            SELECT i.*
            FROM items i INNER JOIN targets t ON t.id = i.target_id
            WHERE t.source = %s AND i.expires_at > NOW()
              AND i.video_url_expires_at <= NOW() + (%s || ' minutes')::interval
            ORDER BY i.video_url_expires_at ASC
            LIMIT %s
            """,
            (HEILIAO_SOURCE, critical_window_minutes, limit),
        ),
        (
            """
            SELECT i.*
            FROM items i INNER JOIN targets t ON t.id = i.target_id
            WHERE t.source = %s AND i.expires_at > NOW()
              AND i.video_url_expires_at <= NOW() + (%s || ' minutes')::interval
            ORDER BY i.video_url_expires_at ASC, i.published_at DESC
            LIMIT %s
            """,
            (HEILIAO_SOURCE, refresh_window_minutes, limit),
        ),
    ]
    seen_ids: set[str] = set()
    for sql, params in queries:
        if processed >= limit:
            break
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        for row in rows:
            row_id = str(row["id"])
            if row_id in seen_ids or processed >= limit:
                continue
            seen_ids.add(row_id)
            processed += 1
            metadata = row["metadata"] or {}
            source_url = metadata.get("source_url") or row.get("link")
            video_id = metadata.get("heiliao_video_id")
            try:
                if not source_url or not video_id:
                    raise ValueError("missing source_url or heiliao_video_id")
                detail = parse_heiliao_detail_page(source_url)
                player = next((candidate for candidate in detail["players"] if candidate["video_id"] == video_id), None)
                if not player:
                    raise ValueError("matching player not found")
                verified = verify_heiliao_hls_url(player["video_url"], detail["url"])
                next_metadata = metadata | {
                    "resolver": "heiliao-dplayer",
                    "resolved_at": now_iso(),
                    "video_url_expires_at": verified["video_url_expires_at"].isoformat(),
                    "date_modified": detail.get("modified_at").isoformat() if detail.get("modified_at") else metadata.get("date_modified"),
                }
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE items
                        SET video_url = %s, video_url_expires_at = %s, metadata = %s, stored_at = stored_at
                        WHERE id = %s
                        """,
                        (verified["video_url"], verified["video_url_expires_at"], Jsonb(next_metadata), row["id"]),
                    )
                refreshed += 1
            except Exception as exc:
                failed += 1
                print(f"[Heiliao] refresh failed for {row['guid']}: {exc}")
            conn.commit()
    return {"processed": processed, "refreshed": refreshed, "failed": failed}


def extract_cg91_json_ld(soup: BeautifulSoup) -> dict:
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.get_text("", strip=True))
        except json.JSONDecodeError:
            continue
        candidates = payload if isinstance(payload, list) else [payload]
        for candidate in candidates:
            if isinstance(candidate, dict) and candidate.get("@type") in {"BlogPosting", "Article"}:
                return candidate
    return {}


def clean_site_text(value: str | None, *, reject_seo: bool = True) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    text = text.replace("|51爆料网", "").strip(" -|｜\t\r\n")
    seo_markers = (
        "持续追踪",
        "获取最新网址",
        "最新地址",
        "永久地址",
        "官方交流群",
    )
    if reject_seo and any(marker in text for marker in seo_markers):
        return ""
    return text


def extract_meta_description(soup: BeautifulSoup, entity: dict) -> str:
    for selector in ('meta[name="description"]', 'meta[property="og:description"]'):
        meta = soup.select_one(selector)
        content = meta.get("content") if meta else None
        if isinstance(content, str) and content.strip():
            return clean_site_text(content)
    description = entity.get("description") if isinstance(entity.get("description"), str) else ""
    return clean_site_text(description)


def extract_site_post_body(soup: BeautifulSoup) -> str:
    content_scope = soup.select_one(".post-content") or soup.select_one("article.post")
    if not content_scope:
        return ""
    for removable in content_scope.select("script,style,iframe,ins,div.dplayer"):
        removable.decompose()
    text = clean_site_text(content_scope.get_text(" ", strip=True), reject_seo=False)
    markers = (
        "👥 官方交流群",
        "点击加入",
        "获取最新网址",
        "最新地址",
        "永久地址",
    )
    for marker in markers:
        index = text.find(marker)
        if index >= 0:
            text = text[index + len(marker) :].strip()
    email_match = re.search(r"\[email\s*protected\]", text, flags=re.IGNORECASE)
    if email_match:
        text = text[email_match.end() :].strip()
    return clean_site_text(text)


def extract_cg91_page_id(url: str) -> str:
    match = re.search(r"/archives/(\d+)/?", urlparse(url).path)
    if not match:
        return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
    return match.group(1)


def parse_cg91_list_page(base_url: str, page_url: str) -> tuple[list[dict], str | None]:
    soup = BeautifulSoup(fetch_heiliao_html(page_url), "html.parser")
    items: list[dict] = []
    seen: set[str] = set()
    for article in soup.select("article"):
        link = article.select_one('a[href*="/archives/"]')
        heading = article.select_one("h2,h1,h3")
        if not link or not link.get("href") or not heading:
            continue
        detail_url = urlunparse(urlparse(urljoin(page_url, link["href"]))._replace(fragment=""))
        if not re.search(r"/archives/\d+/?$", urlparse(detail_url).path):
            continue
        if detail_url in seen:
            continue
        seen.add(detail_url)
        text = article.get_text(" ", strip=True)
        items.append(
            {
                "url": detail_url,
                "page_id": extract_cg91_page_id(detail_url),
                "title": heading.get_text(" ", strip=True),
                "published_at": parse_chinese_date(text),
                "raw_meta": text[:500],
            }
        )

    next_url = None
    for selector in ('link[rel="next"]', 'a[rel="next"]', 'a.next', '.page-navigator a.next'):
        next_link = soup.select_one(selector)
        href = next_link.get("href") if next_link else None
        if href:
            next_url = urljoin(page_url, href)
            break
    if not next_url:
        for link in soup.find_all("a", href=True):
            label = link.get_text(" ", strip=True).lower()
            if label in {"下一页", "next", "›", "»"} or "下一页" in label:
                next_url = urljoin(page_url, link["href"])
                break
    if next_url:
        parsed_base = urlparse(base_url)
        parsed_next = urlparse(next_url)
        if parsed_next.netloc and parsed_next.netloc.lower() != parsed_base.netloc.lower():
            next_url = None
    return items, next_url


def parse_cg91_detail_page(detail_url: str, list_item: dict | None = None) -> dict:
    soup = BeautifulSoup(fetch_heiliao_html(detail_url), "html.parser")
    entity = extract_cg91_json_ld(soup)
    title_el = soup.select_one("h1.post-title") or soup.find("h1")
    title = (title_el.get_text(" ", strip=True) if title_el else None) or (list_item or {}).get("title") or "91吃瓜视频"
    published_at = parse_datetime(entity.get("datePublished")) or (list_item or {}).get("published_at") or now_utc()
    modified_at = parse_datetime(entity.get("dateModified"))
    description = extract_meta_description(soup, entity) or extract_site_post_body(soup)
    image_value = entity.get("image")
    image = image_value.get("url") if isinstance(image_value, dict) and isinstance(image_value.get("url"), str) else image_value
    image = image if isinstance(image, str) else None
    page_id = extract_cg91_page_id(detail_url)
    content_scope = soup.select_one("article.post") or soup
    players = []
    for index, player in enumerate(content_scope.select("div.dplayer[data-config]"), start=1):
        try:
            config = json.loads(player["data-config"])
        except (KeyError, json.JSONDecodeError):
            continue
        video_config = config.get("video") if isinstance(config, dict) else None
        video_url = video_config.get("url") if isinstance(video_config, dict) else None
        video_type = video_config.get("type") if isinstance(video_config, dict) else None
        if not isinstance(video_url, str) or not video_url.strip():
            continue
        if not isinstance(video_type, str) or video_type.lower() != "hls":
            continue
        video_id = (player.get("data-video_id") or f"{page_id}{index:03d}").strip()
        video_title = (player.get("data-video_title") or f"{title}{index:03d}").strip()
        tags = [tag.strip() for tag in (player.get("data-video_tag_name") or "").split(",") if tag.strip()]
        players.append(
            {
                "guid": f"cg91:{page_id}:{video_id}",
                "page_id": page_id,
                "player_index": index,
                "video_id": video_id,
                "video_title": video_title,
                "video_url": video_url.strip(),
                "video_type": video_type.lower(),
                "tags": tags,
            }
        )
    return {
        "url": detail_url,
        "page_id": page_id,
        "title": title,
        "description": description,
        "image": image,
        "published_at": published_at,
        "modified_at": modified_at,
        "players": players,
    }


def ensure_cg91_target(conn, base_url: str, *, public_pool: bool = True) -> dict:
    target_row = upsert_target(conn, f"cg91:{base_url}")
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO target_profiles (target_id, scope, tags, category, weight, is_public_pool)
            VALUES (%s, 'system', %s, 'adult', 45, %s)
            ON CONFLICT (target_id) DO UPDATE SET
                scope = EXCLUDED.scope,
                tags = EXCLUDED.tags,
                category = EXCLUDED.category,
                weight = EXCLUDED.weight,
                is_public_pool = EXCLUDED.is_public_pool,
                updated_at = NOW()
            """,
            (target_row["id"], Jsonb([CG91_SITE_NAME, "吃瓜", "视频"]), public_pool),
        )
    return target_row


def upsert_cg91_video_item(conn, target_row: dict, detail: dict, player: dict, verified: dict, retention_hours: int) -> bool:
    published_at = detail.get("published_at") or now_utc()
    expires_at = published_at + timedelta(hours=retention_hours)
    content = detail.get("description") or detail.get("title") or player.get("video_title")
    images = [detail["image"]] if detail.get("image") else []
    presentation = build_item_author_presentation(
        target_row,
        author=CG91_SITE_NAME,
        fullname=CG91_SITE_NAME,
        x_url=None,
        link=detail["url"],
    )
    metadata = {
        "target": format_target_row(target_row),
        "target_type": CG91_KIND,
        "target_value": target_row["value"],
        "site_name": CG91_SITE_NAME,
        "source_url": detail["url"],
        "page_id": detail["page_id"],
        "player_index": player["player_index"],
        "page_video_count": len(detail.get("players") or []),
        "cg91_video_id": player["video_id"],
        "video_type": player["video_type"],
        "tags": player.get("tags") or [],
        "date_modified": detail.get("modified_at").isoformat() if detail.get("modified_at") else None,
        "resolver": "cg91-dplayer",
        "resolved_at": now_iso(),
        "video_url_expires_at": verified["video_url_expires_at"].isoformat(),
    }
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO items (
                target_id, guid, author, fullname,
                display_author, display_handle, author_profile_url, author_profile_platform,
                title, content, raw_content, translated_content,
                link, x_url, images, video_url, expires_at, video_url_expires_at,
                published_at, stored_at, is_retweet, metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, %s, NULL, %s, %s, %s, %s, %s, NOW(), FALSE, %s)
            ON CONFLICT (target_id, guid) DO UPDATE SET
                display_author = EXCLUDED.display_author,
                display_handle = EXCLUDED.display_handle,
                author_profile_url = EXCLUDED.author_profile_url,
                author_profile_platform = EXCLUDED.author_profile_platform,
                title = EXCLUDED.title,
                content = EXCLUDED.content,
                raw_content = EXCLUDED.raw_content,
                images = EXCLUDED.images,
                video_url = EXCLUDED.video_url,
                expires_at = EXCLUDED.expires_at,
                video_url_expires_at = EXCLUDED.video_url_expires_at,
                published_at = COALESCE(items.published_at, EXCLUDED.published_at),
                metadata = items.metadata || EXCLUDED.metadata
            RETURNING (xmax = 0) AS inserted
            """,
            (
                target_row["id"],
                player["guid"],
                CG91_SITE_NAME,
                CG91_SITE_NAME,
                presentation["display_author"],
                presentation["display_handle"],
                presentation["author_profile_url"],
                presentation["author_profile_platform"],
                player.get("video_title") or detail.get("title"),
                content,
                detail.get("title"),
                detail["url"],
                Jsonb(images),
                verified["video_url"],
                expires_at,
                verified["video_url_expires_at"],
                published_at,
                Jsonb(metadata),
            ),
        )
        row = cur.fetchone()
    return bool(row and row.get("inserted"))


def monitor_cg91_site(conn, *, base_url: str, max_pages: int, retention_hours: int, public_pool: bool, dry_run: bool = False) -> dict:
    base_url = normalize_cg91_target_value(base_url)
    target_row = None if dry_run else ensure_cg91_target(conn, base_url, public_pool=public_pool)
    page_url = base_url + "/"
    cutoff = now_utc() - timedelta(hours=retention_hours)
    inserted = 0
    updated = 0
    verified_count = 0
    parsed_videos = 0
    skipped_existing = 0
    skipped_detail_errors = 0
    skipped_unverified = 0
    pages = 0
    samples = []
    latest_guid = None
    for _ in range(max_pages):
        pages += 1
        list_items, next_url = parse_cg91_list_page(base_url, page_url)
        page_inserted = 0
        page_existing = 0
        page_old = 0
        page_updated = 0
        page_verified = 0
        page_unverified = 0
        page_detail_errors = 0
        page_parsed_videos = 0
        print(f"[91cg] page={pages} list_items={len(list_items)} url={page_url}")
        if not list_items:
            print(f"[91cg] page={pages} empty_list stop=true")
            break
        for list_item in list_items:
            if list_item.get("published_at") and list_item["published_at"] < cutoff:
                page_old += 1
                continue
            try:
                detail = parse_cg91_detail_page(list_item["url"], list_item)
            except Exception as exc:
                skipped_detail_errors += 1
                page_detail_errors += 1
                print(f"[91cg] skip detail {list_item.get('url')}: {exc}")
                continue
            if not detail["players"]:
                continue
            page_parsed_videos += len(detail["players"])
            parsed_videos += len(detail["players"])
            for player in detail["players"]:
                latest_guid = latest_guid or player["guid"]
                if target_row and item_exists_for_guid(conn, str(target_row["id"]), player["guid"]):
                    page_existing += 1
                    skipped_existing += 1
                    continue
                try:
                    verified = verify_heiliao_hls_url(player["video_url"], detail["url"])
                except Exception as exc:
                    skipped_unverified += 1
                    page_unverified += 1
                    print(f"[91cg] skip unverified {player['guid']}: {exc}")
                    continue
                verified_count += 1
                page_verified += 1
                if dry_run:
                    samples.append({"guid": player["guid"], "title": player.get("video_title"), "link": detail["url"], "published_at": detail["published_at"].isoformat() if detail.get("published_at") else None, "video_url": verified["video_url"], "video_url_expires_at": verified["video_url_expires_at"].isoformat()})
                    continue
                if upsert_cg91_video_item(conn, target_row, detail, player, verified, retention_hours):
                    inserted += 1
                    page_inserted += 1
                else:
                    updated += 1
                    page_updated += 1
        if target_row:
            upsert_crawl_state(conn, target_row["id"], last_guid=latest_guid, last_error=None, success=True)
        print(
            f"[91cg] page={pages} parsed_videos={page_parsed_videos} verified={page_verified} "
            f"inserted={page_inserted} updated={page_updated} existing={page_existing} old={page_old} "
            f"detail_errors={page_detail_errors} unverified={page_unverified}"
        )
        if not next_url:
            break
        page_url = next_url
    return {"pages": pages, "parsed_videos": parsed_videos, "verified": verified_count, "inserted": inserted, "updated": updated, "skipped_existing": skipped_existing, "skipped_detail_errors": skipped_detail_errors, "skipped_unverified": skipped_unverified, "samples": samples[:10]}


def refresh_cg91_playback_urls(conn, limit: int, refresh_window_minutes: int, critical_window_minutes: int) -> dict[str, int]:
    processed = 0
    refreshed = 0
    failed = 0
    queries = [
        ("""SELECT i.* FROM items i INNER JOIN targets t ON t.id = i.target_id WHERE t.source = %s AND i.expires_at > NOW() AND i.video_url_expires_at <= NOW() + (%s || ' minutes')::interval ORDER BY i.video_url_expires_at ASC LIMIT %s""", (CG91_SOURCE, critical_window_minutes, limit)),
        ("""SELECT i.* FROM items i INNER JOIN targets t ON t.id = i.target_id WHERE t.source = %s AND i.expires_at > NOW() AND i.video_url_expires_at <= NOW() + (%s || ' minutes')::interval ORDER BY i.video_url_expires_at ASC, i.published_at DESC LIMIT %s""", (CG91_SOURCE, refresh_window_minutes, limit)),
    ]
    seen_ids: set[str] = set()
    for sql, params in queries:
        if processed >= limit:
            break
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        for row in rows:
            row_id = str(row["id"])
            if row_id in seen_ids or processed >= limit:
                continue
            seen_ids.add(row_id)
            processed += 1
            metadata = row["metadata"] or {}
            source_url = metadata.get("source_url") or row.get("link")
            video_id = metadata.get("cg91_video_id")
            try:
                if not source_url or not video_id:
                    raise ValueError("missing source_url or cg91_video_id")
                detail = parse_cg91_detail_page(source_url)
                player = next((candidate for candidate in detail["players"] if candidate["video_id"] == video_id), None)
                if not player:
                    raise ValueError("matching player not found")
                verified = verify_heiliao_hls_url(player["video_url"], detail["url"])
                next_metadata = metadata | {"resolver": "cg91-dplayer", "resolved_at": now_iso(), "video_url_expires_at": verified["video_url_expires_at"].isoformat(), "date_modified": detail.get("modified_at").isoformat() if detail.get("modified_at") else metadata.get("date_modified")}
                with conn.cursor() as cur:
                    cur.execute("""UPDATE items SET video_url = %s, video_url_expires_at = %s, metadata = %s, stored_at = stored_at WHERE id = %s""", (verified["video_url"], verified["video_url_expires_at"], Jsonb(next_metadata), row["id"]))
                refreshed += 1
            except Exception as exc:
                failed += 1
                print(f"[91cg] refresh failed for {row['guid']}: {exc}")
            conn.commit()
    return {"processed": processed, "refreshed": refreshed, "failed": failed}


def cleanup_records(conn, retention_days: int, max_records: int) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS count FROM items")
        before_count = cur.fetchone()["count"]

        threshold = now_utc() - timedelta(days=retention_days)
        cur.execute("DELETE FROM video_resolution_queue WHERE expires_at <= NOW()")
        cur.execute(
            """
            DELETE FROM items i
            USING targets t
            WHERE t.id = i.target_id
              AND t.source IN ('youtube', 'heiliao', 'cg91', 'baoliao51', 'douyin', '18mh', 'rou', 'dadaafa', 'badnews', '91porna', '91porn', '91rb', '18j', 'avgood', '705hs', 'xxxtik', 'affair', 'attach', 'dirtyship', 'influencersgonewild', 'missav')
              AND i.expires_at <= NOW()
            """
        )
        cur.execute(
            """
            DELETE FROM items i
            WHERE i.stored_at < %s
              AND (
                i.video_url IS NULL
                OR NOT EXISTS (
                  SELECT 1
                  FROM video_stats vs
                  WHERE vs.item_id = i.id
                    AND vs.score >= 20
                )
              )
              AND NOT EXISTS (
                SELECT 1
                FROM target_profiles tp
                WHERE tp.target_id = i.target_id
                  AND tp.is_public_pool = TRUE
              )
            """,
            (threshold,),
        )

        if max_records > 0:
            cur.execute(
                """
                WITH ranked_items AS (
                    SELECT
                        i.id,
                        ROW_NUMBER() OVER (
                            ORDER BY
                                CASE
                                  WHEN i.video_url IS NOT NULL AND COALESCE(vs.score, 0) >= 20 THEN 1
                                  WHEN i.video_url IS NOT NULL AND COALESCE(tp.is_public_pool, FALSE) THEN 2
                                  WHEN i.video_url IS NOT NULL THEN 3
                                  ELSE 4
                                END,
                                i.stored_at DESC
                        ) AS keep_rank
                    FROM items i
                    LEFT JOIN video_stats vs ON vs.item_id = i.id
                    LEFT JOIN target_profiles tp ON tp.target_id = i.target_id
                ),
                doomed AS (
                    SELECT id
                    FROM ranked_items
                    WHERE keep_rank > %s
                )
                DELETE FROM items
                WHERE id IN (SELECT id FROM doomed)
                """,
                (max_records,),
            )

        cur.execute(
            """
            DELETE FROM targets t
            WHERE NOT EXISTS (
                SELECT 1 FROM subscriptions s WHERE s.target_id = t.id
            )
              AND NOT EXISTS (
                SELECT 1 FROM items i WHERE i.target_id = t.id
            )
              AND NOT EXISTS (
                SELECT 1 FROM video_resolution_queue vrq WHERE vrq.target_id = t.id
            )
            """
        )

        cur.execute("SELECT COUNT(*) AS count FROM items")
        after_count = cur.fetchone()["count"]

    return {"before": before_count, "after": after_count, "deleted": before_count - after_count}


def query_records(
    conn,
    *,
    limit: int,
    target: str | None,
    keyword: str | None,
    since: str | None,
    until: str | None,
    api_key: str | None,
) -> list[dict]:
    like_keyword = f"%{keyword.lower()}%" if keyword else None
    normalized_target = target.lower() if target else None
    since_dt = parse_datetime(since)
    until_dt = parse_datetime(until)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                i.guid,
                i.author,
                i.fullname,
                i.title,
                i.content,
                i.raw_content,
                i.translated_content,
                i.link,
                i.x_url,
                i.images,
                i.video_url,
                i.expires_at,
                i.video_url_expires_at,
                i.published_at,
                i.stored_at,
                i.is_retweet,
                t.source,
                t.kind,
                t.value
            FROM items i
            INNER JOIN targets t ON t.id = i.target_id
            WHERE (
                %s IS NULL
                OR EXISTS (
                    SELECT 1
                    FROM subscriptions s
                    INNER JOIN clients c ON c.id = s.client_id
                    WHERE s.target_id = i.target_id
                      AND c.api_key = %s
                      AND c.status = 'active'
                )
            )
              AND (
                %s IS NULL
                OR LOWER(CASE
                    WHEN t.source = 'youtube' THEN 'youtube:' || t.value
                    WHEN t.source = 'heiliao' THEN 'heiliao:' || t.value
                    WHEN t.source = 'cg91' THEN 'cg91:' || t.value
                    WHEN t.source = 'baoliao51' THEN 'baoliao51:' || t.value
                    WHEN t.source = 'douyin' THEN 'douyin:' || t.value
                    WHEN t.source = '18mh' THEN '18mh:' || t.value
                    WHEN t.source = 'rou' THEN 'rou:' || t.value
                    WHEN t.source = 'dadaafa' THEN 'dadaafa:' || t.value
                    WHEN t.source = '91porna' THEN '91porna:' || t.value
                    WHEN t.source = '91porn' THEN '91porn:' || t.value
                    WHEN t.source = '91rb' THEN '91rb:' || t.value
                    WHEN t.source = 'avgood' THEN 'avgood:' || t.value
                    WHEN t.source = '705hs' THEN '705hs:' || t.value
                    WHEN t.source = 'xxxtik' THEN 'xxxtik:' || t.value
                    WHEN t.source = 'affair' THEN 'affair:' || t.value
                    WHEN t.source = 'dirtyship' THEN 'dirtyship:' || t.value
                    WHEN t.source = 'influencersgonewild' THEN 'influencersgonewild:' || t.value
                    WHEN t.source = 'missav' THEN 'missav:' || t.value
                    WHEN t.source = '18j' THEN '18j:' || t.value
                    WHEN t.kind = 'keyword' THEN 'search:' || t.value
                    ELSE t.value
                END) = %s
              )
              AND (
                %s IS NULL
                OR LOWER(COALESCE(i.content, '')) LIKE %s
                OR LOWER(COALESCE(i.raw_content, '')) LIKE %s
                OR LOWER(COALESCE(i.translated_content, '')) LIKE %s
                OR LOWER(COALESCE(i.author, '')) LIKE %s
              )
              AND (%s IS NULL OR i.stored_at >= %s)
              AND (%s IS NULL OR i.stored_at <= %s)
            ORDER BY COALESCE(i.published_at, i.stored_at) DESC, i.stored_at DESC
            LIMIT %s
            """,
            (
                api_key,
                api_key,
                normalized_target,
                normalized_target,
                like_keyword,
                like_keyword,
                like_keyword,
                like_keyword,
                like_keyword,
                since_dt,
                since_dt,
                until_dt,
                until_dt,
                limit,
            ),
        )
        rows = cur.fetchall()

    records = []
    for row in rows:
        records.append(
            {
                "target": format_target_row(row),
                "author": row["author"],
                "fullname": row["fullname"],
                "guid": row["guid"],
                "title": row["title"],
                "content": row["content"],
                "raw_content": row["raw_content"],
                "translated_content": row["translated_content"],
                "link": row["link"],
                "x_url": row["x_url"],
                "images": row["images"] or [],
                "video_url": row["video_url"],
                "expires_at": row["expires_at"].isoformat() if row["expires_at"] else None,
                "video_url_expires_at": row["video_url_expires_at"].isoformat() if row["video_url_expires_at"] else None,
                "published_at": row["published_at"].isoformat() if row["published_at"] else None,
                "stored_at": row["stored_at"].isoformat() if row["stored_at"] else None,
                "is_retweet": row["is_retweet"],
            }
        )
    return records


def print_record(record: dict, index: int | None = None) -> None:
    prefix = f"{index}. " if index is not None else ""
    print(f"{prefix}[{record.get('stored_at', '-')}] {record.get('target', '-')}")
    print(f"   作者: {record.get('author', '-')}")
    if record.get("fullname"):
        print(f"   昵称: {record['fullname']}")
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


def command_register_client(args) -> int:
    with get_db_connection() as conn:
        with conn.transaction():
            client = register_client(conn, args.label)
        payload = {
            "id": str(client["id"]),
            "label": client["label"],
            "apiKey": client["api_key"],
            "feedToken": client["feed_token"],
            "feedUrlPath": f"/rss/{client['feed_token']}.xml",
            "createdAt": client["created_at"].isoformat(),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def douyin_api_headers(request_id: str, referer: str) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Content-Type": "text/plain",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36",
        "Referer": referer,
        "time": str(int(datetime.now().timestamp() * 1000))[:11],
        "version": "1.0.0",
        "deviceType": "web",
        "requestId": request_id,
        "language": "zh-CN",
    }


def douyin_api_key(request_id: str) -> bytes:
    request_bytes = bytes.fromhex(request_id.replace("-", ""))
    return hmac.new(DOUYIN_API_SECRET.encode("utf-8"), request_bytes, hashlib.sha256).digest()


def douyin_encrypt_payload(payload: dict, request_id: str) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    compressed = gzip.compress(raw)
    iv = secrets.token_bytes(16)
    cipher = AES.new(douyin_api_key(request_id), AES.MODE_CBC, iv)
    encrypted = cipher.encrypt(pad(compressed, AES.block_size))
    return base64.b64encode(iv + encrypted).decode("ascii")


def douyin_decrypt_payload(payload: str, request_id: str) -> dict:
    encrypted = base64.b64decode(payload)
    if len(encrypted) <= 16:
        raise ValueError("Douyin encrypted response is too short.")
    iv, ciphertext = encrypted[:16], encrypted[16:]
    cipher = AES.new(douyin_api_key(request_id), AES.MODE_CBC, iv)
    compressed = unpad(cipher.decrypt(ciphertext), AES.block_size)
    return json.loads(gzip.decompress(compressed).decode("utf-8"))


def douyin_api_post(base_url: str, path: str, data: dict) -> dict:
    request_id = str(uuid.uuid4())
    body = {
        "data": data,
        "token": "",
        "deviceId": str(uuid.uuid4()),
    }
    response = requests.post(
        urljoin(base_url + "/", path.lstrip("/")),
        headers=douyin_api_headers(request_id, base_url + "/"),
        data=douyin_encrypt_payload(body, request_id),
        timeout=DOUYIN_REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    decoded = douyin_decrypt_payload(response.text, request_id)
    if decoded.get("status") != "y":
        raise ValueError(f"Douyin API returned non-success status: {decoded.get('status')}")
    data_payload = decoded.get("data")
    if not isinstance(data_payload, dict):
        raise ValueError("Douyin API response has no data object.")
    return data_payload


def parse_douyin_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(value.strip(), fmt).replace(tzinfo=DOUYIN_TIMEZONE).astimezone(timezone.utc)
        except ValueError:
            continue
    return parse_datetime(value)


def douyin_item_tags(item: dict) -> list[str]:
    tags = []
    for tag in item.get("tags") or []:
        if isinstance(tag, dict) and isinstance(tag.get("name"), str) and tag["name"].strip():
            tags.append(tag["name"].strip())
    return tags


def clean_douyin_description(value: str | None) -> str:
    description = (value or "").strip()
    return "" if description in DOUYIN_EMPTY_DESCRIPTIONS else description


def douyin_video_url(base_url: str, raw_url: str) -> str:
    return urljoin(base_url + "/", raw_url.strip())


def douyin_detail_url(base_url: str, video_id: str) -> str:
    return urljoin(base_url + "/", f"recommend/?id={quote(video_id, safe='')}")


def parse_douyin_recommend_page(base_url: str, page: int, page_size: int = 10) -> dict:
    payload = douyin_api_post(base_url, "/api/movie/recommend", {"page": str(page), "page_size": str(page_size)})
    items = payload.get("data") if isinstance(payload.get("data"), list) else []
    return {
        "items": items,
        "current_page": int(payload.get("current_page") or page),
        "last_page": int(payload.get("last_page") or page),
        "total": int(payload.get("total") or 0),
        "page_size": int(payload.get("page_size") or page_size),
    }


def fetch_douyin_detail_item(base_url: str, video_id: str) -> dict:
    payload = douyin_api_post(base_url, "/api/movie/detail", {"id": video_id})
    if not isinstance(payload, dict) or not payload.get("id"):
        raise ValueError("Douyin detail API response is missing video data.")
    return payload


def normalize_douyin_item(base_url: str, item: dict) -> dict | None:
    if item.get("isAd") not in (None, "n"):
        return None
    video_id = str(item.get("id") or "").strip()
    if not video_id:
        return None
    play_links = []
    for link in item.get("play_links") or []:
        if not isinstance(link, dict):
            continue
        raw_url = link.get("m3u8_url")
        if not isinstance(raw_url, str) or not raw_url.strip() or not raw_url.strip().lower().endswith(".m3u8"):
            continue
        play_links.append({"code": str(link.get("code") or ""), "name": str(link.get("name") or ""), "m3u8_url": douyin_video_url(base_url, raw_url)})
    if not play_links:
        return None
    user = item.get("user") if isinstance(item.get("user"), dict) else {}
    title = str(item.get("name") or "").strip() or "抖阴视频"
    description = clean_douyin_description(str(item.get("description") or ""))
    image = item.get("img") if isinstance(item.get("img"), str) and item.get("img") else None
    return {
        "guid": f"douyin:{video_id}",
        "video_id": video_id,
        "title": title,
        "description": description,
        "image": image,
        "published_at": parse_douyin_datetime(item.get("show_at")) or now_utc(),
        "duration": item.get("duration"),
        "click": item.get("click"),
        "love": item.get("love"),
        "favorite": item.get("favorite"),
        "comment": item.get("comment"),
        "user_id": user.get("id"),
        "username": user.get("username"),
        "nickname": user.get("nickname"),
        "tags": douyin_item_tags(item),
        "play_links": play_links,
        "source_url": douyin_detail_url(base_url, video_id),
        "raw_pay_type": item.get("pay_type"),
        "raw_layer_type": item.get("layer_type"),
    }


def ensure_douyin_target(conn, base_url: str, *, public_pool: bool = True) -> dict:
    target_row = upsert_target(conn, f"douyin:{base_url}")
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO target_profiles (target_id, scope, tags, category, weight, is_public_pool)
            VALUES (%s, 'system', %s, 'adult', 45, %s)
            ON CONFLICT (target_id) DO UPDATE SET scope = EXCLUDED.scope, tags = EXCLUDED.tags, category = EXCLUDED.category, weight = EXCLUDED.weight, is_public_pool = EXCLUDED.is_public_pool, updated_at = NOW()
            """,
            (target_row["id"], Jsonb([DOUYIN_SITE_NAME, "抖阴", "视频"]), public_pool),
        )
    return target_row


def verify_douyin_video(item: dict) -> dict:
    errors = []
    for link in item["play_links"]:
        try:
            verified = verify_heiliao_hls_url(link["m3u8_url"], item["source_url"])
            return {**verified, "selected_link": link}
        except Exception as exc:
            errors.append(f"{link.get('code') or link.get('name') or link.get('m3u8_url')}: {exc}")
    raise ValueError("; ".join(errors) or "no playable Douyin links")


def upsert_douyin_video_item(conn, target_row: dict, item: dict, verified: dict, retention_hours: int) -> bool:
    published_at = item.get("published_at") or now_utc()
    expires_at = published_at + timedelta(hours=retention_hours)
    content = item.get("description") or item.get("title")
    images = [item["image"]] if item.get("image") else []
    presentation = build_item_author_presentation(
        target_row,
        author=DOUYIN_SITE_NAME,
        fullname=DOUYIN_SITE_NAME,
        x_url=None,
        link=item["source_url"],
    )
    metadata = {
        "target": format_target_row(target_row),
        "target_type": DOUYIN_KIND,
        "target_value": target_row["value"],
        "site_name": DOUYIN_SITE_NAME,
        "source_url": item["source_url"],
        "douyin_video_id": item["video_id"],
        "douyin_play_links": item["play_links"],
        "selected_link": verified.get("selected_link"),
        "duration": item.get("duration"),
        "click": item.get("click"),
        "love": item.get("love"),
        "favorite": item.get("favorite"),
        "comment": item.get("comment"),
        "user_id": item.get("user_id"),
        "username": item.get("username"),
        "nickname": item.get("nickname"),
        "tags": item.get("tags") or [],
        "raw_pay_type": item.get("raw_pay_type"),
        "raw_layer_type": item.get("raw_layer_type"),
        "resolver": "douyin-encrypted-api",
        "resolved_at": now_iso(),
        "video_url_expires_at": verified["video_url_expires_at"].isoformat(),
    }
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO items (
                target_id, guid, author, fullname,
                display_author, display_handle, author_profile_url, author_profile_platform,
                title, content, raw_content, translated_content,
                link, x_url, images, video_url, expires_at, video_url_expires_at,
                published_at, stored_at, is_retweet, metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, %s, NULL, %s, %s, %s, %s, %s, NOW(), FALSE, %s)
            ON CONFLICT (target_id, guid) DO UPDATE SET
                display_author = EXCLUDED.display_author,
                display_handle = EXCLUDED.display_handle,
                author_profile_url = EXCLUDED.author_profile_url,
                author_profile_platform = EXCLUDED.author_profile_platform,
                title = EXCLUDED.title,
                content = EXCLUDED.content,
                raw_content = EXCLUDED.raw_content,
                images = EXCLUDED.images,
                video_url = EXCLUDED.video_url,
                expires_at = EXCLUDED.expires_at,
                video_url_expires_at = EXCLUDED.video_url_expires_at,
                published_at = COALESCE(items.published_at, EXCLUDED.published_at),
                metadata = items.metadata || EXCLUDED.metadata
            RETURNING (xmax = 0) AS inserted
            """,
            (
                target_row["id"],
                item["guid"],
                DOUYIN_SITE_NAME,
                DOUYIN_SITE_NAME,
                presentation["display_author"],
                presentation["display_handle"],
                presentation["author_profile_url"],
                presentation["author_profile_platform"],
                item.get("title"),
                content,
                item.get("title"),
                item["source_url"],
                Jsonb(images),
                verified["video_url"],
                expires_at,
                verified["video_url_expires_at"],
                published_at,
                Jsonb(metadata),
            ),
        )
        row = cur.fetchone()
    return bool(row and row.get("inserted"))


def monitor_douyin_site(conn, *, base_url: str, max_pages: int, retention_hours: int, public_pool: bool, dry_run: bool = False) -> dict:
    base_url = normalize_douyin_target_value(base_url)
    target_row = None if dry_run else ensure_douyin_target(conn, base_url, public_pool=public_pool)
    cutoff = now_utc() - timedelta(hours=retention_hours)
    inserted = updated = parsed_items = verified_count = skipped_existing = skipped_unverified = skipped_ad = skipped_old = pages = 0
    samples = []
    latest_guid = None
    for page in range(1, max_pages + 1):
        pages += 1
        page_payload = parse_douyin_recommend_page(base_url, page)
        raw_items = page_payload["items"]
        print(
            f"[douyin] page={page} raw_items={len(raw_items)} current_page={page_payload['current_page']} "
            f"last_page={page_payload['last_page']} total={page_payload['total']}"
        )
        if not raw_items:
            print(f"[douyin] page={page} empty_list stop=true")
            break
        page_inserted = page_existing = page_old = page_updated = page_verified = page_unverified = page_skipped_ad = page_parsed_items = 0
        for raw_item in raw_items:
            item = normalize_douyin_item(base_url, raw_item)
            if not item:
                skipped_ad += 1
                page_skipped_ad += 1
                continue
            parsed_items += 1
            page_parsed_items += 1
            latest_guid = latest_guid or item["guid"]
            if item["published_at"] < cutoff:
                skipped_old += 1
                page_old += 1
                continue
            if target_row and item_exists_for_guid(conn, str(target_row["id"]), item["guid"]):
                skipped_existing += 1
                page_existing += 1
                continue
            try:
                verified = verify_douyin_video(item)
            except Exception as exc:
                skipped_unverified += 1
                page_unverified += 1
                print(f"[douyin] skip unverified {item['guid']}: {exc}")
                continue
            verified_count += 1
            page_verified += 1
            if dry_run:
                samples.append({"guid": item["guid"], "title": item.get("title"), "published_at": item["published_at"].isoformat(), "video_url": verified["video_url"], "video_url_expires_at": verified["video_url_expires_at"].isoformat()})
                continue
            if upsert_douyin_video_item(conn, target_row, item, verified, retention_hours):
                inserted += 1
                page_inserted += 1
            else:
                updated += 1
                page_updated += 1
        if target_row:
            upsert_crawl_state(conn, target_row["id"], last_guid=latest_guid, last_error=None, success=True)
        print(
            f"[douyin] page={page} parsed_items={page_parsed_items} verified={page_verified} "
            f"inserted={page_inserted} updated={page_updated} existing={page_existing} old={page_old} "
            f"ad_or_invalid={page_skipped_ad} unverified={page_unverified}"
        )
        if page_payload["current_page"] >= page_payload["last_page"]:
            break
        if page_inserted == 0 and (page_existing > 0 or page_old == len(raw_items)):
            break
    return {"pages": pages, "parsed_items": parsed_items, "verified": verified_count, "inserted": inserted, "updated": updated, "skipped_existing": skipped_existing, "skipped_unverified": skipped_unverified, "skipped_ad": skipped_ad, "skipped_old": skipped_old, "samples": samples[:10]}


def refresh_douyin_playback_urls(conn, limit: int, refresh_window_minutes: int, critical_window_minutes: int) -> dict[str, int]:
    processed = refreshed = failed = 0
    queries = [
        ("""SELECT i.* FROM items i INNER JOIN targets t ON t.id = i.target_id WHERE t.source = %s AND i.expires_at > NOW() AND i.video_url_expires_at <= NOW() + (%s || ' minutes')::interval ORDER BY i.video_url_expires_at ASC LIMIT %s""", (DOUYIN_SOURCE, critical_window_minutes, limit)),
        ("""SELECT i.* FROM items i INNER JOIN targets t ON t.id = i.target_id WHERE t.source = %s AND i.expires_at > NOW() AND i.video_url_expires_at <= NOW() + (%s || ' minutes')::interval ORDER BY i.video_url_expires_at ASC, i.published_at DESC LIMIT %s""", (DOUYIN_SOURCE, refresh_window_minutes, limit)),
    ]
    seen_ids: set[str] = set()
    for sql, params in queries:
        if processed >= limit:
            break
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        for row in rows:
            row_id = str(row["id"])
            if row_id in seen_ids or processed >= limit:
                continue
            seen_ids.add(row_id)
            processed += 1
            metadata = row["metadata"] or {}
            base_url = normalize_douyin_target_value(metadata.get("target_value") or DOUYIN_DEFAULT_BASE_URL)
            video_id = metadata.get("douyin_video_id") or str(row["guid"]).replace("douyin:", "", 1)
            try:
                detail_item = normalize_douyin_item(base_url, fetch_douyin_detail_item(base_url, video_id))
                if not detail_item:
                    raise ValueError("Douyin detail API returned no playable item.")
                item = {"source_url": detail_item["source_url"], "play_links": detail_item["play_links"]}
                verified = verify_douyin_video(item)
                next_metadata = metadata | {
                    "resolver": "douyin-encrypted-api",
                    "resolved_at": now_iso(),
                    "source_url": detail_item["source_url"],
                    "douyin_play_links": detail_item["play_links"],
                    "selected_link": verified.get("selected_link"),
                    "video_url_expires_at": verified["video_url_expires_at"].isoformat(),
                }
                with conn.cursor() as cur:
                    cur.execute("""UPDATE items SET video_url = %s, video_url_expires_at = %s, metadata = %s, stored_at = stored_at WHERE id = %s""", (verified["video_url"], verified["video_url_expires_at"], Jsonb(next_metadata), row["id"]))
                refreshed += 1
            except Exception as exc:
                failed += 1
                print(f"[douyin] refresh failed for {row['guid']}: {exc}")
    return {"processed": processed, "refreshed": refreshed, "failed": failed}


def command_monitor(args) -> int:
    instances = load_instances()
    runtime_penalties: dict[str, int] = {}
    retention_days = args.retention_days if args.retention_days is not None else DEFAULT_RETENTION_DAYS
    max_records = args.max_records if args.max_records is not None else DEFAULT_MAX_RECORDS
    shard_index = args.shard_index if args.shard_index is not None else 0
    shard_count = args.shard_count if args.shard_count is not None else 1

    if shard_count <= 0:
        raise ValueError("shard-count must be greater than 0.")
    if shard_index < 0 or shard_index >= shard_count:
        raise ValueError("shard-index must be between 0 and shard-count - 1.")

    with get_db_connection() as conn:
        if args.targets:
            target_rows = [row for row in (upsert_target(conn, target) for target in parse_targets(args.targets)) if row.get("source") == "twitter"]
        elif args.system_only:
            target_rows = load_system_targets(conn)
        elif args.include_system:
            target_map = {row["id"]: row for row in load_active_targets(conn)}
            target_map.update({row["id"]: row for row in load_system_targets(conn)})
            target_rows = list(target_map.values())
        else:
            target_rows = load_active_targets(conn)

        target_rows = select_targets_for_shard(target_rows, shard_index, shard_count)

        if not target_rows:
            print(
                f"[系统] 当前分片没有活跃订阅目标，跳过本轮监控 "
                f"(shard_index={shard_index}, shard_count={shard_count})"
            )
            return 0

        print(
            f"[{datetime.now()}] 开始监控，共 {len(target_rows)} 个目标 "
            f"(shard_index={shard_index}, shard_count={shard_count})"
        )
        new_records = 0
        for target_row in target_rows:
            target = format_target_row(target_row)
            previous_id = target_row.get("last_guid")
            try:
                ordered_instances = order_instances_for_attempts(instances, runtime_penalties)
                tweets = scrape_nitter_with_playwright(target, ordered_instances, runtime_penalties)
                if not tweets:
                    upsert_crawl_state(conn, target_row["id"], last_guid=previous_id, last_error="No tweets returned.", success=False)
                    conn.commit()
                    continue

                current_id = tweets[0]["guid"]
                if previous_id == current_id:
                    print(f"[{target}] 无更新")
                    upsert_crawl_state(conn, target_row["id"], last_guid=current_id, last_error=None, success=True)
                    conn.commit()
                    continue

                inserted = insert_items(conn, target_row, tweets, previous_id)
                upsert_crawl_state(conn, target_row["id"], last_guid=current_id, last_error=None, success=True)
                conn.commit()
                new_records += inserted
                print(f"[{target}] 已保存 {inserted} 条新记录到数据库")
            except Exception as exc:
                conn.rollback()
                upsert_crawl_state(conn, target_row["id"], last_guid=previous_id, last_error=str(exc)[:500], success=False)
                conn.commit()
                print(f"[{target}] 处理异常: {exc}")

        print(f"[系统] 本轮新增 {new_records} 条记录")

        if not args.skip_cleanup:
            stats = cleanup_records(conn, retention_days, max_records)
            conn.commit()
            print(
                f"[系统] 清理完成: 保留 {stats['after']} 条，删除 {stats['deleted']} 条 "
                f"(retention_days={retention_days}, max_records={max_records})"
            )
    return 0


def command_monitor_youtube(args) -> int:
    retention_days = args.retention_days if args.retention_days is not None else DEFAULT_RETENTION_DAYS
    max_records = args.max_records if args.max_records is not None else DEFAULT_MAX_RECORDS
    shard_index = args.shard_index if args.shard_index is not None else 0
    shard_count = args.shard_count if args.shard_count is not None else 1

    if shard_count <= 0:
        raise ValueError("shard-count must be greater than 0.")
    if shard_index < 0 or shard_index >= shard_count:
        raise ValueError("shard-index must be between 0 and shard-count - 1.")

    with get_db_connection() as conn:
        if args.targets:
            target_rows = [row for row in (upsert_target(conn, target) for target in parse_targets(args.targets)) if row.get("source") == "youtube"]
        else:
            target_rows = load_youtube_targets(conn)

        target_rows = select_targets_for_shard(target_rows, shard_index, shard_count)
        if not target_rows:
            print(
                f"[YouTube] 当前分片没有活跃 YouTube 订阅目标，跳过 "
                f"(shard_index={shard_index}, shard_count={shard_count})"
            )
            return 0

        resolved_records = 0
        for target_row in target_rows:
            target = format_target_row(target_row)
            try:
                resolved = monitor_youtube_target(conn, target_row)
                conn.commit()
                resolved_records += resolved
                print(f"[{target}] 已解析 {resolved} 条 YouTube 视频")
            except Exception as exc:
                conn.rollback()
                upsert_crawl_state(conn, target_row["id"], last_guid=target_row.get("last_guid"), last_error=str(exc)[:500], success=False)
                conn.commit()
                print(f"[{target}] YouTube 处理异常: {exc}")

        print(f"[YouTube] 本轮解析 {resolved_records} 条视频")
        if not args.skip_cleanup:
            stats = cleanup_records(conn, retention_days, max_records)
            conn.commit()
            print(
                f"[YouTube] 清理完成: 保留 {stats['after']} 条，删除 {stats['deleted']} 条 "
                f"(retention_days={retention_days}, max_records={max_records})"
            )
    return 0


def command_subscribe(args) -> int:
    with get_db_connection() as conn:
        client = resolve_client(conn, args.api_key)
        if not client or client["status"] != "active":
            print("[系统] 无效的 API key")
            return 1

        if args.action == "list":
            subscriptions = list_subscriptions(conn, client["id"])
            if not subscriptions:
                print("[系统] 当前没有订阅目标")
                return 0
            print("[系统] 当前订阅列表:")
            for idx, target in enumerate(subscriptions, start=1):
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

        with conn.transaction():
            if args.action == "add":
                add_subscriptions(conn, client["id"], targets)
                print(f"[系统] 已新增 {len(targets)} 个订阅目标")
            elif args.action == "remove":
                remove_subscriptions(conn, client["id"], targets)
                print(f"[系统] 已移除 {len(targets)} 个订阅目标")
            elif args.action == "set":
                replace_subscriptions(conn, client["id"], targets)
                print(f"[系统] 已重置订阅列表，共 {len(targets)} 个目标")
            else:
                print(f"[系统] 未知订阅动作: {args.action}")
                return 1

        current = list_subscriptions(conn, client["id"])
        if current:
            print("[系统] 当前订阅:")
            for idx, target in enumerate(current, start=1):
                print(f"{idx}. {target}")
    return 0


def command_query(args) -> int:
    limit = args.limit if args.limit > 0 else 20
    with get_db_connection() as conn:
        records = query_records(
            conn,
            limit=limit,
            target=args.target,
            keyword=args.keyword,
            since=args.since,
            until=args.until,
            api_key=args.api_key,
        )

    print(f"[系统] 查询结果 {len(records)} 条")
    for idx, record in enumerate(records, start=1):
        print_record(record, idx)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as fh:
            json.dump(records, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        print(f"[系统] 查询结果已写入 {output_path}")

    return 0


def command_cleanup(args) -> int:
    retention_days = args.retention_days if args.retention_days is not None else DEFAULT_RETENTION_DAYS
    max_records = args.max_records if args.max_records is not None else DEFAULT_MAX_RECORDS
    with get_db_connection() as conn:
        stats = cleanup_records(conn, retention_days, max_records)
        conn.commit()
    print(
        f"[系统] 清理完成: 处理前 {stats['before']} 条，处理后 {stats['after']} 条，"
        f"删除 {stats['deleted']} 条"
    )
    return 0


def command_refresh_youtube_playback_urls(args) -> int:
    with get_db_connection() as conn:
        stats = refresh_youtube_playback_urls(
            conn,
            limit=max(1, args.limit),
            refresh_window_minutes=max(1, args.refresh_window_minutes),
            critical_window_minutes=max(1, args.critical_window_minutes),
        )
        conn.commit()
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def command_monitor_heiliao(args) -> int:
    base_url = args.base_url or HEILIAO_DEFAULT_BASE_URL
    retention_hours = args.retention_hours if args.retention_hours is not None else HEILIAO_RETENTION_HOURS
    if args.retention_days is not None:
        retention_hours = args.retention_days * 24
    max_records = args.max_records if args.max_records is not None else DEFAULT_MAX_RECORDS
    if args.dry_run and not DATABASE_URL:
        stats = monitor_heiliao_site(
            None,
            base_url=base_url,
            max_pages=max(1, args.max_pages),
            retention_hours=max(1, retention_hours),
            public_pool=not args.private_pool,
            dry_run=True,
        )
        print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
        return 0

    with get_db_connection() as conn:
        stats = monitor_heiliao_site(
            conn,
            base_url=base_url,
            max_pages=max(1, args.max_pages),
            retention_hours=max(1, retention_hours),
            public_pool=not args.private_pool,
            dry_run=args.dry_run,
        )
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
        if not args.skip_cleanup and not args.dry_run:
            cleanup_stats = cleanup_records(conn, max(1, (retention_hours + 23) // 24), max_records)
            conn.commit()
            stats = {**stats, "cleanup": cleanup_stats}
    print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
    return 0


def command_refresh_heiliao_playback_urls(args) -> int:
    with get_db_connection() as conn:
        stats = refresh_heiliao_playback_urls(
            conn,
            limit=max(1, args.limit),
            refresh_window_minutes=max(1, args.refresh_window_minutes),
            critical_window_minutes=max(1, args.critical_window_minutes),
        )
        conn.commit()
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def command_monitor_cg91(args) -> int:
    base_url = args.base_url or CG91_DEFAULT_BASE_URL
    retention_hours = args.retention_hours if args.retention_hours is not None else CG91_RETENTION_HOURS
    if args.retention_days is not None:
        retention_hours = args.retention_days * 24
    max_records = args.max_records if args.max_records is not None else DEFAULT_MAX_RECORDS
    if args.dry_run and not DATABASE_URL:
        stats = monitor_cg91_site(None, base_url=base_url, max_pages=max(1, args.max_pages), retention_hours=max(1, retention_hours), public_pool=not args.private_pool, dry_run=True)
        print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
        return 0
    with get_db_connection() as conn:
        stats = monitor_cg91_site(conn, base_url=base_url, max_pages=max(1, args.max_pages), retention_hours=max(1, retention_hours), public_pool=not args.private_pool, dry_run=args.dry_run)
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
        if not args.skip_cleanup and not args.dry_run:
            cleanup_stats = cleanup_records(conn, max(1, (retention_hours + 23) // 24), max_records)
            conn.commit()
            stats = {**stats, "cleanup": cleanup_stats}
    print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
    return 0


def command_refresh_cg91_playback_urls(args) -> int:
    with get_db_connection() as conn:
        stats = refresh_cg91_playback_urls(conn, limit=max(1, args.limit), refresh_window_minutes=max(1, args.refresh_window_minutes), critical_window_minutes=max(1, args.critical_window_minutes))
        conn.commit()
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def command_monitor_baoliao51(args) -> int:
    base_url = args.base_url or BAOLIAO51_DEFAULT_BASE_URL
    retention_hours = args.retention_hours if args.retention_hours is not None else BAOLIAO51_RETENTION_HOURS
    if args.retention_days is not None:
        retention_hours = args.retention_days * 24
    max_records = args.max_records if args.max_records is not None else DEFAULT_MAX_RECORDS
    if args.dry_run and not DATABASE_URL:
        stats = monitor_baoliao51_site(None, base_url=base_url, max_pages=max(1, args.max_pages), retention_hours=max(1, retention_hours), public_pool=not args.private_pool, dry_run=True)
        print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
        return 0
    with get_db_connection() as conn:
        stats = monitor_baoliao51_site(conn, base_url=base_url, max_pages=max(1, args.max_pages), retention_hours=max(1, retention_hours), public_pool=not args.private_pool, dry_run=args.dry_run)
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
        if not args.skip_cleanup and not args.dry_run:
            cleanup_stats = cleanup_records(conn, max(1, (retention_hours + 23) // 24), max_records)
            conn.commit()
            stats = {**stats, "cleanup": cleanup_stats}
    print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
    return 0


def command_refresh_baoliao51_playback_urls(args) -> int:
    with get_db_connection() as conn:
        stats = refresh_baoliao51_playback_urls(conn, limit=max(1, args.limit), refresh_window_minutes=max(1, args.refresh_window_minutes), critical_window_minutes=max(1, args.critical_window_minutes))
        conn.commit()
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def command_monitor_douyin(args) -> int:
    base_url = args.base_url or DOUYIN_DEFAULT_BASE_URL
    retention_hours = args.retention_hours if args.retention_hours is not None else DOUYIN_RETENTION_HOURS
    if args.retention_days is not None:
        retention_hours = args.retention_days * 24
    max_records = args.max_records if args.max_records is not None else DEFAULT_MAX_RECORDS
    if args.dry_run and not DATABASE_URL:
        stats = monitor_douyin_site(None, base_url=base_url, max_pages=max(1, args.max_pages), retention_hours=max(1, retention_hours), public_pool=not args.private_pool, dry_run=True)
        print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
        return 0
    with get_db_connection() as conn:
        stats = monitor_douyin_site(conn, base_url=base_url, max_pages=max(1, args.max_pages), retention_hours=max(1, retention_hours), public_pool=not args.private_pool, dry_run=args.dry_run)
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
        if not args.skip_cleanup and not args.dry_run:
            cleanup_stats = cleanup_records(conn, max(1, (retention_hours + 23) // 24), max_records)
            conn.commit()
            stats = {**stats, "cleanup": cleanup_stats}
    print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
    return 0


def command_refresh_douyin_playback_urls(args) -> int:
    with get_db_connection() as conn:
        stats = refresh_douyin_playback_urls(conn, limit=max(1, args.limit), refresh_window_minutes=max(1, args.refresh_window_minutes), critical_window_minutes=max(1, args.critical_window_minutes))
        conn.commit()
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def command_monitor_18mh(args) -> int:
    base_url = args.base_url or MH18_DEFAULT_BASE_URL
    retention_hours = args.retention_hours if args.retention_hours is not None else MH18_RETENTION_HOURS
    if args.retention_days is not None:
        retention_hours = args.retention_days * 24
    max_records = args.max_records if args.max_records is not None else DEFAULT_MAX_RECORDS
    if args.dry_run and not DATABASE_URL:
        stats = monitor_18mh_site(None, base_url=base_url, max_pages=max(1, args.max_pages), retention_hours=max(1, retention_hours), public_pool=not args.private_pool, dry_run=True)
        print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
        return 0
    with get_db_connection() as conn:
        stats = monitor_18mh_site(conn, base_url=base_url, max_pages=max(1, args.max_pages), retention_hours=max(1, retention_hours), public_pool=not args.private_pool, dry_run=args.dry_run)
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
        if not args.skip_cleanup and not args.dry_run:
            cleanup_stats = cleanup_records(conn, max(1, (retention_hours + 23) // 24), max_records)
            conn.commit()
            stats = {**stats, "cleanup": cleanup_stats}
    print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
    return 0


def command_refresh_18mh_playback_urls(args) -> int:
    with get_db_connection() as conn:
        stats = refresh_18mh_playback_urls(conn, limit=max(1, args.limit), refresh_window_minutes=max(1, args.refresh_window_minutes), critical_window_minutes=max(1, args.critical_window_minutes))
        conn.commit()
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def command_monitor_rou(args) -> int:
    base_url = args.base_url or ROU_DEFAULT_BASE_URL
    retention_hours = args.retention_hours if args.retention_hours is not None else ROU_RETENTION_HOURS
    if args.retention_days is not None:
        retention_hours = args.retention_days * 24
    max_records = args.max_records if args.max_records is not None else DEFAULT_MAX_RECORDS
    if args.dry_run and not DATABASE_URL:
        stats = monitor_rou_site(None, base_url=base_url, max_pages=max(1, args.max_pages), retention_hours=max(1, retention_hours), public_pool=not args.private_pool, dry_run=True)
        print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
        return 0
    with get_db_connection() as conn:
        stats = monitor_rou_site(conn, base_url=base_url, max_pages=max(1, args.max_pages), retention_hours=max(1, retention_hours), public_pool=not args.private_pool, dry_run=args.dry_run)
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
        if not args.skip_cleanup and not args.dry_run:
            cleanup_stats = cleanup_records(conn, max(1, (retention_hours + 23) // 24), max_records)
            conn.commit()
            stats = {**stats, "cleanup": cleanup_stats}
    print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
    return 0


def command_refresh_rou_playback_urls(args) -> int:
    with get_db_connection() as conn:
        stats = refresh_rou_playback_urls(conn, limit=max(1, args.limit), refresh_window_minutes=max(1, args.refresh_window_minutes), critical_window_minutes=max(1, args.critical_window_minutes))
        conn.commit()
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def command_monitor_dadaafa(args) -> int:
    base_url = args.base_url or DADAAFA_DEFAULT_BASE_URL
    retention_hours = args.retention_hours if args.retention_hours is not None else DADAAFA_RETENTION_HOURS
    if args.retention_days is not None:
        retention_hours = args.retention_days * 24
    max_records = args.max_records if args.max_records is not None else DEFAULT_MAX_RECORDS
    if args.dry_run and not DATABASE_URL:
        stats = monitor_dadaafa_site(None, base_url=base_url, max_pages=max(1, args.max_pages), retention_hours=max(1, retention_hours), public_pool=not args.private_pool, dry_run=True)
        print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
        return 0
    with get_db_connection() as conn:
        stats = monitor_dadaafa_site(conn, base_url=base_url, max_pages=max(1, args.max_pages), retention_hours=max(1, retention_hours), public_pool=not args.private_pool, dry_run=args.dry_run)
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
        if not args.skip_cleanup and not args.dry_run:
            cleanup_stats = cleanup_records(conn, max(1, (retention_hours + 23) // 24), max_records)
            conn.commit()
            stats = {**stats, "cleanup": cleanup_stats}
    print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
    return 0


def command_refresh_dadaafa_playback_urls(args) -> int:
    with get_db_connection() as conn:
        stats = refresh_dadaafa_playback_urls(conn, limit=max(1, args.limit), refresh_window_minutes=max(1, args.refresh_window_minutes), critical_window_minutes=max(1, args.critical_window_minutes))
        conn.commit()
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def command_monitor_18j(args) -> int:
    base_url = args.base_url or J18_DEFAULT_BASE_URL
    retention_hours = args.retention_hours if args.retention_hours is not None else J18_RETENTION_HOURS
    if args.retention_days is not None:
        retention_hours = args.retention_days * 24
    max_records = args.max_records if args.max_records is not None else DEFAULT_MAX_RECORDS
    if args.dry_run and not DATABASE_URL:
        stats = monitor_18j_site(None, base_url=base_url, max_pages=max(1, args.max_pages), retention_hours=max(1, retention_hours), public_pool=not args.private_pool, dry_run=True)
        print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
        return 0
    with get_db_connection() as conn:
        stats = monitor_18j_site(conn, base_url=base_url, max_pages=max(1, args.max_pages), retention_hours=max(1, retention_hours), public_pool=not args.private_pool, dry_run=args.dry_run)
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
        if not args.skip_cleanup and not args.dry_run:
            cleanup_stats = cleanup_records(conn, max(1, (retention_hours + 23) // 24), max_records)
            conn.commit()
            stats = {**stats, "cleanup": cleanup_stats}
    print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
    return 0


def command_refresh_18j_playback_urls(args) -> int:
    with get_db_connection() as conn:
        stats = refresh_18j_playback_urls(conn, limit=max(1, args.limit), refresh_window_minutes=max(1, args.refresh_window_minutes), critical_window_minutes=max(1, args.critical_window_minutes))
        conn.commit()
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def command_monitor_1mtif(args) -> int:
    base_url = args.base_url or MTIF_DEFAULT_BASE_URL
    retention_hours = args.retention_hours if args.retention_hours is not None else MTIF_RETENTION_HOURS
    if args.retention_days is not None:
        retention_hours = args.retention_days * 24
    max_records = args.max_records if args.max_records is not None else DEFAULT_MAX_RECORDS
    if args.dry_run and not DATABASE_URL:
        stats = monitor_mtif_site(None, base_url=base_url, max_pages=max(1, args.max_pages), retention_hours=max(1, retention_hours), public_pool=not args.private_pool, dry_run=True)
        print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
        return 0
    with get_db_connection() as conn:
        stats = monitor_mtif_site(conn, base_url=base_url, max_pages=max(1, args.max_pages), retention_hours=max(1, retention_hours), public_pool=not args.private_pool, dry_run=args.dry_run)
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
        if not args.skip_cleanup and not args.dry_run:
            cleanup_stats = cleanup_records(conn, max(1, (retention_hours + 23) // 24), max_records)
            conn.commit()
            stats = {**stats, "cleanup": cleanup_stats}
    print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
    return 0


def command_refresh_1mtif_playback_urls(args) -> int:
    with get_db_connection() as conn:
        stats = refresh_mtif_playback_urls(conn, limit=max(1, args.limit), refresh_window_minutes=max(1, args.refresh_window_minutes), critical_window_minutes=max(1, args.critical_window_minutes))
        conn.commit()
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def command_monitor_91porna(args) -> int:
    base_url = args.base_url or PORNA91_DEFAULT_BASE_URL
    retention_hours = args.retention_hours if args.retention_hours is not None else PORNA91_RETENTION_HOURS
    if args.retention_days is not None:
        retention_hours = args.retention_days * 24
    max_records = args.max_records if args.max_records is not None else DEFAULT_MAX_RECORDS
    if args.dry_run and not DATABASE_URL:
        stats = monitor_porna91_site(None, base_url=base_url, max_pages=max(1, args.max_pages), retention_hours=max(1, retention_hours), public_pool=not args.private_pool, dry_run=True)
        print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
        return 0
    with get_db_connection() as conn:
        stats = monitor_porna91_site(conn, base_url=base_url, max_pages=max(1, args.max_pages), retention_hours=max(1, retention_hours), public_pool=not args.private_pool, dry_run=args.dry_run)
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
        if not args.skip_cleanup and not args.dry_run:
            cleanup_stats = cleanup_records(conn, max(1, (retention_hours + 23) // 24), max_records)
            conn.commit()
            stats = {**stats, "cleanup": cleanup_stats}
    print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
    return 0


def command_refresh_91porna_playback_urls(args) -> int:
    with get_db_connection() as conn:
        stats = refresh_porna91_playback_urls(conn, limit=max(1, args.limit), refresh_window_minutes=max(1, args.refresh_window_minutes), critical_window_minutes=max(1, args.critical_window_minutes))
        conn.commit()
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def command_monitor_91porn(args) -> int:
    base_url = args.base_url or PORN91_DEFAULT_BASE_URL
    retention_hours = args.retention_hours if args.retention_hours is not None else PORN91_RETENTION_HOURS
    if args.retention_days is not None:
        retention_hours = args.retention_days * 24
    max_records = args.max_records if args.max_records is not None else DEFAULT_MAX_RECORDS
    if args.dry_run and not DATABASE_URL:
        stats = monitor_91porn_site(None, base_url=base_url, max_pages=max(1, args.max_pages), retention_hours=max(1, retention_hours), public_pool=not args.private_pool, dry_run=True)
        print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
        return 0
    with get_db_connection() as conn:
        stats = monitor_91porn_site(conn, base_url=base_url, max_pages=max(1, args.max_pages), retention_hours=max(1, retention_hours), public_pool=not args.private_pool, dry_run=args.dry_run)
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
        if not args.skip_cleanup and not args.dry_run:
            cleanup_stats = cleanup_records(conn, max(1, (retention_hours + 23) // 24), max_records)
            conn.commit()
            stats = {**stats, "cleanup": cleanup_stats}
    print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
    return 0


def command_refresh_91porn_playback_urls(args) -> int:
    with get_db_connection() as conn:
        stats = refresh_91porn_playback_urls(conn, limit=max(1, args.limit), refresh_window_minutes=max(1, args.refresh_window_minutes), critical_window_minutes=max(1, args.critical_window_minutes))
        conn.commit()
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def command_monitor_91rb(args) -> int:
    base_url = args.base_url or RB91_DEFAULT_BASE_URL
    retention_hours = args.retention_hours if args.retention_hours is not None else RB91_RETENTION_HOURS
    if args.retention_days is not None:
        retention_hours = args.retention_days * 24
    max_records = args.max_records if args.max_records is not None else DEFAULT_MAX_RECORDS
    if args.dry_run and not DATABASE_URL:
        stats = monitor_91rb_site(None, base_url=base_url, max_pages=max(1, args.max_pages), retention_hours=max(1, retention_hours), public_pool=not args.private_pool, dry_run=True)
        print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
        return 0
    with get_db_connection() as conn:
        stats = monitor_91rb_site(conn, base_url=base_url, max_pages=max(1, args.max_pages), retention_hours=max(1, retention_hours), public_pool=not args.private_pool, dry_run=args.dry_run)
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
        if not args.skip_cleanup and not args.dry_run:
            cleanup_stats = cleanup_records(conn, max(1, (retention_hours + 23) // 24), max_records)
            conn.commit()
            stats = {**stats, "cleanup": cleanup_stats}
    print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
    return 0


def command_refresh_91rb_playback_urls(args) -> int:
    with get_db_connection() as conn:
        stats = refresh_91rb_playback_urls(conn, limit=max(1, args.limit), refresh_window_minutes=max(1, args.refresh_window_minutes), critical_window_minutes=max(1, args.critical_window_minutes))
        conn.commit()
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def command_monitor_avgood(args) -> int:
    base_url = args.base_url or AVGOOD_DEFAULT_BASE_URL
    retention_hours = args.retention_hours if args.retention_hours is not None else AVGOOD_RETENTION_HOURS
    if args.retention_days is not None:
        retention_hours = args.retention_days * 24
    max_records = args.max_records if args.max_records is not None else DEFAULT_MAX_RECORDS
    if args.dry_run and not DATABASE_URL:
        stats = monitor_avgood_site(None, base_url=base_url, max_pages=max(1, args.max_pages), retention_hours=max(1, retention_hours), public_pool=not args.private_pool, dry_run=True)
        print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
        return 0
    with get_db_connection() as conn:
        stats = monitor_avgood_site(conn, base_url=base_url, max_pages=max(1, args.max_pages), retention_hours=max(1, retention_hours), public_pool=not args.private_pool, dry_run=args.dry_run)
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
        if not args.skip_cleanup and not args.dry_run:
            cleanup_stats = cleanup_records(conn, max(1, (retention_hours + 23) // 24), max_records)
            conn.commit()
            stats = {**stats, "cleanup": cleanup_stats}
    print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
    return 0


def command_refresh_avgood_playback_urls(args) -> int:
    with get_db_connection() as conn:
        stats = refresh_avgood_playback_urls(conn, limit=max(1, args.limit), refresh_window_minutes=max(1, args.refresh_window_minutes), critical_window_minutes=max(1, args.critical_window_minutes))
        conn.commit()
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def command_monitor_705hs(args) -> int:
    base_url = args.base_url or HS705_DEFAULT_BASE_URL
    retention_hours = args.retention_hours if args.retention_hours is not None else HS705_RETENTION_HOURS
    if args.retention_days is not None:
        retention_hours = args.retention_days * 24
    max_records = args.max_records if args.max_records is not None else DEFAULT_MAX_RECORDS
    if args.dry_run and not DATABASE_URL:
        stats = monitor_hs705_site(None, base_url=base_url, max_pages=max(1, args.max_pages), retention_hours=max(1, retention_hours), public_pool=not args.private_pool, dry_run=True)
        print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
        return 0
    with get_db_connection() as conn:
        stats = monitor_hs705_site(conn, base_url=base_url, max_pages=max(1, args.max_pages), retention_hours=max(1, retention_hours), public_pool=not args.private_pool, dry_run=args.dry_run)
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
        if not args.skip_cleanup and not args.dry_run:
            cleanup_stats = cleanup_records(conn, max(1, (retention_hours + 23) // 24), max_records)
            conn.commit()
            stats = {**stats, "cleanup": cleanup_stats}
    print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
    return 0


def command_refresh_705hs_playback_urls(args) -> int:
    with get_db_connection() as conn:
        stats = refresh_hs705_playback_urls(conn, limit=max(1, args.limit), refresh_window_minutes=max(1, args.refresh_window_minutes), critical_window_minutes=max(1, args.critical_window_minutes))
        conn.commit()
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def command_monitor_xxxtik(args) -> int:
    base_url = args.base_url or XXXTIK_DEFAULT_BASE_URL
    retention_hours = args.retention_hours if args.retention_hours is not None else XXXTIK_RETENTION_HOURS
    if args.retention_days is not None:
        retention_hours = args.retention_days * 24
    max_records = args.max_records if args.max_records is not None else DEFAULT_MAX_RECORDS
    if args.dry_run and not DATABASE_URL:
        stats = monitor_xxxtik_site(None, base_url=base_url, max_pages=max(1, args.max_pages), retention_hours=max(1, retention_hours), public_pool=not args.private_pool, dry_run=True)
        print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
        return 0
    with get_db_connection() as conn:
        stats = monitor_xxxtik_site(conn, base_url=base_url, max_pages=max(1, args.max_pages), retention_hours=max(1, retention_hours), public_pool=not args.private_pool, dry_run=args.dry_run)
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
        if not args.skip_cleanup and not args.dry_run:
            cleanup_stats = cleanup_records(conn, max(1, (retention_hours + 23) // 24), max_records)
            conn.commit()
            stats = {**stats, "cleanup": cleanup_stats}
    print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
    return 0


def command_refresh_xxxtik_playback_urls(args) -> int:
    with get_db_connection() as conn:
        stats = refresh_xxxtik_playback_urls(conn, limit=max(1, args.limit), refresh_window_minutes=max(1, args.refresh_window_minutes), critical_window_minutes=max(1, args.critical_window_minutes))
        conn.commit()
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def command_monitor_affair(args) -> int:
    base_url = args.base_url or AFFAIR_DEFAULT_BASE_URL
    retention_hours = args.retention_hours if args.retention_hours is not None else AFFAIR_RETENTION_HOURS
    if args.retention_days is not None:
        retention_hours = args.retention_days * 24
    max_records = args.max_records if args.max_records is not None else DEFAULT_MAX_RECORDS
    if args.dry_run and not DATABASE_URL:
        stats = monitor_affair_site(None, base_url=base_url, max_pages=max(1, args.max_pages), retention_hours=max(1, retention_hours), public_pool=not args.private_pool, dry_run=True)
        print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
        return 0
    with get_db_connection() as conn:
        stats = monitor_affair_site(conn, base_url=base_url, max_pages=max(1, args.max_pages), retention_hours=max(1, retention_hours), public_pool=not args.private_pool, dry_run=args.dry_run)
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
        if not args.skip_cleanup and not args.dry_run:
            cleanup_stats = cleanup_records(conn, max(1, (retention_hours + 23) // 24), max_records)
            conn.commit()
            stats = {**stats, "cleanup": cleanup_stats}
    print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
    return 0


def command_refresh_affair_playback_urls(args) -> int:
    with get_db_connection() as conn:
        stats = refresh_affair_playback_urls(conn, limit=max(1, args.limit), refresh_window_minutes=max(1, args.refresh_window_minutes), critical_window_minutes=max(1, args.critical_window_minutes))
        conn.commit()
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def command_monitor_attach(args) -> int:
    base_url = args.base_url or ATTACH_DEFAULT_BASE_URL
    retention_hours = args.retention_hours if args.retention_hours is not None else ATTACH_RETENTION_HOURS
    if args.retention_days is not None:
        retention_hours = args.retention_days * 24
    max_records = args.max_records if args.max_records is not None else DEFAULT_MAX_RECORDS
    if args.dry_run and not DATABASE_URL:
        stats = monitor_attach_site(None, base_url=base_url, max_pages=max(1, args.max_pages), retention_hours=max(1, retention_hours), public_pool=not args.private_pool, dry_run=True)
        print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
        return 0
    with get_db_connection() as conn:
        stats = monitor_attach_site(conn, base_url=base_url, max_pages=max(1, args.max_pages), retention_hours=max(1, retention_hours), public_pool=not args.private_pool, dry_run=args.dry_run)
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
        if not args.skip_cleanup and not args.dry_run:
            cleanup_stats = cleanup_records(conn, max(1, (retention_hours + 23) // 24), max_records)
            conn.commit()
            stats = {**stats, "cleanup": cleanup_stats}
    print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
    return 0


def command_refresh_attach_playback_urls(args) -> int:
    with get_db_connection() as conn:
        stats = refresh_attach_playback_urls(conn, limit=max(1, args.limit), refresh_window_minutes=max(1, args.refresh_window_minutes), critical_window_minutes=max(1, args.critical_window_minutes))
        conn.commit()
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def command_monitor_dirtyship(args) -> int:
    base_url = args.base_url or DIRTYSHIP_DEFAULT_BASE_URL
    retention_hours = args.retention_hours if args.retention_hours is not None else DIRTYSHIP_RETENTION_HOURS
    if args.retention_days is not None:
        retention_hours = args.retention_days * 24
    max_records = args.max_records if args.max_records is not None else DEFAULT_MAX_RECORDS
    if args.dry_run and not DATABASE_URL:
        stats = monitor_dirtyship_site(None, base_url=base_url, max_pages=max(1, args.max_pages), retention_hours=max(1, retention_hours), public_pool=not args.private_pool, dry_run=True)
        print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
        return 0
    with get_db_connection() as conn:
        stats = monitor_dirtyship_site(conn, base_url=base_url, max_pages=max(1, args.max_pages), retention_hours=max(1, retention_hours), public_pool=not args.private_pool, dry_run=args.dry_run)
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
        if not args.skip_cleanup and not args.dry_run:
            cleanup_stats = cleanup_records(conn, max(1, (retention_hours + 23) // 24), max_records)
            conn.commit()
            stats = {**stats, "cleanup": cleanup_stats}
    print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
    return 0


def command_refresh_dirtyship_playback_urls(args) -> int:
    with get_db_connection() as conn:
        stats = refresh_dirtyship_playback_urls(conn, limit=max(1, args.limit), refresh_window_minutes=max(1, args.refresh_window_minutes), critical_window_minutes=max(1, args.critical_window_minutes))
        conn.commit()
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def command_monitor_influencersgonewild(args) -> int:
    base_url = args.base_url or INFLUENCERSGONEWILD_DEFAULT_BASE_URL
    retention_hours = args.retention_hours if args.retention_hours is not None else INFLUENCERSGONEWILD_RETENTION_HOURS
    if args.retention_days is not None:
        retention_hours = args.retention_days * 24
    max_records = args.max_records if args.max_records is not None else DEFAULT_MAX_RECORDS
    if args.dry_run and not DATABASE_URL:
        stats = monitor_influencersgonewild_site(None, base_url=base_url, max_pages=max(1, args.max_pages), retention_hours=max(1, retention_hours), public_pool=not args.private_pool, dry_run=True)
        print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
        return 0
    with get_db_connection() as conn:
        stats = monitor_influencersgonewild_site(conn, base_url=base_url, max_pages=max(1, args.max_pages), retention_hours=max(1, retention_hours), public_pool=not args.private_pool, dry_run=args.dry_run)
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
        if not args.skip_cleanup and not args.dry_run:
            cleanup_stats = cleanup_records(conn, max(1, (retention_hours + 23) // 24), max_records)
            conn.commit()
            stats = {**stats, "cleanup": cleanup_stats}
    print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
    return 0


def command_refresh_influencersgonewild_playback_urls(args) -> int:
    with get_db_connection() as conn:
        stats = refresh_influencersgonewild_playback_urls(conn, limit=max(1, args.limit), refresh_window_minutes=max(1, args.refresh_window_minutes), critical_window_minutes=max(1, args.critical_window_minutes))
        conn.commit()
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def command_monitor_missav(args) -> int:
    base_url = args.base_url or MISSAV_DEFAULT_BASE_URL
    retention_hours = args.retention_hours if args.retention_hours is not None else MISSAV_RETENTION_HOURS
    if args.retention_days is not None:
        retention_hours = args.retention_days * 24
    max_records = args.max_records if args.max_records is not None else DEFAULT_MAX_RECORDS
    if args.dry_run and not DATABASE_URL:
        stats = monitor_missav_site(None, base_url=base_url, max_pages=max(1, args.max_pages), retention_hours=max(1, retention_hours), public_pool=not args.private_pool, dry_run=True)
        print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
        return 0
    with get_db_connection() as conn:
        stats = monitor_missav_site(conn, base_url=base_url, max_pages=max(1, args.max_pages), retention_hours=max(1, retention_hours), public_pool=not args.private_pool, dry_run=args.dry_run)
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
        if not args.skip_cleanup and not args.dry_run:
            cleanup_stats = cleanup_records(conn, max(1, (retention_hours + 23) // 24), max_records)
            conn.commit()
            stats = {**stats, "cleanup": cleanup_stats}
    print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
    return 0


def command_refresh_missav_playback_urls(args) -> int:
    with get_db_connection() as conn:
        stats = refresh_missav_playback_urls(conn, limit=max(1, args.limit), refresh_window_minutes=max(1, args.refresh_window_minutes), critical_window_minutes=max(1, args.critical_window_minutes))
        conn.commit()
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def command_monitor_badnews(args) -> int:
    base_url = args.base_url or BADNEWS_DEFAULT_BASE_URL
    retention_hours = args.retention_hours if args.retention_hours is not None else BADNEWS_RETENTION_HOURS
    if args.retention_days is not None:
        retention_hours = args.retention_days * 24
    max_records = args.max_records if args.max_records is not None else DEFAULT_MAX_RECORDS
    if args.dry_run and not DATABASE_URL:
        stats = monitor_badnews_site(None, base_url=base_url, max_pages=max(1, args.max_pages), retention_hours=max(1, retention_hours), public_pool=not args.private_pool, dry_run=True)
        print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
        return 0
    with get_db_connection() as conn:
        stats = monitor_badnews_site(conn, base_url=base_url, max_pages=max(1, args.max_pages), retention_hours=max(1, retention_hours), public_pool=not args.private_pool, dry_run=args.dry_run)
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
        if not args.skip_cleanup and not args.dry_run:
            cleanup_stats = cleanup_records(conn, max(1, (retention_hours + 23) // 24), max_records)
            conn.commit()
            stats = {**stats, "cleanup": cleanup_stats}
    print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
    return 0


def command_refresh_badnews_playback_urls(args) -> int:
    with get_db_connection() as conn:
        stats = refresh_badnews_playback_urls(conn, limit=max(1, args.limit), refresh_window_minutes=max(1, args.refresh_window_minutes), critical_window_minutes=max(1, args.critical_window_minutes))
        conn.commit()
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def command_monitor_bdrq(args) -> int:
    base_url = args.base_url or BDRQ_DEFAULT_BASE_URL
    retention_hours = args.retention_hours if args.retention_hours is not None else BDRQ_RETENTION_HOURS
    if args.retention_days is not None:
        retention_hours = args.retention_days * 24
    max_records = args.max_records if args.max_records is not None else DEFAULT_MAX_RECORDS
    if args.dry_run and not DATABASE_URL:
        stats = monitor_bdrq_site(None, base_url=base_url, max_pages=max(1, args.max_pages), retention_hours=max(1, retention_hours), public_pool=not args.private_pool, dry_run=True)
        print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
        return 0
    with get_db_connection() as conn:
        stats = monitor_bdrq_site(conn, base_url=base_url, max_pages=max(1, args.max_pages), retention_hours=max(1, retention_hours), public_pool=not args.private_pool, dry_run=args.dry_run)
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
        if not args.skip_cleanup and not args.dry_run:
            cleanup_stats = cleanup_records(conn, max(1, (retention_hours + 23) // 24), max_records)
            conn.commit()
            stats = {**stats, "cleanup": cleanup_stats}
    print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
    return 0


def command_refresh_bdrq_playback_urls(args) -> int:
    with get_db_connection() as conn:
        stats = refresh_bdrq_playback_urls(conn, limit=max(1, args.limit), refresh_window_minutes=max(1, args.refresh_window_minutes), critical_window_minutes=max(1, args.critical_window_minutes))
        conn.commit()
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def command_monitor_tikporn(args) -> int:
    base_url = args.base_url or TIKPORN_DEFAULT_BASE_URL
    retention_hours = args.retention_hours if args.retention_hours is not None else TIKPORN_RETENTION_HOURS
    if args.retention_days is not None:
        retention_hours = args.retention_days * 24
    max_records = args.max_records if args.max_records is not None else DEFAULT_MAX_RECORDS
    if args.dry_run and not DATABASE_URL:
        stats = monitor_tikporn_site(None, base_url=base_url, max_pages=max(1, args.max_pages), retention_hours=max(1, retention_hours), public_pool=not args.private_pool, dry_run=True)
        print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
        return 0
    with get_db_connection() as conn:
        stats = monitor_tikporn_site(conn, base_url=base_url, max_pages=max(1, args.max_pages), retention_hours=max(1, retention_hours), public_pool=not args.private_pool, dry_run=args.dry_run)
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
        if not args.skip_cleanup and not args.dry_run:
            cleanup_stats = cleanup_records(conn, max(1, (retention_hours + 23) // 24), max_records)
            conn.commit()
            stats = {**stats, "cleanup": cleanup_stats}
    print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
    return 0


def command_refresh_tikporn_playback_urls(args) -> int:
    with get_db_connection() as conn:
        stats = refresh_tikporn_playback_urls(conn, limit=max(1, args.limit), refresh_window_minutes=max(1, args.refresh_window_minutes), critical_window_minutes=max(1, args.critical_window_minutes))
        conn.commit()
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def command_seed_system_targets(args) -> int:
    target_configs = parse_system_targets_file(args.file)
    with get_db_connection() as conn:
        stats = seed_system_targets(conn, target_configs)
        conn.commit()
    print(json.dumps({**stats, "targets": [config["target"] for config in target_configs]}, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Twitter/X 监控与 PostgreSQL 存储工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    register_parser = subparsers.add_parser("register-client", help="生成客户端 API key 与 feed token")
    register_parser.add_argument("--label", help="客户端标签")
    register_parser.set_defaults(func=command_register_client)

    monitor_parser = subparsers.add_parser("monitor", help="抓取订阅目标并保存最新推文")
    monitor_parser.add_argument("--targets", help="覆盖订阅列表，逗号或换行分隔")
    monitor_parser.add_argument("--retention-days", type=int, default=None, help="保留天数")
    monitor_parser.add_argument("--max-records", type=int, default=None, help="最大保留记录数")
    monitor_parser.add_argument("--skip-cleanup", action="store_true", help="本轮监控后不执行清理")
    monitor_parser.add_argument("--include-system", action="store_true", help="同时抓取系统公共视频池目标")
    monitor_parser.add_argument("--system-only", action="store_true", help="只抓取系统公共视频池目标")
    monitor_parser.add_argument("--shard-index", type=int, default=0, help="当前分片编号，从 0 开始")
    monitor_parser.add_argument("--shard-count", type=int, default=1, help="总分片数")
    monitor_parser.set_defaults(func=command_monitor)

    youtube_monitor_parser = subparsers.add_parser("monitor-youtube", help="单独抓取 YouTube RSS 并解析播放 URL")
    youtube_monitor_parser.add_argument("--targets", help="覆盖 YouTube 订阅目标，逗号或换行分隔，格式 youtube:UC...")
    youtube_monitor_parser.add_argument("--retention-days", type=int, default=None, help="Twitter 保留天数参数；YouTube 固定按 expires_at 清理")
    youtube_monitor_parser.add_argument("--max-records", type=int, default=None, help="最大保留记录数")
    youtube_monitor_parser.add_argument("--skip-cleanup", action="store_true", help="本轮监控后不执行清理")
    youtube_monitor_parser.add_argument("--shard-index", type=int, default=0, help="当前分片编号，从 0 开始")
    youtube_monitor_parser.add_argument("--shard-count", type=int, default=1, help="总分片数")
    youtube_monitor_parser.set_defaults(func=command_monitor_youtube)

    heiliao_monitor_parser = subparsers.add_parser("monitor-heiliao", help="单独抓取黑料不打烊视频并入库")
    heiliao_monitor_parser.add_argument("--base-url", default=HEILIAO_DEFAULT_BASE_URL, help="黑料不打烊站点入口")
    heiliao_monitor_parser.add_argument("--max-pages", type=int, default=5, help="单次最多分页数")
    heiliao_monitor_parser.add_argument("--retention-hours", type=int, default=None, help="视频业务保留小时数，默认 84")
    heiliao_monitor_parser.add_argument("--retention-days", type=int, default=None, help="兼容旧参数：视频业务保留天数")
    heiliao_monitor_parser.add_argument("--max-records", type=int, default=None, help="最大保留记录数")
    heiliao_monitor_parser.add_argument("--skip-cleanup", action="store_true", help="本轮监控后不执行清理")
    heiliao_monitor_parser.add_argument("--private-pool", action="store_true", help="不加入公共视频池")
    heiliao_monitor_parser.add_argument("--dry-run", action="store_true", help="只解析和验证，不写入数据库")
    heiliao_monitor_parser.set_defaults(func=command_monitor_heiliao)

    cg91_monitor_parser = subparsers.add_parser("monitor-91cg", help="单独抓取 91吃瓜网视频并入库")
    cg91_monitor_parser.add_argument("--base-url", default=CG91_DEFAULT_BASE_URL, help="91吃瓜网站点入口")
    cg91_monitor_parser.add_argument("--max-pages", type=int, default=4, help="单次最多分页数")
    cg91_monitor_parser.add_argument("--retention-hours", type=int, default=None, help="视频业务保留小时数，默认 84")
    cg91_monitor_parser.add_argument("--retention-days", type=int, default=None, help="兼容旧参数：视频业务保留天数")
    cg91_monitor_parser.add_argument("--max-records", type=int, default=None, help="最大保留记录数")
    cg91_monitor_parser.add_argument("--skip-cleanup", action="store_true", help="本轮监控后不执行清理")
    cg91_monitor_parser.add_argument("--private-pool", action="store_true", help="不加入公共视频池")
    cg91_monitor_parser.add_argument("--dry-run", action="store_true", help="只解析和验证，不写入数据库")
    cg91_monitor_parser.set_defaults(func=command_monitor_cg91)

    baoliao51_monitor_parser = subparsers.add_parser("monitor-51baoliao", help="单独抓取 51爆料网视频并入库")
    baoliao51_monitor_parser.add_argument("--base-url", default=BAOLIAO51_DEFAULT_BASE_URL, help="51爆料网站点入口")
    baoliao51_monitor_parser.add_argument("--max-pages", type=int, default=1, help="单次最多分页数")
    baoliao51_monitor_parser.add_argument("--retention-hours", type=int, default=None, help="视频业务保留小时数，默认 84")
    baoliao51_monitor_parser.add_argument("--retention-days", type=int, default=None, help="兼容旧参数：视频业务保留天数")
    baoliao51_monitor_parser.add_argument("--max-records", type=int, default=None, help="最大保留记录数")
    baoliao51_monitor_parser.add_argument("--skip-cleanup", action="store_true", help="本轮监控后不执行清理")
    baoliao51_monitor_parser.add_argument("--private-pool", action="store_true", help="不加入公共视频池")
    baoliao51_monitor_parser.add_argument("--dry-run", action="store_true", help="只解析和验证，不写入数据库")
    baoliao51_monitor_parser.set_defaults(func=command_monitor_baoliao51)

    douyin_monitor_parser = subparsers.add_parser("monitor-douyin", help="单独抓取抖阴视频并入库")
    douyin_monitor_parser.add_argument("--base-url", default=DOUYIN_DEFAULT_BASE_URL, help="抖阴站点入口；临时域名变化时优先改 DOUYIN_BASE_URL")
    douyin_monitor_parser.add_argument("--max-pages", type=int, default=2, help="单次最多分页数")
    douyin_monitor_parser.add_argument("--retention-hours", type=int, default=None, help="视频业务保留小时数，默认 84")
    douyin_monitor_parser.add_argument("--retention-days", type=int, default=None, help="兼容旧参数：视频业务保留天数")
    douyin_monitor_parser.add_argument("--max-records", type=int, default=None, help="最大保留记录数")
    douyin_monitor_parser.add_argument("--skip-cleanup", action="store_true", help="本轮监控后不执行清理")
    douyin_monitor_parser.add_argument("--private-pool", action="store_true", help="不加入公共视频池")
    douyin_monitor_parser.add_argument("--dry-run", action="store_true", help="只解析和验证，不写入数据库")
    douyin_monitor_parser.set_defaults(func=command_monitor_douyin)

    mh18_monitor_parser = subparsers.add_parser("monitor-18mh", help="单独抓取 18mh/禁漫天堂视频并入库")
    mh18_monitor_parser.add_argument("--base-url", default=MH18_DEFAULT_BASE_URL, help="18mh 站点入口；也可传 https://18mh.net/mv/all")
    mh18_monitor_parser.add_argument("--max-pages", type=int, default=2, help="单次最多分页数")
    mh18_monitor_parser.add_argument("--retention-hours", type=int, default=None, help="视频业务保留小时数，默认 84")
    mh18_monitor_parser.add_argument("--retention-days", type=int, default=None, help="兼容旧参数：视频业务保留天数")
    mh18_monitor_parser.add_argument("--max-records", type=int, default=None, help="最大保留记录数")
    mh18_monitor_parser.add_argument("--skip-cleanup", action="store_true", help="本轮监控后不执行清理")
    mh18_monitor_parser.add_argument("--private-pool", action="store_true", help="不加入公共视频池")
    mh18_monitor_parser.add_argument("--dry-run", action="store_true", help="只解析和验证，不写入数据库")
    mh18_monitor_parser.set_defaults(func=command_monitor_18mh)

    rou_monitor_parser = subparsers.add_parser("monitor-rou", help="单独抓取 RouVideo/肉視頻视频并入库")
    rou_monitor_parser.add_argument("--base-url", default=ROU_DEFAULT_BASE_URL, help="RouVideo 站点入口；也可传 https://rou.video/v")
    rou_monitor_parser.add_argument("--max-pages", type=int, default=2, help="单次最多分页数")
    rou_monitor_parser.add_argument("--retention-hours", type=int, default=None, help="视频业务保留小时数，默认 84")
    rou_monitor_parser.add_argument("--retention-days", type=int, default=None, help="兼容旧参数：视频业务保留天数")
    rou_monitor_parser.add_argument("--max-records", type=int, default=None, help="最大保留记录数")
    rou_monitor_parser.add_argument("--skip-cleanup", action="store_true", help="本轮监控后不执行清理")
    rou_monitor_parser.add_argument("--private-pool", action="store_true", help="不加入公共视频池")
    rou_monitor_parser.add_argument("--dry-run", action="store_true", help="只解析和验证，不写入数据库")
    rou_monitor_parser.set_defaults(func=command_monitor_rou)

    dadaafa_monitor_parser = subparsers.add_parser("monitor-dadaafa", help="单独抓取 DadaAFA 视频并入库")
    dadaafa_monitor_parser.add_argument("--base-url", default=DADAAFA_DEFAULT_BASE_URL, help="DadaAFA 站点入口；也可传 https://dadaafa.cc/?utm_source=xx&tab=new")
    dadaafa_monitor_parser.add_argument("--max-pages", type=int, default=2, help="单次最多分页数")
    dadaafa_monitor_parser.add_argument("--retention-hours", type=int, default=None, help="视频业务保留小时数，默认 84")
    dadaafa_monitor_parser.add_argument("--retention-days", type=int, default=None, help="兼容旧参数：视频业务保留天数")
    dadaafa_monitor_parser.add_argument("--max-records", type=int, default=None, help="最大保留记录数")
    dadaafa_monitor_parser.add_argument("--skip-cleanup", action="store_true", help="本轮监控后不执行清理")
    dadaafa_monitor_parser.add_argument("--private-pool", action="store_true", help="不加入公共视频池")
    dadaafa_monitor_parser.add_argument("--dry-run", action="store_true", help="只解析和验证，不写入数据库")
    dadaafa_monitor_parser.set_defaults(func=command_monitor_dadaafa)

    j18_monitor_parser = subparsers.add_parser("monitor-18j", help="单独抓取 18J.TV 视频并入库")
    j18_monitor_parser.add_argument("--base-url", default=J18_DEFAULT_BASE_URL, help="18J.TV 站点入口；也可传 https://18j.tv/show/1/")
    j18_monitor_parser.add_argument("--max-pages", type=int, default=2, help="单次最多分页数")
    j18_monitor_parser.add_argument("--retention-hours", type=int, default=None, help="视频业务保留小时数，默认 84")
    j18_monitor_parser.add_argument("--retention-days", type=int, default=None, help="兼容旧参数：视频业务保留天数")
    j18_monitor_parser.add_argument("--max-records", type=int, default=None, help="最大保留记录数")
    j18_monitor_parser.add_argument("--skip-cleanup", action="store_true", help="本轮监控后不执行清理")
    j18_monitor_parser.add_argument("--private-pool", action="store_true", help="不加入公共视频池")
    j18_monitor_parser.add_argument("--dry-run", action="store_true", help="只解析和验证，不写入数据库")
    j18_monitor_parser.set_defaults(func=command_monitor_18j)

    mtif_monitor_parser = subparsers.add_parser("monitor-1mtif", help="单独抓取 1mtif/蜜桃视频并入库")
    mtif_monitor_parser.add_argument("--base-url", default=MTIF_DEFAULT_BASE_URL, help="1mtif 站点入口；默认从 /type/2 最新分页抓取")
    mtif_monitor_parser.add_argument("--max-pages", type=int, default=2, help="单次最多分页数")
    mtif_monitor_parser.add_argument("--retention-hours", type=int, default=None, help="视频业务保留小时数，默认 84")
    mtif_monitor_parser.add_argument("--retention-days", type=int, default=None, help="兼容旧参数：视频业务保留天数")
    mtif_monitor_parser.add_argument("--max-records", type=int, default=None, help="最大保留记录数")
    mtif_monitor_parser.add_argument("--skip-cleanup", action="store_true", help="本轮监控后不执行清理")
    mtif_monitor_parser.add_argument("--private-pool", action="store_true", help="不加入公共视频池")
    mtif_monitor_parser.add_argument("--dry-run", action="store_true", help="只解析和验证，不写入数据库")
    mtif_monitor_parser.set_defaults(func=command_monitor_1mtif)

    porna91_monitor_parser = subparsers.add_parser("monitor-91porna", help="单独抓取 91porna 视频并入库")
    porna91_monitor_parser.add_argument("--base-url", default=PORNA91_DEFAULT_BASE_URL, help="91porna 站点入口；也可传 https://91porna.com/comic/index/video?category=new_update")
    porna91_monitor_parser.add_argument("--max-pages", type=int, default=2, help="单次最多分页数")
    porna91_monitor_parser.add_argument("--retention-hours", type=int, default=None, help="视频业务保留小时数，默认 84")
    porna91_monitor_parser.add_argument("--retention-days", type=int, default=None, help="兼容旧参数：视频业务保留天数")
    porna91_monitor_parser.add_argument("--max-records", type=int, default=None, help="最大保留记录数")
    porna91_monitor_parser.add_argument("--skip-cleanup", action="store_true", help="本轮监控后不执行清理")
    porna91_monitor_parser.add_argument("--private-pool", action="store_true", help="不加入公共视频池")
    porna91_monitor_parser.add_argument("--dry-run", action="store_true", help="只解析和验证，不写入数据库")
    porna91_monitor_parser.set_defaults(func=command_monitor_91porna)

    porn91_monitor_parser = subparsers.add_parser("monitor-91porn", help="单独抓取 91porn 视频并入库")
    porn91_monitor_parser.add_argument("--base-url", default=PORN91_DEFAULT_BASE_URL, help="91porn 站点入口；也可传 https://91porn.com/v.php?next=watch&page=1")
    porn91_monitor_parser.add_argument("--max-pages", type=int, default=2, help="单次最多分页数")
    porn91_monitor_parser.add_argument("--retention-hours", type=int, default=None, help="视频业务保留小时数，默认 84")
    porn91_monitor_parser.add_argument("--retention-days", type=int, default=None, help="兼容旧参数：视频业务保留天数")
    porn91_monitor_parser.add_argument("--max-records", type=int, default=None, help="最大保留记录数")
    porn91_monitor_parser.add_argument("--skip-cleanup", action="store_true", help="本轮监控后不执行清理")
    porn91_monitor_parser.add_argument("--private-pool", action="store_true", help="不加入公共视频池")
    porn91_monitor_parser.add_argument("--dry-run", action="store_true", help="只解析和验证，不写入数据库")
    porn91_monitor_parser.set_defaults(func=command_monitor_91porn)

    rb91_monitor_parser = subparsers.add_parser("monitor-91rb", help="单独抓取 91热爆视频并入库")
    rb91_monitor_parser.add_argument("--base-url", default=RB91_DEFAULT_BASE_URL, help="91热爆站点入口；也可传 https://www.91rb.com/latest-updates/")
    rb91_monitor_parser.add_argument("--max-pages", type=int, default=2, help="单次最多分页数")
    rb91_monitor_parser.add_argument("--retention-hours", type=int, default=None, help=f"视频业务保留小时数，默认 {RB91_RETENTION_HOURS}")
    rb91_monitor_parser.add_argument("--retention-days", type=int, default=None, help="兼容旧参数：视频业务保留天数")
    rb91_monitor_parser.add_argument("--max-records", type=int, default=None, help="最大保留记录数")
    rb91_monitor_parser.add_argument("--skip-cleanup", action="store_true", help="本轮监控后不执行清理")
    rb91_monitor_parser.add_argument("--private-pool", action="store_true", help="不加入公共视频池")
    rb91_monitor_parser.add_argument("--dry-run", action="store_true", help="只解析和验证，不写入数据库")
    rb91_monitor_parser.set_defaults(func=command_monitor_91rb)

    avgood_monitor_parser = subparsers.add_parser("monitor-avgood", help="单独抓取 AvGood 视频并入库")
    avgood_monitor_parser.add_argument("--base-url", default=AVGOOD_DEFAULT_BASE_URL, help="AvGood 站点入口；也可传 https://avgood.com/c/664/")
    avgood_monitor_parser.add_argument("--max-pages", type=int, default=1, help="单次最多分页数")
    avgood_monitor_parser.add_argument("--retention-hours", type=int, default=None, help="视频业务保留小时数，默认 84")
    avgood_monitor_parser.add_argument("--retention-days", type=int, default=None, help="兼容旧参数：视频业务保留天数")
    avgood_monitor_parser.add_argument("--max-records", type=int, default=None, help="最大保留记录数")
    avgood_monitor_parser.add_argument("--skip-cleanup", action="store_true", help="本轮监控后不执行清理")
    avgood_monitor_parser.add_argument("--private-pool", action="store_true", help="不加入公共视频池")
    avgood_monitor_parser.add_argument("--dry-run", action="store_true", help="只解析和验证，不写入数据库")
    avgood_monitor_parser.set_defaults(func=command_monitor_avgood)

    hs705_monitor_parser = subparsers.add_parser("monitor-705hs", help="单独抓取 992KP/705hs 视频并入库")
    hs705_monitor_parser.add_argument("--base-url", default=HS705_DEFAULT_BASE_URL, help="705hs 站点入口；也可传 https://705hs.com/Html/60/index-1.html")
    hs705_monitor_parser.add_argument("--max-pages", type=int, default=1, help="单次最多分页数")
    hs705_monitor_parser.add_argument("--retention-hours", type=int, default=None, help="视频业务保留小时数，默认 84")
    hs705_monitor_parser.add_argument("--retention-days", type=int, default=None, help="兼容旧参数：视频业务保留天数")
    hs705_monitor_parser.add_argument("--max-records", type=int, default=None, help="最大保留记录数")
    hs705_monitor_parser.add_argument("--skip-cleanup", action="store_true", help="本轮监控后不执行清理")
    hs705_monitor_parser.add_argument("--private-pool", action="store_true", help="不加入公共视频池")
    hs705_monitor_parser.add_argument("--dry-run", action="store_true", help="只解析和验证，不写入数据库")
    hs705_monitor_parser.set_defaults(func=command_monitor_705hs)

    xxxtik_monitor_parser = subparsers.add_parser("monitor-xxxtik", help="单独抓取 xxxtik 视频并入库")
    xxxtik_monitor_parser.add_argument("--base-url", default=XXXTIK_DEFAULT_BASE_URL, help="xxxtik 站点入口；也可传 https://xxxtik.com")
    xxxtik_monitor_parser.add_argument("--max-pages", type=int, default=1, help="单次最多分页数")
    xxxtik_monitor_parser.add_argument("--retention-hours", type=int, default=None, help=f"视频业务保留小时数，默认 {XXXTIK_RETENTION_HOURS}")
    xxxtik_monitor_parser.add_argument("--retention-days", type=int, default=None, help="兼容旧参数：视频业务保留天数")
    xxxtik_monitor_parser.add_argument("--max-records", type=int, default=None, help="最大保留记录数")
    xxxtik_monitor_parser.add_argument("--skip-cleanup", action="store_true", help="本轮监控后不执行清理")
    xxxtik_monitor_parser.add_argument("--private-pool", action="store_true", help="不加入公共视频池")
    xxxtik_monitor_parser.add_argument("--dry-run", action="store_true", help="只解析和验证，不写入数据库")
    xxxtik_monitor_parser.set_defaults(func=command_monitor_xxxtik)

    affair_monitor_parser = subparsers.add_parser("monitor-affair", help="单独抓取 911爆料/Affair 视频并入库")
    affair_monitor_parser.add_argument("--base-url", default=AFFAIR_DEFAULT_BASE_URL, help="911爆料分类入口；也可传 https://affair.zhkrsawaw.cc/category/jrgb/")
    affair_monitor_parser.add_argument("--max-pages", type=int, default=2, help="单次最多分页数")
    affair_monitor_parser.add_argument("--retention-hours", type=int, default=None, help=f"视频业务保留小时数，默认 {AFFAIR_RETENTION_HOURS}")
    affair_monitor_parser.add_argument("--retention-days", type=int, default=None, help="兼容旧参数：视频业务保留天数")
    affair_monitor_parser.add_argument("--max-records", type=int, default=None, help="最大保留记录数")
    affair_monitor_parser.add_argument("--skip-cleanup", action="store_true", help="本轮监控后不执行清理")
    affair_monitor_parser.add_argument("--private-pool", action="store_true", help="不加入公共视频池")
    affair_monitor_parser.add_argument("--dry-run", action="store_true", help="只解析和验证，不写入数据库")
    affair_monitor_parser.set_defaults(func=command_monitor_affair)

    attach_monitor_parser = subparsers.add_parser("monitor-attach", help="单独抓取黑料吃瓜网 Attach 分类视频并入库")
    attach_monitor_parser.add_argument("--base-url", default=ATTACH_DEFAULT_BASE_URL, help="Attach 分类入口；也可传 https://attach.bslqmdvk.cc/category/zxcg/")
    attach_monitor_parser.add_argument("--max-pages", type=int, default=1, help="单次最多分页数")
    attach_monitor_parser.add_argument("--retention-hours", type=int, default=None, help=f"视频业务保留小时数，默认 {ATTACH_RETENTION_HOURS}")
    attach_monitor_parser.add_argument("--retention-days", type=int, default=None, help="兼容旧参数：视频业务保留天数")
    attach_monitor_parser.add_argument("--max-records", type=int, default=None, help="最大保留记录数")
    attach_monitor_parser.add_argument("--skip-cleanup", action="store_true", help="本轮监控后不执行清理")
    attach_monitor_parser.add_argument("--private-pool", action="store_true", help="不加入公共视频池")
    attach_monitor_parser.add_argument("--dry-run", action="store_true", help="只解析和验证，不写入数据库")
    attach_monitor_parser.set_defaults(func=command_monitor_attach)

    dirtyship_monitor_parser = subparsers.add_parser("monitor-dirtyship", help="单独抓取 DirtyShip 视频并入库")
    dirtyship_monitor_parser.add_argument("--base-url", default=DIRTYSHIP_DEFAULT_BASE_URL, help="DirtyShip 站点入口；也可传 https://dirtyship.com")
    dirtyship_monitor_parser.add_argument("--max-pages", type=int, default=2, help="单次最多分页数")
    dirtyship_monitor_parser.add_argument("--retention-hours", type=int, default=None, help=f"视频业务保留小时数，默认 {DIRTYSHIP_RETENTION_HOURS}")
    dirtyship_monitor_parser.add_argument("--retention-days", type=int, default=None, help="兼容旧参数：视频业务保留天数")
    dirtyship_monitor_parser.add_argument("--max-records", type=int, default=None, help="最大保留记录数")
    dirtyship_monitor_parser.add_argument("--skip-cleanup", action="store_true", help="本轮监控后不执行清理")
    dirtyship_monitor_parser.add_argument("--private-pool", action="store_true", help="不加入公共视频池")
    dirtyship_monitor_parser.add_argument("--dry-run", action="store_true", help="只解析和验证，不写入数据库")
    dirtyship_monitor_parser.set_defaults(func=command_monitor_dirtyship)

    influencersgonewild_monitor_parser = subparsers.add_parser("monitor-influencersgonewild", help="单独抓取 InfluencersGoneWild 视频并入库")
    influencersgonewild_monitor_parser.add_argument("--base-url", default=INFLUENCERSGONEWILD_DEFAULT_BASE_URL, help="InfluencersGoneWild 站点入口；也可传 https://influencersgonewild.com")
    influencersgonewild_monitor_parser.add_argument("--max-pages", type=int, default=3, help="单次最多分页数")
    influencersgonewild_monitor_parser.add_argument("--retention-hours", type=int, default=None, help=f"视频业务保留小时数，默认 {INFLUENCERSGONEWILD_RETENTION_HOURS}")
    influencersgonewild_monitor_parser.add_argument("--retention-days", type=int, default=None, help="兼容旧参数：视频业务保留天数")
    influencersgonewild_monitor_parser.add_argument("--max-records", type=int, default=None, help="最大保留记录数")
    influencersgonewild_monitor_parser.add_argument("--skip-cleanup", action="store_true", help="本轮监控后不执行清理")
    influencersgonewild_monitor_parser.add_argument("--private-pool", action="store_true", help="不加入公共视频池")
    influencersgonewild_monitor_parser.add_argument("--dry-run", action="store_true", help="只解析和验证，不写入数据库")
    influencersgonewild_monitor_parser.set_defaults(func=command_monitor_influencersgonewild)

    missav_monitor_parser = subparsers.add_parser("monitor-missav", help="单独抓取 MISSAV 视频并入库")
    missav_monitor_parser.add_argument("--base-url", default=MISSAV_DEFAULT_BASE_URL, help="MISSAV 分类入口；也可传 https://missav.app/vodtype/20/")
    missav_monitor_parser.add_argument("--max-pages", type=int, default=1, help="单次最多分页数")
    missav_monitor_parser.add_argument("--retention-hours", type=int, default=None, help=f"视频业务保留小时数，默认 {MISSAV_RETENTION_HOURS}")
    missav_monitor_parser.add_argument("--retention-days", type=int, default=None, help="兼容旧参数：视频业务保留天数")
    missav_monitor_parser.add_argument("--max-records", type=int, default=None, help="最大保留记录数")
    missav_monitor_parser.add_argument("--skip-cleanup", action="store_true", help="本轮监控后不执行清理")
    missav_monitor_parser.add_argument("--private-pool", action="store_true", help="不加入公共视频池")
    missav_monitor_parser.add_argument("--dry-run", action="store_true", help="只解析和验证，不写入数据库")
    missav_monitor_parser.set_defaults(func=command_monitor_missav)

    badnews_monitor_parser = subparsers.add_parser("monitor-badnews", help="单独抓取 Bad.news 视频并入库")
    badnews_monitor_parser.add_argument("--base-url", default=BADNEWS_DEFAULT_BASE_URL, help="Bad.news 站点入口；也可传 https://bad.news/sort-new/page-1")
    badnews_monitor_parser.add_argument("--max-pages", type=int, default=2, help="单次最多分页数")
    badnews_monitor_parser.add_argument("--retention-hours", type=int, default=None, help="视频业务保留小时数，默认 84")
    badnews_monitor_parser.add_argument("--retention-days", type=int, default=None, help="兼容旧参数：视频业务保留天数")
    badnews_monitor_parser.add_argument("--max-records", type=int, default=None, help="最大保留记录数")
    badnews_monitor_parser.add_argument("--skip-cleanup", action="store_true", help="本轮监控后不执行清理")
    badnews_monitor_parser.add_argument("--private-pool", action="store_true", help="不加入公共视频池")
    badnews_monitor_parser.add_argument("--dry-run", action="store_true", help="只解析和验证，不写入数据库")
    badnews_monitor_parser.set_defaults(func=command_monitor_badnews)

    bdrq_monitor_parser = subparsers.add_parser("monitor-bdrq", help="单独抓取 背德人妻 视频并入库")
    bdrq_monitor_parser.add_argument("--base-url", default=BDRQ_DEFAULT_BASE_URL, help="背德人妻站点入口；默认同时抓取两个内置列表")
    bdrq_monitor_parser.add_argument("--max-pages", type=int, default=1, help="单次每个列表最多分页数；默认 1 页，两条列表合并抓取")
    bdrq_monitor_parser.add_argument("--retention-hours", type=int, default=None, help="视频业务保留小时数，默认 84")
    bdrq_monitor_parser.add_argument("--retention-days", type=int, default=None, help="兼容旧参数：视频业务保留天数")
    bdrq_monitor_parser.add_argument("--max-records", type=int, default=None, help="最大保留记录数")
    bdrq_monitor_parser.add_argument("--skip-cleanup", action="store_true", help="本轮监控后不执行清理")
    bdrq_monitor_parser.add_argument("--private-pool", action="store_true", help="不加入公共视频池")
    bdrq_monitor_parser.add_argument("--dry-run", action="store_true", help="只解析和验证，不写入数据库")
    bdrq_monitor_parser.set_defaults(func=command_monitor_bdrq)

    tikporn_monitor_parser = subparsers.add_parser("monitor-tikporn", help="单独抓取 Tik.Porn 视频并入库")
    tikporn_monitor_parser.add_argument("--base-url", default=TIKPORN_DEFAULT_BASE_URL, help="Tik.Porn 站点入口")
    tikporn_monitor_parser.add_argument("--max-pages", type=int, default=1, help="兼容参数；Tik.Porn 最新接口当前固定返回最新一页")
    tikporn_monitor_parser.add_argument("--retention-hours", type=int, default=None, help="视频业务保留小时数，默认 84")
    tikporn_monitor_parser.add_argument("--retention-days", type=int, default=None, help="兼容旧参数：视频业务保留天数")
    tikporn_monitor_parser.add_argument("--max-records", type=int, default=None, help="最大保留记录数")
    tikporn_monitor_parser.add_argument("--skip-cleanup", action="store_true", help="本轮监控后不执行清理")
    tikporn_monitor_parser.add_argument("--private-pool", action="store_true", help="不加入公共视频池")
    tikporn_monitor_parser.add_argument("--dry-run", action="store_true", help="只解析和验证，不写入数据库")
    tikporn_monitor_parser.set_defaults(func=command_monitor_tikporn)

    subscribe_parser = subparsers.add_parser("subscribe", help="用 API key 管理订阅列表")
    subscribe_parser.add_argument("action", choices=["add", "remove", "set", "list"], help="订阅动作")
    subscribe_parser.add_argument("--api-key", required=True, help="客户端 API key")
    subscribe_parser.add_argument("--targets", help="目标列表，逗号或换行分隔")
    subscribe_parser.set_defaults(func=command_subscribe)

    query_parser = subparsers.add_parser("query", help="查询历史保存结果")
    query_parser.add_argument("--api-key", help="仅查询某个客户端可见的数据")
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

    refresh_youtube_parser = subparsers.add_parser("refresh-youtube-playback-urls", help="刷新 YouTube 播放 URL 并处理解析队列")
    refresh_youtube_parser.add_argument("--limit", type=int, default=30, help="单次最多处理条数")
    refresh_youtube_parser.add_argument("--refresh-window-minutes", type=int, default=90, help="普通刷新窗口")
    refresh_youtube_parser.add_argument("--critical-window-minutes", type=int, default=15, help="临界过期窗口")
    refresh_youtube_parser.set_defaults(func=command_refresh_youtube_playback_urls)

    refresh_heiliao_parser = subparsers.add_parser("refresh-heiliao-playback-urls", help="刷新黑料不打烊播放 URL")
    refresh_heiliao_parser.add_argument("--limit", type=int, default=30, help="单次最多处理条数")
    refresh_heiliao_parser.add_argument("--refresh-window-minutes", type=int, default=HEILIAO_REFRESH_WINDOW_MINUTES, help="普通刷新窗口")
    refresh_heiliao_parser.add_argument("--critical-window-minutes", type=int, default=HEILIAO_CRITICAL_WINDOW_MINUTES, help="临界过期窗口")
    refresh_heiliao_parser.set_defaults(func=command_refresh_heiliao_playback_urls)

    refresh_cg91_parser = subparsers.add_parser("refresh-91cg-playback-urls", help="刷新 91吃瓜网播放 URL")
    refresh_cg91_parser.add_argument("--limit", type=int, default=30, help="单次最多处理条数")
    refresh_cg91_parser.add_argument("--refresh-window-minutes", type=int, default=HEILIAO_REFRESH_WINDOW_MINUTES, help="普通刷新窗口")
    refresh_cg91_parser.add_argument("--critical-window-minutes", type=int, default=HEILIAO_CRITICAL_WINDOW_MINUTES, help="临界过期窗口")
    refresh_cg91_parser.set_defaults(func=command_refresh_cg91_playback_urls)

    refresh_baoliao51_parser = subparsers.add_parser("refresh-51baoliao-playback-urls", help="刷新 51爆料网播放 URL")
    refresh_baoliao51_parser.add_argument("--limit", type=int, default=30, help="单次最多处理条数")
    refresh_baoliao51_parser.add_argument("--refresh-window-minutes", type=int, default=BAOLIAO51_REFRESH_WINDOW_MINUTES, help="普通刷新窗口")
    refresh_baoliao51_parser.add_argument("--critical-window-minutes", type=int, default=BAOLIAO51_CRITICAL_WINDOW_MINUTES, help="临界过期窗口")
    refresh_baoliao51_parser.set_defaults(func=command_refresh_baoliao51_playback_urls)

    refresh_douyin_parser = subparsers.add_parser("refresh-douyin-playback-urls", help="刷新抖阴播放 URL")
    refresh_douyin_parser.add_argument("--limit", type=int, default=30, help="单次最多处理条数")
    refresh_douyin_parser.add_argument("--refresh-window-minutes", type=int, default=HEILIAO_REFRESH_WINDOW_MINUTES, help="普通刷新窗口")
    refresh_douyin_parser.add_argument("--critical-window-minutes", type=int, default=HEILIAO_CRITICAL_WINDOW_MINUTES, help="临界过期窗口")
    refresh_douyin_parser.set_defaults(func=command_refresh_douyin_playback_urls)

    refresh_mh18_parser = subparsers.add_parser("refresh-18mh-playback-urls", help="刷新 18mh/禁漫天堂播放 URL")
    refresh_mh18_parser.add_argument("--limit", type=int, default=30, help="单次最多处理条数")
    refresh_mh18_parser.add_argument("--refresh-window-minutes", type=int, default=MH18_REFRESH_WINDOW_MINUTES, help="普通刷新窗口")
    refresh_mh18_parser.add_argument("--critical-window-minutes", type=int, default=MH18_CRITICAL_WINDOW_MINUTES, help="临界过期窗口")
    refresh_mh18_parser.set_defaults(func=command_refresh_18mh_playback_urls)

    refresh_rou_parser = subparsers.add_parser("refresh-rou-playback-urls", help="刷新 RouVideo/肉視頻播放 URL")
    refresh_rou_parser.add_argument("--limit", type=int, default=30, help="单次最多处理条数")
    refresh_rou_parser.add_argument("--refresh-window-minutes", type=int, default=ROU_REFRESH_WINDOW_MINUTES, help="普通刷新窗口")
    refresh_rou_parser.add_argument("--critical-window-minutes", type=int, default=ROU_CRITICAL_WINDOW_MINUTES, help="临界过期窗口")
    refresh_rou_parser.set_defaults(func=command_refresh_rou_playback_urls)

    refresh_dadaafa_parser = subparsers.add_parser("refresh-dadaafa-playback-urls", help="刷新 DadaAFA 播放 URL（仅处理带过期时间的历史记录）")
    refresh_dadaafa_parser.add_argument("--limit", type=int, default=30, help="单次最多处理条数")
    refresh_dadaafa_parser.add_argument("--refresh-window-minutes", type=int, default=DADAAFA_REFRESH_WINDOW_MINUTES, help="普通刷新窗口")
    refresh_dadaafa_parser.add_argument("--critical-window-minutes", type=int, default=DADAAFA_CRITICAL_WINDOW_MINUTES, help="临界过期窗口")
    refresh_dadaafa_parser.set_defaults(func=command_refresh_dadaafa_playback_urls)

    refresh_j18_parser = subparsers.add_parser("refresh-18j-playback-urls", help="刷新 18J.TV 播放 URL（仅处理带过期时间的历史记录）")
    refresh_j18_parser.add_argument("--limit", type=int, default=30, help="单次最多处理条数")
    refresh_j18_parser.add_argument("--refresh-window-minutes", type=int, default=J18_REFRESH_WINDOW_MINUTES, help="普通刷新窗口")
    refresh_j18_parser.add_argument("--critical-window-minutes", type=int, default=J18_CRITICAL_WINDOW_MINUTES, help="临界过期窗口")
    refresh_j18_parser.set_defaults(func=command_refresh_18j_playback_urls)

    refresh_mtif_parser = subparsers.add_parser("refresh-1mtif-playback-urls", help="刷新 1mtif 播放 URL（仅处理带过期时间的历史记录）")
    refresh_mtif_parser.add_argument("--limit", type=int, default=30, help="单次最多处理条数")
    refresh_mtif_parser.add_argument("--refresh-window-minutes", type=int, default=MTIF_REFRESH_WINDOW_MINUTES, help="普通刷新窗口")
    refresh_mtif_parser.add_argument("--critical-window-minutes", type=int, default=MTIF_CRITICAL_WINDOW_MINUTES, help="临界过期窗口")
    refresh_mtif_parser.set_defaults(func=command_refresh_1mtif_playback_urls)

    refresh_porna91_parser = subparsers.add_parser("refresh-91porna-playback-urls", help="刷新 91porna 播放 URL（仅处理带过期时间的历史记录）")
    refresh_porna91_parser.add_argument("--limit", type=int, default=30, help="单次最多处理条数")
    refresh_porna91_parser.add_argument("--refresh-window-minutes", type=int, default=PORNA91_REFRESH_WINDOW_MINUTES, help="普通刷新窗口")
    refresh_porna91_parser.add_argument("--critical-window-minutes", type=int, default=PORNA91_CRITICAL_WINDOW_MINUTES, help="临界过期窗口")
    refresh_porna91_parser.set_defaults(func=command_refresh_91porna_playback_urls)

    refresh_porn91_parser = subparsers.add_parser("refresh-91porn-playback-urls", help="刷新 91porn 播放 URL（仅处理带过期时间的历史记录）")
    refresh_porn91_parser.add_argument("--limit", type=int, default=30, help="单次最多处理条数")
    refresh_porn91_parser.add_argument("--refresh-window-minutes", type=int, default=PORN91_REFRESH_WINDOW_MINUTES, help="普通刷新窗口")
    refresh_porn91_parser.add_argument("--critical-window-minutes", type=int, default=PORN91_CRITICAL_WINDOW_MINUTES, help="临界过期窗口")
    refresh_porn91_parser.set_defaults(func=command_refresh_91porn_playback_urls)

    refresh_rb91_parser = subparsers.add_parser("refresh-91rb-playback-urls", help="刷新 91热爆播放 URL（仅处理带过期时间的历史记录）")
    refresh_rb91_parser.add_argument("--limit", type=int, default=30, help="单次最多处理条数")
    refresh_rb91_parser.add_argument("--refresh-window-minutes", type=int, default=RB91_REFRESH_WINDOW_MINUTES, help="普通刷新窗口")
    refresh_rb91_parser.add_argument("--critical-window-minutes", type=int, default=RB91_CRITICAL_WINDOW_MINUTES, help="临界过期窗口")
    refresh_rb91_parser.set_defaults(func=command_refresh_91rb_playback_urls)

    refresh_avgood_parser = subparsers.add_parser("refresh-avgood-playback-urls", help="刷新 AvGood 播放 URL（仅处理带过期时间的历史记录）")
    refresh_avgood_parser.add_argument("--limit", type=int, default=30, help="单次最多处理条数")
    refresh_avgood_parser.add_argument("--refresh-window-minutes", type=int, default=AVGOOD_REFRESH_WINDOW_MINUTES, help="普通刷新窗口")
    refresh_avgood_parser.add_argument("--critical-window-minutes", type=int, default=AVGOOD_CRITICAL_WINDOW_MINUTES, help="临界过期窗口")
    refresh_avgood_parser.set_defaults(func=command_refresh_avgood_playback_urls)

    refresh_hs705_parser = subparsers.add_parser("refresh-705hs-playback-urls", help="刷新 992KP/705hs 播放 URL（仅处理带过期时间的历史记录）")
    refresh_hs705_parser.add_argument("--limit", type=int, default=30, help="单次最多处理条数")
    refresh_hs705_parser.add_argument("--refresh-window-minutes", type=int, default=HS705_REFRESH_WINDOW_MINUTES, help="普通刷新窗口")
    refresh_hs705_parser.add_argument("--critical-window-minutes", type=int, default=HS705_CRITICAL_WINDOW_MINUTES, help="临界过期窗口")
    refresh_hs705_parser.set_defaults(func=command_refresh_705hs_playback_urls)

    refresh_xxxtik_parser = subparsers.add_parser("refresh-xxxtik-playback-urls", help="刷新 xxxtik 播放 URL（仅处理带过期时间的历史记录）")
    refresh_xxxtik_parser.add_argument("--limit", type=int, default=30, help="单次最多处理条数")
    refresh_xxxtik_parser.add_argument("--refresh-window-minutes", type=int, default=XXXTIK_REFRESH_WINDOW_MINUTES, help="普通刷新窗口")
    refresh_xxxtik_parser.add_argument("--critical-window-minutes", type=int, default=XXXTIK_CRITICAL_WINDOW_MINUTES, help="临界过期窗口")
    refresh_xxxtik_parser.set_defaults(func=command_refresh_xxxtik_playback_urls)

    refresh_affair_parser = subparsers.add_parser("refresh-affair-playback-urls", help="刷新 911爆料/Affair 播放 URL（仅处理带过期时间的历史记录）")
    refresh_affair_parser.add_argument("--limit", type=int, default=30, help="单次最多处理条数")
    refresh_affair_parser.add_argument("--refresh-window-minutes", type=int, default=AFFAIR_REFRESH_WINDOW_MINUTES, help="普通刷新窗口")
    refresh_affair_parser.add_argument("--critical-window-minutes", type=int, default=AFFAIR_CRITICAL_WINDOW_MINUTES, help="临界过期窗口")
    refresh_affair_parser.set_defaults(func=command_refresh_affair_playback_urls)

    refresh_attach_parser = subparsers.add_parser("refresh-attach-playback-urls", help="刷新 Attach 播放 URL（仅处理带过期时间的历史记录）")
    refresh_attach_parser.add_argument("--limit", type=int, default=30, help="单次最多处理条数")
    refresh_attach_parser.add_argument("--refresh-window-minutes", type=int, default=ATTACH_REFRESH_WINDOW_MINUTES, help="普通刷新窗口")
    refresh_attach_parser.add_argument("--critical-window-minutes", type=int, default=ATTACH_CRITICAL_WINDOW_MINUTES, help="临界过期窗口")
    refresh_attach_parser.set_defaults(func=command_refresh_attach_playback_urls)

    refresh_dirtyship_parser = subparsers.add_parser("refresh-dirtyship-playback-urls", help="刷新 DirtyShip 播放 URL（仅处理带过期时间的历史记录）")
    refresh_dirtyship_parser.add_argument("--limit", type=int, default=30, help="单次最多处理条数")
    refresh_dirtyship_parser.add_argument("--refresh-window-minutes", type=int, default=DIRTYSHIP_REFRESH_WINDOW_MINUTES, help="普通刷新窗口")
    refresh_dirtyship_parser.add_argument("--critical-window-minutes", type=int, default=DIRTYSHIP_CRITICAL_WINDOW_MINUTES, help="临界过期窗口")
    refresh_dirtyship_parser.set_defaults(func=command_refresh_dirtyship_playback_urls)

    refresh_influencersgonewild_parser = subparsers.add_parser("refresh-influencersgonewild-playback-urls", help="刷新 InfluencersGoneWild 播放 URL（仅处理带过期时间的历史记录）")
    refresh_influencersgonewild_parser.add_argument("--limit", type=int, default=30, help="单次最多处理条数")
    refresh_influencersgonewild_parser.add_argument("--refresh-window-minutes", type=int, default=INFLUENCERSGONEWILD_REFRESH_WINDOW_MINUTES, help="普通刷新窗口")
    refresh_influencersgonewild_parser.add_argument("--critical-window-minutes", type=int, default=INFLUENCERSGONEWILD_CRITICAL_WINDOW_MINUTES, help="临界过期窗口")
    refresh_influencersgonewild_parser.set_defaults(func=command_refresh_influencersgonewild_playback_urls)

    refresh_missav_parser = subparsers.add_parser("refresh-missav-playback-urls", help="刷新 MISSAV 播放 URL（仅处理带过期时间的历史记录）")
    refresh_missav_parser.add_argument("--limit", type=int, default=30, help="单次最多处理条数")
    refresh_missav_parser.add_argument("--refresh-window-minutes", type=int, default=MISSAV_REFRESH_WINDOW_MINUTES, help="普通刷新窗口")
    refresh_missav_parser.add_argument("--critical-window-minutes", type=int, default=MISSAV_CRITICAL_WINDOW_MINUTES, help="临界过期窗口")
    refresh_missav_parser.set_defaults(func=command_refresh_missav_playback_urls)

    refresh_badnews_parser = subparsers.add_parser("refresh-badnews-playback-urls", help="刷新 Bad.news 播放 URL（仅处理带过期时间的历史记录）")
    refresh_badnews_parser.add_argument("--limit", type=int, default=30, help="单次最多处理条数")
    refresh_badnews_parser.add_argument("--refresh-window-minutes", type=int, default=BADNEWS_REFRESH_WINDOW_MINUTES, help="普通刷新窗口")
    refresh_badnews_parser.add_argument("--critical-window-minutes", type=int, default=BADNEWS_CRITICAL_WINDOW_MINUTES, help="临界过期窗口")
    refresh_badnews_parser.set_defaults(func=command_refresh_badnews_playback_urls)

    refresh_bdrq_parser = subparsers.add_parser("refresh-bdrq-playback-urls", help="刷新 背德人妻 播放 URL（仅处理带过期时间的历史记录）")
    refresh_bdrq_parser.add_argument("--limit", type=int, default=30, help="单次最多处理条数")
    refresh_bdrq_parser.add_argument("--refresh-window-minutes", type=int, default=BDRQ_REFRESH_WINDOW_MINUTES, help="普通刷新窗口")
    refresh_bdrq_parser.add_argument("--critical-window-minutes", type=int, default=BDRQ_CRITICAL_WINDOW_MINUTES, help="临界过期窗口")
    refresh_bdrq_parser.set_defaults(func=command_refresh_bdrq_playback_urls)

    refresh_tikporn_parser = subparsers.add_parser("refresh-tikporn-playback-urls", help="刷新 Tik.Porn 播放 URL（仅处理带过期时间的历史记录）")
    refresh_tikporn_parser.add_argument("--limit", type=int, default=30, help="单次最多处理条数")
    refresh_tikporn_parser.add_argument("--refresh-window-minutes", type=int, default=TIKPORN_REFRESH_WINDOW_MINUTES, help="普通刷新窗口")
    refresh_tikporn_parser.add_argument("--critical-window-minutes", type=int, default=TIKPORN_CRITICAL_WINDOW_MINUTES, help="临界过期窗口")
    refresh_tikporn_parser.set_defaults(func=command_refresh_tikporn_playback_urls)

    seed_system_parser = subparsers.add_parser("seed-system-targets", help="初始化系统公共视频池目标")
    seed_system_parser.add_argument("--file", help="系统目标 JSON 文件；默认使用内置目标")
    seed_system_parser.set_defaults(func=command_seed_system_targets)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    lock_name = lock_name_for_command(args.func.__name__, args)
    if not lock_name:
        return args.func(args)
    print(f"[db-lock] waiting lock={lock_name}")
    lock_conn, lock_slot = wait_for_db_lock(lock_name)
    print(f"[db-lock] acquired lock={lock_name} slot={lock_slot}")
    try:
        return args.func(args)
    finally:
        try:
            release_db_lock(lock_conn, lock_name)
            release_db_slot(lock_conn, lock_slot)
            print(f"[db-lock] released lock={lock_name} slot={lock_slot}")
        except OperationalError as exc:
            print(f"[db-lock] release failed lock={lock_name}: {exc}")
        finally:
            lock_conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
