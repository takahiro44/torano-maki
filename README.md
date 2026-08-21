# torano-maki

営業ナレッジをAI利活用前提の構造で蓄積し、探索・ロープレに活かすプロダクト

> 大塚商会インターンシップ 2026 / Dチーム

---

## 背景と課題

これまで社員が作成した日報は「ためるだけ」で、必要なときに引き出せていなかった。

- **先輩側** — 後輩指導の時間が取れず、自分のノウハウを言語化できていない
- **新人・異動者側** — 何を誰に聞けばいいか分からず、聞きに行くこと自体に躊躇がある

## アプローチ

商談録音・AIヒアリング・テキストを入力とし、LLMで**構造化されたナレッジ単位**に変換して蓄積する。
蓄積されたナレッジは、検索とロープレを通じて再利用される。

```
ためる  →  整える  →  使う  →  回す
入力コスト   構造化抽出   探索・      貢献の
ほぼゼロ     スキーマ化   ロープレ    可視化
```

## 主要機能

| 機能 | 内容 |
|---|---|
| ナレッジ蓄積 | 音声・テキストから構造化ナレッジを自動生成 |
| ナレッジ探索 | 状況で絞り込み、出典つきで回答 |
| ロープレ | 蓄積データから顧客ペルソナを生成し練習 |

<!-- TODO: 実装が進んだら機能ごとに詳細を追記 -->

---

## 技術構成

| 層 | 技術 | 選定理由 |
|---|---|---|
| LLM推論 | Ollama | ARM64ネイティブ対応。量子化により帯域制約を緩和 |
| 音声認識 | <!-- TODO: 検証後に確定 --> | ARM64でのCUDA対応状況により決定 |
| 埋め込み | sentence-transformers | 日本語対応の埋め込みモデルをローカル実行 |
| バックエンド | FastAPI / Pydantic | スキーマ定義がLLM・DB・APIの型を兼ねる |
| フロントエンド | Vite / React / TypeScript | SPAに徹し、バックエンドと責務を分離 |
| データストア | PostgreSQL + pgvector | 構造化データとベクトルを同一トランザクションで扱う |
| 非同期ジョブ | FastAPI BackgroundTasks + `jobs`テーブル | 音声処理をHTTPリクエスト内で完結させない。Celeryは規模に対して過剰 |

### 未確定事項

着手前に決める必要があるもの。候補と経緯は [`docs/decisions.md`](docs/decisions.md) を参照。

- **音声認識ライブラリ** — ARM64 + CUDA で動くものを検証して決定する
- **埋め込みモデルと次元数** — `vector(N)` の定義に必要。**これが決まらないとDBを作れない**

技術選定の理由は [`docs/decisions.md`](docs/decisions.md) に記録している。
**方針に疑問を持ったら、まずここを読むこと。**

## 実行環境

**NVIDIA DGX Spark**
詳細は2026/08/21に記載予定

x86_64向けのビルド済みパッケージが利用できない場合がある。
環境構築でハマった内容は [`docs/setup-notes.md`](docs/setup-notes.md) に記録すること。

---

## セットアップ

### 何をコンテナに入れるか

**PostgreSQL のみ Docker で動かす。アプリ本体はホストでネイティブに動かす。**

| 対象 | 動かす場所 | 理由 |
|---|---|---|
| PostgreSQL + pgvector | Docker | OSごとに導入手順が異なり、4人での再現が難しいため |
| FastAPI | ホスト（`uv run`） | `--reload` の即時反映を優先。ビルド待ちを発生させない |
| フロントエンド | ホスト（`npm run dev`） | Vite の HMR をそのまま使う |
| Ollama | DGX Spark にネイティブ | ARM64 + CUDA のコンテナ内GPU利用は検証コストが高い |

### 0. 事前準備

