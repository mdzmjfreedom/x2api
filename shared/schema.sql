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
    CONSTRAINT targets_source_check CHECK (source IN ('twitter', 'youtube', 'heiliao', 'cg91', 'baoliao51', 'douyin', '18mh', 'rou', 'dadaafa', '18j', '1mtif', 'tikporn', '91porna', '91porn', '91rb', 'badnews', 'bdrq', 'avgood', '705hs', 'xxxtik', 'affair', 'attach', 'dirtyship', 'influencersgonewild', 'missav')),
    CONSTRAINT targets_kind_check CHECK (kind IN ('user', 'keyword', 'channel', 'site')),
    CONSTRAINT targets_youtube_kind_check CHECK (source <> 'youtube' OR kind = 'channel'),
    CONSTRAINT targets_heiliao_kind_check CHECK (source <> 'heiliao' OR kind = 'site'),
    CONSTRAINT targets_cg91_kind_check CHECK (source <> 'cg91' OR kind = 'site'),
    CONSTRAINT targets_baoliao51_kind_check CHECK (source <> 'baoliao51' OR kind = 'site'),
    CONSTRAINT targets_douyin_kind_check CHECK (source <> 'douyin' OR kind = 'site'),
    CONSTRAINT targets_18mh_kind_check CHECK (source <> '18mh' OR kind = 'site'),
    CONSTRAINT targets_rou_kind_check CHECK (source <> 'rou' OR kind = 'site'),
    CONSTRAINT targets_dadaafa_kind_check CHECK (source <> 'dadaafa' OR kind = 'site'),
    CONSTRAINT targets_18j_kind_check CHECK (source <> '18j' OR kind = 'site'),
    CONSTRAINT targets_1mtif_kind_check CHECK (source <> '1mtif' OR kind = 'site'),
    CONSTRAINT targets_tikporn_kind_check CHECK (source <> 'tikporn' OR kind = 'site'),
    CONSTRAINT targets_91porna_kind_check CHECK (source <> '91porna' OR kind = 'site'),
    CONSTRAINT targets_91porn_kind_check CHECK (source <> '91porn' OR kind = 'site'),
    CONSTRAINT targets_badnews_kind_check CHECK (source <> 'badnews' OR kind = 'site'),
    CONSTRAINT targets_bdrq_kind_check CHECK (source <> 'bdrq' OR kind = 'site'),
    CONSTRAINT targets_avgood_kind_check CHECK (source <> 'avgood' OR kind = 'site'),
    CONSTRAINT targets_705hs_kind_check CHECK (source <> '705hs' OR kind = 'site'),
    CONSTRAINT targets_xxxtik_kind_check CHECK (source <> 'xxxtik' OR kind = 'site'),
    CONSTRAINT targets_affair_kind_check CHECK (source <> 'affair' OR kind = 'site'),
    CONSTRAINT targets_attach_kind_check CHECK (source <> 'attach' OR kind = 'site'),
    CONSTRAINT targets_dirtyship_kind_check CHECK (source <> 'dirtyship' OR kind = 'site'),
    CONSTRAINT targets_influencersgonewild_kind_check CHECK (source <> 'influencersgonewild' OR kind = 'site'),
    CONSTRAINT targets_missav_kind_check CHECK (source <> 'missav' OR kind = 'site'),
    CONSTRAINT targets_unique_normalized UNIQUE (source, kind, normalized_value)
);

