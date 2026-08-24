# 実装計画: ナレッジ抽出ワークフロー（最小構成）

対象リポジトリ: `torano-maki`  
作成日: 2026-08-24  
担当想定: ナレッジ蓄積（`ingest` / `extraction`）  
ブランチ例: `feat/knowledge-extraction`

この文書は「自由テキスト → LLM → 構造化ナレッジ → DB」を、**既存のひな形とチームルールの上で**動かすための実装計画である。  
外部から持ち込まれた「新規 backend / Ollama / Alembic / knowledge_fragments」案は **採用しない**。理由は末尾の「持ち込んだ案との差分」を参照。

---

## 1. 目的

先輩営業が入力した自由テキストから、DGX 上の Qwen（vLLM）が構造化ナレッジを抽出し、PostgreSQL に `draft` として格納する。人間が確認・修正・承認／却下できるところまでを最小でつなぐ。

```
自由テキスト
    ↓  POST /ingest/text （プレビューは保存しない）
LLM（Qwen / json_schema / thinking OFF）
    ↓
構造化候補（1入力から複数可）
    ↓  POST /ingest/text?save=true または /ingest/extract-and-save
knowledge テーブル（status=draft, original_content=元文）
    ↓  PATCH status / approve / reject
confirmed（検索対象） or rejected
```

---

## 2. スコープ

### 含む

- `POST /ingest/text/preview` … 抽出プレビュー（DB 非保存）
- `POST /ingest/text` … 抽出して `draft` 保存（`original_content` に元文、CPU で embedding）
- 確認は既存 `GET /knowledge` / `GET /knowledge/{id}`
- 修正・承認／却下は既存 `PATCH /knowledge/{id}`（`status`: draft / confirmed / rejected）。専用 approve API は作らない
- vLLM（Qwen）への接続、json_schema、thinking OFF

### 含まない（後続タスク）

- situation 等を DB 列にする（次フェーズ。当面は `content` に整形して格納）
- ベクトル検索の改修（既存 `/search` をそのまま使う）
- 音声・議事録（`transcription.py` は未確定のため触らない）
- **フロントエンド**（このタスクのゴールは「自由テキスト → AI 構造化 → DB」。画面接続は C タスク）
- 評価データセット（Dタスク）
- ユーザー認証・`users` テーブル（CLAUDE.md 3.1 でデモ範囲外）
- Ollama コンテナ、Alembic、新 DB、新テーブル `knowledge_fragments`

---

## 3. この環境で「できること / やってはいけないこと」

出典: `gpu.txt`（実測）、`README.md`、`CLAUDE.md`、`docs/decisions.md`

### できる（この MVP で使う）

| 能力 | 使い方 |
|---|---|
| OpenAI 互換 `chat.completions` | `BASE_URL` + `MODEL_NAME` |
| モデル名 | **必ず** `Qwen3.8-27B-NVFP4`（他は 404） |
| `response_format.json_schema` | OpenAI 互換。Pydantic の `model_json_schema()` |
| `structured_outputs.json` | vLLM 固有の制約デコード。公式 Online Serving の extra body |
| Thinking OFF | `chat_template_kwargs.enable_thinking=false`（JSON 汚染 / Qwen で structured が無効化されるのを防ぐ） |
| 長文 128K | 商談ログ全文を 1 リクエストに載せてよい |
| 同時 16 seq | 抽出とロープレの並行は可。サーバは止めない |

### この API では実装するな

| やりたいこと | 理由 | 代替 |
|---|---|---|
| `/v1/embeddings` | 404 | 既存 `services/embedding.py`（CPU / e5-large / 1024 次元） |
| 音声 STT | 404、ライブラリ未確定 | 今はやらない |
| Ollama / 別モデル起動 | 共有 GPU 1 台。Qwen を落とすな | `.env` で既存 vLLM を指す |
| DGX 上で大きな PyTorch | ユニファイドメモリ約 121GB | 埋め込みは各自 PC |

### アプリ配置（README）

- PostgreSQL + pgvector のみ Docker
- FastAPI はホスト（`uv run`）
- フロントはホスト（今は触らない）
- vLLM は DGX。`.env` の `BASE_URL` / `MODEL_NAME`

