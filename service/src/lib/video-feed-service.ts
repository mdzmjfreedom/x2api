import { createHash } from "node:crypto";

import { getSql, type QueryChunk } from "@/lib/db";
import { decodeCursor, encodeCursor, normalizeLimit } from "@/lib/pagination";
import { cacheDeleteJson } from "@/lib/redis-cache";
import { asRows } from "@/lib/sql-result";
import { resolveAuthorPresentation, type AuthorPresentation } from "@/lib/author-presentation";
import type { TargetSource } from "@/lib/targets";
import { isOpenSearchFeedEnabled } from "@/lib/opensearch";

export type VideoFeedSource = "user" | "public" | "mixed";
export const VIDEO_FEED_EVENT_TYPES = ["impression", "play", "finish", "like", "dislike", "skip", "share"] as const;
export type VideoEventType = (typeof VIDEO_FEED_EVENT_TYPES)[number];
export const VIDEO_FEED_SEEN_EVENT_TYPES = VIDEO_FEED_EVENT_TYPES;

export type VideoPlaybackFailureRemovalInput = {
  clientId: string;
  itemId: string;
  reason?: string | null;
  retryCount?: number | null;
  watchMs?: number | null;
  metadata?: Record<string, unknown>;
};

export type VideoFeedQuery = {
  clientId: string;
  limit?: number;
  cursor?: string | null;
  keyword?: string | null;
  tag?: string | null;
  category?: string | null;
  tags?: string[] | null;
  categories?: string[] | null;
  source?: VideoFeedSource;
};

type VideoFeedItemBase = {
  id: string;
  videoUrl: string;
  playbackHeaders: Record<string, string> | null;
  coverUrl: string | null;
  title: string | null;
  caption: string | null;
  author: string | null;
  fullname: string | null;
  xUrl: string | null;
  link: string | null;
  publishedAt: string | null;
  storedAt: string;
  source: TargetSource;
  target: string;
  targetLink: string | null;
  kind: "user" | "keyword" | "channel" | "site";
  category: string | null;
  tags: string[];
  videoKey: string;
  expiresAt: string;
  videoUrlExpiresAt: string;
  stats: {
    impressions: number;
    plays: number;
    finishes: number;
    likes: number;
    dislikes: number;
    skips: number;
    shares: number;
    score: number;
  };
};

export type VideoFeedItem = VideoFeedItemBase & AuthorPresentation;

export type VideoCategory = {
  slug: string;
  name: string;
  weight: number;
  isSensitive: boolean;
  defaultHidden: boolean;
};

type VideoFeedCursor = {
  sortTime?: string;
  storedAt?: string;
  id?: string;
  seenIds?: string[];
  seenGuids?: string[];
  seenVideoKeys?: string[];
  lastAuthor?: string | null;
  lastTarget?: string | null;
};

type VideoFeedRow = VideoFeedItemBase & {
  guid: string;
  videoKey: string;
  sortTime: string;
} & Partial<AuthorPresentation>;

type VideoFeedTimeBucket = "recent" | "week" | "older";
type VideoFeedCandidatePool = "all" | "user" | "public";

type VideoFeedDiversityItem = {
  id: string;
  guid?: string | null;
  videoKey?: string | null;
  author?: string | null;
  fullname?: string | null;
  target: string;
};

type DiversityState = {
  ids: Set<string>;
  guids: Set<string>;
  videoKeys: Set<string>;
  authorCounts: Map<string, number>;
  targetCounts: Map<string, number>;
  lastAuthor: string | null;
  lastTarget: string | null;
};

const MAX_AUTHOR_PER_PAGE = 2;
const MAX_TARGET_PER_PAGE = 3;
const MAX_CURSOR_SEEN_VALUES = 120;
const MAX_VIDEO_FEED_KEYWORD_LENGTH = 80;

function isVideoFeedCursor(value: unknown): value is VideoFeedCursor {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }

  const candidate = value as Partial<VideoFeedCursor>;
  const hasLegacyBoundary =
    typeof candidate.sortTime === "string" &&
    typeof candidate.storedAt === "string" &&
    typeof candidate.id === "string";

  const hasSeenIds =
    Array.isArray(candidate.seenIds) &&
    candidate.seenIds.every((id) => typeof id === "string") &&
    (candidate.seenGuids === undefined ||
      (Array.isArray(candidate.seenGuids) && candidate.seenGuids.every((guid) => typeof guid === "string"))) &&
    (candidate.seenVideoKeys === undefined ||
      (Array.isArray(candidate.seenVideoKeys) && candidate.seenVideoKeys.every((key) => typeof key === "string"))) &&
    (candidate.lastAuthor === undefined || candidate.lastAuthor === null || typeof candidate.lastAuthor === "string") &&
    (candidate.lastTarget === undefined || candidate.lastTarget === null || typeof candidate.lastTarget === "string");

  return hasLegacyBoundary || hasSeenIds;
}

function normalizeDiversityKey(value: string | null | undefined) {
  return value?.trim().toLowerCase() || null;
}

export function normalizeVideoFeedKeyword(value: string | null | undefined) {
  const normalized = value?.trim().replace(/\s+/g, " ");
  if (!normalized) {
    return null;
  }
  if (normalized.length > MAX_VIDEO_FEED_KEYWORD_LENGTH) {
    throw new Error(`Invalid keyword. Expected at most ${MAX_VIDEO_FEED_KEYWORD_LENGTH} characters.`);
  }
  return normalized;
}

function escapePostgresLikePattern(value: string) {
  return value.replace(/[\\%_]/g, (match) => `\\${match}`);
}

