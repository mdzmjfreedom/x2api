CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS clients (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    api_key TEXT NOT NULL UNIQUE,
    feed_token TEXT NOT NULL UNIQUE,
    label TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    last_seen_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT clients_status_check CHECK (status IN ('active', 'disabled'))
);

CREATE TABLE IF NOT EXISTS targets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source TEXT NOT NULL DEFAULT 'twitter',
    kind TEXT NOT NULL,
    value TEXT NOT NULL,
    normalized_value TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT targets_source_check CHECK (source IN ('twitter', 'youtube')),
    CONSTRAINT targets_kind_check CHECK (kind IN ('user', 'keyword', 'channel')),
    CONSTRAINT targets_youtube_kind_check CHECK (source <> 'youtube' OR kind = 'channel'),
    CONSTRAINT targets_unique_normalized UNIQUE (source, kind, normalized_value)
);

ALTER TABLE targets ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'twitter';
ALTER TABLE targets DROP CONSTRAINT IF EXISTS targets_unique_normalized;
ALTER TABLE targets DROP CONSTRAINT IF EXISTS targets_kind_check;
ALTER TABLE targets DROP CONSTRAINT IF EXISTS targets_source_check;
ALTER TABLE targets DROP CONSTRAINT IF EXISTS targets_youtube_kind_check;
ALTER TABLE targets ADD CONSTRAINT targets_source_check CHECK (source IN ('twitter', 'youtube'));
ALTER TABLE targets ADD CONSTRAINT targets_kind_check CHECK (kind IN ('user', 'keyword', 'channel'));
ALTER TABLE targets ADD CONSTRAINT targets_youtube_kind_check CHECK (source <> 'youtube' OR kind = 'channel');
ALTER TABLE targets ADD CONSTRAINT targets_unique_normalized UNIQUE (source, kind, normalized_value);

CREATE TABLE IF NOT EXISTS subscriptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    target_id UUID NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (client_id, target_id)
);

CREATE TABLE IF NOT EXISTS crawl_state (
    target_id UUID PRIMARY KEY REFERENCES targets(id) ON DELETE CASCADE,
    last_guid TEXT,
    last_checked_at TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,
    last_error TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    target_id UUID NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    guid TEXT NOT NULL,
    author TEXT,
    fullname TEXT,
    title TEXT,
    content TEXT,
    raw_content TEXT,
    translated_content TEXT,
    link TEXT,
    x_url TEXT,
    images JSONB NOT NULL DEFAULT '[]'::jsonb,
    video_url TEXT,
    expires_at TIMESTAMPTZ NOT NULL DEFAULT '2099-12-31 23:59:59+00',
    video_url_expires_at TIMESTAMPTZ NOT NULL DEFAULT '2099-12-31 23:59:59+00',
    published_at TIMESTAMPTZ,
    stored_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_retweet BOOLEAN NOT NULL DEFAULT FALSE,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (target_id, guid)
);

ALTER TABLE items ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ NOT NULL DEFAULT '2099-12-31 23:59:59+00';
ALTER TABLE items ADD COLUMN IF NOT EXISTS video_url_expires_at TIMESTAMPTZ NOT NULL DEFAULT '2099-12-31 23:59:59+00';