**Mac** — [Docker Desktop](https://www.docker.com/products/docker-desktop/) をインストール（Apple Silicon 版）
```bash
brew install --cask docker
```
インストール後、**Docker Desktop アプリを起動する**。メニューバーのアイコンが Running になっていないとコマンドが失敗する。PC再起動のたびに起動が必要。

**Windows** — Docker Desktop（WSL2 バックエンド）
```powershell
winget install --id Docker.DockerDesktop
wsl --install   # WSL2 が未導入の場合。実行後に再起動
```

### 1. 環境変数

```bash
cp .env.example .env      # Windows: copy .env.example .env
```

`.env` は Git 管理外。**コミットしないこと。**

### 2. データベースを起動

```bash
docker compose up -d
docker compose ps          # STATUS が healthy になれば成功
```

### 3. バックエンド

```bash
cd backend
uv sync                                     # Python 3.12 も自動で取得される
uv run uvicorn app.main:app --reload
```

→ http://127.0.0.1:8000/docs でAPIドキュメントが開く

**埋め込み処理を担当する人だけ**、追加で以下を実行する。
PyTorch を含むため 3〜4GB になるので、必要な人以外は入れない。

```bash
uv sync --group ml
```

### 4. フロントエンド

```bash
cd frontend
npm ci                                      # npm install ではない
npm run dev
```

→ http://localhost:5173 で疎通確認画面が開く

### 5. 環境の確認

http://localhost:5173 を開き、以下が **OK** になっていれば環境構築は完了。

| 項目 | 期待値 |
|---|---|
| フロントエンド | OK |
| バックエンド API | OK |
| データベース | OK |
| pgvector | OK |
| 埋め込み設定 | **NG のままで正常**（モデルと次元数が未確定のため） |

コマンドで確認する場合:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/health/db
curl http://127.0.0.1:8000/health/config
```

### 依存を追加するとき

**バージョンを固定するため、直接インストールしないこと**（[`CLAUDE.md`](CLAUDE.md) 3.2）。

```bash
# バックエンド
cd backend && uv add <パッケージ>          # pip install は使わない

# フロントエンド
cd frontend && npm install <パッケージ>
```

更新された `uv.lock` / `package-lock.json` を必ずコミットする。

---

## Docker の使い方

覚えるのは以下だけでよい。

```bash
docker compose up -d          # 起動
docker compose ps             # 状態確認
docker compose logs -f db     # ログを見る（エラー調査はここ）
docker compose down           # 停止（データは残る）

# DBに直接つなぐ
docker compose exec db psql -U torano -d torano_maki

# テーブルの中身をGUIで見る → http://localhost:8080
docker compose --profile tools up -d
```

### ⚠️ データを消すコマンド

```bash
docker compose down -v        # ボリュームごと削除 = 全データが消える
```

`-v` の有無でデータが消えるかが変わる。**付ける前に必ず確認すること。**

現時点ではマイグレーションツールを導入していないため（[`CLAUDE.md`](CLAUDE.md) 3.1）、
**スキーマを変更したときはDBを作り直す。**

```bash
docker compose down -v && docker compose up -d
```

`docker/initdb/` の SQL は**ボリュームが空のときしか実行されない。**
SQLを追記しても反映されない場合は、上記で作り直す必要がある。

### つまずいたら

| 症状 | 原因 | 対処 |
|---|---|---|
| **`ユーザー"torano"のパスワード認証に失敗しました`** | **別のPostgreSQLに繋がっている**（下記） | `.env` の `POSTGRES_PORT` を別の値に変える |
| `port is already allocated` | 指定ポートが使用中 | `.env` の `POSTGRES_PORT` を変更 |
| `Cannot connect to the Docker daemon` | Docker Desktop が未起動 | アプリを起動する |
| `type "vector" does not exist` | 初期化SQLが未実行 | `docker compose down -v && docker compose up -d` |
| Mac で起動が遅い | Docker Desktop のメモリ不足 | Settings → Resources でメモリを4GB以上に |
| `.env` が読まれない | ファイル名が `.env.txt` になっている | Windowsの拡張子表示をONにして確認 |
| `npm ci` が失敗する | Node のバージョン違い | `.nvmrc` の 22 に合わせる |

> ⚠️ **ポート競合が「エラーなし」で起きることがある**
>
> ネイティブのPostgreSQLが動いている環境では、**Windowsだと Docker と両方が
> 5432 をLISTENできてしまい、「使用中」エラーが出ないままホストからの接続が
> ネイティブ側に吸われる。** 結果、認証エラーだけが出て原因が非常に分かりにくい。
>
> このプロジェクトが既定で **5433** を使っているのはこれを避けるため。
> それでも認証エラーが出る場合は、何が5432/5433を掴んでいるか確認する:
>
> ```bash
> # Windows
> netstat -ano | findstr :5433
> # Mac
> lsof -i :5433
> ```

解決したら [`docs/setup-notes.md`](docs/setup-notes.md) に記録すること。

---

## ディレクトリ構成

```
torano-maki/
├── backend/
│   ├── app/
│   │   ├── main.py             # ルーター登録のみ（ロジックを置かない）
│   │   ├── config.py           # 環境変数
│   │   ├── db.py               # DB接続
│   │   ├── models/             # ★スキーマの単一の源
│   │   │   ├── knowledge.py    #   Pydantic（LLM出力・APIの型を兼ねる）
│   │   │   └── tables.py       #   SQLAlchemy
│   │   ├── api/                # 機能ごとに分割（コンフリクト回避）
│   │   │   ├── health.py       #   疎通確認
│   │   │   ├── ingest.py       #   ナレッジ蓄積
│   │   │   ├── search.py       #   ナレッジ探索
│   │   │   └── roleplay.py     #   ロープレ
│   │   └── services/
│   │       ├── transcription.py  # 音声認識（差し替え可能な単一の窓口）
│   │       ├── extraction.py     # LLM構造化抽出
│   │       ├── embedding.py      # ベクトル化
│   │       ├── search.py         # 検索
│   │       └── roleplay.py       # ペルソナ生成・対話
│   ├── .python-version         # Python 3.12 に固定
│   ├── pyproject.toml
│   └── uv.lock                 # 依存のバージョン固定。必ずコミットする
├── frontend/                   # Vite + React
│   ├── src/
│   │   ├── App.tsx             # 疎通確認画面
│   │   ├── api/client.ts       # API呼び出し口
│   │   └── types/api.ts        # レスポンス型
│   ├── .nvmrc                  # Node 22 に固定
│   └── package-lock.json       # 依存のバージョン固定。必ずコミットする
├── docker/
│   └── initdb/                 # DB初回作成時に実行されるSQL
├── docs/
│   ├── decisions.md            # 技術選定の理由
│   └── setup-notes.md          # 環境構築でハマった内容
├── docker-compose.yml          # PostgreSQL + pgvector
├── .gitattributes              # 改行コード統一（Mac/Windows混在のため）
├── CLAUDE.md                   # Claude Code 向けルール
└── README.md
```

**API・services を機能ごとに分割しているのは、4人が並行作業しても
同じファイルを同時に編集しなくて済むようにするため。**
担当割りは [`CLAUDE.md`](CLAUDE.md) 1.1 を参照。

---

## 開発ルール

Git運用・コーディング規約は [`CLAUDE.md`](CLAUDE.md) を参照。
**作業開始前に必ず目を通すこと。**

## チーム

| 担当 | 名前 |
|---|---|
| <!-- TODO --> | 山口 亮 |
| <!-- TODO --> | 佐藤 拓斗 |
| <!-- TODO --> | 岡本 貴大 |
| <!-- TODO --> | 近藤 優花 |