export function compactVideoFeedCursorSeenValues(values: string[]) {
  const compacted: string[] = [];
  const seen = new Set<string>();

  for (let index = values.length - 1; index >= 0 && compacted.length < MAX_CURSOR_SEEN_VALUES; index -= 1) {
    const value = values[index]?.trim();
    const key = normalizeDiversityKey(value);
    if (!value || !key || seen.has(key)) {
      continue;
    }

    seen.add(key);
    compacted.push(value);
  }

  return compacted.reverse();
}

export function buildVideoFeedNextCursorPayload(input: {
  seenIds: string[];
  seenGuids: string[];
  seenVideoKeys: string[];
  items: Array<{
    id: string;
    guid?: string | null;
    videoKey?: string | null;
    sortTime: string;
    storedAt: string;
    author?: string | null;
    fullname?: string | null;
    target: string;
  }>;
}) {
  const lastItem = input.items[input.items.length - 1];
  if (!lastItem) {
    return null;
  }

  return {
    sortTime: lastItem.sortTime,
    storedAt: lastItem.storedAt,
    id: lastItem.id,
    seenIds: compactVideoFeedCursorSeenValues([...input.seenIds, ...input.items.map((item) => item.id)]),
    seenGuids: compactVideoFeedCursorSeenValues([
      ...input.seenGuids,
      ...input.items.map((item) => item.guid).filter((guid): guid is string => typeof guid === "string" && guid.length > 0),
    ]),
    seenVideoKeys: compactVideoFeedCursorSeenValues([
      ...input.seenVideoKeys,
      ...input.items.map((item) => item.videoKey).filter((videoKey): videoKey is string => typeof videoKey === "string" && videoKey.length > 0),
    ]),
    lastAuthor: getAuthorKey(lastItem) ?? null,
    lastTarget: normalizeDiversityKey(lastItem.target) ?? null,
  };
}

function getAuthorKey(item: VideoFeedDiversityItem) {
  return normalizeDiversityKey(item.author) ?? normalizeDiversityKey(item.fullname);
}

function createDiversityState<T extends VideoFeedDiversityItem>(
  selected: T[],
  previousLastAuthor?: string | null,
  previousLastTarget?: string | null,
): DiversityState {
  const state: DiversityState = {
    ids: new Set<string>(),
    guids: new Set<string>(),
    videoKeys: new Set<string>(),
    authorCounts: new Map<string, number>(),
    targetCounts: new Map<string, number>(),
    lastAuthor: normalizeDiversityKey(previousLastAuthor),
    lastTarget: normalizeDiversityKey(previousLastTarget),
  };

  for (const item of selected) {
    addDiversityItem(state, item);
  }

  return state;
}

function incrementCount(counts: Map<string, number>, key: string | null) {
  if (!key) {
    return;
  }

  counts.set(key, (counts.get(key) ?? 0) + 1);
}

function addDiversityItem(state: DiversityState, item: VideoFeedDiversityItem) {
  const authorKey = getAuthorKey(item);
  const targetKey = normalizeDiversityKey(item.target);
  const guidKey = normalizeDiversityKey(item.guid);
  const videoKey = normalizeDiversityKey(item.videoKey);

  state.ids.add(item.id);
  if (guidKey) {
    state.guids.add(guidKey);
  }
  if (videoKey) {
    state.videoKeys.add(videoKey);
  }
  incrementCount(state.authorCounts, authorKey);
  incrementCount(state.targetCounts, targetKey);
  state.lastAuthor = authorKey;
  state.lastTarget = targetKey;
}

function canSelectDiversityItem(
  state: DiversityState,
  item: VideoFeedDiversityItem,
  options: { enforceLimits: boolean; enforceConsecutive: boolean },
) {
  const authorKey = getAuthorKey(item);
  const targetKey = normalizeDiversityKey(item.target);
  const guidKey = normalizeDiversityKey(item.guid);
  const videoKey = normalizeDiversityKey(item.videoKey);

  if (state.ids.has(item.id) || (guidKey && state.guids.has(guidKey)) || (videoKey && state.videoKeys.has(videoKey))) {
    return false;
  }

  if (options.enforceConsecutive && ((authorKey && authorKey === state.lastAuthor) || (targetKey && targetKey === state.lastTarget))) {
    return false;
  }

  if (!options.enforceLimits) {
    return true;
  }

  if (authorKey && (state.authorCounts.get(authorKey) ?? 0) >= MAX_AUTHOR_PER_PAGE) {
    return false;
  }

  if (targetKey && (state.targetCounts.get(targetKey) ?? 0) >= MAX_TARGET_PER_PAGE) {
    return false;
  }

  return true;
}

function appendDiverseItems<T extends VideoFeedDiversityItem>(
  selected: T[],
  candidates: T[],
  limit: number,
  state: DiversityState,
  options: { enforceLimits: boolean; enforceConsecutive: boolean },
) {
  let remaining = candidates;
  let madeProgress = true;

  while (selected.length < limit && remaining.length > 0 && madeProgress) {
    madeProgress = false;
    const nextRemaining: T[] = [];

    for (const item of remaining) {
      if (selected.length >= limit) {
        nextRemaining.push(item);
        continue;
      }

      if (canSelectDiversityItem(state, item, options)) {
        selected.push(item);
        addDiversityItem(state, item);
        madeProgress = true;
      } else {
        nextRemaining.push(item);
      }
    }

    remaining = nextRemaining;
  }
}

