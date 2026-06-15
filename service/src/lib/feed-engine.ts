import { getSql, type QueryChunk } from "@/lib/db";
import { getOpenSearchClient, getOpenSearchItemsIndex } from "@/lib/opensearch";
import { decodeCursor, encodeCursor, normalizeLimit } from "@/lib/pagination";
import { cachedJson } from "@/lib/redis-cache";
import { asRows } from "@/lib/sql-result";
import type { TargetSource } from "@/lib/targets";
import {
  buildVideoFeedNextCursorPayload,
  compactVideoFeedCursorSeenValues,
  normalizeVideoFeedKeyword,
  selectDiverseVideoItems,
  VIDEO_FEED_SEEN_EVENT_TYPES,
  type VideoFeedItem,
  type VideoFeedQuery,
} from "@/lib/video-feed-service";
import { resolveAuthorPresentation, type AuthorPresentation } from "@/lib/author-presentation";

type OpenSearchJson = Record<string, unknown>;

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

type FeedProfileFeature = {
  value: string;
  weight: number;
};

type FeedProfileFeatureType = "categories" | "tags" | "sources" | "targets" | "authors";
type WindowedFeedProfile = OpenSearchJson & {
  windows?: Record<string, OpenSearchJson>;
};
type DirectionalFeedProfile = OpenSearchJson & {
  positive?: FeedProfileFeature[];
  negative?: FeedProfileFeature[];
};

type UserFeedProfile = {
  shortProfile: WindowedFeedProfile;
  longProfile: WindowedFeedProfile;
  negativeProfile: WindowedFeedProfile;
  sourceProfile: DirectionalFeedProfile;
  targetProfile: DirectionalFeedProfile;
  authorProfile: DirectionalFeedProfile;
  exploreRatio: number;
  confidence: number;
  eventCount: number;
  hasProfile: boolean;
};

type UserFeedProfileRow = {
  shortProfile: unknown;
  longProfile: unknown;
  negativeProfile: unknown;
  sourceProfile: unknown;
  targetProfile: unknown;
  authorProfile: unknown;
  exploreRatio: string | number | null;
  confidence: string | number | null;
  eventCount: number | null;
};

type OpenSearchFeedSource = {
  id: string;
  guid?: string | null;
  video_url: string;
  playback_headers?: Record<string, string> | null;
  video_key?: string | null;
  cover_url?: string | null;
  title?: string | null;
  caption?: string | null;
  author?: string | null;
  fullname?: string | null;
  display_author?: string | null;
  display_handle?: string | null;
  author_profile_url?: string | null;
  author_profile_platform?: string | null;
  x_url?: string | null;
  link?: string | null;
  published_at?: string | null;
  stored_at: string;
  sort_at?: string | null;
  source: TargetSource;
  target: string;
  target_id?: string | null;
  target_link?: string | null;
  kind: "user" | "keyword" | "channel" | "site";
  category?: string | null;
  tags?: string[];
  expires_at: string;
  video_url_expires_at: string;
  score?: number;
  impressions?: number;
  plays?: number;
  finishes?: number;
  likes?: number;
  dislikes?: number;
  skips?: number;
  shares?: number;
};

type OpenSearchHit = {
  _id: string;
  _source?: OpenSearchFeedSource;
};

type OpenSearchSearchResponse = {
  body?: {
    hits?: {
      hits?: OpenSearchHit[];
    };
  };
  hits?: {
    hits?: OpenSearchHit[];
  };
};

type OpenSearchFeedRow = {
  id: string;
  guid: string;
  videoKey: string;
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
  sortTime: string;
  source: TargetSource;
  target: string;
  targetLink: string | null;
  kind: "user" | "keyword" | "channel" | "site";
  category: string | null;
  tags: string[];
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
} & Partial<AuthorPresentation>;

type VideoKeyRow = {
  videoKey: string | null;
};

const VIDEO_SOURCE_VALUES = [
  "youtube",
  "heiliao",
  "cg91",
  "baoliao51",
  "douyin",
  "18mh",
  "rou",
  "dadaafa",
  "18j",
  "1mtif",
  "tikporn",
  "91porna",
  "91porn",
  "91rb",
  "badnews",
  "bdrq",
  "avgood",
  "705hs",
  "xxxtik",
  "affair",
  "attach",
  "dirtyship",
  "influencersgonewild",
  "missav",
];

