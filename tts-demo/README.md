# 商談デモ音声の生成（tts-demo）

商談台本から、Google Cloud の Gemini TTS で2話者の商談音声を1本生成する。

台本は ChatGPT に書かせる。**その出力JSONを `drafts/` に置いて1コマンド打てば、
検証・正規化から音声の生成・結合までが通る。**
**数本をまとめた1つのJSONで渡してよい。** 入っている本数ぶん音声が出る。

```bash
uv run make_audio.py drafts/03_bundle.json
#  → scripts/03_delivery_delay.json, scripts/03_stock_visibility.json …（正規化した台本）
#  → output/03_delivery_delay.wav,   output/03_stock_visibility.wav   …（音声）
```

営業ナレッジ抽出の検証用デモデータ。**登場する企業・人物・会話内容はすべて架空。**

## 構成

| ファイル | 役割 |
|---|---|
| `prompts/script_prompt.md` | ChatGPT に台本を書かせるプロンプト（**まずここ**） |
| `drafts/` | ChatGPT の出力の置き場。**git管理外**（正は `scripts/`）。数本の束でよい |
| `import_script.py` | 下書きJSONを検証・正規化して `scripts/` に置く |
| `make_audio.py` | 取り込みから音声生成までを1コマンドで通す |
| `generate_tts.py` | 合成の本体（台本に依存しない） |
| `check_audio.py` | 生成した音声を機械的に点検する（聴く前の足切り） |
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

## 台本から音声を作る

```bash
cd tts-demo
uv venv && uv pip install -r requirements.txt   # 初回のみ
```

1. `prompts/script_prompt.md` のプロンプトを ChatGPT に貼り、末尾の設定だけ書き換える
2. 返ってきた JSON を `drafts/<番号>_<英小文字の名前>.json` に保存する
3. 音声にする

   ```bash
   uv run make_audio.py drafts/03_bundle.json
   ```

**まず声を聴いてから全部生成すること。** 先頭N発話だけ作れば、APIの呼び出しは
その数で済む。人物像や声が想像と違ったら台本JSONを直してからやり直す。

```bash
uv run make_audio.py drafts/03_bundle.json --limit 4
```

| 使いたい場面 | 指定 |
|---|---|
| 合成せず、台本の検証だけしたい（APIを呼ばない） | `--dry-run` |
| 束のうち1本だけ音声にしたい | `--only 03_delivery_delay` |
| `drafts/` にある下書きをまとめて音声にしたい | `uv run make_audio.py drafts` |
| `scripts/` 側で手直しした台本を下書きで上書きしたい | `--force-script` |
| 台本は同じだが、声を作り直したい | `--force-audio` |

### 数本をまとめた台本（束）

**1ファイルに何本入っていても、束ね方が違っても受ける。**
ChatGPT の返し方は毎回同じとは限らないため、次のどれでも同じ結果になる。

```jsonc
{ "speakers": {…}, "scripts": [ {…}, {…} ] }   // scripts / items / 台本 などのキー
[ {…}, {…} ]                                    // 配列で来た場合
{ "03_delivery_delay": {…}, "03_stock": {…} }   // 名前をキーにした場合
{ "title": …, "speakers": …, "turns": [ … ] }   // 1本だけの場合
```

- **音声のファイル名は台本の `id`**（`slug` / `name` でもよい）。
  英数字でないものや、そもそも無い場合は `<下書きの名前>_01` を振る。
  `--prefix long_` を付けると `long_001.wav` のように接頭辞を足せる
  （短編版と長編版で `id` が重なるときに使う）
- 束の側に `speakers` があれば、持っていない台本に配る。
  人物像を台本ごとに書き直させなくてよい
- **1本でも検証に落ちたら、その束は1本も `scripts/` に書かない。**
  3本目で落ちて1・2本目だけ残ると、束と取り込み済みの中身が食い違うため
- 中身の確認だけしたいときは `uv run import_script.py drafts/03_bundle.json --list`

### 生成したら点検する

**「合成に成功した」だけでは足りない。** APIが成功を返しても、本文ではなく
プロンプトを読み上げた音声が返ることがある（実例は `docs/setup-notes.md`）。
エラーにならないため、聴くまで気づけない。

```bash
uv run check_audio.py          # 台本の発話数・長さ・話速・無音を突き合わせる
```

合成側でも、本文の文字数から見込んだ長さを大きく超えた音声は失敗とみなし、
短いプロンプトで合成し直す。それでも長い場合は `要確認` と表示される。

### 途中で失敗しても、続きから再開する

`make_audio.py` は台本の指紋（sha256）を `output/<台本名>/script.sha256` に残す。
**指紋が前回と同じなら、既にある `part_NN.wav` は作り直さない。**
API の呼び直しは無駄な課金になるため、途中で落ちたら同じコマンドを打てばよい。