export function selectDiverseVideoItems<T extends VideoFeedDiversityItem>(input: {
  selected?: T[];
  candidates: T[];
  limit: number;
  previousLastAuthor?: string | null;
  previousLastTarget?: string | null;
  enforceLimits?: boolean;
  enforceConsecutive?: boolean;
}) {
  const selected = [...(input.selected ?? [])];
  const state = createDiversityState(selected, input.previousLastAuthor, input.previousLastTarget);

  appendDiverseItems(selected, input.candidates, input.limit, state, {
    enforceLimits: input.enforceLimits ?? true,
    enforceConsecutive: input.enforceConsecutive ?? true,
  });

  return selected;
}

function compareVideoFeedRowsByTime(
  left: { id: string; sortTime: string | Date; storedAt: string | Date },
  right: { id: string; sortTime: string | Date; storedAt: string | Date },
) {
  const leftSortTime = left.sortTime instanceof Date ? left.sortTime.toISOString() : left.sortTime;
  const rightSortTime = right.sortTime instanceof Date ? right.sortTime.toISOString() : right.sortTime;
  if (leftSortTime !== rightSortTime) {
    return rightSortTime.localeCompare(leftSortTime);
  }

  const leftStoredAt = left.storedAt instanceof Date ? left.storedAt.toISOString() : left.storedAt;
  const rightStoredAt = right.storedAt instanceof Date ? right.storedAt.toISOString() : right.storedAt;
  if (leftStoredAt !== rightStoredAt) {
    return rightStoredAt.localeCompare(leftStoredAt);
  }

  return right.id.localeCompare(left.id);
}

function stableFeedJitter(clientId: string, itemId: string) {
  return Number.parseInt(createHash("sha256").update(`${clientId}:${itemId}`).digest("hex").slice(0, 8), 16);
}

function compareVideoFeedRowsForClient<T extends { id: string; sortTime: string | Date; storedAt: string | Date }>(clientId: string) {
  return (left: T, right: T) => {
    const timeOrder = compareVideoFeedRowsByTime(left, right);
    if (timeOrder !== 0) {
      return timeOrder;
    }

    return stableFeedJitter(clientId, left.id) - stableFeedJitter(clientId, right.id);
  };
}

export function mergeVideoFeedCandidatePools<
  T extends { id: string; guid?: string | null; videoKey?: string | null; sortTime: string | Date; storedAt: string | Date },
>(pools: T[][]) {
  const merged: T[] = [];
  const seenIds = new Set<string>();
  const seenGuids = new Set<string>();
  const seenVideoKeys = new Set<string>();

  for (const item of pools.flat().sort(compareVideoFeedRowsByTime)) {
    const guidKey = normalizeDiversityKey(item.guid);
    const videoKey = normalizeDiversityKey(item.videoKey);

    if (seenIds.has(item.id) || (guidKey && seenGuids.has(guidKey)) || (videoKey && seenVideoKeys.has(videoKey))) {
      continue;
    }

    merged.push(item);
    seenIds.add(item.id);
    if (guidKey) {
      seenGuids.add(guidKey);
    }
    if (videoKey) {
      seenVideoKeys.add(videoKey);
    }
  }

  return merged;
}