ALTER TABLE targets ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'twitter';
ALTER TABLE targets DROP CONSTRAINT IF EXISTS targets_unique_normalized;
ALTER TABLE targets DROP CONSTRAINT IF EXISTS targets_kind_check;
ALTER TABLE targets DROP CONSTRAINT IF EXISTS targets_source_check;
ALTER TABLE targets DROP CONSTRAINT IF EXISTS targets_youtube_kind_check;
ALTER TABLE targets DROP CONSTRAINT IF EXISTS targets_heiliao_kind_check;
ALTER TABLE targets DROP CONSTRAINT IF EXISTS targets_cg91_kind_check;
ALTER TABLE targets DROP CONSTRAINT IF EXISTS targets_baoliao51_kind_check;
ALTER TABLE targets DROP CONSTRAINT IF EXISTS targets_douyin_kind_check;
ALTER TABLE targets DROP CONSTRAINT IF EXISTS targets_18mh_kind_check;
ALTER TABLE targets DROP CONSTRAINT IF EXISTS targets_rou_kind_check;
ALTER TABLE targets DROP CONSTRAINT IF EXISTS targets_dadaafa_kind_check;
ALTER TABLE targets DROP CONSTRAINT IF EXISTS targets_18j_kind_check;
ALTER TABLE targets DROP CONSTRAINT IF EXISTS targets_1mtif_kind_check;
ALTER TABLE targets DROP CONSTRAINT IF EXISTS targets_tikporn_kind_check;
ALTER TABLE targets DROP CONSTRAINT IF EXISTS targets_91porna_kind_check;
ALTER TABLE targets DROP CONSTRAINT IF EXISTS targets_91porn_kind_check;
ALTER TABLE targets DROP CONSTRAINT IF EXISTS targets_badnews_kind_check;
ALTER TABLE targets DROP CONSTRAINT IF EXISTS targets_bdrq_kind_check;
ALTER TABLE targets DROP CONSTRAINT IF EXISTS targets_avgood_kind_check;
ALTER TABLE targets DROP CONSTRAINT IF EXISTS targets_705hs_kind_check;
ALTER TABLE targets DROP CONSTRAINT IF EXISTS targets_xxxtik_kind_check;
ALTER TABLE targets DROP CONSTRAINT IF EXISTS targets_affair_kind_check;
ALTER TABLE targets DROP CONSTRAINT IF EXISTS targets_attach_kind_check;
ALTER TABLE targets DROP CONSTRAINT IF EXISTS targets_dirtyship_kind_check;
ALTER TABLE targets DROP CONSTRAINT IF EXISTS targets_influencersgonewild_kind_check;
ALTER TABLE targets DROP CONSTRAINT IF EXISTS targets_missav_kind_check;
ALTER TABLE targets ADD CONSTRAINT targets_source_check CHECK (source IN ('twitter', 'youtube', 'heiliao', 'cg91', 'baoliao51', 'douyin', '18mh', 'rou', 'dadaafa', '18j', '1mtif', 'tikporn', '91porna', '91porn', '91rb', 'badnews', 'bdrq', 'avgood', '705hs', 'xxxtik', 'affair', 'attach', 'dirtyship', 'influencersgonewild', 'missav'));
ALTER TABLE targets ADD CONSTRAINT targets_kind_check CHECK (kind IN ('user', 'keyword', 'channel', 'site'));
ALTER TABLE targets ADD CONSTRAINT targets_youtube_kind_check CHECK (source <> 'youtube' OR kind = 'channel');
ALTER TABLE targets ADD CONSTRAINT targets_heiliao_kind_check CHECK (source <> 'heiliao' OR kind = 'site');
ALTER TABLE targets ADD CONSTRAINT targets_cg91_kind_check CHECK (source <> 'cg91' OR kind = 'site');
ALTER TABLE targets ADD CONSTRAINT targets_baoliao51_kind_check CHECK (source <> 'baoliao51' OR kind = 'site');
ALTER TABLE targets ADD CONSTRAINT targets_douyin_kind_check CHECK (source <> 'douyin' OR kind = 'site');
ALTER TABLE targets ADD CONSTRAINT targets_18mh_kind_check CHECK (source <> '18mh' OR kind = 'site');
ALTER TABLE targets ADD CONSTRAINT targets_rou_kind_check CHECK (source <> 'rou' OR kind = 'site');
ALTER TABLE targets ADD CONSTRAINT targets_dadaafa_kind_check CHECK (source <> 'dadaafa' OR kind = 'site');
ALTER TABLE targets ADD CONSTRAINT targets_18j_kind_check CHECK (source <> '18j' OR kind = 'site');
ALTER TABLE targets ADD CONSTRAINT targets_1mtif_kind_check CHECK (source <> '1mtif' OR kind = 'site');
ALTER TABLE targets ADD CONSTRAINT targets_tikporn_kind_check CHECK (source <> 'tikporn' OR kind = 'site');
ALTER TABLE targets ADD CONSTRAINT targets_91porna_kind_check CHECK (source <> '91porna' OR kind = 'site');
ALTER TABLE targets ADD CONSTRAINT targets_91porn_kind_check CHECK (source <> '91porn' OR kind = 'site');
ALTER TABLE targets ADD CONSTRAINT targets_badnews_kind_check CHECK (source <> 'badnews' OR kind = 'site');
ALTER TABLE targets ADD CONSTRAINT targets_bdrq_kind_check CHECK (source <> 'bdrq' OR kind = 'site');
ALTER TABLE targets ADD CONSTRAINT targets_avgood_kind_check CHECK (source <> 'avgood' OR kind = 'site');
ALTER TABLE targets ADD CONSTRAINT targets_705hs_kind_check CHECK (source <> '705hs' OR kind = 'site');
ALTER TABLE targets ADD CONSTRAINT targets_xxxtik_kind_check CHECK (source <> 'xxxtik' OR kind = 'site');
ALTER TABLE targets ADD CONSTRAINT targets_affair_kind_check CHECK (source <> 'affair' OR kind = 'site');
ALTER TABLE targets ADD CONSTRAINT targets_attach_kind_check CHECK (source <> 'attach' OR kind = 'site');
ALTER TABLE targets ADD CONSTRAINT targets_dirtyship_kind_check CHECK (source <> 'dirtyship' OR kind = 'site');
ALTER TABLE targets ADD CONSTRAINT targets_influencersgonewild_kind_check CHECK (source <> 'influencersgonewild' OR kind = 'site');
ALTER TABLE targets ADD CONSTRAINT targets_missav_kind_check CHECK (source <> 'missav' OR kind = 'site');
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
    display_author TEXT,
    display_handle TEXT,
    author_profile_url TEXT,
    author_profile_platform TEXT,
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
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_retweet BOOLEAN NOT NULL DEFAULT FALSE,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (target_id, guid)
);

