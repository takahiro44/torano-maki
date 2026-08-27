# DBを作り直したあと、データを戻す手順

**このPR（ロープレ履歴）は `docker/initdb/02_schema.sql` を変更している。**
`initdb` はボリュームが空のときしか実行されないため、取り込んだだけでは
新しい列が増えない。作り直しが要る（CLAUDE.md 4.10）。

```bash
docker compose down -v && docker compose up -d
```

`-v` はボリュームごと消す。**手元のDBの中身は全部消える。**
消える前に退避し、あとで戻す。その手順をここに置く。

> 付録A に「作り直さずに列だけ足す」方法も書いた。手元に消したくないデータが
> 多い人はそちらでもよい。ただし**DDLの正は `02_schema.sql`** で、付録Aは
> それと同じ結果を作るための近道でしかない。

---

## この変更で増える列

`roleplay_sessions` に3つ。既存の列・テーブルは変えていない。

| 列 | 何のためか |
|---|---|
| `category` | 履歴一覧に「値引き」などの場面名を出す。`query` には検索用の言い換え文が入るため復元できない |
| `root_session_id` | 「もう一度」で作った練習を1回目へ紐づける。同じ場面の試行を1クエリでまとめるため |
| `attempt_no` | 何回目の挑戦か。一覧の行ごとに数え直さないため |

索引も2つ増える（`created_at DESC` と `root_session_id`）。

---

## 順番

**退避 → 作り直し → 復元。** 先に `down -v` を打つと退避できない。

### 手順0. 何が入っているか見る

消えて困るものがあるかをここで判断する。

```bash
docker compose exec -T db psql -U torano -d torano_maki -c \
  "SELECT source_type, origin, count(*) FROM data_sources GROUP BY 1,2 ORDER BY 1;"
```

**見るべきは `source_type` ではなく `origin`。** 同梱商談22件も画面から入れた音声も
どちらも `source_type='audio'` で入る。両者を分けているのは `origin` の方である。

| 出てきたもの | 戻せるか |
|---|---|
| `audio` / `synthetic`（同梱商談22件・119ナレッジ） | ✅ JSONがリポジトリにあるので手順3で戻る。退避は要らない |
| `audio` / `real`（自分が画面からアップロードした音声） | ⚠️ **手順1で退避しないと戻せない。** リポジトリにもDGXにも無い |
| 画面から手で登録したテキスト（`manual` / `document`） | ❌ 戻す仕組みが無い。必要なら登録内容を手元に控える |
| `chat_reviews`（上司レビュー） | ❌ 同上 |
| ロープレの練習履歴 | ❌ 消える。**この変更の対象そのものなので、消えた状態から始まる**（付録Aなら残せる） |

### 手順1. 音声由来のナレッジを退避する

音声本体は出力されない。DBに残っている文字起こし・ナレッジ・根拠・要約だけがJSONになる。
詳しくは [audio-knowledge-transfer.md](audio-knowledge-transfer.md)。

**`origin='real'` のものだけでよい。** synthetic は手順3で戻る。
**複数あるならファイルごとに実行する。** 既定は「最新の1件」しか出さない。

```bash
docker compose exec -T db psql -U torano -d torano_maki -Atc \
  "SELECT id, file_name FROM data_sources
    WHERE source_type = 'audio' AND origin = 'real' ORDER BY created_at;"
```

**`status='confirmed'` のナレッジが1件も無い音声は書き出せない**（エラーで止まる）。
文字起こししただけで承認していない音声は、先に画面で承認するか、諦める。

出てきたIDごとに:

```bash
cd backend
uv run python scripts/export_audio_knowledge.py \
  --source-id <UUID> --output ../data/backup_<わかる名前>.json
```

`data/` はGit管理外なので、`down -v` しても消えない。

### 手順2. 作り直す

```bash
docker compose down -v && docker compose up -d
```

### 手順3. 戻す

**リポジトリ同梱の商談（22件）から先に入れる。** これは全員共通で、JSONがGitに入っている。

```bash
cd backend
uv run python scripts/load_extraction_json.py
```

続けて、手順1で退避したJSONを1つずつ。

```bash
uv run python scripts/load_extraction_json.py --file ../data/backup_<わかる名前>.json
```

