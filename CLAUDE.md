# CLAUDE.md

このファイルは Claude Code が本リポジトリで作業する際のルールを定義する。
**4人が同じルールで開発するため、必ず遵守すること。**

---

## 1. プロジェクト概要

営業ナレッジをAI利活用前提の構造で蓄積し、探索・ロープレに活かすプロダクト。
商談録音やテキストから、LLMで構造化ナレッジを生成してPostgreSQLに蓄積する。

詳細は `README.md` を参照。

### 1.1 担当領域（オーナーシップ）

**4人が並行して作業するため、ファイル単位でオーナーを決める。**
自分の担当外のファイルを大きく変更する必要が出た場合は、**実装前に人間へ確認すること。**

| 領域 | 主なファイル | 担当 |
|---|---|---|
| スキーマ定義 | `backend/app/models/` | <!-- TODO --> |
| ナレッジ蓄積（音声・抽出） | `backend/app/api/ingest.py` / `services/transcription.py` / `services/extraction.py` | <!-- TODO --> |
| ナレッジ探索（検索） | `backend/app/api/search.py` / `services/embedding.py` / `services/search.py` | <!-- TODO --> |
| ロープレ | `backend/app/api/roleplay.py` / `services/roleplay.py` | <!-- TODO --> |
| フロントエンド | `frontend/src/` | <!-- TODO --> |
| 環境構築・基盤 | `docker-compose.yml` / `backend/app/config.py` / `db.py` | <!-- TODO --> |

**共有ファイル（変更時は要相談）**
`backend/app/models/` … スキーマの単一の源。初日に確定させ、以後は安易に変えない
`backend/app/main.py` … ルーター登録のみに留め、ロジックを置かない
`CLAUDE.md` / `README.md` … 変更したらチーム全員に共有する

---

## 2. 実行環境の制約（重要）

**NVIDIA DGX Spark**

詳細は2026/08/21に記載する

環境構築で判明した制約は `docs/setup-notes.md` に追記すること。

---

## 3. 技術スタック

以下を使用する。**チームの合意なく別のライブラリやフレームワークを導入しないこと。**

| 層 | 技術 |
|---|---|
| LLM推論 | DGX Spark 上の vLLM（OpenAI互換API） |
| 埋め込み | sentence-transformers（**各自のPCのCPUで実行**） |
| バックエンド | FastAPI / Pydantic / SQLAlchemy |
| フロントエンド | Vite / React / TypeScript |
| CSS | Tailwind CSS v4（Viteプラグイン。設定は `src/index.css` の `@theme`） |
| データストア | PostgreSQL + pgvector |
| Python管理 | uv |
| DBドライバ | psycopg[binary] / pgvector |
| 設定管理 | pydantic-settings（`.env` を読む） |
| 非同期ジョブ | FastAPI BackgroundTasks + `jobs` テーブル |
| DB構築 | Docker Compose（`pgvector/pgvector` イメージ） |

新しい依存が必要になった場合は、**追加せずに理由を説明して確認を求めること。**

### 3.1 意図的に採用しないもの

以下は**この規模では過剰**と判断した。勝手に導入しないこと。

- **Celery / Redis** … 非同期は BackgroundTasks + DBのジョブ管理で足りる
- **認証・ユーザー管理** … デモ範囲外。ログイン機能は作らない
- **ORM以外のマイグレーションツール** … 初期はスキーマ変更時にDBを作り直す（データは捨てる前提）

### 3.2 環境の揃え方（厳守）

**バージョンはロックファイルで固定する。** 各自が同じコマンドを打つだけでは揃わない。

| 対象 | 固定するもの | バージョン |
|---|---|---|
| Python | `backend/.python-version` | 3.12 |
| Pythonの依存 | `backend/uv.lock` | — |
| Node | `frontend/.nvmrc` | 24（LTS） |
| Nodeの依存 | `frontend/package-lock.json` | — |

- **`pip install` を使わない。必ず `uv add <パッケージ>`**
  `pip install` では `uv.lock` が更新されず、その人の環境だけ動く状態になる
- **フロントの環境再現は `npm ci`。`npm install` は依存を追加するときだけ**
  `npm install` はロックファイルを書き換えるため、再現目的では使わない
