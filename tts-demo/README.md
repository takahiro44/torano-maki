# 商談デモ音声の生成（tts-demo）

商談台本（`scripts/*.json`）から、Google Cloud の Gemini TTS で
2話者の商談音声を1本生成する。台本は複数あり、`--script` で選ぶ。

営業ナレッジ抽出の検証用デモデータ。**登場する企業・人物・会話内容はすべて架空。**

## 構成

| ファイル | 役割 |
|---|---|
| `generate_tts.py` | 生成スクリプト（台本に依存しない） |
| `scripts/01_order_entry.json` | 台本①（受注業務の課題ヒアリング / 47発話） |
| `scripts/02_stock_leadtime.json` | 台本②（在庫・納期回答の業務改善 / 55発話） |
| `requirements.txt` | 依存（`google-cloud-texttospeech`） |
| `output/` | 生成物。**git管理外**（`.gitignore` 済み） |

## 設定

| 項目 | 値 |
|---|---|
| Model | `gemini-3.1-flash-tts-preview` |
| Language | `ja-JP` |
| Encoding / Sample rate | `LINEAR16` / 22050 Hz |

**音声IDと人物像は台本JSONの `speakers` に持たせている。**
台本ごとに話者の性別・年代が違うため、スクリプト側に固定値を置くと
台本を増やすたびにコードを書き換えることになる。

| 台本 | Sales | Customer |
|---|---|---|
| ① 受注業務 | `Achird`（30代男性営業） | `Algieba`（40〜50代男性業務部長） |
| ② 在庫・納期 | `Kore`（30代女性営業 高橋美咲） | `Algieba`（50代男性業務部長 中村） |

話し方の指示は `generate_tts.py` のテンプレート
（`MULTI_SPEAKER_PROMPT` / `SINGLE_SPEAKER_PROMPT`）に
JSONの `role` と `persona` を差し込んで組み立てる。

## 前提（認証）

**認証情報はコードに書かない。ADC（Application Default Credentials）を使う。**
サービスアカウントのJSON鍵はリポジトリに置かないこと。

1. Google Cloud プロジェクトで Cloud Text-to-Speech API を有効化する
2. ADC でログインする

   ```bash
   gcloud auth application-default login
   gcloud auth application-default set-quota-project <PROJECT_ID>
   ```

## 実行

```bash
cd tts-demo
uv venv && uv pip install -r requirements.txt   # 初回のみ
uv run generate_tts.py --script 02_stock_leadtime --mode per-turn
```

`--script` には `scripts/` 配下の名前かパスを渡す。
名前を間違えると、利用できる台本の一覧を出して止まる。

動作確認だけしたいときは先頭N発話に絞れる（APIの呼び出し回数を減らせる）。

```bash
uv run generate_tts.py --script 02_stock_leadtime --mode per-turn --limit 4
```

途中で失敗したときは `--resume` で、既にある `part_NN.wav` を作り直さずに再開できる。

## 出力

**出力先は台本ごとに分かれる。** 共有すると、合成前の `part_*.wav` の
掃除で別の台本の中間ファイルまで消えてしまうため。

```text
output/02_stock_leadtime/part_01.wav …  分割生成した中間ファイル（結合後も残す）
output/02_stock_leadtime.wav            最終成果物
```

## 2つの合成モード

**Cloud Text-to-Speech の入力上限は 4000 bytes**（`input.text` / `input.prompt`
それぞれ。超えると `400 InvalidArgument`）。台本全文は約8.9KBあり1回では送れない。
そして**リクエストをまたぐと声の同一性が保証されない。**
分割の仕方でこの影響が変わるため、モードを2つ用意している。

| モード | 分割の単位 | 声の同一性 | 掛け合いの自然さ |
|---|---|---|---|
| `--mode per-turn`（推奨） | 1発話ごと | 高い。プリセット音声を名前で固定するため人物が入れ替わらない | やや劣る |
| `--mode chunk` | 約3800 bytesごと | **低い。継ぎ目で別人の声になることがある** | 高い |

`chunk` モードで実測したところ、チャンクの境界（5:08 など）で声質と音量が
はっきり変わった。パートごとの RMS は 2772 / 1800 / 3101 / 3588 とばらついていた。
`per-turn` は `VoiceSelectionParams(name=...)` でプリセット音声を直接指定するため、
リクエストが分かれても同じ声が使われる。

### chunk モードの分割ルール

1. 台本を `Sales: 本文` / `Customer: 本文` の行に変換する
2. **UTF-8 の byte数**で数える
   （日本語は1文字3byteなので、文字数で判断すると3倍近く見誤る）
3. 声が変わる継ぎ目を減らすため、**最小のチャンク数**へできるだけ**均等に**分ける。
   極端に短いチャンクは声の揺れが大きくなるため作らない
4. **発話の途中では絶対に切らない。** 話者が入れ替わる境界でのみ区切る

チャンクサイズは `--chunk-bytes` で変えられる（上限 4000）。

## 結合と音量調整

標準ライブラリの `wave` で結合する。継ぎ目が詰まって聞こえないよう、
パート間に 250ms の無音を挟む。

リクエストごとに音量が変わるため、既定で全パートの平均音量（RMS）に揃えてから
結合する。クリップしないようゲインは頭打ちにしている。
無効にするには `--no-normalize`。

再合成せずに結合だけやり直したいときは `--concat-only`（`output/part_*.wav` を使う）。

## 台本を追加するには

`scripts/` にJSONを1つ足すだけでよい。`generate_tts.py` は変更しない。

```json
{
  "title": "在庫・納期回答の業務改善ヒアリング",
  "speakers": {
    "Sales":    { "voice": "Kore",    "role": "営業担当者", "persona": "30代女性の法人営業担当者で、…" },
    "Customer": { "voice": "Algieba", "role": "顧客側",     "persona": "50代男性の業務部長で、…" }
  },
  "turns": [
    { "speaker": "Sales", "text": "…" }
  ]
}
```

- `persona` は「Salesは<persona>」「話し手は<persona>」の形で文中に埋め込まれる。
  そのまま文が続く書き方にすること
- `turns` の `speaker` は `speakers` に定義したキーだけ。
  未定義の話者名があると、**合成を始める前に**エラーで止まる
- 1発話が上限（既定 3800 bytes）を超えるとエラーになる。台本側で分ける
