-- Knowledge のテーブル定義。**このSQLがDDLの正。**
--
-- SQLAlchemy の Base.metadata.create_all() は使わない。
-- 変更を反映するには:
--   docker compose down -v && docker compose up -d

-- ============================================================================
-- vector(1024) は backend/app/config.py の DEFAULT_EMBEDDING_DIM と一致させる。
-- モデル: intfloat/multilingual-e5-large
-- ============================================================================

CREATE TABLE data_sources (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type   TEXT NOT NULL
                  CHECK (source_type IN ('audio', 'document', 'manual', 'roleplay', 'interview')),
    filename      TEXT,
    conducted_at  TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE knowledge (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    data_source_id    UUID        REFERENCES data_sources(id),

    knowledge_type    TEXT        NOT NULL DEFAULT 'sales_knowhow',
    title             VARCHAR(100) NOT NULL,

    -- CBR ケース構造 (Aamodt & Plaza 1994)。NULL 許容。
    situation         TEXT,
    customer_issue    TEXT,
    sales_action      TEXT,
    action_reason     TEXT,
    result            TEXT,
    learning          TEXT,

    -- 人間には出さない検索用。embedding の入力
    search_text       TEXT,

    -- 原文。抽出元を辿るため（指示スキーマ外だが復元に必要）
    original_content  TEXT,

    embedding         vector(1024),

    status            TEXT        NOT NULL DEFAULT 'draft'
                                  CHECK (status IN ('draft', 'confirmed', 'rejected', 'archived')),

    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at        TIMESTAMPTZ
);

CREATE INDEX idx_knowledge_active
    ON knowledge (status)
    WHERE deleted_at IS NULL;

CREATE INDEX idx_knowledge_data_source
    ON knowledge (data_source_id);

CREATE INDEX idx_knowledge_embedding
    ON knowledge USING hnsw (embedding vector_cosine_ops);

CREATE INDEX idx_knowledge_search_text_trgm
    ON knowledge USING gin (search_text gin_trgm_ops);

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_knowledge_updated_at
    BEFORE UPDATE ON knowledge
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();
