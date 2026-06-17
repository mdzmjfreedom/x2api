import { getSql } from "@/lib/db";
import { isOpenSearchFeedEnabled } from "@/lib/opensearch";
import {
  listItemsByFeedTokenFromOpenSearch,
  listItemsFromOpenSearch,
} from "@/lib/opensearch-item-service";
import { buildCursorPage, decodeCursor, normalizeLimit } from "@/lib/pagination";
import { asRows } from "@/lib/sql-result";
import { resolveAuthorPresentation, type AuthorPresentation } from "@/lib/author-presentation";
import type { TargetSource } from "@/lib/targets";

type ItemQuery = {
  clientId: string;
  limit?: number;
  cursor?: string | null;
  keyword?: string | null;
  target?: string | null;
  tags?: string[] | null;
  categories?: string[] | null;
  since?: string | null;
};

export type ItemPagination = {
  limit: number;
  nextCursor: string | null;
  hasMore: boolean;
};

export type ListItemsResult = {
  items: ItemRecord[];
  pagination: ItemPagination;
};

type ItemRecordBase = {
  id: string;
  target: string;
  targetLink: string | null;
  source: TargetSource;
  kind: "user" | "keyword" | "channel" | "site";
  category: string | null;
  isSensitive: boolean;
  tags: string[];
  author: string | null;
  fullname: string | null;
  title: string | null;
  content: string | null;
  rawContent: string | null;
  translatedContent: string | null;
  link: string | null;
  xUrl: string | null;
  images: string[];
  videoUrl: string | null;
  expiresAt: string;
  videoUrlExpiresAt: string;
  publishedAt: string | null;
  storedAt: string;
  guid: string;
  isRetweet: boolean;
};

export type ItemRecord = ItemRecordBase & AuthorPresentation;

type ItemCursor = {
  sortTime: string;
  storedAt: string;
  id: string;
};

type ItemRow = ItemRecordBase & {
  sortTime: string;
} & Partial<AuthorPresentation>;

type ItemRecordRow = ItemRecordBase & Partial<AuthorPresentation>;

function isItemCursor(value: unknown): value is ItemCursor {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }

  const candidate = value as Partial<ItemCursor>;
  return (
    typeof candidate.sortTime === "string" &&
    typeof candidate.storedAt === "string" &&
    typeof candidate.id === "string"
  );
}

