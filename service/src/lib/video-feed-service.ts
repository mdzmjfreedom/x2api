import { getSql } from "@/lib/db";
import { getOpenSearchClient, getOpenSearchItemsIndex } from "@/lib/opensearch";
import { cacheDeleteJson } from "@/lib/redis-cache";
import { asRows } from "@/lib/sql-result";
import { listVideoFeedFromOpenSearch } from "@/lib/feed-engine";
import type { AuthorPresentation } from "@/lib/author-presentation";
import type { TargetSource } from "@/lib/targets";

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

function getAuthorKey(item: VideoFeedDiversityItem) {
  return normalizeDiversityKey(item.author) ?? normalizeDiversityKey(item.fullname);
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

export async function listVideoFeed(query: VideoFeedQuery) {
  return await listVideoFeedFromOpenSearch(query);
}

async function invalidateVideoFeedEventCaches(clientId: string) {
  await Promise.all([
    cacheDeleteJson("os-feed-seen-video-keys-v2", [clientId]),
    cacheDeleteJson("os-feed-profile-v1", [clientId]),
  ]);
}

async function updateOpenSearchVideoStats(itemId: string) {
  const client = getOpenSearchClient();
  if (!client) {
    return;
  }

  const sql = getSql();
  const rows = asRows<{
    score: number;
    impressions: number;
    plays: number;
    finishes: number;
    likes: number;
    dislikes: number;
    skips: number;
    shares: number;
  }>(await sql`
    SELECT
      COALESCE(score, 0) AS score,
      COALESCE(impressions, 0) AS impressions,
      COALESCE(plays, 0) AS plays,
      COALESCE(finishes, 0) AS finishes,
      COALESCE(likes, 0) AS likes,
      COALESCE(dislikes, 0) AS dislikes,
      COALESCE(skips, 0) AS skips,
      COALESCE(shares, 0) AS shares
    FROM video_stats
    WHERE item_id = ${itemId}
    LIMIT 1
  `);
  const row = rows[0];
  if (!row) {
    return;
  }

  try {
    const params = {
      index: getOpenSearchItemsIndex(),
      id: itemId,
      body: {
        doc: {
          score: Number(row.score),
          quality_score: Math.max(Number(row.score), 0),
          impressions: Number(row.impressions),
          plays: Number(row.plays),
          finishes: Number(row.finishes),
          likes: Number(row.likes),
          dislikes: Number(row.dislikes),
          skips: Number(row.skips),
          shares: Number(row.shares),
        },
      },
      retry_on_conflict: 3,
    } as unknown as Parameters<typeof client.update>[0];
    await client.update(params);
  } catch (error) {
    console.warn("[video-feed] Failed to update OpenSearch stats", error);
  }
}

async function deleteOpenSearchVideoItem(itemId: string) {
  const client = getOpenSearchClient();
  if (!client) {
    return;
  }

  try {
    const params = {
      index: getOpenSearchItemsIndex(),
      id: itemId,
      ignore: [404],
    } as unknown as Parameters<typeof client.delete>[0];
    await client.delete(params);
  } catch (error) {
    console.warn("[video-feed] Failed to delete OpenSearch item", error);
  }
}

async function getOpenSearchVideoDocument(itemId: string) {
  const client = getOpenSearchClient();
  if (!client) {
    return null;
  }

  try {
    const response = await client.get({
      index: getOpenSearchItemsIndex(),
      id: itemId,
    } as unknown as Parameters<typeof client.get>[0]);
    const source = (response as { _source?: Record<string, unknown> })._source;
    return source ?? null;
  } catch {
    return null;
  }
}

function isActiveVideoDocument(source: Record<string, unknown> | null) {
  if (!source) {
    return false;
  }

  const hasVideo = Boolean(source.has_video) || (typeof source.video_url === "string" && source.video_url.length > 0);
  if (!hasVideo) {
    return false;
  }

  const expiresAt = typeof source.expires_at === "string" ? Date.parse(source.expires_at) : Number.NaN;
  const videoUrlExpiresAt = typeof source.video_url_expires_at === "string" ? Date.parse(source.video_url_expires_at) : Number.NaN;
  const now = Date.now();

  if (Number.isFinite(expiresAt) && expiresAt <= now) {
    return false;
  }

  if (Number.isFinite(videoUrlExpiresAt) && videoUrlExpiresAt <= now) {
    return false;
  }

  return true;
}

export async function recordVideoEvent(input: {
  clientId: string;
  itemId: string;
  eventType: VideoEventType;
  watchMs?: number | null;
  metadata?: Record<string, unknown>;
}) {
  const source = await getOpenSearchVideoDocument(input.itemId);
  if (!isActiveVideoDocument(source)) {
    throw new Error("Invalid itemId.");
  }

  const sql = getSql();
  const watchMs = typeof input.watchMs === "number" && Number.isFinite(input.watchMs) ? Math.max(0, Math.floor(input.watchMs)) : null;
  const metadata = input.metadata ?? {};

  await sql`
    INSERT INTO feed_events (client_id, item_id, event_type, watch_ms, metadata)
    VALUES (${input.clientId}, ${input.itemId}, ${input.eventType}, ${watchMs}, ${JSON.stringify(metadata)}::jsonb)
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
    VALUES (
      ${input.itemId},
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

  await updateOpenSearchVideoStats(input.itemId);
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
  const source = await getOpenSearchVideoDocument(input.itemId);
  if (!source || !isActiveVideoDocument(source)) {
    return { removed: false };
  }

  const sql = getSql();
  const failureMetadata = buildPlaybackFailureRemovalMetadata(input);
  const rows = asRows<{ id: string }>(await sql`
    UPDATE items i
    SET
      expires_at = LEAST(i.expires_at, NOW()),
      video_url_expires_at = LEAST(i.video_url_expires_at, NOW()),
      metadata = COALESCE(i.metadata, '{}'::jsonb) || jsonb_build_object('video_feed_playback_failure', ${JSON.stringify(failureMetadata)}::jsonb)
    WHERE i.id = ${input.itemId}
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

  if (rows.length > 0) {
    await deleteOpenSearchVideoItem(input.itemId);
  }
  return { removed: rows.length > 0 };
}

export async function listVideoTags() {
  const sql = getSql();
  return asRows<{ name: string; type: "category" | "topic" | "system"; weight: number }>(await sql`
    SELECT name, type, weight
    FROM tags
    ORDER BY type ASC, weight DESC, name ASC
  `);
}

export async function listVideoCategories() {
  const sql = getSql();
  return asRows<VideoCategory>(await sql`
    SELECT
      slug,
      name,
      weight,
      is_sensitive AS "isSensitive",
      default_hidden AS "defaultHidden"
    FROM categories
    ORDER BY weight DESC, name ASC
  `);
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