ALTER TABLE items ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ NOT NULL DEFAULT '2099-12-31 23:59:59+00';
ALTER TABLE items ADD COLUMN IF NOT EXISTS video_url_expires_at TIMESTAMPTZ NOT NULL DEFAULT '2099-12-31 23:59:59+00';
ALTER TABLE items ADD COLUMN IF NOT EXISTS display_author TEXT;
ALTER TABLE items ADD COLUMN IF NOT EXISTS display_handle TEXT;
ALTER TABLE items ADD COLUMN IF NOT EXISTS author_profile_url TEXT;
ALTER TABLE items ADD COLUMN IF NOT EXISTS author_profile_platform TEXT;
ALTER TABLE items ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;
UPDATE items
SET updated_at = COALESCE(updated_at, stored_at, NOW())
WHERE updated_at IS NULL;
ALTER TABLE items ALTER COLUMN updated_at SET DEFAULT NOW();
ALTER TABLE items ALTER COLUMN updated_at SET NOT NULL;

CREATE OR REPLACE FUNCTION x2_twitter_username(raw_value TEXT)
RETURNS TEXT AS $$
DECLARE
    username TEXT;
BEGIN
    username := BTRIM(COALESCE(raw_value, ''));
    IF username = '' THEN
        RETURN NULL;
    END IF;

    username := regexp_replace(username, '^https?://(www\.)?(twitter\.com|x\.com)/', '', 'i');
    username := regexp_replace(username, '^@+', '');
    username := BTRIM(username, '@/ ');
    username := split_part(username, '/', 1);

    IF username ~ '^[A-Za-z0-9_]{1,15}$' THEN
        RETURN username;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

CREATE OR REPLACE FUNCTION x2_youtube_profile_url(raw_value TEXT)
RETURNS TEXT AS $$
DECLARE
    raw TEXT;
    match TEXT[];