export async function listItems(query: ItemQuery): Promise<ListItemsResult> {
  if (isOpenSearchFeedEnabled()) {
    try {
      return await listItemsFromOpenSearch(query);
    } catch (error) {
      console.warn("[items] OpenSearch query failed, falling back to PostgreSQL", error);
    }
  }

  const sql = getSql();
  const limit = normalizeLimit(query.limit);
  const cursor = decodeCursor(query.cursor, isItemCursor);
  const searchText = query.keyword?.trim() ? `%${query.keyword.trim().toLowerCase()}%` : null;
  const targetFilter = query.target?.trim().toLowerCase() || null;
  const normalizedTags = [...new Set((query.tags ?? []).map((tag) => tag.trim().toLowerCase()).filter(Boolean))];
  const normalizedCategories = [...new Set((query.categories ?? []).map((category) => category.trim().toLowerCase()).filter(Boolean))];
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
  const sinceFilter = query.since ? new Date(query.since).toISOString() : null;
  const visibleItems = sql`
    SELECT
      i.id,
      i.stored_at AS "storedAt",
      COALESCE(i.published_at, i.stored_at) AS "sortTime",
      i.guid,
      ROW_NUMBER() OVER (
        PARTITION BY i.guid
        ORDER BY COALESCE(i.published_at, i.stored_at) DESC, i.stored_at DESC, i.id DESC
      ) AS "dedupeRank"
    FROM subscriptions s
    INNER JOIN targets t ON t.id = s.target_id
    INNER JOIN items i ON i.target_id = t.id
    LEFT JOIN target_profiles tp ON tp.target_id = t.id
    WHERE s.client_id = ${query.clientId}
      AND (
        i.expires_at > NOW()
      )
      AND (
        t.source NOT IN ('youtube', 'heiliao', 'cg91', 'baoliao51', 'douyin', '18mh', 'rou', 'dadaafa', '18j', '1mtif', 'tikporn', '91porna', '91porn', 'badnews', 'bdrq', 'avgood', '705hs', 'xxxtik', 'affair', 'attach', 'dirtyship', 'influencersgonewild', 'missav')
        OR i.video_url_expires_at > NOW() + INTERVAL '10 minutes'
      )
      AND (
        ${searchText}::text IS NULL
        OR LOWER(COALESCE(i.content, '')) LIKE ${searchText}
        OR LOWER(COALESCE(i.author, '')) LIKE ${searchText}
      )
      AND (
        ${targetFilter}::text IS NULL
        OR LOWER(
          CASE
            WHEN t.source = 'youtube' THEN 'youtube:' || t.value
            WHEN t.source = 'heiliao' THEN 'heiliao:' || t.value
            WHEN t.source = 'cg91' THEN 'cg91:' || t.value
            WHEN t.source = 'baoliao51' THEN 'baoliao51:' || t.value
            WHEN t.source = 'douyin' THEN 'douyin:' || t.value
            WHEN t.source = '18mh' THEN '18mh:' || t.value
            WHEN t.source = 'dadaafa' THEN 'dadaafa:' || t.value
            WHEN t.source = '18j' THEN '18j:' || t.value
            WHEN t.source = '1mtif' THEN '1mtif:' || t.value
            WHEN t.source = 'tikporn' THEN 'tikporn:' || t.value
            WHEN t.source = '91porna' THEN '91porna:' || t.value
            WHEN t.source = '91porn' THEN '91porn:' || t.value
            WHEN t.source = 'badnews' THEN 'badnews:' || t.value
            WHEN t.source = 'bdrq' THEN 'bdrq:' || t.value
            WHEN t.source = 'avgood' THEN 'avgood:' || t.value
            WHEN t.source = '705hs' THEN '705hs:' || t.value
            WHEN t.source = 'xxxtik' THEN 'xxxtik:' || t.value
            WHEN t.source = 'affair' THEN 'affair:' || t.value
            WHEN t.source = 'dirtyship' THEN 'dirtyship:' || t.value
            WHEN t.source = 'influencersgonewild' THEN 'influencersgonewild:' || t.value
            WHEN t.source = 'missav' THEN 'missav:' || t.value
            WHEN t.source = 'rou' THEN 'rou:' || t.value
            WHEN t.kind = 'keyword' THEN 'search:' || t.value
            ELSE t.value
          END
        ) = ${targetFilter}
        OR (t.source IN ('heiliao', 'cg91', 'baoliao51', 'douyin', '18mh', 'rou', 'dadaafa', '18j', '1mtif', 'tikporn', '91porna', '91porn', 'badnews', 'bdrq', 'avgood', '705hs', 'xxxtik', 'affair', 'attach', 'dirtyship', 'influencersgonewild', 'missav') AND LOWER(t.source) = ${targetFilter})
      )
      AND (
        ${sinceFilter}::timestamptz IS NULL
        OR i.stored_at >= ${sinceFilter}::timestamptz
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
  `;

  const rows = asRows<ItemRow>(await sql`
    WITH visible_items AS (
      ${visibleItems}
    ),
    deduped_items AS (
      SELECT *
      FROM visible_items
      WHERE "dedupeRank" = 1
    ),
    ordered_items AS (
      SELECT id, "storedAt", "sortTime", guid
      FROM deduped_items
      WHERE (
        ${cursor?.sortTime ?? null}::timestamptz IS NULL
        OR (
          ROW(
            "sortTime",
            "storedAt",
            id
          ) < ROW(
            ${cursor?.sortTime ?? null}::timestamptz,
            ${cursor?.storedAt ?? null}::timestamptz,
            ${cursor?.id ?? null}::uuid
          )
        )
      )
      ORDER BY "sortTime" DESC, "storedAt" DESC, id DESC
      LIMIT ${limit + 1}
    )
    SELECT
      i.id,
      CASE
        WHEN t.source = 'youtube' THEN 'youtube:' || t.value
        WHEN t.source IN ('heiliao', 'cg91', 'baoliao51', 'douyin', '18mh', 'rou', 'dadaafa', '18j', '1mtif', 'tikporn', '91porna', '91porn', 'badnews', 'bdrq', 'avgood', '705hs', 'xxxtik', 'affair', 'attach', 'dirtyship', 'influencersgonewild', 'missav') THEN t.source
        WHEN t.kind = 'keyword' THEN 'search:' || t.value
        ELSE t.value
      END AS target,
      CASE
        WHEN t.source IN ('heiliao', 'cg91', 'baoliao51', 'douyin', '18mh', 'rou', 'dadaafa', '18j', '1mtif', 'tikporn', '91porna', '91porn', 'badnews', 'bdrq', 'avgood', '705hs', 'xxxtik', 'affair', 'attach', 'dirtyship', 'influencersgonewild', 'missav') THEN t.value
        WHEN t.source = 'youtube' THEN 'https://www.youtube.com/channel/' || t.value
        WHEN t.source = 'twitter' AND t.kind = 'user' THEN 'https://x.com/' || t.value
        ELSE NULL
      END AS "targetLink",
      t.kind,
      t.source,
      tp.category,
      COALESCE(cat.is_sensitive, FALSE) AS "isSensitive",
      COALESCE((
        SELECT ARRAY_AGG(DISTINCT tag_name ORDER BY tag_name)
        FROM (
          SELECT tag.name AS tag_name
          FROM item_tags it
          INNER JOIN tags tag ON tag.id = it.tag_id
          WHERE it.item_id = i.id
          UNION
          SELECT profile_tag.name AS tag_name
          FROM jsonb_array_elements_text(COALESCE(tp.tags, '[]'::jsonb)) AS profile_tag(name)
        ) tag_values
      ), ARRAY[]::text[]) AS tags,
      i.author,
      i.fullname,
      i.display_author AS "displayAuthor",
      i.display_handle AS "displayHandle",
      i.author_profile_url AS "authorProfileUrl",
      i.author_profile_platform AS "authorProfilePlatform",
      i.title,
      i.content,
      NULL::text AS "rawContent",
      NULL::text AS "translatedContent",
      i.link,
      i.x_url AS "xUrl",
      ARRAY(
        SELECT jsonb_array_elements_text(i.images)
      ) AS images,
      i.video_url AS "videoUrl",
      i.expires_at AS "expiresAt",
      i.video_url_expires_at AS "videoUrlExpiresAt",
      i.published_at AS "publishedAt",
      i.stored_at AS "storedAt",
      oi."sortTime",
      i.guid,
      i.is_retweet AS "isRetweet"
    FROM ordered_items oi
    INNER JOIN items i ON i.id = oi.id
    INNER JOIN targets t ON t.id = i.target_id
    LEFT JOIN target_profiles tp ON tp.target_id = t.id
    LEFT JOIN categories cat ON cat.slug = tp.category
    ORDER BY oi."sortTime" DESC, oi."storedAt" DESC, oi.id DESC
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
    items: page.items.map(({ sortTime: _sortTime, ...item }) => ({
      ...item,
      ...resolveAuthorPresentation(item),
    })),
    pagination: {
      limit,
      nextCursor: page.pagination.nextCursor,
      hasMore: page.pagination.hasMore,
    },
  };
}

export async function listItemsByFeedToken(feedToken: string, limit = 50) {
  if (isOpenSearchFeedEnabled()) {
    try {
      return await listItemsByFeedTokenFromOpenSearch({ feedToken, limit });
    } catch (error) {
      console.warn("[items:rss] OpenSearch query failed, falling back to PostgreSQL", error);
    }
  }

  const sql = getSql();
  const normalizedLimit = normalizeLimit(limit);

  const rows = asRows<ItemRecordRow>(await sql`
    WITH visible_items AS (
      SELECT
        i.id,
        CASE
          WHEN t.source = 'youtube' THEN 'youtube:' || t.value
          WHEN t.source IN ('heiliao', 'cg91', 'baoliao51', 'douyin', '18mh', 'rou', 'dadaafa', '18j', '1mtif', 'tikporn', '91porna', '91porn', 'badnews', 'bdrq', 'avgood', '705hs', 'xxxtik', 'affair', 'attach', 'dirtyship', 'influencersgonewild', 'missav') THEN t.source
          WHEN t.kind = 'keyword' THEN 'search:' || t.value
          ELSE t.value
        END AS target,
        CASE
          WHEN t.source IN ('heiliao', 'cg91', 'baoliao51', 'douyin', '18mh', 'rou', 'dadaafa', '18j', '1mtif', 'tikporn', '91porna', '91porn', 'badnews', 'bdrq', 'avgood', '705hs', 'xxxtik', 'affair', 'attach', 'dirtyship', 'influencersgonewild', 'missav') THEN t.value
          WHEN t.source = 'youtube' THEN 'https://www.youtube.com/channel/' || t.value
          WHEN t.source = 'twitter' AND t.kind = 'user' THEN 'https://x.com/' || t.value
          ELSE NULL
        END AS "targetLink",
        t.source,
        t.kind,
        tp.category,
        COALESCE(cat.is_sensitive, FALSE) AS "isSensitive",
        COALESCE((
          SELECT ARRAY_AGG(DISTINCT tag_name ORDER BY tag_name)
          FROM (
            SELECT tag.name AS tag_name
            FROM item_tags it
            INNER JOIN tags tag ON tag.id = it.tag_id
            WHERE it.item_id = i.id
            UNION
            SELECT profile_tag.name AS tag_name
            FROM jsonb_array_elements_text(COALESCE(tp.tags, '[]'::jsonb)) AS profile_tag(name)
          ) tag_values
        ), ARRAY[]::text[]) AS tags,
        i.author,
        i.fullname,
        i.display_author AS "displayAuthor",
        i.display_handle AS "displayHandle",
        i.author_profile_url AS "authorProfileUrl",
        i.author_profile_platform AS "authorProfilePlatform",
        i.title,
        i.content,
        NULL::text AS "rawContent",
        NULL::text AS "translatedContent",
        i.link,
        i.x_url AS "xUrl",
        ARRAY(
          SELECT jsonb_array_elements_text(i.images)
        ) AS images,
        i.video_url AS "videoUrl",
        i.expires_at AS "expiresAt",
        i.video_url_expires_at AS "videoUrlExpiresAt",
        i.published_at AS "publishedAt",
        i.stored_at AS "storedAt",
        COALESCE(i.published_at, i.stored_at) AS "sortTime",
        i.guid,
        i.is_retweet AS "isRetweet",
        ROW_NUMBER() OVER (
          PARTITION BY i.guid
          ORDER BY COALESCE(i.published_at, i.stored_at) DESC, i.stored_at DESC, i.id DESC
        ) AS "dedupeRank"
      FROM clients c
      INNER JOIN subscriptions s ON s.client_id = c.id
      INNER JOIN targets t ON t.id = s.target_id
      INNER JOIN items i ON i.target_id = t.id
      LEFT JOIN target_profiles tp ON tp.target_id = t.id
      LEFT JOIN categories cat ON cat.slug = tp.category
      WHERE c.feed_token = ${feedToken}
        AND c.status = 'active'
        AND i.expires_at > NOW()
        AND (
          t.source NOT IN ('youtube', 'heiliao', 'cg91', 'baoliao51', 'douyin', '18mh', 'rou', 'dadaafa', '18j', '1mtif', 'tikporn', '91porna', '91porn', 'badnews', 'bdrq', 'avgood', '705hs', 'xxxtik', 'affair', 'attach', 'dirtyship', 'influencersgonewild', 'missav')
          OR i.video_url_expires_at > NOW() + INTERVAL '10 minutes'
        )
    ),
    deduped_items AS (
      SELECT *
      FROM visible_items
      WHERE "dedupeRank" = 1
    )
    SELECT
      id,
      target,
      source,
      kind,
      category,
      "isSensitive",
      tags,
      author,
      fullname,
      "displayAuthor",
      "displayHandle",
      "authorProfileUrl",
      "authorProfilePlatform",
      title,
      content,
      "rawContent",
      "translatedContent",
      link,
      "xUrl",
      images,
      "videoUrl",
      "expiresAt",
      "videoUrlExpiresAt",
      "publishedAt",
      "storedAt",
      guid,
      "isRetweet"
    FROM deduped_items
    ORDER BY "sortTime" DESC, "storedAt" DESC, id DESC
    LIMIT ${normalizedLimit}
  `);

  return rows.map((item) => ({
    ...item,
    ...resolveAuthorPresentation(item),
  }));
}
