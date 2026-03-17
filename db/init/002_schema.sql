CREATE TABLE IF NOT EXISTS conversation_messages (
    id BIGSERIAL PRIMARY KEY,
    scope_type TEXT NOT NULL,
    guild_id BIGINT NULL,
    channel_id BIGINT NULL,
    thread_id BIGINT NULL,
    dm_user_id BIGINT NULL,
    author_user_id BIGINT NOT NULL,
    role TEXT NOT NULL,
    discord_message_id BIGINT NULL,
    content TEXT NOT NULL,
    moderation_result_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_conversation_scope_created_at
    ON conversation_messages (scope_type, guild_id, channel_id, thread_id, dm_user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS memory_entries (
    id BIGSERIAL PRIMARY KEY,
    scope_type TEXT NOT NULL,
    guild_id BIGINT NULL,
    channel_id BIGINT NULL,
    thread_id BIGINT NULL,
    dm_user_id BIGINT NULL,
    user_id BIGINT NULL,
    memory_kind TEXT NOT NULL,
    memory_text TEXT NOT NULL,
    embedding vector(1536),
    confidence_score DOUBLE PRECISION NOT NULL DEFAULT 0.5,
    source_message_id BIGINT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_memory_scope_updated_at
    ON memory_entries (scope_type, guild_id, channel_id, thread_id, dm_user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS image_generations (
    id BIGSERIAL PRIMARY KEY,
    scope_type TEXT NOT NULL,
    guild_id BIGINT NULL,
    channel_id BIGINT NULL,
    thread_id BIGINT NULL,
    dm_user_id BIGINT NULL,
    requester_user_id BIGINT NOT NULL,
    prompt TEXT NOT NULL,
    revised_prompt TEXT NULL,
    output_url TEXT NULL,
    model_deployment TEXT NOT NULL,
    moderation_result_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS video_generations (
    id BIGSERIAL PRIMARY KEY,
    scope_type TEXT NOT NULL,
    guild_id BIGINT NULL,
    channel_id BIGINT NULL,
    thread_id BIGINT NULL,
    dm_user_id BIGINT NULL,
    requester_user_id BIGINT NOT NULL,
    prompt TEXT NOT NULL,
    output_url TEXT NULL,
    model_deployment TEXT NOT NULL,
    moderation_result_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS speech_generations (
    id BIGSERIAL PRIMARY KEY,
    scope_type TEXT NOT NULL,
    guild_id BIGINT NULL,
    channel_id BIGINT NULL,
    thread_id BIGINT NULL,
    dm_user_id BIGINT NULL,
    requester_user_id BIGINT NOT NULL,
    input_text TEXT NOT NULL,
    output_file_path TEXT NULL,
    model_deployment TEXT NOT NULL,
    voice TEXT NOT NULL,
    moderation_result_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS scope_settings (
    id BIGSERIAL PRIMARY KEY,
    scope_type TEXT NOT NULL,
    guild_id BIGINT NULL,
    channel_id BIGINT NULL,
    thread_id BIGINT NULL,
    dm_user_id BIGINT NULL,
    bot_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    memory_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    image_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    video_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    speech_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    retention_days_raw_logs INTEGER NOT NULL DEFAULT 30,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_scope_settings UNIQUE NULLS NOT DISTINCT (scope_type, guild_id, channel_id, thread_id, dm_user_id)
);

CREATE TABLE IF NOT EXISTS user_profiles (
    id BIGSERIAL PRIMARY KEY,
    guild_id BIGINT NULL,
    user_id BIGINT NOT NULL,
    profile_memory_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    preferences_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_user_profiles UNIQUE NULLS NOT DISTINCT (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS admin_audit_log (
    id BIGSERIAL PRIMARY KEY,
    actor_user_id BIGINT NOT NULL,
    action TEXT NOT NULL,
    scope_type TEXT NOT NULL,
    guild_id BIGINT NULL,
    channel_id BIGINT NULL,
    thread_id BIGINT NULL,
    dm_user_id BIGINT NULL,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