接続例:

```
BASE_URL=http://192.168.128.142:8080/v1
MODEL_NAME=Qwen3.8-27B-NVFP4
```

LAN に届かないときは SSH 転送して `http://127.0.0.1:8080/v1`。  
Windows ノートに NVIDIA GPU は無い。抽出は **リモート vLLM**、埋め込みは **CPU**。

---

## 4. 既存コードに載せる（新規プロジェクトを作らない）

| 役割 | 正となるファイル | 今回やること |
|---|---|---|
| ナレッジ型（LLM / API） | `backend/app/models/knowledge.py` | 抽出用の **別 Pydantic** を同ファイルまたは近傍に追加。既存 `Knowledge` の必須は `content` のみ、という方針は壊さない |
| DDL | `docker/initdb/02_schema.sql` | **MVP では変更しない** |
| ORM | `backend/app/models/tables.py` | 変更しない |
| LLM 抽出 | `backend/app/services/extraction.py` | ここが実装の本体 |
| 取り込み API | `backend/app/api/ingest.py` | エンドポイントを足す |
| CRUD / 承認 | `backend/app/api/knowledge.py` | 既存 PATCH を使う。必要なら approve/reject を薄く追加 |
| 設定 | `backend/app/config.py` / `.env` | `BASE_URL` / `MODEL_NAME` を埋める |
| ルーター登録 | `backend/app/main.py` | ingest は登録済み。**ロジックは置かない** |
| 埋め込み | `backend/app/services/embedding.py` | 保存時に既存 `embed_passages` を呼ぶ（プレフィックスは中に閉じる） |

スキーマを 3 箇所に増やさない（CLAUDE.md 6）。  
列を増やすなら **SQL → tables.py → knowledge.py を揃えて DB 作り直し**。それは **別 PR・チーム確認**。

### なぜ `knowledge_fragments` を作らないか

既存 `knowledge` がすでに:

- UUID PK
- `content` / `original_content`
- `embedding vector(1024)`
- `status` = draft / confirmed / rejected
- `source_type` = manual / meeting / audio
- 論理削除 `deleted_at`

手元案の Integer PK・768 次元・users FK・alembic は、確定済みの DDL と衝突する。

### 構造化フィールド（situation 等）の扱い

手元案の多数カラムは **DB にはまだ無い**。MVP では:

1. LLM 出力用 Pydantic（`ExtractedItem`）に situation / action / learning 等を持たせる
2. 保存時はそれらを **読みやすい日本語の `content` に整形**して 1 ナレッジ 1 行にする
3. 元文は必ず `original_content` に残す
4. `status=draft`、`source_type=manual`

列として持つ必要が出たら、チーム合意のうえ DDL を増やす。勝手にテーブルを増やさない。

`content` 整形の例（実装時に関数化）:

```
【タイトル】価格指摘への切り返し
【状況】… 
【行動】…
【結果】…
【学び】…
```

タイトル専用列は無いので、見出しは `content` 先頭に含める。

---

## 5. データと状態

既存の正:

| 項目 | 値 |
|---|---|
| PK | UUID |
| 埋め込み | `intfloat/multilingual-e5-large` / **1024** |
| 抽出直後 | `draft`（検索対象にしない想定。search 側のフィルタは既存実装に従う） |
| 承認後 | `confirmed` |
| 却下 | `rejected`（物理削除しない） |
| 登録者 | `created_by` 文字列。認証なし。未指定なら null または固定名 |
| 元文 | `original_content` |

status 遷移:

```
draft  → confirmed（承認）
draft  → rejected（却下）
confirmed の content 変更時は既存どおり embedding を作り直す
```

`raw_input` という列名は使わない。既存の `original_content` が同じ役割。

---

## 6. LLM 抽出の実装方針（vLLM Structured Outputs）

出典: https://docs.vllm.ai/en/latest/features/structured_outputs/#online-serving-openai-api

`guided_json` は vLLM 0.12 で廃止。代わりに extra body の `structured_outputs.json` を使う。  
OpenAI 互換の `response_format.json_schema` も併記する（公式の Online Serving 例）。  
`strict: true` は付けない。任意フィールドが多いスキーマでは required 全指定と衝突しやすい。

