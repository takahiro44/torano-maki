# セットアップ記録

DGX Spark（ARM64 + CUDA）および各自の開発環境で、
**ハマった内容と解決策**を記録する。

**目的は、同じ罠を4人が個別に踏まないこと。**
解決したら必ずここに追記してから次に進む。うまくいかなかった試行も
「これは駄目だった」と分かるだけで他の3人の時間を節約できるので、残す価値がある。

---

## 記録フォーマット

```
### YYYY-MM-DD 見出し（何をしようとして詰まったか）

- **やろうとしたこと**:
- **起きたこと**（エラーメッセージをそのまま貼る）:
- **原因**:
- **解決策 / 回避策**:
- **未解決の場合**: 次に試すこと
```

---

## 環境情報

### DGX Spark（推論用）

<!-- TODO: 実機を触れるようになったら埋める -->

| 項目 | 値 |
|---|---|
| マシン | NVIDIA DGX Spark |
| アーキテクチャ | <!-- uname -m --> |
| OS | <!-- --> |
| CUDA | <!-- nvcc --version --> |
| Python | <!-- --> |
| Ollama | <!-- ollama --version --> |

### 各自の開発環境

DBコンテナが動作確認できたら、自分の環境の行を埋めること。
**Mac（Apple Silicon）はまだ誰も検証していない。**

| メンバー | OS / アーキテクチャ | Docker | 動作確認 |
|---|---|---|---|
| Windows（岡本） | Windows 11 / x86_64 | 29.2.1 / Compose 5.1.0 | ✅ 2026-08-21 |
| Windows | | | <!-- TODO --> |
| Mac | macOS / arm64 | | <!-- TODO --> |
| Mac | macOS / arm64 | | <!-- TODO --> |

### 動作確認済みのバージョン（DBコンテナ）

| 項目 | 値 |
|---|---|
| イメージ | `pgvector/pgvector:pg17` |
| PostgreSQL | 17.11 (Debian 17.11-1.pgdg12+2) |
| pgvector | 0.8.6 |
| pg_trgm | 1.6 |

---

## 記録

### 2026-08-21 DBコンテナの動作確認（Windows / x86_64）

- **やろうとしたこと**: `docker compose up -d` で PostgreSQL + pgvector を起動し、
  ベクトル型が実際に使えるところまで確認する
- **結果**: 成功。以下を確認した
  - `vector(3)` への挿入とコサイン距離演算子 `<=>` が正しく動作
  - `pg_trgm` の `similarity()` が動作
  - TimeZone が `Asia/Tokyo` になっている
  - `docker compose down -v && docker compose up -d` で初期化SQLが再実行される
  - `--profile tools` を付けたときだけ adminer が起動する（デフォルトでは起動しない）
- **未検証**: **Mac（Apple Silicon / arm64）での動作**。
  `pgvector/pgvector:pg17` は arm64 対応イメージなので動く見込みだが、
  実際に確認したら結果をこの下に追記すること

### 2026-08-21 初期化SQLを追記しても反映されない

- **やろうとしたこと**: `docker/initdb/` にSQLを置いてDBを初期化する
- **起きたこと**: 一度起動した後にSQLを追記しても、再起動しただけでは実行されない
- **原因**: `/docker-entrypoint-initdb.d` のスクリプトは、
  **データディレクトリが空のとき（＝ボリューム新規作成時）にしか実行されない**仕様
- **解決策**: ボリュームごと作り直す

  ```bash
  docker compose down -v && docker compose up -d
  ```

  `-v` を付け忘れるとボリュームが残るため、何度やっても反映されない。
  **`-v` はデータを完全に削除するので、実行前に消えて困るデータがないか確認すること。**

### 2026-08-21 `Cannot connect to the Docker daemon`

- **起きたこと**:

  ```
  failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine;
  check if the path is correct and if the daemon is running
  ```

- **原因**: Docker Desktop アプリが起動していなかった
- **解決策**: Docker Desktop を起動する。**PC再起動のたびに必要。**
  Docker コマンドが失敗する相談の大半はこれなので、まず最初に確認する

### 2026-08-21 Windows の CRLF が `.env` に影響しないか検証

- **懸念**: Mac 2人 / Windows 2人の混在環境。Windowsで `.env` がCRLFになると、
  値の末尾に `\r` が混入して接続文字列が壊れる可能性がある
- **検証結果**: **問題なし。** Docker Compose v5.1.0 はCRLFの `.env` を正しく解釈し、
  値に `\r` は混入しなかった
- **ただし**: コンテナ内で実行される `.sh` / `.sql` はCRLFだと壊れるため、
  `.gitattributes` でLFを強制している。このファイルは消さないこと

### 2026-08-21 `.gitignore` の `models/` がスキーマ定義を巻き込む

- **起きたこと**: 大容量ファイル除外のつもりで `models/` と書くと、
  **`backend/app/models/`（Pydanticスキーマ定義）まで除外される**
- **原因**: 先頭に `/` が無いパターンは、どの階層のディレクトリにもマッチする
- **解決策**: リポジトリ直下だけを対象にする

  ```
  /models/
  /data/
  /uploads/
  /outputs/
  ```

- **確認方法**: `git check-ignore -v <パス>` で、意図しないファイルが
  除外されていないか確認できる