- **`uv.lock` / `package-lock.json` は必ずコミットする**
- **ロックファイルがコンフリクトしたら手で直さない。**
  `main` 側を採用したうえで `uv add` / `npm install` をやり直す
- FastAPIの依存性注入は `Annotated` で書く（`Depends()` を引数の既定値に直接書かない）

### 3.3 確定した重要事項

| 項目 | 値 |
|---|---|
| 埋め込みモデル | `intfloat/multilingual-e5-large` |
| **埋め込みの次元数** | **1024** |

**次元数は3箇所で一致していなければならない。**

| 場所 | 内容 |
|---|---|
| `docker/initdb/02_schema.sql` | `vector(1024)` ← **DDLの正** |
| `backend/app/config.py` | `DEFAULT_EMBEDDING_DIM` |
| `.env` | `EMBEDDING_DIM` |

ズレたまま動かすと**ベクトルを挿入する瞬間まで誰も気づけない。**
`/health/db` の `embedding_dim_matches` で検知できるので、
おかしいと思ったらまずここを見ること。

変更するには DB の作り直しが必要（3.1 の方針どおりデータは捨てる）。

```bash
docker compose down -v && docker compose up -d
```

**`e5` 系はプレフィックスが必要。** 付け忘れると精度が落ちるが、
エラーにはならないので気づきにくい。`services/embedding.py` の中に閉じ込め、
呼び出し側が意識しなくてよい形にすること。

```python
"passage: <本文>"   # 保存するとき
"query: <検索文>"   # 検索するとき
```

### 3.4 未確定事項（決まるまで実装を進めない）

| 項目 | 状態 | 影響 |
|---|---|---|
| 音声認識ライブラリ | ❌ 未確定 | ARM64 + CUDA の対応状況を検証してから決定 |

---

## 4. Git / GitHub 運用

### 4.1 Claude Code の権限境界

| 操作 | 可否 |
|---|---|
| ブランチ作成・切り替え | ✅ 自動で行ってよい |
| ファイルの編集 | ✅ 自動で行ってよい |
| commit | ⚠️ 変更内容を提示し、承認を得てから |
| feature branch への push | ✅ commit承認後は自動でよい |
| `gh` によるPR作成 | ✅ 自動で行ってよい |
| **`main` への merge** | ❌ **人間が行う。Claude Codeは実行しない** |
| コンフリクトの解決 | ❌ **内容を提示して確認を求める** |

### 4.2 禁止事項（厳守）

- **`main` への直接コミット・直接pushは禁止**
- **`git push --force` / `--force-with-lease` は禁止**
- **`git reset --hard` / `git checkout .` / `git clean` / 履歴を書き換える `rebase` を独断で実行しない**
- **`git add .` を使わない。** ファイルを明示して `git add` すること
- **他メンバーのブランチを操作しない**
- **秘密情報をコミットしない**（`.env`、APIキー）
- **巨大ファイルをコミットしない**（音声ファイル、GGUFなどのモデル重み、データセット）
  → 一度コミットすると履歴から消えず、リポジトリが恒久的に肥大化する

### 4.3 作業開始時

1. `git status` で作業ツリーがクリーンか確認する
2. **`git branch --show-current` で現在のブランチを確認する**
3. `git switch main && git pull` で `main` を最新化する
4. **4.10 の「取り込んだ後の確認」を実施する**
5. `main` から feature branch を作成する
6. **他メンバーの担当領域（1.1）を確認し、不必要に変更しない**

**2 は毎回必ず行うこと。** 前回の作業でブランチを切り替えたまま、あるいは
PRがマージされて `main` に戻ったまま、という状態は頻繁に起きる。
確認せずに編集を始めると `main` への直接コミット（4.2の禁止事項）に直結する。

### 4.4 ブランチ

`main` から作業ブランチを切り、Pull Request でマージする。

| 接頭辞 | 用途 |
|---|---|
| `feat/` | 機能追加 |
| `fix/` | バグ修正 |
| `docs/` | ドキュメント |
| `refactor/` | 動作を変えない整理 |
| `chore/` | 設定・依存関係 |

- 英小文字とハイフンのみ。例: `feat/knowledge-extraction`
- **個人名を使わない**
- **1ブランチ1目的**
- 長期保持せず、動作する単位で早めにPRを作成する

