# 商談文字起こしからのナレッジ抽出検証

`sales_demo_perturn.wav` の既存文字起こし `medium_glossary.json` をDGX Spark上の
OpenAI互換LLMへ渡し、ER図に対応する5種類のレコードをJSONとして生成する。

このディレクトリは検証専用であり、既存のDB・DDL・バックエンドのスキーマは変更しない。
FastAPIへ引き継ぐためのER図、ファイル配置、処理フローは
[`docs/knowledge-extraction-design.md`](../../docs/knowledge-extraction-design.md) にまとめている。

## 生成するデータ

- `data_sources`
- `utterance_segments`
- `knowledge_units`
- `knowledge_evidence`
- `call_summaries`

Pydanticの入出力契約は [`schema.py`](schema.py) に分離している。実行スクリプト、
テスト、コミット済みの出力が同じモデルを使うため、構造のずれを検出できる。

LLMには話者、ナレッジ、根拠の発話番号、商談要約だけを生成させる。
UUIDと外部キーはスクリプト側で決定的に生成するため、LLMが存在しないIDを作ることはない。

`search_text` は抽出した各項目を連結して生成する。DGXのvLLMは埋め込みAPIを提供しないため、
この検証では `embedding` と `embedding_model` は `null` のままにする。

## 入力データ

次のファイルを `input/` に置く。

```text
input/
├── sales_demo_perturn.wav   # git管理しない
└── medium_glossary.json     # git管理する
```

`medium_glossary.json` はgit管理する。音声から再生成するのに手間がかかるうえ、
メンバーごとに文字起こしが違うと抽出結果を比較できなくなるため、
検証の入力を固定する。音声は容量が大きいため管理しない。

次のファイルからコピーした。音声は作業開始時のブランチにだけ存在したもの。

```text
tts-demo/output/sales_demo_perturn.wav
experiments/stt/output/medium_glossary.json
```

## 実行

リポジトリ直下の `.env` に `BASE_URL` と `MODEL_NAME` が必要。

```bash
cd experiments/knowledge-extraction
uv sync
uv run python run_experiment.py
```

`.env` を変更せず、1回の実行だけモデル名を上書きすることもできる。

```bash
uv run python run_experiment.py --model-name Qwen3.8-27B-NVFP4
```

生成物は `output/knowledge_extraction.json` に保存される。このファイルはgit管理するため、
実行していないメンバーも結果を読める。LLMの生応答は `output/raw_attempt_<N>.json` に
保存するため、パースに失敗した場合も原因を確認できる。こちらは実行のたびに変わるので
git管理しない。

コミット済みの `output/knowledge_extraction.json` は、2026-08-24に成功した結果である。
FastAPI実装時の入出力例と回帰テストに使う。`run_experiment.py` を実行すると上書きされるため、
再実行後は `git diff` で意図しない差分が出ていないか確認すること。

出力制約はプロンプトだけに依存せず、Pydanticによる次の検証を行う。

- 未定義キーがない
- 全発話に話者が重複なく割り当てられている
- 根拠の開始・終了発話が実在し、順序が正しい
- 必須項目と型がER図に対応している

検証に失敗した場合はエラー内容をLLMへ返し、既定で最大3回まで修正を試す。

## 2026-08-24の検証結果

`medium_glossary.json`（74セグメント）を `Qwen3.8-27B-NVFP4` へ渡した。
1回目の応答がすべての構造検証を通り、再試行は発生しなかった。

| 項目 | 結果 |
|---|---:|
| 実行時間 | 183.2秒 |
| プロンプト | 5,114 tokens |
| 出力 | 4,042 tokens |
| ナレッジ | 5件 |
| 根拠範囲 | 8件 |
| 話者割当 | salesperson 45件 / customer 29件 / unknown 0件 |

抽出された主題は、在庫データと実態の乖離、在庫判断の属人化、課題の再定義、
段階的な導入提案、現場担当者へのヒアリングだった。いずれも実在する発話範囲を
根拠として参照しており、このサンプルではナレッジ抽出の形を作れることを確認できた。

最初の実行では、リポジトリ直下の `.env` から旧モデル名
`DiffusionGemma-NVFP4` が読み込まれ、DGXからHTTP 404が返った。検証時はプロセス内だけ
`Qwen3.8-27B-NVFP4` に上書きした。再実行前に、実際に読み込まれる `.env` が
リポジトリ直下のものか確認すること。

### この検証で残る制約

- 話者は音響的な話者分離ではなく、文字起こしの文脈からLLMが推定している。
- `occurred_at` は商談日時を入力データが持たないため、音声ファイルの更新日時を使う。
- 埋め込みは生成していない。`search_text` からローカルの埋め込みモデルで別途生成する。
- LLM出力の事実性は自動判定していない。根拠発話をUIで人間が確認できる設計が必要。

## ローカルテスト

```bash
uv run ruff check .
uv run pytest -q
```