台本を1文字でも直すと指紋が変わり、全パートを作り直す。
**古い part が混ざったまま結合されると、音声だけ古いことに誰も気づけない。**

### 台本の崩れはスクリプト側で吸収する

台本はモデルが書くため、スキーマが合っていても細部が毎回ぶれる。
`import_script.py` が次を自動で直すので、手で整える必要はない。

| 崩れ | 扱い |
|---|---|
| ` ```json … ``` ` で囲まれている / 前置きが付いている | 剥がして読む |
| 発話配列のキーが `turns` ではなく `dialogue` | どちらでも読む |
| 話者名が「営業」「顧客」「お客様」など | `Sales` / `Customer` に寄せる |
| 同席者が `Customer2` `顧客2` などで登場する | 3人目として扱う（声は別にする） |
| `speakers` が人物像の文字列だけ | `{"persona": …}` とみなす |
| **`voice`（音声ID）が無い** | **人物像から性別・年代を読んで割り当てる**（下記） |
| `persona` が体言止め（「30代女性の営業」） | 「です。」と口調の指示を補う |
| 1発話が上限（3800 bytes）を超える | 句点で分割する |
| 改行・全角空白・空の発話 | 詰める / 落とす |

`title` `speakers` `turns` 以外のキー（`id` `product` `gold_knowledge` など）は
**そのまま `scripts/` に残す。** 合成には使わないが、
抽出結果の答え合わせに使うため落とさない。

### 音声IDが無いときの割り当て

台本に `voice` が無ければ、人物像の「30代女性」「50代男性」から割り当てる。

- **名前から性別は推測しない。** `Gacrux` は名前の印象と違って女性の声だった。
  全30音声に同じ文を読ませ、基本周波数(F0)を実測して分類してある
  （手順と実測値は `docs/setup-notes.md`）
- **営業は台本をまたいで同じ声になる。** 同じ営業担当者が何本もの台本に登場するため
- **顧客は台本ごとに散らす。** 固定すると「40代女性の顧客」が全台本で同じ声になる。
  営業に使う音声は顧客に回さない
- 同じ台本の中で声が重複することはない

意図と違ったら `scripts/<台本>.json` の `voice` を書き換え、
`--force-script --force-audio` で作り直す。

一方、**人が直すべきものは合成前に止める**（APIを呼ぶ前に落とす）。
`Sales` / `Customer` のどちらとも判断できない話者名、JSONとして壊れている、
`turns` が空、といった場合は理由を出して終了する。

### コーディングエージェントに任せる場合

台本の整備は Claude Code などのエージェントが行う前提。
**エージェントに渡す手順はこれだけでよい。**

```bash
cd tts-demo
uv run make_audio.py drafts/<下書き>.json --dry-run   # 1. 検証（APIを呼ばない）
uv run make_audio.py drafts/<下書き>.json --limit 4    # 2. 声を確かめる（人が聴く）
uv run make_audio.py drafts/<下書き>.json              # 3. 全部生成する
```

- **失敗は必ず非ゼロで終了し、どの台本の何行目かをメッセージに出す。**
  「合成できたつもりで空のwavが残る」状態は作らない
- 直してよいのは `drafts/` と `scripts/` のJSONだけ。
  **`generate_tts.py` は触らない**（実測で決めた合成手順が入っている）
- 台本を直したら同じコマンドを打ち直す。指紋が変わったぶんだけ作り直す
- `--limit` を付けた生成物は途中までの音声。**成果物としては使わない**

### 台本だけ取り込む / 台本から直接合成する

分けて実行することもできる。

```bash
uv run import_script.py drafts/03_delivery_delay.json   # 検証だけして scripts/ に置く
uv run generate_tts.py --script 03_delivery_delay       # scripts/ の台本から合成する
```

`--script` には `scripts/` 配下の名前かパスを渡す。
名前を間違えると、利用できる台本の一覧を出して止まる。
`generate_tts.py` 単体で使うときは `--resume` で、既にある `part_NN.wav` を
作り直さずに再開できる（`make_audio.py` は指紋を見て自動で判断する）。

## 出力

**出力先は台本ごとに分かれる。** 共有すると、合成前の `part_*.wav` の
掃除で別の台本の中間ファイルまで消えてしまうため。

```text
output/02_stock_leadtime/part_01.wav …  分割生成した中間ファイル（結合後も残す）
output/02_stock_leadtime.wav            最終成果物
```

## 2つの合成モード（既定は `per-turn`）

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

## 台本のスキーマ

`scripts/` にJSONを1つ足すだけでよい。`generate_tts.py` は変更しない。
ChatGPT に書かせる場合は `prompts/script_prompt.md` を使うこと。

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
- 1発話が上限（既定 3800 bytes）を超えると `generate_tts.py` はエラーで止まる。
  `make_audio.py` / `import_script.py` を通した台本は、句点で分割済みなので起きない
