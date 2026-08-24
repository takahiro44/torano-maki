# 商談デモ音声の生成（tts-demo）

商談台本（`dialogue.json`）から、Google Cloud の Gemini TTS で
2話者の商談音声 `output/sales_demo.wav` を1本生成する。

営業ナレッジ抽出の検証用デモデータ。**登場する企業・人物・会話内容はすべて架空。**

## 構成

| ファイル | 役割 |
|---|---|
| `generate_tts.py` | 生成スクリプト |
| `dialogue.json` | 台本（`speaker` は `Sales` / `Customer` のみ） |
| `requirements.txt` | 依存（`google-cloud-texttospeech`） |
| `output/` | 生成物。**git管理外**（`.gitignore` 済み） |

## 設定

| 項目 | 値 |
|---|---|
| Model | `gemini-3.1-flash-tts-preview` |
| Language | `ja-JP` |
| Encoding / Sample rate | `LINEAR16` / 22050 Hz |
| Sales（大塚商会の30代男性営業） | `Achird` |
| Customer（顧客企業の40〜50代男性業務部長） | `Algieba` |

話し方の指示は `generate_tts.py` の `PROMPT` / `SPEAKER_PROMPTS` にある。

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
uv run generate_tts.py --mode per-turn
```

動作確認だけしたいときは先頭N発話に絞れる（APIの呼び出し回数を減らせる）。

```bash
uv run generate_tts.py --mode per-turn --limit 4
```

## 出力

```text
output/part_01.wav …  分割生成した中間ファイル（結合後も残す）
output/sales_demo.wav 最終成果物
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

1. `dialogue.json` を `Sales: 本文` / `Customer: 本文` の行に変換する
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

## 台本を差し替えるには

`dialogue.json` を編集するだけでよい。`speaker` は `Sales` / `Customer` のいずれか。
それ以外の話者名があると、合成前にエラーで止まる。
