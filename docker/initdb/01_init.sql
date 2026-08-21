-- このスクリプトは「ボリュームが空のとき」しか実行されない。
-- 起動後にここへSQLを追記しても反映されないため、反映するには
--   docker compose down -v && docker compose up -d
-- でボリュームごと作り直す必要がある。

-- pgvector はイメージに含まれているだけでは使えず、DBごとに有効化が必要
CREATE EXTENSION IF NOT EXISTS vector;

-- 日本語のあいまい一致・部分一致検索で使う可能性があるため先に入れておく
CREATE EXTENSION IF NOT EXISTS pg_trgm;