function videoKeyExpression(alias: "i" | "watched_item"): QueryChunk {
  return {
    text: `
    CASE
      WHEN ${alias}.metadata->>'youtube_video_id' IS NOT NULL THEN 'youtube:' || (${alias}.metadata->>'youtube_video_id')
      WHEN ${alias}.metadata->>'heiliao_video_id' IS NOT NULL THEN 'heiliao:' || (${alias}.metadata->>'heiliao_video_id')
      WHEN ${alias}.metadata->>'cg91_video_id' IS NOT NULL THEN 'cg91:' || (${alias}.metadata->>'cg91_video_id')
      WHEN ${alias}.metadata->>'baoliao51_video_id' IS NOT NULL THEN 'baoliao51:' || (${alias}.metadata->>'baoliao51_video_id')
      WHEN ${alias}.metadata->>'douyin_video_id' IS NOT NULL THEN 'douyin:' || (${alias}.metadata->>'douyin_video_id')
      WHEN ${alias}.metadata->>'mh18_video_id' IS NOT NULL THEN '18mh:' || (${alias}.metadata->>'mh18_video_id')
      WHEN ${alias}.metadata->>'rou_video_id' IS NOT NULL THEN 'rou:' || (${alias}.metadata->>'rou_video_id')
      WHEN ${alias}.metadata->>'dadaafa_video_id' IS NOT NULL THEN 'dadaafa:' || (${alias}.metadata->>'dadaafa_video_id')
      WHEN ${alias}.metadata->>'j18_video_id' IS NOT NULL THEN '18j:' || (${alias}.metadata->>'j18_video_id')
      WHEN ${alias}.metadata->>'mtif_video_id' IS NOT NULL THEN '1mtif:' || (${alias}.metadata->>'mtif_video_id')
      WHEN ${alias}.metadata->>'tikporn_video_id' IS NOT NULL THEN 'tikporn:' || (${alias}.metadata->>'tikporn_video_id')
      WHEN ${alias}.metadata->>'porna91_video_id' IS NOT NULL THEN '91porna:' || (${alias}.metadata->>'porna91_video_id')
      WHEN ${alias}.metadata->>'porn91_video_id' IS NOT NULL THEN '91porn:' || (${alias}.metadata->>'porn91_video_id')
      WHEN ${alias}.metadata->>'rb91_video_id' IS NOT NULL THEN '91rb:' || (${alias}.metadata->>'rb91_video_id')
      WHEN ${alias}.metadata->>'badnews_video_id' IS NOT NULL THEN 'badnews:' || (${alias}.metadata->>'badnews_video_id')
      WHEN ${alias}.metadata->>'bdrq_video_id' IS NOT NULL THEN 'bdrq:' || (${alias}.metadata->>'bdrq_video_id')
      WHEN ${alias}.metadata->>'avgood_video_id' IS NOT NULL THEN 'avgood:' || (${alias}.metadata->>'avgood_video_id')
      WHEN ${alias}.metadata->>'hs705_video_id' IS NOT NULL THEN '705hs:' || (${alias}.metadata->>'hs705_video_id')
      WHEN ${alias}.metadata->>'xxxtik_post_uuid' IS NOT NULL THEN 'xxxtik:' || (${alias}.metadata->>'xxxtik_post_uuid')
      WHEN ${alias}.metadata->>'affair_video_id' IS NOT NULL THEN 'affair:' || (${alias}.metadata->>'affair_video_id')
      WHEN ${alias}.metadata->>'attach_detail_id' IS NOT NULL THEN 'attach:' || (${alias}.metadata->>'attach_detail_id')
      WHEN ${alias}.metadata->>'dirtyship_detail_id' IS NOT NULL THEN 'dirtyship:' || (${alias}.metadata->>'dirtyship_detail_id')
      WHEN ${alias}.metadata->>'influencersgonewild_detail_id' IS NOT NULL THEN 'influencersgonewild:' || (${alias}.metadata->>'influencersgonewild_detail_id')
      WHEN ${alias}.metadata->>'missav_video_id' IS NOT NULL THEN 'missav:' || (${alias}.metadata->>'missav_video_id')
      WHEN ${alias}.guid LIKE 'heiliao:%' THEN ${alias}.guid
      WHEN ${alias}.guid LIKE 'cg91:%' THEN ${alias}.guid
      WHEN ${alias}.guid LIKE 'baoliao51:%' THEN ${alias}.guid
      WHEN ${alias}.guid LIKE 'douyin:%' THEN ${alias}.guid
      WHEN ${alias}.guid LIKE '18mh:%' THEN ${alias}.guid
      WHEN ${alias}.guid LIKE 'rou:%' THEN ${alias}.guid
      WHEN ${alias}.guid LIKE 'dadaafa:%' THEN ${alias}.guid
      WHEN ${alias}.guid LIKE '18j:%' THEN ${alias}.guid
      WHEN ${alias}.guid LIKE '1mtif:%' THEN ${alias}.guid
      WHEN ${alias}.guid LIKE 'tikporn:%' THEN ${alias}.guid
      WHEN ${alias}.guid LIKE '91porna:%' THEN ${alias}.guid
      WHEN ${alias}.guid LIKE '91porn:%' THEN ${alias}.guid
      WHEN ${alias}.guid LIKE '91rb:%' THEN ${alias}.guid
      WHEN ${alias}.guid LIKE 'badnews:%' THEN ${alias}.guid
      WHEN ${alias}.guid LIKE 'bdrq:%' THEN ${alias}.guid
      WHEN ${alias}.guid LIKE 'avgood:%' THEN ${alias}.guid
      WHEN ${alias}.guid LIKE '705hs:%' THEN ${alias}.guid
      WHEN ${alias}.guid LIKE 'xxxtik:%' THEN ${alias}.guid
      WHEN ${alias}.guid LIKE 'affair:%' THEN ${alias}.guid
      WHEN ${alias}.guid LIKE 'attach:%' THEN ${alias}.guid
      WHEN ${alias}.guid LIKE 'dirtyship:%' THEN ${alias}.guid
      WHEN ${alias}.guid LIKE 'influencersgonewild:%' THEN ${alias}.guid
      WHEN ${alias}.guid LIKE 'missav:%' THEN ${alias}.guid
      WHEN ${alias}.guid LIKE 'yt:video:%' THEN 'youtube:' || replace(${alias}.guid, 'yt:video:', '')
      WHEN ${alias}.video_url LIKE 'https://video.twimg.com/%' THEN split_part(${alias}.video_url, '?', 1)
      ELSE ${alias}.video_url
    END
  `,
    values: [],
  };
}

export function parseVideoFeedSource(raw: string | null): VideoFeedSource {
  if (!raw) {
    return "mixed";
  }

  if (raw === "user" || raw === "public" || raw === "mixed") {
    return raw;
  }

  throw new Error("Invalid source. Expected user, public, or mixed.");
}

export function parseVideoEventType(value: unknown): VideoEventType {
  if (typeof value === "string" && (VIDEO_FEED_EVENT_TYPES as readonly string[]).includes(value)) {
    return value as VideoEventType;
  }

  throw new Error("Invalid eventType.");
}