const EMPTY_PROFILE: UserFeedProfile = {
  shortProfile: {},
  longProfile: {},
  negativeProfile: {},
  sourceProfile: {},
  targetProfile: {},
  authorProfile: {},
  exploreRatio: 0.4,
  confidence: 0,
  eventCount: 0,
  hasProfile: false,
};

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

function normalizeKey(value: string | null | undefined) {
  return value?.trim().toLowerCase() || null;
}

function normalizedUnique(values: Array<string | null | undefined>, max = 200) {
  const result: string[] = [];
  const seen = new Set<string>();
  for (const value of values) {
    const key = normalizeKey(value);
    if (!key || seen.has(key)) {
      continue;
    }
    seen.add(key);
    result.push(key);
    if (result.length >= max) {
      break;
    }
  }
  return result;
}

function uniquePreservingValues(values: Array<string | null | undefined>, max = 200) {
  const result: string[] = [];
  const seen = new Set<string>();
  for (const value of values) {
    const trimmed = value?.trim();
    const key = normalizeKey(trimmed);
    if (!trimmed || !key || seen.has(key)) {
      continue;
    }
    seen.add(key);
    result.push(trimmed);
    if (result.length >= max) {
      break;
    }
  }
  return result;
}

function combineSeenVideoKeys(recentSeenVideoKeys: string[], cursorSeenVideoKeys: string[], max = 1000) {
  return uniquePreservingValues([...recentSeenVideoKeys, ...cursorSeenVideoKeys], max);
}

async function getSubscribedTargetIds(clientId: string) {
  return cachedJson("os-feed-subscriptions", [clientId], 120, async () => {
    const sql = getSql();
    const rows = asRows<{ targetId: string }>(await sql`
      SELECT target_id AS "targetId"
      FROM subscriptions
      WHERE client_id = ${clientId}
    `);
    return rows.map((row) => row.targetId);
  });
}

async function getRecentSeenVideoKeys(clientId: string) {
  return cachedJson("os-feed-seen-video-keys-v2", [clientId], 45, async () => {
    const sql = getSql();
    const watchedVideoKey = videoKeyExpression("watched_item");
    const rows = asRows<VideoKeyRow>(await sql`
      SELECT DISTINCT ${watchedVideoKey} AS "videoKey"
      FROM feed_events fe
      INNER JOIN items watched_item ON watched_item.id = fe.item_id
      WHERE fe.client_id = ${clientId}
        AND fe.event_type = ANY(${VIDEO_FEED_SEEN_EVENT_TYPES}::text[])
        AND fe.created_at >= NOW() - INTERVAL '30 days'
      LIMIT 1000
    `);
    return uniquePreservingValues(rows.map((row) => row.videoKey), 1000);
  });
}

function asJsonObject(value: unknown): OpenSearchJson {
  if (typeof value === "string") {
    try {
      const parsed: unknown = JSON.parse(value);
      return asJsonObject(parsed);
    } catch {
      return {};
    }
  }
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value as OpenSearchJson;
  }
  return {};
}

function numericValue(value: unknown, fallback: number) {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string") {
    const parsed = Number.parseFloat(value);
    if (Number.isFinite(parsed)) {
      return parsed;
    }
  }
  return fallback;
}

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}

function emptyProfile(): UserFeedProfile {
  return {
    ...EMPTY_PROFILE,
    shortProfile: {},
    longProfile: {},
    negativeProfile: {},
    sourceProfile: {},
    targetProfile: {},
    authorProfile: {},
  };
}

async function getUserFeedProfile(clientId: string): Promise<UserFeedProfile> {
  return cachedJson("os-feed-profile-v1", [clientId], 300, async () => {
    const sql = getSql();
    const rows = asRows<UserFeedProfileRow>(await sql`
      SELECT
        short_profile AS "shortProfile",
        long_profile AS "longProfile",
        negative_profile AS "negativeProfile",
        source_profile AS "sourceProfile",
        target_profile AS "targetProfile",
        author_profile AS "authorProfile",
        explore_ratio AS "exploreRatio",
        confidence,
        event_count AS "eventCount"
      FROM user_feed_profiles
      WHERE client_id = ${clientId}
    `);
    const row = rows[0];
    if (!row) {
      return emptyProfile();
    }

    return {
      shortProfile: asJsonObject(row.shortProfile),
      longProfile: asJsonObject(row.longProfile),
      negativeProfile: asJsonObject(row.negativeProfile),
      sourceProfile: asJsonObject(row.sourceProfile),
      targetProfile: asJsonObject(row.targetProfile),
      authorProfile: asJsonObject(row.authorProfile),
      exploreRatio: clamp(numericValue(row.exploreRatio, EMPTY_PROFILE.exploreRatio), 0.05, 0.45),
      confidence: clamp(numericValue(row.confidence, 0), 0, 1),
      eventCount: Math.max(0, Math.floor(numericValue(row.eventCount, 0))),
      hasProfile: true,
    };
  });
}

