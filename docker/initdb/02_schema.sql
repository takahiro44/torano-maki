-- Knowledge のテーブル定義。**このSQLがDDLの正。**
--
-- SQLAlchemy の Base.metadata.create_all() は使わない。
-- 変更を反映するには:
--   docker compose down -v && docker compose up -d
--
-- ER は docs/knowledge-extraction-design.md の検証結果に合わせる。
-- vector(1024) は backend/app/config.py の DEFAULT_EMBEDDING_DIM と一致させる。

-- ============================================================================
-- 1. DATA_SOURCES
-- ============================================================================
CREATE TABLE data_sources (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type   VARCHAR(20) NOT NULL
                  CHECK (source_type IN ('audio', 'document', 'manual', 'roleplay', 'interview')),
    file_name     VARCHAR(255),
    occurred_at   TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- 2. UTTERANCE_SEGMENTS
-- ============================================================================
CREATE TABLE utterance_segments (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    data_source_id  UUID NOT NULL REFERENCES data_sources(id),
    sequence_no     INT NOT NULL,
    speaker         VARCHAR(100) NOT NULL,
    start_sec       DOUBLE PRECISION NOT NULL,
    end_sec         DOUBLE PRECISION NOT NULL,
    content         TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (data_source_id, sequence_no)
);

CREATE INDEX idx_segments_source
    ON utterance_segments (data_source_id);

CREATE INDEX idx_segments_source_seq
    ON utterance_segments (data_source_id, sequence_no);

-- ============================================================================
-- 3. KNOWLEDGE_UNITS
-- ============================================================================
CREATE TABLE knowledge_units (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    data_source_id    UUID REFERENCES data_sources(id),

    knowledge_type    VARCHAR(50) NOT NULL DEFAULT 'sales_knowhow',
    title             VARCHAR(100) NOT NULL,

    situation         TEXT,
    problem           TEXT,
    judgment          TEXT,
    action            TEXT,
    reasoning         TEXT,
    outcome           TEXT,
    lesson            TEXT,

    applicable_situations TEXT,
    limitations       TEXT,

    industry          VARCHAR(100),
    product           VARCHAR(100),
    sales_stage       VARCHAR(50),

    search_text       TEXT,
    embedding         vector(1024),
    embedding_model   VARCHAR(100),

    status            VARCHAR(20) NOT NULL DEFAULT 'draft'
                      CHECK (status IN ('draft', 'confirmed', 'rejected', 'archived')),

    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at        TIMESTAMPTZ
);

-- HNSW を使う。ivfflat(lists=100) は件数が少ないデモでは訓練不足になりやすい。
CREATE INDEX idx_knowledge_embedding
    ON knowledge_units USING hnsw (embedding vector_cosine_ops)
    WHERE deleted_at IS NULL AND status = 'confirmed';

CREATE INDEX idx_knowledge_search_trgm
    ON knowledge_units USING gin (search_text gin_trgm_ops)
    WHERE deleted_at IS NULL;

CREATE INDEX idx_knowledge_status
    ON knowledge_units (status)
    WHERE deleted_at IS NULL;

CREATE INDEX idx_knowledge_type_status
    ON knowledge_units (knowledge_type, status)
    WHERE deleted_at IS NULL;

CREATE INDEX idx_knowledge_source
    ON knowledge_units (data_source_id)
    WHERE data_source_id IS NOT NULL;

CREATE INDEX idx_knowledge_industry
    ON knowledge_units (industry)
    WHERE industry IS NOT NULL AND deleted_at IS NULL;

CREATE INDEX idx_knowledge_product
    ON knowledge_units (product)
    WHERE product IS NOT NULL AND deleted_at IS NULL;

CREATE INDEX idx_knowledge_sales_stage
    ON knowledge_units (sales_stage)
    WHERE sales_stage IS NOT NULL AND deleted_at IS NULL;

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_knowledge_updated_at
    BEFORE UPDATE ON knowledge_units
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ============================================================================
-- 4. KNOWLEDGE_EVIDENCE
-- ============================================================================
CREATE TABLE knowledge_evidence (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    knowledge_id         UUID NOT NULL REFERENCES knowledge_units(id) ON DELETE CASCADE,
    start_utterance_id   UUID NOT NULL REFERENCES utterance_segments(id),
    end_utterance_id     UUID NOT NULL REFERENCES utterance_segments(id),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_evidence_knowledge
    ON knowledge_evidence (knowledge_id);

CREATE INDEX idx_evidence_start
    ON knowledge_evidence (start_utterance_id);

CREATE INDEX idx_evidence_end
    ON knowledge_evidence (end_utterance_id);

-- ============================================================================
-- 5. CALL_SUMMARIES
-- ============================================================================
CREATE TABLE call_summaries (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    data_source_id  UUID NOT NULL UNIQUE REFERENCES data_sources(id),
    summary         TEXT NOT NULL,
    customer_needs  JSONB NOT NULL DEFAULT '[]',
    proposals       JSONB NOT NULL DEFAULT '[]',
    decisions       JSONB NOT NULL DEFAULT '[]',
    next_actions    JSONB NOT NULL DEFAULT '[]',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- 6. CHAT_REVIEWS
-- ============================================================================
-- AIチャットの会話ログを上司に確認してもらい、回答をナレッジ化するための記録。
-- チャット自体はサーバに永続化しない設計（models/chat.py）のため、
-- 上司に見せる文脈はここで chat_history にスナップショットする。
CREATE TABLE chat_reviews (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chat_history            JSONB NOT NULL,
    summary                 TEXT NOT NULL,
    understood_points       JSONB NOT NULL DEFAULT '[]',
    knowledge_gaps          JSONB NOT NULL DEFAULT '[]',
    status                  VARCHAR(20) NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'answered')),
    supervisor_response     TEXT,
    answered_data_source_id UUID REFERENCES data_sources(id),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    answered_at             TIMESTAMPTZ
);

CREATE INDEX idx_chat_reviews_status ON chat_reviews (status);