BEGIN
    raw := BTRIM(COALESCE(raw_value, ''));
    IF raw = '' THEN
        RETURN NULL;
    END IF;

    raw := regexp_replace(raw, '^youtube:', '', 'i');

    match := regexp_match(raw, '[?&]channel_id=([^&#]+)', 'i');
    IF match IS NOT NULL THEN
        RETURN 'https://www.youtube.com/channel/' || match[1];
    END IF;

    IF raw ~ '^UC[A-Za-z0-9_-]+$' THEN
        RETURN 'https://www.youtube.com/channel/' || raw;
    END IF;

    IF raw ~ '^@[A-Za-z0-9_.-]+$' THEN
        RETURN 'https://www.youtube.com/' || raw;
    END IF;

    match := regexp_match(raw, 'youtube\.com/channel/([^/?#]+)', 'i');
    IF match IS NOT NULL THEN
        RETURN 'https://www.youtube.com/channel/' || match[1];
    END IF;

    match := regexp_match(raw, '[?&]user=([^&#]+)', 'i');
    IF match IS NOT NULL THEN
        RETURN 'https://www.youtube.com/user/' || match[1];
    END IF;

    match := regexp_match(raw, '[?&]playlist_id=([^&#]+)', 'i');
    IF match IS NOT NULL THEN
        RETURN 'https://www.youtube.com/playlist?list=' || match[1];
    END IF;

    match := regexp_match(raw, 'youtube\.com/(user|c)/([^/?#]+)', 'i');
    IF match IS NOT NULL THEN
        RETURN 'https://www.youtube.com/' || lower(match[1]) || '/' || match[2];
    END IF;

    match := regexp_match(raw, 'youtube\.com/(@[^/?#]+)', 'i');
    IF match IS NOT NULL THEN
        RETURN 'https://www.youtube.com/' || match[1];
    END IF;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

CREATE OR REPLACE FUNCTION x2_http_url(raw_value TEXT)
RETURNS TEXT AS $$
DECLARE
    raw TEXT;
BEGIN
    raw := BTRIM(COALESCE(raw_value, ''));
    IF raw = '' OR raw ~ '\s' THEN
        RETURN NULL;
    END IF;

    IF raw ~* '^https?://[^[:space:]]+$' THEN
        RETURN raw;
    END IF;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

CREATE OR REPLACE FUNCTION x2_douyin_detail_url(base_value TEXT, video_id_value TEXT)
RETURNS TEXT AS $$
DECLARE
    base_url TEXT;
    video_id TEXT;
BEGIN
    base_url := regexp_replace(BTRIM(COALESCE(base_value, '')), '/+$', '');
    video_id := BTRIM(COALESCE(video_id_value, ''));
    IF base_url = '' OR video_id !~ '^[A-Za-z0-9_-]+$' THEN
        RETURN NULL;
    END IF;

    RETURN base_url || '/recommend/?id=' || video_id;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

CREATE OR REPLACE FUNCTION x2_normalized_source(source_value TEXT)
RETURNS TEXT AS $$
DECLARE
    source_key TEXT;