CREATE INDEX IF NOT EXISTS idx_targets_kind_value ON targets (kind, normalized_value);
CREATE INDEX IF NOT EXISTS idx_targets_source_kind_value ON targets (source, kind, normalized_value);
CREATE INDEX IF NOT EXISTS idx_subscriptions_client_id ON subscriptions (client_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_target_id ON subscriptions (target_id);
CREATE INDEX IF NOT EXISTS idx_items_target_id_stored_at ON items (target_id, stored_at DESC);
CREATE INDEX IF NOT EXISTS idx_items_stored_at ON items (stored_at DESC);
CREATE INDEX IF NOT EXISTS idx_items_published_at ON items (published_at DESC);
CREATE INDEX IF NOT EXISTS idx_items_video_feed ON items (stored_at DESC) WHERE video_url IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_items_expires_at ON items (expires_at);
CREATE INDEX IF NOT EXISTS idx_items_video_url_expires_at ON items (video_url_expires_at);

CREATE TABLE IF NOT EXISTS video_resolution_queue (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source TEXT NOT NULL,
    target_id UUID NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    guid TEXT NOT NULL,
    provider_video_id TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_error TEXT,
    resolved_item_id UUID REFERENCES items(id) ON DELETE SET NULL,
    expires_at TIMESTAMPTZ NOT NULL DEFAULT '2099-12-31 23:59:59+00',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT video_resolution_queue_source_check CHECK (source IN ('youtube')),
    CONSTRAINT video_resolution_queue_status_check CHECK (status IN ('pending', 'failed', 'resolved')),
    CONSTRAINT video_resolution_queue_unique_target_guid UNIQUE (target_id, guid)
);

CREATE INDEX IF NOT EXISTS idx_video_resolution_queue_status_next_expires
    ON video_resolution_queue (status, next_attempt_at, expires_at);

CREATE TABLE IF NOT EXISTS categories (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    weight INTEGER NOT NULL DEFAULT 0,
    is_sensitive BOOLEAN NOT NULL DEFAULT FALSE,
    default_hidden BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO categories (slug, name, weight, is_sensitive, default_hidden)
VALUES
    ('tech', '科技', 240, FALSE, FALSE),
    ('ai', 'AI', 230, FALSE, FALSE),
    ('news', '新闻', 220, FALSE, FALSE),
    ('war', '军事', 210, FALSE, FALSE),
    ('finance', '金融', 200, FALSE, FALSE),
    ('markets', '财经', 190, FALSE, FALSE),
    ('business', '商业', 180, FALSE, FALSE),
    ('politics', '政治', 170, FALSE, FALSE),
    ('sports', '体育', 160, FALSE, FALSE),
    ('games', '游戏', 150, FALSE, FALSE),
    ('film', '影视', 140, FALSE, FALSE),
    ('music', '音乐', 130, FALSE, FALSE),
    ('funny', '搞笑', 120, FALSE, FALSE),
    ('pets', '宠物', 110, FALSE, FALSE),
    ('life', '生活', 100, FALSE, FALSE),
    ('education', '教育', 90, FALSE, FALSE),
    ('design', '设计', 80, FALSE, FALSE),
    ('travel', '旅行', 70, FALSE, FALSE),
    ('auto', '汽车', 60, FALSE, FALSE),
    ('food', '美食', 50, FALSE, FALSE),
    ('health', '健康', 40, FALSE, FALSE),
    ('fashion', '时尚', 30, FALSE, FALSE),
    ('adult', '成人', 20, TRUE, TRUE),
    ('other', '其他', 0, FALSE, FALSE)
ON CONFLICT (slug) DO UPDATE SET
    name = EXCLUDED.name,
    weight = EXCLUDED.weight,
    is_sensitive = EXCLUDED.is_sensitive,
    default_hidden = EXCLUDED.default_hidden,
    updated_at = NOW();

CREATE TABLE IF NOT EXISTS target_profiles (
    target_id UUID PRIMARY KEY REFERENCES targets(id) ON DELETE CASCADE,
    scope TEXT NOT NULL DEFAULT 'user',
    tags JSONB NOT NULL DEFAULT '[]'::jsonb,
    category TEXT,
    weight INTEGER NOT NULL DEFAULT 0,
    is_public_pool BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT target_profiles_scope_check CHECK (scope IN ('user', 'system'))
);

CREATE TABLE IF NOT EXISTS tags (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL DEFAULT 'topic',
    weight INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT tags_type_check CHECK (type IN ('category', 'topic', 'system'))
);

CREATE TABLE IF NOT EXISTS item_tags (
    item_id UUID NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    tag_id UUID NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    source TEXT NOT NULL DEFAULT 'rule',
    confidence NUMERIC(4,3) NOT NULL DEFAULT 1.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (item_id, tag_id),
    CONSTRAINT item_tags_source_check CHECK (source IN ('target', 'rule', 'manual', 'ai'))
);

CREATE TABLE IF NOT EXISTS video_stats (
    item_id UUID PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    impressions INTEGER NOT NULL DEFAULT 0,
    plays INTEGER NOT NULL DEFAULT 0,
    finishes INTEGER NOT NULL DEFAULT 0,
    likes INTEGER NOT NULL DEFAULT 0,
    dislikes INTEGER NOT NULL DEFAULT 0,
    skips INTEGER NOT NULL DEFAULT 0,
    shares INTEGER NOT NULL DEFAULT 0,
    score NUMERIC(8,3) NOT NULL DEFAULT 0,
    last_event_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS feed_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    item_id UUID NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    watch_ms INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT feed_events_type_check CHECK (
        event_type IN ('impression', 'play', 'finish', 'like', 'dislike', 'skip', 'share')
    )
);

CREATE INDEX IF NOT EXISTS idx_target_profiles_public_pool ON target_profiles (is_public_pool, weight DESC);
CREATE INDEX IF NOT EXISTS idx_tags_type_weight ON tags (type, weight DESC, name);
CREATE INDEX IF NOT EXISTS idx_item_tags_tag_id ON item_tags (tag_id);
CREATE INDEX IF NOT EXISTS idx_feed_events_client_item ON feed_events (client_id, item_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_feed_events_created_at ON feed_events (created_at DESC);

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS set_clients_updated_at ON clients;
CREATE TRIGGER set_clients_updated_at
BEFORE UPDATE ON clients
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS set_targets_updated_at ON targets;
CREATE TRIGGER set_targets_updated_at
BEFORE UPDATE ON targets
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS set_categories_updated_at ON categories;
CREATE TRIGGER set_categories_updated_at
BEFORE UPDATE ON categories
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();