async function normalizeCategoryFilters(categories: string[]) {
  if (categories.length === 0) {
    return [];
  }
  const sql = getSql();
  const categoryRows = asRows<{ slug: string; name: string }>(await sql`
    SELECT slug, name
    FROM categories
    WHERE EXISTS (
      SELECT 1
      FROM jsonb_array_elements_text(${JSON.stringify(categories)}::jsonb) AS selected_category(name)
      WHERE LOWER(categories.slug) = selected_category.name
         OR LOWER(categories.name) = selected_category.name
    )
  `);
  return normalizedUnique([
    ...categories,
    ...categoryRows.flatMap((category) => [category.slug, category.name]),
  ]);
}

function terms(field: string, values: string[]) {
  if (values.length === 0) {
    return null;
  }
  return { terms: { [field]: values } };
}

function escapeOpenSearchWildcard(value: string) {
  return value.replace(/[\\*?]/g, (match) => `\\${match}`);
}

function sourceFilter(source: VideoFeedQuery["source"], subscribedTargetIds: string[]) {
  if (source === "public") {
    return [{ term: { is_public_pool: true } }];
  }

  if (source === "user") {
    if (subscribedTargetIds.length === 0) {
      return [{ term: { target_id: "__no_subscriptions__" } }];
    }
    return [{ terms: { target_id: subscribedTargetIds } }];
  }

  if (subscribedTargetIds.length === 0) {
    return [{ term: { is_public_pool: true } }];
  }

  return [
    {
      bool: {
        should: [{ terms: { target_id: subscribedTargetIds } }, { term: { is_public_pool: true } }],
        minimum_should_match: 1,
      },
    },
  ];
}

function toNumber(value: number | undefined) {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function toRow(source: OpenSearchFeedSource): OpenSearchFeedRow | null {
  if (!source.id || !source.video_url || !source.stored_at || !source.expires_at || !source.video_url_expires_at) {
    return null;
  }

  const sortTime = source.sort_at ?? source.published_at ?? source.stored_at;
  return {
    id: source.id,
    guid: source.guid ?? source.id,
    videoKey: source.video_key ?? source.video_url,
    videoUrl: source.video_url,
    playbackHeaders: source.playback_headers ?? null,
    coverUrl: source.cover_url ?? null,
    title: source.title ?? null,
    caption: source.caption ?? null,
    author: source.author ?? null,
    fullname: source.fullname ?? null,
    displayAuthor: source.display_author ?? undefined,
    displayHandle: source.display_handle ?? undefined,
    authorProfileUrl: source.author_profile_url ?? undefined,
    authorProfilePlatform: source.author_profile_platform ?? undefined,
    xUrl: source.x_url ?? null,
    link: source.link ?? null,
    publishedAt: source.published_at ?? null,
    storedAt: source.stored_at,
    sortTime,
    source: source.source,
    target: source.target,
    targetLink: source.target_link ?? null,
    kind: source.kind,
    category: source.category ?? null,
    tags: Array.isArray(source.tags) ? source.tags : [],
    expiresAt: source.expires_at,
    videoUrlExpiresAt: source.video_url_expires_at,
    stats: {
      impressions: toNumber(source.impressions),
      plays: toNumber(source.plays),
      finishes: toNumber(source.finishes),
      likes: toNumber(source.likes),
      dislikes: toNumber(source.dislikes),
      skips: toNumber(source.skips),
      shares: toNumber(source.shares),
      score: toNumber(source.score),
    },
  };
}

function featureArray(value: unknown): FeedProfileFeature[] {
  if (!Array.isArray(value)) {
    return [];
  }

  return value
    .map((entry) => {
      if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
        return null;
      }
      const candidate = entry as Record<string, unknown>;
      const normalized = normalizeKey(typeof candidate.value === "string" ? candidate.value : null);
      const weight = numericValue(candidate.weight, 0);
      return normalized && weight > 0 ? { value: normalized, weight } : null;
    })
    .filter((entry): entry is FeedProfileFeature => entry !== null);
}

