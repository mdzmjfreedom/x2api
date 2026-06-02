import { getSql } from "@/lib/db";
import { buildCursorPage, decodeCursor, normalizeLimit } from "@/lib/pagination";
import { asRows } from "@/lib/sql-result";

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

export type ItemRecord = {
  id: string;
  target: string;
  source: "twitter" | "youtube";
  kind: "user" | "keyword" | "channel";
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

type ItemCursor = {
  sortTime: string;
  storedAt: string;
  id: string;
};

type ItemRow = ItemRecord & {
  sortTime: string;
};

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
      CASE
        WHEN t.source = 'youtube' THEN 'youtube:' || t.value
        WHEN t.kind = 'keyword' THEN 'search:' || t.value
        ELSE t.value
      END AS target,
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
      i.title,
      i.content,
      i.raw_content AS "rawContent",
      i.translated_content AS "translatedContent",
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
    FROM subscriptions s
    INNER JOIN targets t ON t.id = s.target_id
    INNER JOIN items i ON i.target_id = t.id
    LEFT JOIN target_profiles tp ON tp.target_id = t.id
    LEFT JOIN categories cat ON cat.slug = tp.category
    WHERE s.client_id = ${query.clientId}
      AND (
        i.expires_at > NOW()
      )
      AND (
        t.source <> 'youtube'
        OR i.video_url_expires_at > NOW() + INTERVAL '10 minutes'
      )
      AND (
        ${searchText}::text IS NULL
        OR LOWER(COALESCE(i.content, '')) LIKE ${searchText}
        OR LOWER(COALESCE(i.raw_content, '')) LIKE ${searchText}
        OR LOWER(COALESCE(i.translated_content, '')) LIKE ${searchText}
        OR LOWER(COALESCE(i.author, '')) LIKE ${searchText}
      )
      AND (
        ${targetFilter}::text IS NULL
        OR LOWER(
          CASE
            WHEN t.source = 'youtube' THEN 'youtube:' || t.value
            WHEN t.kind = 'keyword' THEN 'search:' || t.value
            ELSE t.value
          END
        ) = ${targetFilter}
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
    )
    SELECT
      id,
      target,
      kind,
      source,
      category,
      "isSensitive",
      tags,
      author,
      fullname,
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
      "sortTime",
      guid,
      "isRetweet"
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
    pagination: {
      limit,
      nextCursor: page.pagination.nextCursor,
      hasMore: page.pagination.hasMore,
    },
  };
}

export async function listItemsByFeedToken(feedToken: string, limit = 50) {
  const sql = getSql();
  const normalizedLimit = normalizeLimit(limit);

  const rows = asRows<ItemRecord>(await sql`
    WITH visible_items AS (
      SELECT
        i.id,
        CASE
          WHEN t.source = 'youtube' THEN 'youtube:' || t.value
          WHEN t.kind = 'keyword' THEN 'search:' || t.value
          ELSE t.value
        END AS target,
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
        i.title,
        i.content,
        i.raw_content AS "rawContent",
        i.translated_content AS "translatedContent",
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
          t.source <> 'youtube'
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

  return rows;
}
