import { getSql, type QueryChunk } from "@/lib/db";
import { decodeCursor, encodeCursor, normalizeLimit } from "@/lib/pagination";
import { asRows } from "@/lib/sql-result";

export type VideoFeedSource = "user" | "public" | "mixed";
export type VideoEventType = "impression" | "play" | "finish" | "like" | "dislike" | "skip" | "share";

export type VideoFeedQuery = {
  clientId: string;
  limit?: number;
  cursor?: string | null;
  tag?: string | null;
  category?: string | null;
  tags?: string[] | null;
  categories?: string[] | null;
  source?: VideoFeedSource;
};

export type VideoFeedItem = {
  id: string;
  videoUrl: string;
  coverUrl: string | null;
  title: string | null;
  caption: string | null;
  author: string | null;
  fullname: string | null;
  xUrl: string | null;
  link: string | null;
  publishedAt: string | null;
  storedAt: string;
  source: "twitter" | "youtube" | "heiliao" | "cg91" | "baoliao51" | "douyin";
  target: string;
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

type VideoFeedRow = VideoFeedItem & {
  guid: string;
  videoKey: string;
  sortTime: string;
};

type VideoFeedTimeBucket = "recent" | "week" | "older";

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

function videoKeyExpression(alias: "i" | "watched_item"): QueryChunk {
  return {
    text: `
    CASE
      WHEN ${alias}.metadata->>'youtube_video_id' IS NOT NULL THEN 'youtube:' || (${alias}.metadata->>'youtube_video_id')
      WHEN ${alias}.metadata->>'heiliao_video_id' IS NOT NULL THEN 'heiliao:' || (${alias}.metadata->>'heiliao_video_id')
      WHEN ${alias}.metadata->>'cg91_video_id' IS NOT NULL THEN 'cg91:' || (${alias}.metadata->>'cg91_video_id')
      WHEN ${alias}.metadata->>'baoliao51_video_id' IS NOT NULL THEN 'baoliao51:' || (${alias}.metadata->>'baoliao51_video_id')
      WHEN ${alias}.metadata->>'douyin_video_id' IS NOT NULL THEN 'douyin:' || (${alias}.metadata->>'douyin_video_id')
      WHEN ${alias}.guid LIKE 'heiliao:%' THEN ${alias}.guid
      WHEN ${alias}.guid LIKE 'cg91:%' THEN ${alias}.guid
      WHEN ${alias}.guid LIKE 'baoliao51:%' THEN ${alias}.guid
      WHEN ${alias}.guid LIKE 'douyin:%' THEN ${alias}.guid
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
  if (
    value === "impression" ||
    value === "play" ||
    value === "finish" ||
    value === "like" ||
    value === "dislike" ||
    value === "skip" ||
    value === "share"
  ) {
    return value;
  }

  throw new Error("Invalid eventType.");
}

export async function listVideoFeed(query: VideoFeedQuery) {
  const sql = getSql();
  const limit = normalizeLimit(query.limit, { defaultLimit: 10, maxLimit: 20 });
  const cursor = decodeCursor(query.cursor, isVideoFeedCursor);
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
  const seenIds = [...new Set(cursor?.seenIds ?? [])];
  const seenGuids = [...new Set(cursor?.seenGuids ?? [])];
  const seenVideoKeys = [...new Set(cursor?.seenVideoKeys ?? [])];
  const candidateLimit = Math.max(limit * 3, 30);

  const fetchCandidates = async (bucket: VideoFeedTimeBucket) => {
    const rows = asRows<VideoFeedRow>(await sql`
    WITH candidate_items AS (
      SELECT
        i.id,
        i.guid,
        i.target_id AS "targetId",
        i.video_url AS "videoUrl",
        ${itemVideoKey} AS "videoKey",
        COALESCE(i.metadata->>'video_poster_url', i.images->>0) AS "coverUrl",
        i.title,
        COALESCE(i.translated_content, i.content, i.raw_content) AS caption,
        i.author,
        i.fullname,
        i.x_url AS "xUrl",
        i.link,
        i.published_at AS "publishedAt",
        i.stored_at AS "storedAt",
        t.source,
        COALESCE(i.published_at, i.stored_at) AS "sortTime",
        CASE
          WHEN t.source = 'youtube' THEN 'youtube:' || t.value
          WHEN t.source = 'heiliao' THEN 'heiliao:' || t.value
          WHEN t.source = 'cg91' THEN 'cg91:' || t.value
          WHEN t.source = 'baoliao51' THEN 'baoliao51:' || t.value
          WHEN t.source = 'douyin' THEN 'douyin:' || t.value
          WHEN t.kind = 'keyword' THEN 'search:' || t.value
          ELSE t.value
        END AS target,
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
          t.source NOT IN ('youtube', 'heiliao', 'cg91', 'baoliao51', 'douyin')
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
          ${source}::text = 'mixed'
          OR (${source}::text = 'user' AND EXISTS (
            SELECT 1
            FROM subscriptions s
            WHERE s.target_id = i.target_id
              AND s.client_id = ${query.clientId}
          ))
          OR (${source}::text = 'public' AND COALESCE(tp.is_public_pool, FALSE) = TRUE)
        )
        AND (
          ${source}::text <> 'mixed'
          OR EXISTS (
            SELECT 1
            FROM subscriptions s
            WHERE s.target_id = i.target_id
              AND s.client_id = ${query.clientId}
          )
          OR COALESCE(tp.is_public_pool, FALSE) = TRUE
        )
        AND NOT EXISTS (
          SELECT 1
          FROM feed_events fe
          INNER JOIN items watched_item ON watched_item.id = fe.item_id
          WHERE fe.client_id = ${query.clientId}
            AND fe.event_type IN ('impression', 'play', 'finish', 'dislike')
            AND fe.created_at >= NOW() - INTERVAL '7 days'
            AND ${watchedVideoKey} = ${itemVideoKey}
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
      ci."coverUrl",
      ci.title,
      ci.caption,
      ci.author,
      ci.fullname,
      ci."xUrl",
      ci.link,
      ci."publishedAt",
      ci."storedAt",
      ci.source,
      ci."sortTime",
      ci.target,
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
    LIMIT ${candidateLimit}
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

    const candidates = await fetchCandidates(bucket);
    bucketCandidates.set(bucket, candidates);
    return candidates;
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
  const cursorSeenIds = [...seenIds, ...items.map((item) => item.id)];
  const cursorSeenGuids = [
    ...seenGuids,
    ...items.map((item) => item.guid).filter((guid): guid is string => typeof guid === "string" && guid.length > 0),
  ];
  const cursorSeenVideoKeys = [
    ...seenVideoKeys,
    ...items.map((item) => item.videoKey).filter((videoKey): videoKey is string => typeof videoKey === "string" && videoKey.length > 0),
  ];
  const lastItem = items[items.length - 1];
  const nextCursor =
    items.length > 0
      ? encodeCursor({
          seenIds: cursorSeenIds,
          seenGuids: cursorSeenGuids,
          seenVideoKeys: cursorSeenVideoKeys,
          lastAuthor: lastItem ? (getAuthorKey(lastItem) ?? null) : null,
          lastTarget: lastItem ? (normalizeDiversityKey(lastItem.target) ?? null) : null,
        })
      : null;

  return {
    items: items.map(({ guid: _guid, sortTime: _sortTime, ...item }) => item),
    pagination: {
      limit,
      nextCursor,
      hasMore: items.length === limit,
    },
  };
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
        NOT EXISTS (SELECT 1 FROM targets t WHERE t.id = i.target_id AND t.source IN ('youtube', 'heiliao', 'cg91', 'baoliao51', 'douyin'))
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
        NOT EXISTS (SELECT 1 FROM targets t WHERE t.id = i.target_id AND t.source IN ('youtube', 'heiliao', 'cg91', 'baoliao51', 'douyin'))
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