実装は `httpx` で `/chat/completions` に JSON を直接載せる（openai 依存を増やさない）。  
失敗時だけテキスト JSON パースにフォールバックする。公式どおり Schema はプロンプトにも載せる。

### 推奨呼び出し（httpx。OpenAI SDK なら extra_body に structured_outputs を置く）

```python
schema = ExtractionResult.model_json_schema()
payload = {
    "model": settings.model_name,  # Qwen3.8-27B-NVFP4
    "messages": [
        {"role": "system", "content": EXTRACT_SYSTEM},
        {"role": "user", "content": "次の JSON Schema に従って…\n" + schema_json + "\n入力:\n" + raw_text},
    ],
    "temperature": 0.3,
    "response_format": {
        "type": "json_schema",
        "json_schema": {
            "name": "knowledge_extraction",
            "schema": schema,
        },
    },
    "structured_outputs": {"json": schema},
    "chat_template_kwargs": {"enable_thinking": False},
}
httpx.post(settings.base_url.rstrip("/") + "/chat/completions", json=payload)
```

### 抽出スキーマ（API/LLM 用。DB 列ではない）

`knowledge.py` に追加する想定（名前は実装時に調整可）:

- `ExtractedItem`: title, situation, challenge, judgment, action, reason, result, learning, applicable_scenes, inapplicable_scenes, actual_utterance, industry, product, phase, confidence … すべて title 以外 Optional
- `ExtractionResult`: `items: list[ExtractedItem]`（1 入力複数件）

ルール（プロンプト）:

- 書いてないことは作らない
- 一般論は抽出しない
- 無い項目は null
- 複数ノウハウなら複数 items

### フォールバック

json_schema が無視された場合:

1. コードフェンス除去
2. JSON 配列 / オブジェクトをパース
3. Pydantic 検証に落ちた件は捨ててログ

空配列なら 422 または 200 + extracted=[]（フロントが扱いやすい方。推奨は 200 + 空）。

### タイムアウト

抽出は数秒〜数十秒。同期 API でよい（音声ほど長くない）。クライアントは 60s 程度を見込む。  
失敗時は 502/504 で vLLM 未達と分かるようにする。

---

## 7. API 設計（既存プレフィックスに合わせる）

既存: `/knowledge` CRUD、`/search`、`/ingest` は空。

追加:

### `POST /ingest/text/preview`

Body:

```json
{ "raw_text": "...", "created_by": null }
```

Response:

```json
{
  "raw_text": "...",
  "extracted": [ { "title": "...", "situation": "...", "content": "整形済み本文" } ],
  "saved_ids": []
}
```

`content` は保存時と同じ整形結果をプレビューで返す。

### `POST /ingest/text`

抽出して `draft` 保存。各行:

- `content` = 整形文
- `original_content` = raw_text
- `status` = draft
- `source_type` = manual
- `embedding` = `embed_passages([content])`（既存 create と同じ。draft でも入れておくと承認後すぐ検索できる）

Response: preview と同じ + `saved_ids: [uuid, ...]`

### 確認・修正・却下

既存で足りる:

- 一覧: `GET /knowledge?status=draft`
- 詳細: `GET /knowledge/{id}`
- 修正: `PATCH /knowledge/{id}` `{ "content": "..." }`
- 承認: `PATCH /knowledge/{id}` `{ "status": "confirmed" }`
- 却下: `PATCH /knowledge/{id}` `{ "status": "rejected" }`

任意の糖衣（ingest または knowledge）:

- `POST /knowledge/{id}/approve`
- `POST /knowledge/{id}/reject`

物理 DELETE は既存の論理削除を使う。新規の destroy は作らない。

`main.py` にロジックを足さない。ルーター追加が必要なら `include_router` 1 行のみ。ingest は既にある。

依存性注入は `Annotated[..., Depends(get_db)]`（CLAUDE.md 3.2）。

---

## 8. 設定

`.env`（コミットしない）:

```
BASE_URL=http://192.168.128.142:8080/v1
MODEL_NAME=Qwen3.8-27B-NVFP4
```