BEGIN
    source_key := lower(BTRIM(COALESCE(source_value, '')));
    CASE source_key
        WHEN 'x' THEN RETURN 'twitter';
        WHEN 'twitter' THEN RETURN 'twitter';
        WHEN 'yt' THEN RETURN 'youtube';
        WHEN 'youtube' THEN RETURN 'youtube';
        WHEN '91' THEN RETURN 'cg91';
        WHEN 'cg91' THEN RETURN 'cg91';
        WHEN '51' THEN RETURN 'baoliao51';
        WHEN 'baoliao51' THEN RETURN 'baoliao51';
        WHEN '18mh' THEN RETURN '18mh';
        WHEN 'mh18' THEN RETURN '18mh';
        WHEN 'rou' THEN RETURN 'rou';
        WHEN 'rouvideo' THEN RETURN 'rou';
        WHEN 'rou.video' THEN RETURN 'rou';
        WHEN 'dada' THEN RETURN 'dadaafa';
        WHEN 'dadaafa' THEN RETURN 'dadaafa';
        WHEN '18j' THEN RETURN '18j';
        WHEN '18j.tv' THEN RETURN '18j';
        WHEN 'j18' THEN RETURN '18j';
        WHEN '1mtif' THEN RETURN '1mtif';
        WHEN 'mtif' THEN RETURN '1mtif';
        WHEN '1mtif.sbs' THEN RETURN '1mtif';
        WHEN 'tik' THEN RETURN 'tikporn';
        WHEN 'tikporn' THEN RETURN 'tikporn';
        WHEN 'tik.porn' THEN RETURN 'tikporn';
        WHEN '91porna' THEN RETURN '91porna';
        WHEN 'porna91' THEN RETURN '91porna';
        WHEN '91porn' THEN RETURN '91porn';
        WHEN '91porn.com' THEN RETURN '91porn';
        WHEN 'badnews' THEN RETURN 'badnews';
        WHEN 'bad.news' THEN RETURN 'badnews';
        WHEN 'bdrq' THEN RETURN 'bdrq';
        WHEN 'bdrq45' THEN RETURN 'bdrq';
        WHEN 'bdrq45.cc' THEN RETURN 'bdrq';
        WHEN 'bdrq12' THEN RETURN 'bdrq';
        WHEN 'bdrq12.cc' THEN RETURN 'bdrq';
        WHEN 'avgood' THEN RETURN 'avgood';
        WHEN 'avgood.com' THEN RETURN 'avgood';
        WHEN '705hs' THEN RETURN '705hs';
        WHEN '705hs.com' THEN RETURN '705hs';
        WHEN '992kp' THEN RETURN '705hs';
        WHEN '992kp.com' THEN RETURN '705hs';
        WHEN 'xxxtik' THEN RETURN 'xxxtik';
        WHEN 'xxxtik.com' THEN RETURN 'xxxtik';
        WHEN 'affair' THEN RETURN 'affair';
        WHEN '911bl' THEN RETURN 'affair';
        WHEN '911bl.com' THEN RETURN 'affair';
        WHEN 'affair.zhkrsawaw.cc' THEN RETURN 'affair';
        WHEN 'attach' THEN RETURN 'attach';
        WHEN 'attach.bslqmdvk.cc' THEN RETURN 'attach';
        WHEN 'hlcgw' THEN RETURN 'attach';
        WHEN 'hlcgw.com' THEN RETURN 'attach';
        WHEN 'dirtyship' THEN RETURN 'dirtyship';
        WHEN 'dirtyship.com' THEN RETURN 'dirtyship';
        WHEN 'influencersgonewild' THEN RETURN 'influencersgonewild';
        WHEN 'influencersgonewild.com' THEN RETURN 'influencersgonewild';
        WHEN 'igw' THEN RETURN 'influencersgonewild';
        WHEN 'missav' THEN RETURN 'missav';
        WHEN 'missav.app' THEN RETURN 'missav';
        ELSE RETURN source_key;
    END CASE;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

CREATE OR REPLACE FUNCTION x2_source_display_name(source_value TEXT)
RETURNS TEXT AS $$
DECLARE
    source_key TEXT;
BEGIN
    source_key := x2_normalized_source(source_value);
    CASE source_key
        WHEN 'twitter' THEN RETURN 'X';
        WHEN 'youtube' THEN RETURN 'YouTube';
        WHEN 'heiliao' THEN RETURN '黑料';
        WHEN 'cg91' THEN RETURN '91吃瓜';
        WHEN 'baoliao51' THEN RETURN '51爆料';
        WHEN 'douyin' THEN RETURN '抖阴';
        WHEN '18mh' THEN RETURN '禁漫天堂';
        WHEN 'rou' THEN RETURN '肉視頻';
        WHEN 'dadaafa' THEN RETURN 'DadaAFA';
        WHEN '18j' THEN RETURN '18J.TV';
        WHEN '1mtif' THEN RETURN '蜜桃视频';
        WHEN 'tikporn' THEN RETURN 'Tik.Porn';
        WHEN '91porna' THEN RETURN '91porna';
        WHEN '91porn' THEN RETURN '91porn';
        WHEN 'badnews' THEN RETURN 'Bad.news';
        WHEN 'bdrq' THEN RETURN '背德人妻';
        WHEN 'avgood' THEN RETURN 'AvGood';
        WHEN '705hs' THEN RETURN '992KP';
        WHEN 'xxxtik' THEN RETURN 'xxxtik';
        WHEN 'affair' THEN RETURN '911爆料';
        WHEN 'attach' THEN RETURN '黑料吃瓜网';
        WHEN 'dirtyship' THEN RETURN 'DirtyShip';
        WHEN 'influencersgonewild' THEN RETURN 'InfluencersGoneWild';
        WHEN 'missav' THEN RETURN 'MISSAV';
        ELSE RETURN COALESCE(NULLIF(source_key, ''), 'X');
    END CASE;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

