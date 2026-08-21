-- Knowledge のテーブル定義。**このSQLがDDLの正。**
--
-- SQLAlchemy の Base.metadata.create_all() は使わない。
-- DDLが2箇所（このSQLとPythonのモデル）に存在すると必ず食い違うため、
-- ここを正とし、backend/app/models/tables.py は「このSQLに対応する
-- クエリ用の定義」という位置づけにする。
--
-- このスクリプトはボリュームが空のときしか実行されない。
-- 変更を反映するには DBを作り直すこと:
--   docker compose down -v && docker compose up -d

-- ============================================================================
-- ⚠️ vector(1024) の 1024 は埋め込みモデルの次元数。
--    backend/app/config.py の DEFAULT_EMBEDDING_DIM と必ず一致させること。
--    片方だけ変えると、ベクトルを挿入する瞬間まで誰も気づけない。
--
--    モデル: intfloat/multilingual-e5-large （1024次元）
--    理由は docs/decisions.md を参照。
-- ============================================================================

CREATE TABLE knowledge (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),

    -- 検索対象の本文。入力時に構造化を強制しないため、必須はこれだけ
    content           TEXT        NOT NULL,

    -- AI整理前の元テキスト。AIの整理が誤っていても元に戻せるようにする
    original_content  TEXT,

    -- 埋め込みは登録後に非同期で生成することがあるため NULL を許容する
    embedding         vector(1024),

    -- 人間による確認状態。AIが作った候補は draft で入り、確認後に confirmed
    status            TEXT        NOT NULL DEFAULT 'confirmed'
                                  CHECK (status IN ('draft', 'confirmed', 'rejected')),

    -- どの入力経路から来たか
    source_type       TEXT        NOT NULL DEFAULT 'manual'
                                  CHECK (source_type IN ('manual', 'meeting', 'audio')),

    -- 元になった入力のID。議事録・音声を追加する段階で使う
    source_id         UUID,

    -- 認証を作らないため任意。誰が登録したかの手がかりとして残す
    created_by        TEXT,

    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- 論理削除。誤削除から復帰できるようにする
    deleted_at        TIMESTAMPTZ
);

-- 検索は「削除されていない・確認済み」のものだけを対象にするため、
-- その条件で絞り込む頻度が高い
CREATE INDEX idx_knowledge_active
    ON knowledge (status)
    WHERE deleted_at IS NULL;

CREATE INDEX idx_knowledge_source
    ON knowledge (source_type, source_id);

-- ベクトル検索用のインデックス。
-- コサイン距離（演算子 <=>）で検索するため vector_cosine_ops を指定する。
-- 距離関数を変えるとこのインデックスは効かなくなるので、検索側と揃えること。
CREATE INDEX idx_knowledge_embedding
    ON knowledge USING hnsw (embedding vector_cosine_ops);

-- 本文の部分一致検索用（pg_trgm）。
-- ベクトル検索だけだと固有名詞の完全一致に弱いため、併用できるようにしておく
CREATE INDEX idx_knowledge_content_trgm
    ON knowledge USING gin (content gin_trgm_ops);


-- updated_at を手で更新し忘れるとデータの信頼性が落ちるため、DB側で更新する
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
