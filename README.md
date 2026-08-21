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

着手前に決める必要があるもの。詳細は [`CLAUDE.md`](CLAUDE.md) 3.2 を参照。

- **音声認識ライブラリ** — ARM64 + CUDA で動くものを検証して決定する
- **埋め込みモデルと次元数** — `vector(N)` の定義に必要。**これが決まらないとDBを作れない**

## 実行環境

**NVIDIA DGX Spark**
詳細は2026/08/21に記載予定

x86_64向けのビルド済みパッケージが利用できない場合がある。
環境構築でハマった内容は [`docs/setup-notes.md`](docs/setup-notes.md) に記録すること。

---

## セットアップ

<!-- TODO: 環境構築が固まったら手順を記載 -->

```bash
# バックエンド
cd backend
uv sync
uv run uvicorn app.main:app --reload

# フロントエンド
cd frontend
npm install
npm run dev
```

### 環境変数

`.env.example` をコピーして `.env` を作成する。

```bash
cp .env.example .env
```

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
│   │   │   ├── ingest.py       #   ナレッジ蓄積
│   │   │   ├── search.py       #   ナレッジ探索
│   │   │   └── roleplay.py     #   ロープレ
│   │   └── services/
│   │       ├── transcription.py  # 音声認識（差し替え可能な単一の窓口）
│   │       ├── extraction.py     # LLM構造化抽出
│   │       ├── embedding.py      # ベクトル化
│   │       ├── search.py         # 検索
│   │       └── roleplay.py       # ペルソナ生成・対話
│   └── pyproject.toml
├── frontend/                   # Vite + React
│   └── src/
├── docs/                       # 設計メモ・環境構築記録
├── docker-compose.yml          # PostgreSQL + pgvector
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
