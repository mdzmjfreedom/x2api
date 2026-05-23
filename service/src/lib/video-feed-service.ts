import { getSql, type QueryChunk } from "@/lib/db";
import { buildCursorPage, decodeCursor, normalizeLimit } from "@/lib/pagination";
import { asRows } from "@/lib/sql-result";

export type VideoFeedSource = "user" | "public" | "mixed";
export type VideoEventType = "impression" | "play" | "finish" | "like" | "dislike" | "skip" | "share";

export type VideoFeedQuery = {
  clientId: string;
  limit?: number;
  cursor?: string | null;
  tag?: string | null;
  category?: string | null;
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
  target: string;
  kind: "user" | "keyword";
  tags: string[];
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

type VideoFeedCursor = {
  sortTime: string;
  storedAt: string;
  id: string;
};

type VideoFeedRow = VideoFeedItem & {
  sortTime: string;
};

function isVideoFeedCursor(value: unknown): value is VideoFeedCursor {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }

  const candidate = value as Partial<VideoFeedCursor>;
  return (
    typeof candidate.sortTime === "string" &&
    typeof candidate.storedAt === "string" &&
    typeof candidate.id === "string"
  );
}

function videoKeyExpression(alias: "i" | "watched_item"): QueryChunk {
  return {
    text: `
    CASE
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
  const normalizedTag = query.tag?.trim().toLowerCase() || null;
  const normalizedCategory = query.category?.trim().toLowerCase() || null;
  const source = query.source ?? "mixed";
  const itemVideoKey = videoKeyExpression("i");
  const watchedVideoKey = videoKeyExpression("watched_item");

  const rows = asRows<VideoFeedRow>(await sql`
    WITH candidate_items AS (
      SELECT
        i.id,
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
        COALESCE(i.published_at, i.stored_at) AS "sortTime",
        CASE
          WHEN t.kind = 'keyword' THEN 'search:' || t.value
          ELSE t.value
        END AS target,
        t.kind,
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
          ${normalizedTag}::text IS NULL
          OR EXISTS (
            SELECT 1
            FROM item_tags it
            INNER JOIN tags tag ON tag.id = it.tag_id
            WHERE it.item_id = i.id
              AND LOWER(tag.name) = ${normalizedTag}
          )
          OR EXISTS (
            SELECT 1
            FROM jsonb_array_elements_text(COALESCE(tp.tags, '[]'::jsonb)) AS profile_tag(name)
            WHERE LOWER(profile_tag.name) = ${normalizedTag}
          )
        )
        AND (
          ${normalizedCategory}::text IS NULL
          OR EXISTS (
            SELECT 1
            FROM item_tags it
            INNER JOIN tags tag ON tag.id = it.tag_id
            WHERE it.item_id = i.id
              AND tag.type = 'category'
              AND LOWER(tag.name) = ${normalizedCategory}
          )
          OR LOWER(COALESCE(tp.category, '')) = ${normalizedCategory}
        )
    ),
    deduped_items AS (
      SELECT *
      FROM candidate_items
      WHERE "dedupeRank" = 1
    )
    SELECT
      ci.id,
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
      ci."sortTime",
      ci.target,
      ci.kind,
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
    LIMIT ${limit + 1}
  `);

  const page = buildCursorPage({
    rows,
    limit,
    getCursor: (item) => ({
      sortTime: item.sortTime,
      storedAt: item.storedAt,
      id: item.id,
    }),
  });

  return {
    items: page.items.map(({ sortTime: _sortTime, ...item }) => item),
    pagination: page.pagination,
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
