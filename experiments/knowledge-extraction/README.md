# 商談文字起こしからのナレッジ抽出検証

商談の文字起こしをDGX Spark上のOpenAI互換LLMへ渡し、ER図に対応する
5種類のレコードをJSONとして生成する。

**商談22件ぶんのデータセットが `output/meetings/` にコミットしてある。**
チームで同じデータを見るための正であり、使い方は
[「チームで同じデータを使う」](#チームで同じデータを使う)を参照。
1件だけを対象にした当初の検証は [`run_experiment.py`](run_experiment.py) に残っている。

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

```text
input/
├── transcripts/          # 商談22件の文字起こし。git管理する
│   ├── 01_order_entry.json
│   └── long_001.json ...
└── medium_glossary.json  # 当初の検証1件ぶん。git管理する
```

**文字起こしはgit管理する。** 音声から再生成するのに手間がかかるうえ、
メンバーごとに文字起こしが違うと抽出結果を比較できなくなるため、入力を固定する。
**音声は `.gitignore` で除外する**（CLAUDE.md 4.2）。

音声は `tts-demo/output/` に置く。台本が `tts-demo/scripts/*.json` にあるので、
手元に無い場合は [`tts-demo`](../../tts-demo/) で再生成できる。

### 文字起こしのやり直し

`backend/scripts/transcribe_meetings.py` が `tts-demo/output/*.wav` をまとめて
文字起こしし、ここへ書き出す。DGX上の faster-whisper を
`app.services.transcription.transcribe()` 経由で呼ぶため、
**本番と同じ用語集・VAD設定**になる。

```bash
cd backend
uv run python scripts/transcribe_meetings.py              # 未処理のものだけ
uv run python scripts/transcribe_meetings.py --only long_001 --overwrite
```

`.env` に `STT_BASE_URL` / `STT_MODEL` が必要。22件・音声129分で約11分（実時間の12倍速）。

## チームで同じデータを使う

**JSONを `git pull` しただけでは画面に何も出ない。** JSONとDBは繋がっておらず、
投入スクリプトを実行して初めてフロントから見える。埋め込みは各自のPCのCPUで
生成するため（DGXのvLLMは埋め込みAPIを配信していない）、この一手間が要る。

```bash
cd backend
uv run python scripts/load_extraction_json.py            # 商談22件をまとめて投入
uv run python scripts/load_extraction_json.py --replace  # 入れ直す
```

引数なしで `output/meetings/` を丸ごと、**1トランザクションで**投入する。
1件ずつ指定すると埋め込みモデル（2.2GB）の読み込みが件数分走るため、まとめて渡すこと。

> **`output/knowledge_extraction.json` は既定では読まない。**
> これは2026-08-24の検証結果で、`output/meetings/01_order_entry.json` と**同じ商談**
> （どちらも `tts-demo/scripts/01_order_entry.json` の台本から合成した音声）。
> 両方入れると同じナレッジが検索結果に二重に出る。回帰テストの基準として残してある。

## 実行

リポジトリ直下の `.env` に `BASE_URL` と `MODEL_NAME` が必要。

### 22件をまとめて抽出する

```bash
cd experiments/knowledge-extraction
uv sync
uv run python run_batch.py                      # 未処理のものだけ
uv run python run_batch.py --only long_001 --overwrite
uv run python run_batch.py --concurrency 1      # DGXを占有したくないとき
```

`input/transcripts/*.json` を順に抽出し、**商談ごとに1ファイル**を
`output/meetings/` へ書く。1つの大きなJSONにまとめないのは、差分レビューと
再実行を商談単位で行いたいため（1件やり直すと他21件の差分が出ると読めない）。

抽出ロジックは [`run_experiment.py`](run_experiment.py) の関数をそのまま呼ぶ。
検証済みの処理を二重に持つと、片方だけ直されて結果が食い違うため。

**所要時間は出力トークン数で決まる**（DGX Sparkで約20 tok/s）。
音声5〜9分の商談で1件あたり約3分、22件を同時実行3本で約30分。

> ⚠️ **60分の商談は現状のままでは通らない。**
> `speaker_assignments` を全発話ぶん出力させているため、出力トークンが
> 発話数に比例する（≒ 発話数×37 + ナレッジ件数×660）。60分・約530発話だと
> 2万トークンを超え、`--max-tokens` の既定12000で切れてJSONが壊れる。
> デモ音声が5〜9分なので今は問題になっていないだけ。

### 1件だけ抽出する（当初の検証）

```bash
uv run python run_experiment.py
uv run python run_experiment.py --model-name Qwen3.8-27B-NVFP4
```

生成物は `output/knowledge_extraction.json`。LLMの生応答は
`output/raw/<商談名>/raw_attempt_<N>.json` に残るので、パースに失敗した場合も
原因を確認できる。こちらは実行のたびに変わるのでgit管理しない。

出力制約はプロンプトだけに依存せず、Pydanticによる次の検証を行う。

- 未定義キーがない
- 全発話に話者が重複なく割り当てられている
- 根拠の開始・終了発話が実在し、順序が正しい
- 必須項目と型がER図に対応している

検証に失敗した場合はエラー内容をLLMへ返し、既定で最大3回まで修正を試す。

## 2026-08-26のデータセット（商談22件）

22件すべてが1回目の応答で構造検証を通り、再試行は発生しなかった。

| 項目 | 結果 |
|---|---:|
| 商談 | 22件（音声129分） |
| 発話 | 1,078件 |
| ナレッジ | 119件 |
| 根拠範囲 | 158件 |
| 文字起こし | 約11分（DGX / 実時間の12.2倍速） |
| 抽出 | 約35分（同時実行3本） |

ナレッジの型は `sales_technique` 25 / `pain_point` 22 / `operational_insight` 21 /
`customer_need` 15 / `next_action` 14 / `proposal` 13 / `decision` 9。

商談のテーマは販売管理・在庫・FAX受注OCR・月次決算・給与計算・承認ワークフロー・
契約書管理・商談引き継ぎ・図面データ送信・Wi-Fi障害・セキュリティ研修・
ランサムウェア・IT運用委託・文書電子化・備品購買・安否確認・会議調整・経費精算。

**同じ型が別々の商談から独立して出ている。** 「即座に製品を出さず課題の分解から
始める」が3件、「スモールスタートで抵抗を下げる」が5件。検索やロープレで
横断的に束ねる余地があることを示している。

### ⚠️ 製品名がカタカナに崩れている

22件すべてで確認できた。`search_text` にもそのまま入るため、
**「SMILE V」で検索してもヒットしない可能性がある。**

| 出力 | 正しい表記 |
|---|---|
| エスマイルVセカンドエディション | SMILE V 2nd Edition |
| EvaluV Second Edition Scheduler | eValue V 2nd Edition Scheduler |
| エニフォームOCR | （要確認） |

`experiments/stt/README.md` の「製品名は用語集でも直らない。ただし音と構造は
保たれるので製品マスタでの後処理置換で直せる」がそのまま再現した形。
**このデータセットは未修正のまま入っている。** 置換テーブルを作れば22件まとめて直せる。

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