function windowBucket(profile: WindowedFeedProfile, windowName: string) {
  const windows = asJsonObject(profile.windows);
  return asJsonObject(windows[windowName]);
}

function windowFeatures(profile: WindowedFeedProfile, windowName: string, featureType: FeedProfileFeatureType) {
  return featureArray(windowBucket(profile, windowName)[featureType]);
}

function directionalFeatures(profile: DirectionalFeedProfile, direction: "positive" | "negative") {
  return featureArray(profile[direction]);
}

function addWeightedFeatures(
  accumulator: Map<string, number>,
  features: FeedProfileFeature[],
  multiplier: number,
) {
  for (const feature of features) {
    accumulator.set(feature.value, (accumulator.get(feature.value) ?? 0) + feature.weight * multiplier);
  }
}

function rankedProfileFeatures(accumulator: Map<string, number>, maxFeatures: number): FeedProfileFeature[] {
  const ranked = [...accumulator.entries()].sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]));
  const strongest = ranked[0]?.[1] ?? 1;
  return ranked.slice(0, maxFeatures).map(([value, score]) => ({
    value,
    weight: clamp(score / strongest, 0.2, 1),
  }));
}

function aggregatePositiveFeatures(
  profile: UserFeedProfile,
  featureType: FeedProfileFeatureType,
  mode: "personalized" | "explore",
  maxFeatures: number,
) {
  const accumulator = new Map<string, number>();
  if (mode === "personalized") {
    addWeightedFeatures(accumulator, windowFeatures(profile.shortProfile, "1d", featureType), 1.8);
    addWeightedFeatures(accumulator, windowFeatures(profile.shortProfile, "3d", featureType), 1.35);
  }

  addWeightedFeatures(accumulator, windowFeatures(profile.longProfile, "30d", featureType), mode === "explore" ? 0.95 : 0.85);
  addWeightedFeatures(accumulator, windowFeatures(profile.longProfile, "90d", featureType), mode === "explore" ? 0.75 : 0.55);

  if (mode === "personalized") {
    if (featureType === "sources") {
      addWeightedFeatures(accumulator, directionalFeatures(profile.sourceProfile, "positive"), 0.7);
    } else if (featureType === "targets") {
      addWeightedFeatures(accumulator, directionalFeatures(profile.targetProfile, "positive"), 0.8);
    } else if (featureType === "authors") {
      addWeightedFeatures(accumulator, directionalFeatures(profile.authorProfile, "positive"), 0.65);
    }
  }

  return rankedProfileFeatures(accumulator, maxFeatures);
}

function aggregateNegativeFeatures(profile: UserFeedProfile, featureType: FeedProfileFeatureType, maxFeatures: number) {
  const accumulator = new Map<string, number>();
  addWeightedFeatures(accumulator, windowFeatures(profile.negativeProfile, "30d", featureType), 1);
  if (featureType === "sources") {
    addWeightedFeatures(accumulator, directionalFeatures(profile.sourceProfile, "negative"), 1.1);
  } else if (featureType === "targets") {
    addWeightedFeatures(accumulator, directionalFeatures(profile.targetProfile, "negative"), 1.1);
  } else if (featureType === "authors") {
    addWeightedFeatures(accumulator, directionalFeatures(profile.authorProfile, "negative"), 1.1);
  }
  return rankedProfileFeatures(accumulator, maxFeatures);
}

function profileFeatureClause(featureType: FeedProfileFeatureType, value: string) {
  switch (featureType) {
    case "categories":
      return {
        bool: {
          should: [{ term: { category: value } }, { term: { tags: value } }],
          minimum_should_match: 1,
        },
      };
    case "tags":
      return { term: { tags: value } };
    case "sources":
      return { term: { source: value } };
    case "targets":
      return { term: { target: value } };
    case "authors":
      return {
        bool: {
          should: [
            { term: { author: value } },
            { match_phrase: { fullname: value } },
          ],
          minimum_should_match: 1,
        },
      };
  }
}