CREATE OR REPLACE FUNCTION x2_set_item_author_presentation()
RETURNS TRIGGER AS $$
DECLARE
    target_source TEXT;
    target_kind TEXT;
    target_value TEXT;
    username TEXT;
    profile_url TEXT;
BEGIN
    SELECT source, kind, value
    INTO target_source, target_kind, target_value
    FROM targets
    WHERE id = NEW.target_id;

    target_source := x2_normalized_source(target_source);

    NEW.display_author := COALESCE(
        NULLIF(BTRIM(COALESCE(NEW.fullname, '')), ''),
        NULLIF(BTRIM(COALESCE(NEW.author, '')), ''),
        NULLIF(BTRIM(COALESCE(target_value, '')), ''),
        x2_source_display_name(target_source)
    );

    IF target_source = 'twitter' THEN
        username := COALESCE(
            x2_twitter_username(NEW.author),
            x2_twitter_username(target_value),
            x2_twitter_username(NEW.x_url),
            x2_twitter_username(NEW.link)
        );
        NEW.display_handle := CASE
            WHEN NULLIF(BTRIM(COALESCE(NEW.fullname, '')), '') IS NOT NULL AND username IS NOT NULL
            THEN '@' || username
            ELSE NULL
        END;
        NEW.author_profile_url := CASE WHEN username IS NOT NULL THEN 'https://x.com/' || username ELSE NULL END;
        NEW.author_profile_platform := CASE WHEN username IS NOT NULL THEN 'X' ELSE NULL END;
    ELSIF target_source = 'youtube' THEN
        profile_url := COALESCE(
            x2_youtube_profile_url('youtube:' || COALESCE(target_value, '')),
            x2_youtube_profile_url(NEW.link)
        );
        NEW.display_handle := NULL;
        NEW.author_profile_url := profile_url;
        NEW.author_profile_platform := CASE WHEN profile_url IS NOT NULL THEN 'YouTube' ELSE NULL END;
    ELSIF target_source = 'douyin' THEN
        profile_url := x2_douyin_detail_url(target_value, NEW.metadata->>'douyin_video_id');
        IF profile_url IS NOT NULL THEN
            NEW.link := profile_url;
        ELSE
            profile_url := x2_http_url(NEW.link);
        END IF;
        NEW.display_handle := NULL;
        NEW.author_profile_url := profile_url;
        NEW.author_profile_platform := CASE WHEN profile_url IS NOT NULL THEN x2_source_display_name(target_source) ELSE NULL END;
    ELSIF target_source IN ('heiliao', 'cg91', 'baoliao51', '18mh', 'rou', 'dadaafa', '18j', '1mtif', 'tikporn', '91porna', '91porn', '91rb', 'badnews', 'bdrq', 'avgood', '705hs', 'xxxtik', 'affair', 'attach', 'dirtyship', 'influencersgonewild', 'missav') THEN
        profile_url := x2_http_url(NEW.link);
        NEW.display_handle := NULL;
        NEW.author_profile_url := profile_url;
        NEW.author_profile_platform := CASE WHEN profile_url IS NOT NULL THEN x2_source_display_name(target_source) ELSE NULL END;
    ELSE
        NEW.display_handle := NULL;
        NEW.author_profile_url := NULL;
        NEW.author_profile_platform := NULL;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_items_author_presentation ON items;