### 4.5 作業中

- **他メンバーと同じファイルを大きく編集する必要がある場合は、実装前に人間へ確認する**
- 無関係なリファクタリングやフォーマット変更を同じPRに含めない
- 変更はできるだけ小さく保つ

### 4.6 commit 前の確認

1. `git status` で対象ファイルを確認し、**内容をユーザーに提示する**
2. `git diff` で変更内容を確認する
3. **意図しないファイルが含まれていないか確認する**（音声、モデル、キャッシュ、`.env`）
4. lint / テストを実行する。**失敗した場合は原則commitしない**

   ```bash
   cd backend  && uv run ruff check . && uv run ruff format --check . && uv run pytest -q
   cd frontend && npm run lint && npm run build
   ```

   テストはトランザクションでロールバックされるため、登録済みのデータは消えない
5. ユーザーの承認を得てからcommitする

### 4.7 commit メッセージ

Conventional Commits 形式。日本語で書く。

```
feat: 商談音声から構造化ナレッジを抽出する処理を追加
fix: 長時間音声でタイムアウトする問題を修正
docs: ARM64環境のセットアップ手順を追記
refactor: 埋め込み処理をservices層に切り出し
chore: pgvectorの依存を追加
```

- 1行目は50文字以内
- 1コミット1目的
- 必要なら本文で「なぜそうしたか」を説明する

### 4.8 Pull Request

- 作業完了後、`gh pr create` でPRを作成する
- base branch は `main`
- タイトルは commit メッセージと同じ形式
- 本文に以下を含める
  - **変更内容**
  - **動作確認内容**
  - **注意点・レビューしてほしい箇所**
- **マージ前に最低1人のレビューを受ける**
- **merge は人間が行う。** Claude Code はPR作成までで停止する
- merge後は不要になった feature branch を削除する

### 4.9 main の取り込みとコンフリクト対策

#### `pull` は `main` の上でだけ使う

| ブランチ | コマンド |
|---|---|
| `main` | `git pull` |
| feature branch | **`git pull` を使わない** → `git fetch origin` + `git merge origin/main` |

`git pull` は「fetch + **現在のブランチの上流**を merge」する。
feature branch 上で `git pull` しても取り込まれるのは `origin/自分のブランチ`
（＝自分が push したもの）だけで、**`main` の変更は一切入らない。**
欲しいのは `main` の最新なので、明示的に `git merge origin/main` すること。

#### 取り込むタイミング

| いつ | どこで | コマンド |
|---|---|---|
| 作業開始時（毎回） | `main` | `git switch main && git pull` |
| **PR作成の直前（必須）** | feature branch | `git fetch origin && git merge origin/main` |
| 他メンバーのPRがマージされたと聞いたら | feature branch | 同上 |
| 1日の作業を始めるとき | feature branch | 同上 |

**PR作成前の取り込みは必須。** 自分のブランチでコンフリクトを解消してから出せば、
レビュアーは競合のないPRを見られる。省くとGitHub上で競合表示が出てレビューが止まる。

**放置しないこと。** Claude Code は短時間で大きな差分を作るため、
取り込みを先延ばしにするとコンフリクトの解消コストが急激に上がる。
半日以上 `main` を取り込んでいない状態を作らない。

#### コンフリクトが起きたら

- **`rebase` ではなく `merge` を使う**（履歴書き換えによる事故を防ぐため）
- **他メンバーの変更を推測して削除しない。** 必ず内容を提示して人間に確認する
- **`uv.lock` / `package-lock.json` が競合したら手で直さない。**
  `main` 側を採用したうえで `uv add` / `npm install` をやり直す
  （ロックファイルは解決結果であり、手動マージすると整合性が壊れる）

### 4.10 取り込んだ後の確認（重要）

**`pull` / `merge` しただけでは動かないことがある。**
取り込んだ差分に以下が含まれていたら、対応する作業を行うこと。

