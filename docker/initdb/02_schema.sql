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
-- origin と source_type を1列に混ぜないこと。
-- source_type は「どの媒体から入ったか」、origin は「実商談か合成か」を表す。
-- 混ぜると、合成音声を取り込んだ瞬間にどちらの意味も表せなくなる。
-- 合成データを実商談と誤認させないため、画面とDBの両方で区別できる状態を保つ。
CREATE TABLE data_sources (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type   VARCHAR(20) NOT NULL
                  CHECK (source_type IN ('audio', 'document', 'manual', 'roleplay', 'interview')),
    file_name     VARCHAR(255),
    occurred_at   TIMESTAMPTZ,
    origin        VARCHAR(20) NOT NULL DEFAULT 'real'
                  CHECK (origin IN ('real', 'synthetic')),
    review_status VARCHAR(20) NOT NULL DEFAULT 'unreviewed'
                  CHECK (review_status IN ('unreviewed', 'reviewed')),
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
-- 6. ROLEPLAY_SESSIONS
-- ============================================================================
-- 1回の練習＝1セッション。商談全体ではなく、値引き要求のような
-- 判断が必要な一場面だけを扱う。
--
-- 認証を作らない方針（CLAUDE.md 3.1）のため社員IDを持たない。
-- 匿名の練習履歴として扱う。
CREATE TABLE roleplay_sessions (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    query         TEXT NOT NULL,
    -- 生成時点のシナリオとrubricのスナップショット。
    -- **Knowledge を後から編集しても、この練習の出題内容は変わらない。**
    -- 参照で持つと、あとから根拠を直したときに
    -- 「何を出題されたか」と「何で評価されたか」が食い違う。
    scenario      JSONB NOT NULL,
    status        VARCHAR(20) NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active', 'completed', 'abandoned')),
    -- どの場面から始めたか。**query から復元できない。**
    -- カテゴリ開始時の query には検索用の言い換え文
    -- （models/roleplay.py の CATEGORY_QUERIES）が入るため、
    -- 履歴一覧に「値引き」と出すにはカテゴリ自体を残す必要がある。
    -- 自由入力・Citation から始めた練習には対応する場面が無いので NULL。
    category      VARCHAR(30)
                  CHECK (category IS NULL OR category IN (
                      'needs_discovery', 'price_objection', 'objection',
                      'complaint', 'next_commitment')),
    -- 「もう一度」で作られた練習を、最初の1回へ紐づける。
    --
    -- **親ではなく根を指す。** 3回目が2回目を指す形にすると、
    -- 同じ場面の試行をまとめるのに再帰クエリが要る。
    -- 履歴一覧は1クエリで組み立てたいので、常に1回目を指す。
    -- NULL は「自分が1回目」。自己参照のため、行を作る前に
    -- 自分のIDを入れられない（DEFAULTでは表現できない）。
    root_session_id UUID REFERENCES roleplay_sessions(id) ON DELETE CASCADE,
    -- 何回目の挑戦か。root からの件数を数えれば出せるが、
    -- 一覧の1行ごとに数えることになるため保存しておく。
    attempt_no    INT NOT NULL DEFAULT 1 CHECK (attempt_no >= 1),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at  TIMESTAMPTZ
);

CREATE INDEX idx_roleplay_sessions_status
    ON roleplay_sessions (status, created_at DESC);

-- 履歴一覧は「新しい順」しか引かない。status を先頭に置いた上の索引では
-- 絞り込みなしの並べ替えに使えないため、時刻だけの索引を別に持つ。
CREATE INDEX idx_roleplay_sessions_created_at
    ON roleplay_sessions (created_at DESC);

CREATE INDEX idx_roleplay_sessions_root
    ON roleplay_sessions (root_session_id, attempt_no)
    WHERE root_session_id IS NOT NULL;

-- ============================================================================
-- 7. ROLEPLAY_SESSION_KNOWLEDGE
-- ============================================================================
-- **画面に出す出典はこの表だけから組み立てる。**
-- LLM が返した ID を信用すると、実在しないナレッジを出典として
-- 表示してしまう（CLAUDE.md 6章）。
CREATE TABLE roleplay_session_knowledge (
    session_id    UUID NOT NULL REFERENCES roleplay_sessions(id) ON DELETE CASCADE,
    knowledge_id  UUID NOT NULL REFERENCES knowledge_units(id),
    rank          INT NOT NULL,
    usage_type    VARCHAR(20) NOT NULL DEFAULT 'supporting'
                  CHECK (usage_type IN ('primary', 'supporting')),
    PRIMARY KEY (session_id, knowledge_id)
);

CREATE INDEX idx_roleplay_session_knowledge_knowledge
    ON roleplay_session_knowledge (knowledge_id);

-- ============================================================================
-- 8. ROLEPLAY_TURNS
-- ============================================================================
-- 顧客役の最初の発言も1行として保存する。
-- シナリオ側だけに持たせると、会話の並びを組み立てる処理が
-- 「1件目だけ別扱い」になり、順番の取り違えを起こしやすい。
CREATE TABLE roleplay_turns (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id   UUID NOT NULL REFERENCES roleplay_sessions(id) ON DELETE CASCADE,
    sequence_no  INT NOT NULL,
    role         VARCHAR(20) NOT NULL CHECK (role IN ('learner', 'customer')),
    content      TEXT NOT NULL,
    -- generated はAIが作った発言。人の回答（text / audio）と必ず区別する。
    -- 混ざるとフィードバックが「本人が言っていないこと」を評価する。
    input_mode   VARCHAR(20) NOT NULL CHECK (input_mode IN ('text', 'audio', 'generated')),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (session_id, sequence_no)
);

CREATE INDEX idx_roleplay_turns_session
    ON roleplay_turns (session_id, sequence_no);

-- ============================================================================
-- 9. ROLEPLAY_FEEDBACK
-- ============================================================================
-- 1セッション1件。session_id をそのまま主キーにして、
-- 二重生成を DB 側で防ぐ（再送やダブルクリックで増えないこと）。
CREATE TABLE roleplay_feedback (
    session_id      UUID PRIMARY KEY REFERENCES roleplay_sessions(id) ON DELETE CASCADE,
    rubric_result   JSONB NOT NULL DEFAULT '[]',
    strengths       JSONB NOT NULL DEFAULT '[]',
    improvements    JSONB NOT NULL DEFAULT '[]',
    next_phrase     TEXT NOT NULL,
    focus_next_try  TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- 10. CHAT_REVIEWS
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