未設定なら既存どおり `is_llm_configured` で弾く。誤ったデフォルトモデル名は書かない。

`.env.example` にコメント例だけ追記してよい（実 IP は書かないか、例と明記）。変更したらチームに「`.env` を手で更新」と伝える（CLAUDE.md 4.10）。

openai パッケージは `test.py` で使用済み。**新しいライブラリは追加しない。** 足りなければ `uv add` の前に確認。  
`pip install` 禁止。Alembic / Ollama Python クライアントは入れない。

---

## 9. 実装タスク（小さく、既存ファイル中心）

順序を守る。大きな一括書き換えはしない。

| # | 内容 | 主なファイル | 完了条件 |
|---|---|---|---|
| 0 | `main` 最新化、`feat/knowledge-extraction` を切る | git | main 直編集しない |
| 1 | `.env` に BASE_URL / MODEL_NAME。`GET /v1/models` 相当を短いスクリプトまたは既存 test で確認 | `.env` | Qwen 名が返る |
| 2 | `ExtractedItem` / `ExtractionResult` / ingest 用リクエスト・レスポンス | `models/knowledge.py` | OpenAPI に型が出る |
| 3 | `extract_knowledge()` を実装。thinking OFF + json_schema + structured_outputs.json | `services/extraction.py` | サンプル1文で JSON がパースできる |
| 4 | `content` 整形ヘルパ | `services/extraction.py` | null 項目は見出しごと省略 |
| 5 | preview / extract-and-save | `api/ingest.py` | Swagger から叩ける |
| 6 | 保存時 embedding | ingest から `embed_passages` | `/health/db` の次元一致のまま |
| 7 | （任意）approve / reject | `api/knowledge.py` | PATCH と同等 |
| 8 | 動作確認（下記サンプル） | 手動 | 報告時に結果を残す |

スキーマ SQL は触らない。フロントは触らない。`transcription.py` は触らない。

担当外（検索・フロント・DDL）を大きく変える必要が出たら **実装前に人間へ確認**。

---

## 10. 動作確認

前提: `docker compose up -d`、DB healthy、`.env` の vLLM 到達、backend `uv run uvicorn`。

1. `GET /health` `GET /health/db`
2. `POST /ingest/text/preview` に下記サンプル1
3. 同じ文で `POST /ingest/text` → `saved_ids`
4. `GET /knowledge?status=draft`
5. `PATCH` で content 微修正
6. status を `confirmed` に
7. （任意）`POST /search` で近い件が返るか

### サンプル入力

**1. 完結エピソード（1件想定）**

先日、田中製作所様との商談で、他社より価格が高いと指摘されました。すぐに値引きせず、比較ポイントを何にされているか聞いたところ、保守対応の質を重視していることが分かりました。価格以外の価値、特に24時間対応と現地エンジニアの体制を説明したところ、最終的に受注できました。価格反論には、値引きより先に評価軸を確認するのが有効だと学びました。

**2. 混在（2件想定）**

今日は複数の気づきがあった一日でした。まず、A社の山田部長への訪問では、朝一の時間帯だと機嫌が良く、提案を受け入れてもらいやすいことが分かりました。また、製造業のお客様全般に言えることですが、決算月の1-2ヶ月前に設備投資の意向を必ず確認すべきです。9月と3月に予算執行が集中するので、この時期を逃すと他社に取られます。

**3. 情報少（無理埋めしないこと）**

B商事様の担当が来月から変わるらしい。前任者との関係性を新任者にも引き継がないと。

LLM 未達のときは 5xx とメッセージ。GPU コンテナは止めない。

---

## 11. Git

- `main` に直接 commit / push しない
- ブランチ: `feat/knowledge-extraction`
- commit は Conventional Commits 日本語。例: `feat: 自由テキストから構造化ナレッジを抽出する`
- `git add .` 禁止。ファイルを明示
- `.env` / 音声 / モデルを入れない
- `gpu.txt` をコミットするかはチーム判断（IP が載っている）。載せるなら例示 IP である旨を残す
- PR 作成まで。merge は人間

---

## 12. 持ち込んだ案との差分（他 AI が再提案しないため）