| 変わったファイル | 必要な作業 | 忘れるとどうなる |
|---|---|---|
| `backend/uv.lock` | `cd backend && uv sync` | 依存が足りず `ImportError` |
| `frontend/package-lock.json` | `cd frontend && npm ci` | ビルドが通らない |
| `docker/initdb/` の SQL | `docker compose down -v && docker compose up -d` | スキーマが古いまま |
| `backend/app/models/` | 上と同じ（スキーマ変更を伴う場合） | 挿入時にエラー |
| **`.env.example`** | **`.env` を手で更新する** | **原因の分かりにくい接続エラー** |
| `CLAUDE.md` | **Claude Code を再起動する** | 古いルールで作業される |

#### `.env.example` が特に危険

`.env` は `.gitignore` されているため、**`pull` しても自動では更新されない。**
`.env.example` に項目が増えたりポート番号が変わったりしても、
自分の `.env` は古いまま残る。症状が接続エラーとして現れ、原因を特定しにくい。

差分の確認:

```bash
git pull                      # 出力に出るファイル一覧を見る
git diff HEAD@{1} --stat      # 後から確認したい場合
```

#### `main` にマージしたら、チームに何をすべきか伝える

上記の表に該当する変更を `main` に入れた場合、**マージした人がチームに宣言する。**
「マージしました」だけでは各自が差分を読んで判断することになり、必ず誰かが漏らす。

```
chore/xxx を main にマージしました。
pull後、cd frontend && npm ci を実行してください。
```

---

## 5. コーディング規約

### Python

- 型ヒントを必ず書く
- Pydanticモデルでデータ構造を定義する。dictをそのまま受け渡さない
- 関数は単一責任。長くなったら分割する
- docstringは日本語で、**何をするかではなく「なぜ必要か」を書く**

### TypeScript

- `any` を使わない
- APIのレスポンス型はバックエンドのOpenAPIスキーマと一致させる
- コンポーネントは関数コンポーネントで書く

### 共通

- **コメントは「なぜ」を書く。「何を」はコードで表現する**
- 変数名・関数名は英語、コメントとdocstringは日本語

---

## 6. 設計上の原則

- **ナレッジのスキーマ定義を3箇所に散らかさない。** 役割ごとに正を決める

  | 何の正か | ファイル |
  |---|---|
  | ナレッジの構造（LLMへのJSON Schema・APIの型・フロントの型） | `backend/app/models/knowledge.py`（Pydantic） |
  | **DDL**（列・制約・インデックス・トリガー） | `docker/initdb/02_schema.sql` |
  | 上のDDLに対応するクエリ用の定義 | `backend/app/models/tables.py`（SQLAlchemy） |

  `Base.metadata.create_all()` は**使わない。** DDLが2箇所に存在すると必ず食い違うため
  （理由は `docs/decisions.md`）。列を増やすときは
  **SQL → `tables.py` → `knowledge.py` の3つを揃えて直し**、DBを作り直す。
  ズレていないかは `/health/db` で確認できる
- 音声処理は時間がかかるため、**HTTPリクエスト内で完結させない。** ジョブIDを返して非同期で処理する
- 音声認識ライブラリは差し替えの可能性があるため、**呼び出し口を1つの関数に集約する**
- ナレッジ検索の結果には**必ず出典（source_id）を含める**

---

## 7. 作業の進め方

- **大きな変更を一度に行わない。** 1つの機能を動かしてから次に進む
- 実装前に方針を説明し、確認を取ってからコードを書く
- 既存コードを大幅に書き換える提案をする場合は、**理由を明示して確認を求めること**
- **動作確認していないコードを「完成した」と報告しない**

---

## 8. このファイルの更新

CLAUDE.md を変更した場合は、**チーム全員に共有すること。**
ルールの追加・変更、および技術選定の判断は、理由を `docs/decisions.md` に残す。

> ⚠️ **CLAUDE.md はセッション開始時にしか読み込まれない。**
> `pull` でこのファイルが更新されても、**起動中の Claude Code は古い内容のまま動く。**
> ルールが変わったら Claude Code を再起動すること（4.10）。
> 変更をマージした人は、共有時に「Claude Code の再起動が必要」と明記する。

**「何を選んだか」ではなく「なぜ選んだか」を書く。**
経緯を知らないメンバーが、良かれと思って別の方向へ進めてしまうのを防ぐため。

- 技術選定・設計判断の理由 → `docs/decisions.md`
- 環境構築でハマった内容と解決策 → `docs/setup-notes.md`