function addProfileBoostFunctions(
  functions: unknown[],
  profile: UserFeedProfile,
  mode: "personalized" | "explore",
) {
  if (!profile.hasProfile || profile.eventCount === 0) {
    return;
  }

  const confidenceBoost = mode === "personalized" ? 0.85 + profile.confidence * 0.45 : 0.7 + profile.confidence * 0.25;
  const definitions: Array<{ type: FeedProfileFeatureType; max: number; baseWeight: number }> =
    mode === "personalized"
      ? [
          { type: "categories", max: 8, baseWeight: 2.1 },
          { type: "tags", max: 14, baseWeight: 1.75 },
          { type: "sources", max: 5, baseWeight: 1.15 },
          { type: "targets", max: 8, baseWeight: 1.45 },
          { type: "authors", max: 8, baseWeight: 1.15 },
        ]
      : [
          { type: "categories", max: 5, baseWeight: 0.75 },
          { type: "tags", max: 8, baseWeight: 0.65 },
          { type: "sources", max: 3, baseWeight: 0.35 },
          { type: "targets", max: 3, baseWeight: 0.3 },
          { type: "authors", max: 0, baseWeight: 0 },
        ];

  for (const definition of definitions) {
    if (definition.max <= 0) {
      continue;
    }
    for (const feature of aggregatePositiveFeatures(profile, definition.type, mode, definition.max)) {
      functions.push({
        filter: profileFeatureClause(definition.type, feature.value),
        weight: Number((definition.baseWeight * confidenceBoost * feature.weight).toFixed(3)),
      });
    }
  }
}

function negativeProfileClauses(profile: UserFeedProfile) {
  if (!profile.hasProfile || profile.eventCount === 0) {
    return [];
  }

  const definitions: Array<{ type: FeedProfileFeatureType; max: number }> = [
    { type: "categories", max: 6 },
    { type: "tags", max: 12 },
    { type: "sources", max: 5 },
    { type: "targets", max: 8 },
    { type: "authors", max: 8 },
  ];

  return definitions.flatMap((definition) =>
    aggregateNegativeFeatures(profile, definition.type, definition.max).map((feature) =>
      profileFeatureClause(definition.type, feature.value),
    ),
  );
}

function rankingFunctions(profile: UserFeedProfile, mode: "personalized" | "explore") {
  const functions: unknown[] = [
    {
      exp: {
        sort_at: {
          origin: "now",
          scale: mode === "explore" ? "72h" : "36h",
          decay: 0.5,
        },
      },
      weight: mode === "explore" ? 2.4 : 3,
    },
    {
      field_value_factor: {
        field: "quality_score",
        modifier: "log1p",
        factor: mode === "explore" ? 1.25 : 1.1,
        missing: 0,
      },
      weight: mode === "explore" ? 2.25 : 1.8,
    },
  ];

  if (mode === "explore") {
    functions.push(
      {
        filter: { range: { impressions: { lte: 2 } } },
        weight: 0.85,
      },
      {
        filter: { range: { plays: { lte: 1 } } },
        weight: 0.45,
      },
    );
  }

  addProfileBoostFunctions(functions, profile, mode);
  return functions;
}

function candidateQuery(filter: unknown[], mustNot: unknown[], profile: UserFeedProfile) {
  const positive = {
    constant_score: {
      filter: {
        bool: {
          filter,
          must_not: mustNot,
        },
      },
      boost: 1,
    },
  };
  const negativeClauses = negativeProfileClauses(profile);
  if (negativeClauses.length === 0) {
    return positive;
  }

  return {
    boosting: {
      positive,
      negative: {
        bool: {
          should: negativeClauses,
          minimum_should_match: 1,
        },
      },
      negative_boost: Number(clamp(0.62 - profile.confidence * 0.25, 0.32, 0.62).toFixed(3)),
    },
  };
}