async function listVideoFeedFromPostgres(query: VideoFeedQuery) {
  const sql = getSql();
  const limit = normalizeLimit(query.limit, { defaultLimit: 10, maxLimit: 20 });
  const cursor = decodeCursor(query.cursor, isVideoFeedCursor);
  const keyword = normalizeVideoFeedKeyword(query.keyword);
  const keywordPattern = keyword ? `%${escapePostgresLikePattern(keyword.toLowerCase())}%` : null;
  const normalizedTags = [...new Set([...(query.tags ?? []), query.tag ?? ""].map((tag) => tag.trim().toLowerCase()).filter(Boolean))];
  const normalizedCategories = [
    ...new Set([...(query.categories ?? []), query.category ?? ""].map((category) => category.trim().toLowerCase()).filter(Boolean)),
  ];
  const categoryRows =
    normalizedCategories.length > 0
      ? asRows<{ slug: string; name: string }>(await sql`
          SELECT slug, name
          FROM categories
          WHERE EXISTS (
            SELECT 1
            FROM jsonb_array_elements_text(${JSON.stringify(normalizedCategories)}::jsonb) AS selected_category(name)
            WHERE LOWER(categories.slug) = selected_category.name
               OR LOWER(categories.name) = selected_category.name
          )
        `)
      : [];
  const normalizedCategoryFilters = [
    ...new Set([
      ...normalizedCategories,
      ...categoryRows.flatMap((category) => [category.slug.trim().toLowerCase(), category.name.trim().toLowerCase()]),
    ]),
  ];
  const source = query.source ?? "mixed";
  const itemVideoKey = videoKeyExpression("i");
  const watchedVideoKey = videoKeyExpression("watched_item");
  const seenIds = compactVideoFeedCursorSeenValues(cursor?.seenIds ?? []);
  const seenGuids = compactVideoFeedCursorSeenValues(cursor?.seenGuids ?? []);
  const seenVideoKeys = compactVideoFeedCursorSeenValues(cursor?.seenVideoKeys ?? []);
  const candidateLimit = Math.max(limit * 3, 30);
  const publicCandidateLimit = Math.max(limit * 2, 20);

  const fetchCandidates = async (bucket: VideoFeedTimeBucket, pool: VideoFeedCandidatePool) => {
    const poolCandidateLimit = pool === "public" ? publicCandidateLimit : candidateLimit;
    const rows = asRows<VideoFeedRow>(await sql`
    WITH candidate_items AS (
      SELECT
        i.id,
        i.guid,
        i.target_id AS "targetId",
        i.video_url AS "videoUrl",
        CASE
          WHEN jsonb_typeof(i.metadata->'playback_headers') = 'object' THEN i.metadata->'playback_headers'
          ELSE NULL
        END AS "playbackHeaders",
        ${itemVideoKey} AS "videoKey",
        COALESCE(i.metadata->>'video_poster_url', i.images->>0) AS "coverUrl",
        i.title,
        COALESCE(i.translated_content, i.content, i.raw_content) AS caption,
        i.author,
        i.fullname,
        i.display_author AS "displayAuthor",
        i.display_handle AS "displayHandle",
        i.author_profile_url AS "authorProfileUrl",
        i.author_profile_platform AS "authorProfilePlatform",
        i.x_url AS "xUrl",
        i.link,
        i.published_at AS "publishedAt",
        i.stored_at AS "storedAt",
        t.source,
        COALESCE(i.published_at, i.stored_at) AS "sortTime",
        CASE
          WHEN t.source = 'youtube' THEN 'youtube:' || t.value
          WHEN t.source IN ('heiliao', 'cg91', 'baoliao51', 'douyin', '18mh', 'rou', 'dadaafa', '18j', '1mtif', 'tikporn', '91porna', '91porn', '91rb', 'badnews', 'bdrq', 'avgood', '705hs', 'xxxtik', 'affair', 'attach', 'dirtyship', 'influencersgonewild', 'missav') THEN t.source
          WHEN t.kind = 'keyword' THEN 'search:' || t.value
          ELSE t.value
        END AS target,
        CASE
          WHEN t.source IN ('heiliao', 'cg91', 'baoliao51', 'douyin', '18mh', 'rou', 'dadaafa', '18j', '1mtif', 'tikporn', '91porna', '91porn', '91rb', 'badnews', 'bdrq', 'avgood', '705hs', 'xxxtik', 'affair', 'attach', 'dirtyship', 'influencersgonewild', 'missav') THEN t.value
          WHEN t.source = 'youtube' THEN 'https://www.youtube.com/channel/' || t.value
          WHEN t.source = 'twitter' AND t.kind = 'user' THEN 'https://x.com/' || t.value
          ELSE NULL
        END AS "targetLink",
        t.kind,
        tp.category,
        i.expires_at AS "expiresAt",
        i.video_url_expires_at AS "videoUrlExpiresAt",
        COALESCE(vs.impressions, 0) AS impressions,
        COALESCE(vs.plays, 0) AS plays,
        COALESCE(vs.finishes, 0) AS finishes,
        COALESCE(vs.likes, 0) AS likes,
        COALESCE(vs.dislikes, 0) AS dislikes,
        COALESCE(vs.skips, 0) AS skips,
        COALESCE(vs.shares, 0) AS shares,
        COALESCE(vs.score, 0) AS score,
        ROW_NUMBER() OVER (
          PARTITION BY ${itemVideoKey}
          ORDER BY COALESCE(i.published_at, i.stored_at) DESC, i.stored_at DESC, i.id DESC
        ) AS "dedupeRank"
      FROM items i
      INNER JOIN targets t ON t.id = i.target_id
      LEFT JOIN target_profiles tp ON tp.target_id = t.id
      LEFT JOIN video_stats vs ON vs.item_id = i.id
      WHERE i.video_url IS NOT NULL
        AND i.video_url <> ''
        AND i.expires_at > NOW()
        AND (
          t.source NOT IN ('youtube', 'heiliao', 'cg91', 'baoliao51', 'douyin', '18mh', 'rou', 'dadaafa', '18j', '1mtif', 'tikporn', '91porna', '91porn', '91rb', 'badnews', 'bdrq', 'avgood', '705hs', 'xxxtik', 'affair', 'attach', 'dirtyship', 'influencersgonewild', 'missav')
          OR i.video_url_expires_at > NOW() + INTERVAL '10 minutes'
        )
        AND NOT EXISTS (
          SELECT 1
          FROM jsonb_array_elements_text(${JSON.stringify(seenIds)}::jsonb) AS seen_item(id)
          WHERE seen_item.id = i.id::text
        )
        AND NOT EXISTS (
          SELECT 1
          FROM jsonb_array_elements_text(${JSON.stringify(seenGuids)}::jsonb) AS seen_guid(guid)
          WHERE seen_guid.guid = i.guid
        )
        AND NOT EXISTS (
          SELECT 1
          FROM jsonb_array_elements_text(${JSON.stringify(seenVideoKeys)}::jsonb) AS seen_video_key(video_key)
          WHERE seen_video_key.video_key = ${itemVideoKey}
        )
        AND (
          (${bucket}::text = 'recent' AND COALESCE(i.published_at, i.stored_at) >= NOW() - INTERVAL '24 hours')
          OR (
            ${bucket}::text = 'week'
            AND COALESCE(i.published_at, i.stored_at) < NOW() - INTERVAL '24 hours'
            AND COALESCE(i.published_at, i.stored_at) >= NOW() - INTERVAL '7 days'
          )
          OR (${bucket}::text = 'older' AND COALESCE(i.published_at, i.stored_at) < NOW() - INTERVAL '7 days')
        )
        AND (
          ${pool}::text = 'all'
          OR (${pool}::text = 'user' AND EXISTS (
            SELECT 1
            FROM subscriptions s
            WHERE s.target_id = i.target_id
              AND s.client_id = ${query.clientId}
          ))
          OR (${pool}::text = 'public' AND COALESCE(tp.is_public_pool, FALSE) = TRUE)
        )
        AND NOT EXISTS (
          SELECT 1
          FROM feed_events fe
          INNER JOIN items watched_item ON watched_item.id = fe.item_id
          WHERE fe.client_id = ${query.clientId}
            AND fe.event_type = ANY(${VIDEO_FEED_SEEN_EVENT_TYPES}::text[])
            AND fe.created_at >= NOW() - INTERVAL '30 days'
            AND ${watchedVideoKey} = ${itemVideoKey}
        )
        AND (
          ${keywordPattern}::text IS NULL
          OR LOWER(COALESCE(i.title, '')) LIKE ${keywordPattern} ESCAPE '\\'
          OR LOWER(COALESCE(i.content, '')) LIKE ${keywordPattern} ESCAPE '\\'
          OR LOWER(COALESCE(i.author, '')) LIKE ${keywordPattern} ESCAPE '\\'
        )
        AND (
          ${JSON.stringify(normalizedTags)}::jsonb = '[]'::jsonb
          OR EXISTS (
            SELECT 1
            FROM item_tags it
            INNER JOIN tags tag ON tag.id = it.tag_id
            WHERE it.item_id = i.id
              AND EXISTS (
                SELECT 1
                FROM jsonb_array_elements_text(${JSON.stringify(normalizedTags)}::jsonb) AS selected_tag(name)
                WHERE LOWER(tag.name) = selected_tag.name
              )
          )
          OR EXISTS (
            SELECT 1
            FROM jsonb_array_elements_text(COALESCE(tp.tags, '[]'::jsonb)) AS profile_tag(name)
            WHERE EXISTS (
              SELECT 1
              FROM jsonb_array_elements_text(${JSON.stringify(normalizedTags)}::jsonb) AS selected_tag(name)
              WHERE LOWER(profile_tag.name) = selected_tag.name
            )
          )
        )
        AND (
          ${JSON.stringify(normalizedCategoryFilters)}::jsonb = '[]'::jsonb
          OR EXISTS (
            SELECT 1
            FROM item_tags it
            INNER JOIN tags tag ON tag.id = it.tag_id
            WHERE it.item_id = i.id
              AND tag.type = 'category'
              AND EXISTS (
                SELECT 1
                FROM jsonb_array_elements_text(${JSON.stringify(normalizedCategoryFilters)}::jsonb) AS selected_category(name)
                WHERE LOWER(tag.name) = selected_category.name
              )
          )
          OR EXISTS (
            SELECT 1
            FROM jsonb_array_elements_text(${JSON.stringify(normalizedCategoryFilters)}::jsonb) AS selected_category(name)
            WHERE LOWER(COALESCE(tp.category, '')) = selected_category.name
          )
        )
    ),
    deduped_items AS (
      SELECT *
      FROM candidate_items
      WHERE "dedupeRank" = 1
    )
    SELECT
      ci.id,
      ci.guid,
      ci."videoKey",
      ci."videoUrl",
      ci."playbackHeaders",
      ci."coverUrl",
      ci.title,
      ci.caption,
      ci.author,
      ci.fullname,
      ci."displayAuthor",
      ci."displayHandle",
      ci."authorProfileUrl",
      ci."authorProfilePlatform",
      ci."xUrl",
      ci.link,
      ci."publishedAt",
      ci."storedAt",
      ci.source,
      ci."sortTime",
      ci.target,
      ci."targetLink",
      ci.kind,
      ci.category,
      ci."expiresAt",
      ci."videoUrlExpiresAt",
      COALESCE((
        SELECT ARRAY_AGG(DISTINCT tag_name ORDER BY tag_name)
        FROM (
          SELECT tag.name AS tag_name
          FROM item_tags it
          INNER JOIN tags tag ON tag.id = it.tag_id
          WHERE it.item_id = ci.id
          UNION
          SELECT profile_tag.name AS tag_name
          FROM target_profiles profile
          CROSS JOIN LATERAL jsonb_array_elements_text(COALESCE(profile.tags, '[]'::jsonb)) AS profile_tag(name)
          WHERE profile.target_id = ci."targetId"
        ) tag_values
      ), ARRAY[]::text[]) AS tags,
      json_build_object(
        'impressions', ci.impressions,
        'plays', ci.plays,
        'finishes', ci.finishes,
        'likes', ci.likes,
        'dislikes', ci.dislikes,
        'skips', ci.skips,
        'shares', ci.shares,
        'score', ci.score
      ) AS stats
    FROM deduped_items ci
    WHERE (
      ${cursor?.sortTime ?? null}::timestamptz IS NULL
      OR ROW(ci."sortTime", ci."storedAt", ci.id) < ROW(
        ${cursor?.sortTime ?? null}::timestamptz,
        ${cursor?.storedAt ?? null}::timestamptz,
        ${cursor?.id ?? null}::uuid
      )
    )
    ORDER BY ci."sortTime" DESC, ci."storedAt" DESC, ci.id DESC
    LIMIT ${poolCandidateLimit}
  `);

    return rows;
  };

  const selected: VideoFeedRow[] = [];
  const bucketCandidates = new Map<VideoFeedTimeBucket, VideoFeedRow[]>();

  const getBucketCandidates = async (bucket: VideoFeedTimeBucket) => {
    const cached = bucketCandidates.get(bucket);
    if (cached) {
      return cached;
    }

    const candidates =
      source === "mixed"
        ? mergeVideoFeedCandidatePools(await Promise.all([fetchCandidates(bucket, "user"), fetchCandidates(bucket, "public")]))
        : await fetchCandidates(bucket, source === "public" ? "public" : source === "user" ? "user" : "all");
    const personalizedCandidates = candidates.sort(compareVideoFeedRowsForClient(query.clientId));
    bucketCandidates.set(bucket, personalizedCandidates);
    return personalizedCandidates;
  };

  const appendFromBucket = async (bucket: VideoFeedTimeBucket, enforceLimits: boolean, enforceConsecutive: boolean) => {
    if (selected.length >= limit) {
      return;
    }

    const candidates = await getBucketCandidates(bucket);
    const nextSelected = selectDiverseVideoItems({
      selected,
      candidates,
      limit,
      previousLastAuthor: cursor?.lastAuthor,
      previousLastTarget: cursor?.lastTarget,
      enforceLimits,
      enforceConsecutive,
    });

    selected.splice(0, selected.length, ...nextSelected);
  };

  await appendFromBucket("recent", true, true);
  await appendFromBucket("week", true, true);
  await appendFromBucket("recent", false, true);
  await appendFromBucket("week", false, true);
  await appendFromBucket("recent", false, false);
  await appendFromBucket("week", false, false);
  await appendFromBucket("older", true, true);
  await appendFromBucket("older", false, true);
  await appendFromBucket("older", false, false);

  const items = selected.slice(0, limit);
  const nextCursorPayload = buildVideoFeedNextCursorPayload({
    seenIds,
    seenGuids,
    seenVideoKeys,
    items,
  });
  const nextCursor = nextCursorPayload ? encodeCursor(nextCursorPayload) : null;

  return {
    items: items.map(({ guid: _guid, sortTime: _sortTime, ...item }) => ({
      ...item,
      ...resolveAuthorPresentation(item),
    })),
    pagination: {
      limit,
      nextCursor,
      hasMore: items.length === limit,
    },
  };
}