CREATE TRIGGER trg_items_author_presentation
BEFORE INSERT OR UPDATE OF target_id, author, fullname, link, x_url
ON items
FOR EACH ROW
EXECUTE FUNCTION x2_set_item_author_presentation();

CREATE INDEX IF NOT EXISTS idx_targets_kind_value ON targets (kind, normalized_value);
CREATE INDEX IF NOT EXISTS idx_targets_source_kind_value ON targets (source, kind, normalized_value);
CREATE INDEX IF NOT EXISTS idx_subscriptions_client_id ON subscriptions (client_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_target_id ON subscriptions (target_id);
CREATE INDEX IF NOT EXISTS idx_items_target_id_stored_at ON items (target_id, stored_at DESC);
CREATE INDEX IF NOT EXISTS idx_items_stored_at ON items (stored_at DESC);
CREATE INDEX IF NOT EXISTS idx_items_updated_at_id ON items (updated_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_items_published_at ON items (published_at DESC);
CREATE INDEX IF NOT EXISTS idx_items_video_feed ON items (stored_at DESC) WHERE video_url IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_items_video_feed_sort_time
    ON items ((COALESCE(published_at, stored_at)) DESC, stored_at DESC, id DESC)
    WHERE video_url IS NOT NULL AND video_url <> '';
CREATE INDEX IF NOT EXISTS idx_items_target_video_feed_sort_time
    ON items (target_id, (COALESCE(published_at, stored_at)) DESC, stored_at DESC, id DESC)
    WHERE video_url IS NOT NULL AND video_url <> '';
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

CREATE TABLE IF NOT EXISTS user_feed_profiles (
    client_id UUID PRIMARY KEY REFERENCES clients(id) ON DELETE CASCADE,
    short_profile JSONB NOT NULL DEFAULT '{}'::jsonb,
    long_profile JSONB NOT NULL DEFAULT '{}'::jsonb,
    negative_profile JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_profile JSONB NOT NULL DEFAULT '{}'::jsonb,
    target_profile JSONB NOT NULL DEFAULT '{}'::jsonb,
    author_profile JSONB NOT NULL DEFAULT '{}'::jsonb,
    explore_ratio NUMERIC(4,3) NOT NULL DEFAULT 0.200,
    confidence NUMERIC(4,3) NOT NULL DEFAULT 0,
    event_count INTEGER NOT NULL DEFAULT 0,
    last_event_at TIMESTAMPTZ,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_target_profiles_public_pool ON target_profiles (is_public_pool, weight DESC);
CREATE INDEX IF NOT EXISTS idx_tags_type_weight ON tags (type, weight DESC, name);
CREATE INDEX IF NOT EXISTS idx_item_tags_tag_id ON item_tags (tag_id);
CREATE INDEX IF NOT EXISTS idx_item_tags_item_id ON item_tags (item_id);
CREATE INDEX IF NOT EXISTS idx_feed_events_client_item ON feed_events (client_id, item_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_feed_events_client_recent_video_seen
    ON feed_events (client_id, event_type, created_at DESC, item_id)
    WHERE event_type IN ('impression', 'play', 'finish', 'dislike');
CREATE INDEX IF NOT EXISTS idx_feed_events_client_recent_video_seen_v2
    ON feed_events (client_id, created_at DESC, item_id)
    WHERE event_type IN ('impression', 'play', 'finish', 'like', 'share', 'skip', 'dislike');
CREATE INDEX IF NOT EXISTS idx_feed_events_created_at ON feed_events (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_user_feed_profiles_updated_at ON user_feed_profiles (updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_user_feed_profiles_confidence ON user_feed_profiles (confidence DESC, event_count DESC);

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

DROP TRIGGER IF EXISTS set_items_updated_at ON items;
CREATE TRIGGER set_items_updated_at
BEFORE UPDATE ON items
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS set_categories_updated_at ON categories;
CREATE TRIGGER set_categories_updated_at
BEFORE UPDATE ON categories
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS set_user_feed_profiles_updated_at ON user_feed_profiles;
CREATE TRIGGER set_user_feed_profiles_updated_at
BEFORE UPDATE ON user_feed_profiles
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();