function buildFeedQuery(input: {
  size: number;
  source: VideoFeedQuery["source"];
  subscribedTargetIds: string[];
  seenIds: string[];
  seenGuids: string[];
  seenVideoKeys: string[];
  categoryFilters: string[];
  tagFilters: string[];
  keyword: string | null;
  profile: UserFeedProfile;
  mode: "personalized" | "explore";
}) {
  const filter: unknown[] = [
    { term: { has_video: true } },
    { range: { expires_at: { gt: "now" } } },
    {
      bool: {
        should: [
          { bool: { must_not: [{ terms: { source: VIDEO_SOURCE_VALUES } }] } },
          { range: { video_url_expires_at: { gt: "now+10m" } } },
        ],
        minimum_should_match: 1,
      },
    },
    ...sourceFilter(input.source, input.subscribedTargetIds),
  ];

  const tagTerms = terms("tags", input.tagFilters);
  if (tagTerms) {
    filter.push(tagTerms);
  }

  if (input.keyword) {
    const wildcardKeyword = `*${escapeOpenSearchWildcard(input.keyword.toLowerCase())}*`;
    filter.push({
      bool: {
        should: [
          { match_phrase: { title: input.keyword } },
          { match_phrase: { content: input.keyword } },
          { wildcard: { title: { value: wildcardKeyword } } },
          { wildcard: { content: { value: wildcardKeyword } } },
          { wildcard: { author: { value: wildcardKeyword } } },
        ],
        minimum_should_match: 1,
      },
    });
  }

  if (input.categoryFilters.length > 0) {
    filter.push({
      bool: {
        should: [
          { terms: { category: input.categoryFilters } },
          { terms: { tags: input.categoryFilters } },
        ],
        minimum_should_match: 1,
      },
    });
  }

  const mustNot: unknown[] = [];
  const seenIdTerms = terms("id", input.seenIds);
  if (seenIdTerms) {
    mustNot.push(seenIdTerms);
  }
  const seenGuidTerms = terms("guid", input.seenGuids);
  if (seenGuidTerms) {
    mustNot.push(seenGuidTerms);
  }
  const seenVideoKeyTerms = terms("video_key", input.seenVideoKeys);
  if (seenVideoKeyTerms) {
    mustNot.push(seenVideoKeyTerms);
  }

  const functions = rankingFunctions(input.profile, input.mode);

  return {
    size: input.size,
    track_total_hits: false,
    query: {
      function_score: {
        query: candidateQuery(filter, mustNot, input.profile),
        functions,
        score_mode: "sum",
        boost_mode: "multiply",
      },
    },
    sort: [
      { _score: { order: "desc" } },
      { sort_at: { order: "desc", unmapped_type: "date" } },
      { stored_at: { order: "desc", unmapped_type: "date" } },
      { _id: { order: "desc" } },
    ],
  };
}

function getHits(response: OpenSearchSearchResponse) {
  return response.body?.hits?.hits ?? response.hits?.hits ?? [];
}

function rowsFromResponse(response: OpenSearchSearchResponse) {
  return getHits(response)
    .map((hit) => (hit._source ? toRow(hit._source) : null))
    .filter((row): row is OpenSearchFeedRow => row !== null);
}

function uniqueCandidates(candidates: OpenSearchFeedRow[]) {
  const result: OpenSearchFeedRow[] = [];
  const seenIds = new Set<string>();
  const seenGuids = new Set<string>();
  const seenVideoKeys = new Set<string>();

  for (const candidate of candidates) {
    const guidKey = normalizeKey(candidate.guid);
    const videoKey = normalizeKey(candidate.videoKey);
    if (seenIds.has(candidate.id) || (guidKey && seenGuids.has(guidKey)) || (videoKey && seenVideoKeys.has(videoKey))) {
      continue;
    }

    result.push(candidate);
    seenIds.add(candidate.id);
    if (guidKey) {
      seenGuids.add(guidKey);
    }
    if (videoKey) {
      seenVideoKeys.add(videoKey);
    }
  }

  return result;
}

function selectFeedItems(input: {
  personalizedCandidates: OpenSearchFeedRow[];
  exploreCandidates: OpenSearchFeedRow[];
  profile: UserFeedProfile;
  limit: number;
  previousLastAuthor?: string | null;
  previousLastTarget?: string | null;
}) {
  const exploreRatio = input.profile.hasProfile
    ? input.profile.exploreRatio
    : Math.max(input.profile.exploreRatio, EMPTY_PROFILE.exploreRatio);
  const exploreTarget = Math.min(Math.max(Math.round(input.limit * exploreRatio), input.limit >= 8 ? 1 : 0), Math.ceil(input.limit * 0.4));

  const personalizedLimit = Math.max(input.limit - exploreTarget, 0);
  let selected = selectDiverseVideoItems({
    candidates: uniqueCandidates(input.personalizedCandidates),
    limit: personalizedLimit,
    previousLastAuthor: input.previousLastAuthor,
    previousLastTarget: input.previousLastTarget,
    enforceLimits: true,
    enforceConsecutive: true,
  });

  selected = selectDiverseVideoItems({
    selected,
    candidates: uniqueCandidates(input.exploreCandidates),
    limit: input.limit,
    previousLastAuthor: input.previousLastAuthor,
    previousLastTarget: input.previousLastTarget,
    enforceLimits: true,
    enforceConsecutive: true,
  });

  selected = selectDiverseVideoItems({
    selected,
    candidates: uniqueCandidates(input.personalizedCandidates),
    limit: input.limit,
    previousLastAuthor: input.previousLastAuthor,
    previousLastTarget: input.previousLastTarget,
    enforceLimits: false,
    enforceConsecutive: true,
  });

  selected = selectDiverseVideoItems({
    selected,
    candidates: uniqueCandidates([...input.exploreCandidates, ...input.personalizedCandidates]),
    limit: input.limit,
    previousLastAuthor: input.previousLastAuthor,
    previousLastTarget: input.previousLastTarget,
    enforceLimits: false,
    enforceConsecutive: false,
  });

  return selected.slice(0, input.limit);
}