export async function listVideoFeed(query: VideoFeedQuery) {
  if (!isOpenSearchFeedEnabled()) {
    return listVideoFeedFromPostgres(query);
  }

  try {
    const { listVideoFeedFromOpenSearch } = await import("@/lib/feed-engine");
    return await listVideoFeedFromOpenSearch(query);
  } catch (error) {
    console.warn("[video-feed] OpenSearch feed failed, falling back to PostgreSQL", error);
    return listVideoFeedFromPostgres(query);
  }
}

async function invalidateVideoFeedEventCaches(clientId: string) {
  await Promise.all([
    cacheDeleteJson("os-feed-seen-video-keys-v2", [clientId]),
    cacheDeleteJson("os-feed-profile-v1", [clientId]),
  ]);
}

export async function recordVideoEvent(input: {
  clientId: string;
  itemId: string;
  eventType: VideoEventType;
  watchMs?: number | null;
  metadata?: Record<string, unknown>;
}) {
  const sql = getSql();
  const watchMs = typeof input.watchMs === "number" && Number.isFinite(input.watchMs) ? Math.max(0, Math.floor(input.watchMs)) : null;
  const metadata = input.metadata ?? {};

  await sql`
    INSERT INTO feed_events (client_id, item_id, event_type, watch_ms, metadata)
    SELECT ${input.clientId}, i.id, ${input.eventType}, ${watchMs}, ${JSON.stringify(metadata)}::jsonb
    FROM items i
    WHERE i.id = ${input.itemId}
      AND i.video_url IS NOT NULL
      AND i.video_url <> ''
      AND i.expires_at > NOW()
      AND (
        NOT EXISTS (SELECT 1 FROM targets t WHERE t.id = i.target_id AND t.source IN ('youtube', 'heiliao', 'cg91', 'baoliao51', 'douyin', '18mh', 'rou', 'dadaafa', '18j', '1mtif', 'tikporn', '91porna', '91porn', '91rb', 'badnews', 'bdrq', 'avgood', '705hs', 'xxxtik', 'affair', 'attach', 'dirtyship', 'influencersgonewild', 'missav'))
        OR i.video_url_expires_at > NOW()
      )
  `;

  await sql`
    INSERT INTO video_stats (
      item_id,
      impressions,
      plays,
      finishes,
      likes,
      dislikes,
      skips,
      shares,
      score,
      last_event_at
    )
    SELECT
      i.id,
      CASE WHEN ${input.eventType} = 'impression' THEN 1 ELSE 0 END,
      CASE WHEN ${input.eventType} = 'play' THEN 1 ELSE 0 END,
      CASE WHEN ${input.eventType} = 'finish' THEN 1 ELSE 0 END,
      CASE WHEN ${input.eventType} = 'like' THEN 1 ELSE 0 END,
      CASE WHEN ${input.eventType} = 'dislike' THEN 1 ELSE 0 END,
      CASE WHEN ${input.eventType} = 'skip' THEN 1 ELSE 0 END,
      CASE WHEN ${input.eventType} = 'share' THEN 1 ELSE 0 END,
      CASE
        WHEN ${input.eventType} = 'finish' THEN 3
        WHEN ${input.eventType} = 'like' THEN 5
        WHEN ${input.eventType} = 'share' THEN 4
        WHEN ${input.eventType} = 'play' THEN 1
        WHEN ${input.eventType} = 'skip' THEN -1
        WHEN ${input.eventType} = 'dislike' THEN -5
        ELSE 0
      END,
      NOW()
    FROM items i
    WHERE i.id = ${input.itemId}
      AND i.video_url IS NOT NULL
      AND i.video_url <> ''
      AND i.expires_at > NOW()
      AND (
        NOT EXISTS (SELECT 1 FROM targets t WHERE t.id = i.target_id AND t.source IN ('youtube', 'heiliao', 'cg91', 'baoliao51', 'douyin', '18mh', 'rou', 'dadaafa', '18j', '1mtif', 'tikporn', '91porna', '91porn', '91rb', 'badnews', 'bdrq', 'avgood', '705hs', 'xxxtik', 'affair', 'attach', 'dirtyship', 'influencersgonewild', 'missav'))
        OR i.video_url_expires_at > NOW()
      )
    ON CONFLICT (item_id) DO UPDATE SET
      impressions = video_stats.impressions + EXCLUDED.impressions,
      plays = video_stats.plays + EXCLUDED.plays,
      finishes = video_stats.finishes + EXCLUDED.finishes,
      likes = video_stats.likes + EXCLUDED.likes,
      dislikes = video_stats.dislikes + EXCLUDED.dislikes,
      skips = video_stats.skips + EXCLUDED.skips,
      shares = video_stats.shares + EXCLUDED.shares,
      score = video_stats.score + EXCLUDED.score,
      last_event_at = NOW(),
      updated_at = NOW()
  `;

  await invalidateVideoFeedEventCaches(input.clientId);
}