| 持ち込み案 | このリポジトリ |
|---|---|
| 新規 `backend/` 一式 | 既存 `backend/app/` を拡張 |
| Ollama | DGX vLLM。モデルは Qwen3.8-27B-NVFP4 |
| Alembic | 使わない。DDL 変更時は `docker compose down -v` |
| `knowledge_fragments` Integer PK | 既存 `knowledge` UUID |
| Vector(768) | **1024** / e5-large / CPU |
| `users` + 認証 | 作らない。`created_by` 任意 |
| `status=active` | `confirmed` |
| `pip` / `pyproject` 新規 | `uv` + 既存 `uv.lock` |
| Docker に Ollama | Postgres のみ Docker |
| 抽出 JSON を正規表現だけ | まず json_schema。フォールバックでパース |

`extraction.py` の古い「Schema 無効」コメントは、実装時に gpu.txt に合わせて直す。

---

## 13. チーム確認の結論（2026-08-24）

| # | 問い | 結論 |
|---|---|---|
| 1 | `original_content` に元文を残すか | **デモ範囲でよい。** トレーサビリティ優先 |
| 2 | 抽出は `content` 整形か、列にするか | **当面は整形。** situation 等の列追加は次フェーズ |
| 3 | draft 時点で embedding するか | **する。** 後で方針を変えて消してもよい前提 |
| 4 | 承認 API を新設するか | **既存 PATCH で足りる。** 専用 API は作らない |
| 5 | フロントをこの PR に含めるか | **含めない。** 「Cタスク」は画面を API に繋ぐ作業。今回のゴールは抽出結果を DB に落とすことだけ |

---

## 13.1 今回の完了条件

Swagger または curl で:

1. 自由テキストを `POST /ingest/text/preview` し、構造化 JSON が返る
2. `POST /ingest/text` で `knowledge` に `draft` 行が入り、`original_content` が元文、`content` が整形文
3. `PATCH /knowledge/{id}` で `status=confirmed` にできる

フロントの入力画面は不要。

---

## 14. この MVP の次

1. フロント: 入力 → preview → 保存 → 承認（既存コンポーネント接続）
2. 検索: confirmed のみヒットするか確認・必要ならフィルタ
3. 列追加が必要なら DDL 3点セット + DB 再作成
4. 音声はライブラリ確定後、`transcription.py` の単一口から ingest ジョブへ

以上。実装開始時は本ファイルと `gpu.txt` / `CLAUDE.md` を同時に読むこと。

---

## 15. 変更履歴（2026-08-24 追記）

配信モデルを **`Qwen3.8-27B-NVFP4`** に差し替えた。`.env` の `MODEL_NAME` もこれに合わせる。旧 ID `Qwen3.6-35B-A3B-NVFP4` は 404 になる。

構造化出力は vLLM 公式の Online Serving に合わせた。

| 項目 | 以前の想定 | 今回 |
|---|---|---|
| 制約デコード | `guided_json` または `response_format` の `strict: true` のみ | extra body `structured_outputs: { "json": <schema> }` |
| OpenAI 互換 | `json_schema` + `strict` | `json_schema`（`strict` なし）+ 上記を併用 |
| プロンプト | 入力文だけ | Schema を user メッセージにも載せる（公式 Tip） |
| thinking | OFF | 維持。Qwen の reasoning が structured を壊すため |

### 2026-08-24 デモ画面

登録タブは `POST /ingest/text` を使う。原文は `original_content`、構造化は `extracted` JSONB と表示用 `content`。
入力形式は問わない。長い原文は約4000字単位で分割して抽出し、埋め込みには出典チャンク（800字まで）を載せる。10万字超は 422。同期抽出のチャンク上限は 8。
デモでは保存時ステータスを **confirmed** にし、直後に「探す」でヒットさせる。
埋め込みは構造化テキストと生文を連結したものを使う。一覧・検索は改行を保持して表示する。

構造化の正は `ExtractedItem` → DB の `extracted JSONB`。項目追加は Pydantic と `EXTRACTED_FIELD_LABELS`（フロントの同名定数も揃える）。situation 等を個別 SQL 列にはしない。
**この変更は DDL 追加なので `docker compose down -v && docker compose up -d` が必要（既存データは消える）。**