export async function listVideoFeedFromOpenSearch(query: VideoFeedQuery) {
  const client = getOpenSearchClient();
  if (!client) {
    throw new Error("OpenSearch is not configured.");
  }

  const limit = normalizeLimit(query.limit, { defaultLimit: 10, maxLimit: 20 });
  const cursor = decodeCursor(query.cursor, isVideoFeedCursor);
  const normalizedTags = normalizedUnique([...(query.tags ?? []), query.tag]);
  const normalizedCategories = normalizedUnique([...(query.categories ?? []), query.category]);
  const keyword = normalizeVideoFeedKeyword(query.keyword);
  const source = query.source ?? "mixed";
  const seenIds = compactVideoFeedCursorSeenValues(cursor?.seenIds ?? []);
  const seenGuids = compactVideoFeedCursorSeenValues(cursor?.seenGuids ?? []);
  const cursorSeenVideoKeys = compactVideoFeedCursorSeenValues(cursor?.seenVideoKeys ?? []);

  const [subscribedTargetIds, recentSeenVideoKeys, profile, categoryFilters] = await Promise.all([
    getSubscribedTargetIds(query.clientId),
    getRecentSeenVideoKeys(query.clientId),
    getUserFeedProfile(query.clientId),
    normalizeCategoryFilters(normalizedCategories),
  ]);
  const seenVideoKeys = combineSeenVideoKeys(recentSeenVideoKeys, cursorSeenVideoKeys);

  const personalizedSize = Math.max(limit * 8, 80);
  const exploreSize = Math.max(Math.ceil(limit * Math.max(profile.exploreRatio, 0.2) * 8), 24);
  const baseInput = {
    source,
    subscribedTargetIds,
    seenIds,
    seenGuids,
    seenVideoKeys,
    categoryFilters,
    tagFilters: normalizedTags,
    keyword,
    profile,
  };
  const personalizedBody = buildFeedQuery({
    ...baseInput,
    size: personalizedSize,
    mode: "personalized",
  });
  const exploreBody = buildFeedQuery({
    ...baseInput,
    size: exploreSize,
    mode: "explore",
  });

  const personalizedSearchParams = {
    index: getOpenSearchItemsIndex(),
    body: personalizedBody,
  } as unknown as Parameters<typeof client.search>[0];
  const exploreSearchParams = {
    index: getOpenSearchItemsIndex(),
    body: exploreBody,
  } as unknown as Parameters<typeof client.search>[0];
  const [personalizedResponse, exploreResponse] = (await Promise.all([
    client.search(personalizedSearchParams),
    client.search(exploreSearchParams),
  ])) as unknown as [OpenSearchSearchResponse, OpenSearchSearchResponse];

  const items = selectFeedItems({
    personalizedCandidates: rowsFromResponse(personalizedResponse),
    exploreCandidates: rowsFromResponse(exploreResponse),
    profile,
    limit,
    previousLastAuthor: cursor?.lastAuthor,
    previousLastTarget: cursor?.lastTarget,
  });
  const nextCursorPayload = buildVideoFeedNextCursorPayload({
    seenIds,
    seenGuids,
    seenVideoKeys: cursorSeenVideoKeys,
    items,
  });
  const nextCursor = nextCursorPayload ? encodeCursor(nextCursorPayload) : null;

  return {
    items: items.map(({ guid: _guid, sortTime: _sortTime, ...item }) => ({
      ...item,
      ...resolveAuthorPresentation(item),
    })) as VideoFeedItem[],
    pagination: {
      limit,
      nextCursor,
      hasMore: items.length === limit,
    },
  };
}