export function buildPlaybackFailureRemovalMetadata(input: {
  reason?: string | null;
  retryCount?: number | null;
  watchMs?: number | null;
  metadata?: Record<string, unknown>;
  reportedAt?: string;
}) {
  return {
    ...(input.metadata ?? {}),
    reportType: "video_feed_playback_failure",
    reason: nonEmptyString(input.reason),
    retryCount: normalizeNonNegativeInteger(input.retryCount),
    watchMs: normalizeNonNegativeInteger(input.watchMs),
    reportedAt: input.reportedAt ?? new Date().toISOString(),
  };
}

export async function removeVideoFeedItemAfterPlaybackFailure(input: VideoPlaybackFailureRemovalInput) {
  const sql = getSql();
  const failureMetadata = buildPlaybackFailureRemovalMetadata(input);
  const rows = asRows<{ id: string }>(await sql`
    UPDATE items i
    SET
      expires_at = LEAST(i.expires_at, NOW()),
      video_url_expires_at = LEAST(i.video_url_expires_at, NOW()),
      metadata = i.metadata || jsonb_build_object('video_feed_playback_failure', ${JSON.stringify(failureMetadata)}::jsonb)
    WHERE i.id = ${input.itemId}
      AND i.video_url IS NOT NULL
      AND i.video_url <> ''
      AND i.expires_at > NOW()
      AND EXISTS (
        SELECT 1
        FROM targets t
        LEFT JOIN target_profiles tp ON tp.target_id = t.id
        WHERE t.id = i.target_id
          AND (
            COALESCE(tp.is_public_pool, FALSE) = TRUE
            OR EXISTS (
              SELECT 1
              FROM subscriptions s
              WHERE s.target_id = i.target_id
                AND s.client_id = ${input.clientId}
            )
          )
      )
    RETURNING i.id
  `);

  return { removed: rows.length > 0 };
}

export async function listVideoTags() {
  const sql = getSql();
  const tags = asRows<{ name: string; type: "category" | "topic" | "system"; weight: number }>(await sql`
    SELECT name, type, weight
    FROM tags
    ORDER BY type ASC, weight DESC, name ASC
  `);

  return tags;
}

export async function listVideoCategories() {
  const sql = getSql();
  const categories = asRows<VideoCategory>(await sql`
    SELECT
      slug,
      name,
      weight,
      is_sensitive AS "isSensitive",
      default_hidden AS "defaultHidden"
    FROM categories
    ORDER BY weight DESC, name ASC
  `);

  return categories;
}

function nonEmptyString(value: string | null | undefined) {
  const trimmed = value?.trim() ?? "";
  return trimmed.length > 0 ? trimmed : null;
}

function normalizeNonNegativeInteger(value: number | null | undefined) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return null;
  }

  return Math.max(0, Math.floor(value));
}