退避したJSONは `origin` を持っているので、実商談は実商談として戻る。
**同梱商談と混ざらない。**（この経路は既定が `synthetic` のため、
JSONに `origin` が無いと合成として復元されてしまう。
それを防ぐ修正を `load_extraction_json.py` に入れてある）

どちらも埋め込みは手元のCPUで作り直す。**DGXは要らない**（LLMを呼ばないため）。
数分かかる。

### 手順4. 確認する

```bash
cd backend && uv run uvicorn app.main:app --reload
curl -s http://127.0.0.1:8000/health/db | python -m json.tool
```

- `embedding_dim_matches` が `true`
- `tables` に `roleplay_sessions` がある

新しい列が実際に増えたかは直接見るのが速い。

```bash
docker compose exec -T db psql -U torano -d torano_maki -c \
  "\d roleplay_sessions"
```

`category` / `root_session_id` / `attempt_no` の3行があれば成功。
無ければ `-v` を付け忘れて古いボリュームが残っている。

最後にテストを流す（DBが起動している必要がある）。

```bash
cd backend && uv run pytest -q
```

---

## 付録A. 作り直さずに列だけ足す

手元に消したくないデータがあるなら、同じ結果をこれで作れる。
**`02_schema.sql` と食い違わせないこと。** 増やす列を変更したらこちらも直す。

2026-08-27 に、このSQLを流したDBと、`02_schema.sql` から初期化した
使い捨てコンテナの `\d roleplay_sessions` が一致することを確認済み
（列・索引・CHECK・外部キーすべて）。

```bash
docker compose exec -T db psql -U torano -d torano_maki <<'SQL'
ALTER TABLE roleplay_sessions
    ADD COLUMN IF NOT EXISTS category VARCHAR(30),
    ADD COLUMN IF NOT EXISTS root_session_id UUID
        REFERENCES roleplay_sessions(id) ON DELETE CASCADE,
    ADD COLUMN IF NOT EXISTS attempt_no INT NOT NULL DEFAULT 1;

ALTER TABLE roleplay_sessions
    DROP CONSTRAINT IF EXISTS roleplay_sessions_category_check,
    ADD CONSTRAINT roleplay_sessions_category_check
        CHECK (category IS NULL OR category IN (
            'needs_discovery', 'price_objection', 'objection',
            'complaint', 'next_commitment'));

ALTER TABLE roleplay_sessions
    DROP CONSTRAINT IF EXISTS roleplay_sessions_attempt_no_check,
    ADD CONSTRAINT roleplay_sessions_attempt_no_check CHECK (attempt_no >= 1);

CREATE INDEX IF NOT EXISTS idx_roleplay_sessions_created_at
    ON roleplay_sessions (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_roleplay_sessions_root
    ON roleplay_sessions (root_session_id, attempt_no)
    WHERE root_session_id IS NOT NULL;
SQL
```

既存の練習履歴は `category = NULL` / `attempt_no = 1` になる。
一覧には出るが、場面名と「2回目」は付かない。**元の情報がどこにも無いため復元できない。**

---

## 付録B. コーディングエージェントへ渡す指示（コピペ用）

各自のClaude Codeに、そのまま貼れば通るように書いてある。

```
main を取り込んだ結果 docker/initdb/02_schema.sql が変わっている。
docs/db-rebuild-restore.md の手順に従って、DBを作り直してデータを戻してほしい。

守ること:
- 手順の順番を変えない（退避 → down -v → up -d → 復元）。
  先に down -v を打つと音声由来のナレッジが復元不能になる。
- 手順0 と手順1 を必ず先に実行し、data_sources に source_type='audio' の行が
  あった場合は、その件数ぶん export_audio_knowledge.py を実行してから進むこと。
  1件も無ければ退避は不要。
- 退避したJSONの出力先は data/ 配下にすること（Git管理外なので down -v で消えない）。
  リポジトリにコミットしないこと（実商談の文字起こし全文が入っている）。
- 手順4 の確認まで実施し、\d roleplay_sessions に category / root_session_id /
  attempt_no の3列があることを目視で確認した結果を報告すること。
- 手元に消したくないデータが多い場合は、付録A の ALTER TABLE を使ってよいか
  先に私に確認すること。勝手にどちらかを選ばない。
```
