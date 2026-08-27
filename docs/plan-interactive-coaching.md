# 実装計画: 上司レビューの診断強化 / Petの次の一手 / ロープレのヒント

対象リポジトリ: `torano-maki`

作成日: 2026-08-27

対象機能: 上司レビューとロープレに、AIチャット・ナレッジ登録と同じ「AIが支える」体験を入れる

主な担当領域: 上司レビュー / ロープレ / フロントエンド / スキーマ

---

## 1. 目的

現状、AIが人を支えているのは **AIチャット** と **ナレッジ登録** の2画面だけである。
**上司レビュー** と **ロープレ** は、AIが「処理する」ことはしても「支える」ことをしていない。

| 画面 | 今のAIの役割 | 足りていないこと |
|---|---|---|
| AIチャット | 検索して答える・次の一手を出す | — |
| ナレッジ登録 | 抽出する・相談に乗る | — |
| **上司レビュー** | 会話を要約するだけ | 上司が「何を教えればいいか」を自分で読み取っている |
| **ロープレ** | 顧客役をやるだけ | 詰まった後輩を助ける者がいない |

本計画はこの2画面に手を入れる。加えて、両画面に居場所を持たない
アシスタント（AgentPet）を全画面へ広げ、**次の一手を示す役**を持たせる。

### 完成時の一文

> 上司はレビューを開いた瞬間に「後輩がどこまで分かっていて、自分が答えるべきはどれか」が分かる。
> 後輩はロープレで詰まっても、答えを見せられずに一段ずつ助けてもらえる。

---

## 2. 対象範囲

### 今回やること

- A. 上司レビューの診断強化（理解度マップ ＋ ナレッジDB照合）
- B. Pet の「次の一手」提案（`lib/suggest.ts`）
- C. ロープレの「詰まった」ヒント（段階的開示・記録なし）

### 今回やらないこと（理由つき）

| 見送るもの | 理由 |
|---|---|
| 上司⇄AI の対話ヒアリング | Aの土台（理解度マップ）が無いと問いを立てられない。Aの後に別計画で扱う |
| 振り返り後の質疑応答 | 単体では成立するが、今回の3件と独立している。分けた方がPRが小さい |
| ヒント使用レベルのDB記録 | 列追加＝DB作り直し（CLAUDE.md 3.1）。今回は入れない（5.4） |
| 開始前の対話で場面を絞る | 既に30秒の生成待ちがある前に対話を挟むと、デモで重くなる |
| 顧客役の難易度調整 | 軽いが、練習の質に効く度合いが低い |

---

## 3. 機能A: 上司レビューの診断強化

### 3.1 なぜ必要か

`services/chat_review.py` の `_SYSTEM_PROMPT` は、**会話ログだけ**を見て
`knowledge_gaps` を出している。ナレッジDBを引いていない。

そのため上司には、性質のまったく違う2つが同じ「不足していた点」として並ぶ。

| 実際の状態 | 上司がやるべきこと | かかる時間 |
|---|---|---|
| DBに**有る**のに後輩が辿り着けなかった | 答えを書く必要はない。既存ナレッジの `applicable_situations` を直す | 1分 |
| DBに**無い**（上司しか持っていない暗黙知） | ここに時間を使う | 5分 |

判別は今、上司が毎回頭の中でやっている。
**「どこを教えてあげると良いか」の答えは、ほぼこの判別に尽きる。**

もう1つ、今の分類は粗すぎる。`understood_points` / `knowledge_gaps` の2値だと、
**「AIの答えをそのまま繰り返しただけ」が `understood_points` に入る。**
これは上司が最も教えるべき層であり、理解できていた事項として畳んではいけない。

### 3.2 出力契約（`backend/app/models/chat_review.py`）

```python
class UnderstandingLevel(StrEnum):
    """後輩の理解の段階。

    **`SHAKY` を独立させる理由。** 2値にすると「AIの答えを繰り返しただけ」が
    理解できていた側へ入る。そこは上司が最も教えるべき層であり、
    畳んでしまうと教えどころが1つ消える。
    """

    UNDERSTOOD = "understood"   # 自分の言葉で言えていた
    SHAKY = "shaky"             # 言えてはいるが、根拠が本人の中に無い
    MISSING = "missing"         # 触れていない、または誤解している


class ReviewTopic(BaseModel):
    """会話から読み取れた論点1つ。"""

    model_config = ConfigDict(extra="forbid")

    topic: str = Field(min_length=1, max_length=80)
    level: UnderstandingLevel
    # 上司が判定を検証できる形にする。根拠を出さない判定は信用されない
    evidence: str = Field(min_length=1, max_length=200, description="そう判断した後輩の発言")
    why_it_matters: str = Field(min_length=1, max_length=200)


class GapDbState(StrEnum):
    """その疑問に対するナレッジDBの状態。

    **Qwen に決めさせない。** サーバが実際に検索した結果から埋める
    （`agent_loop.py` の「出典は Tool の結果からのみ作る」と同じ理由）。
    """

    MISSING = "missing"                        # 検索しても近いものが無い
    FOUND_BUT_UNREACHABLE = "found_but_unreachable"  # 有るのに後輩が辿り着けなかった
    THIN = "thin"                              # 当たるが、この状況には薄い


class TeachingPoint(BaseModel):
    """上司に答えてほしい問い1つ。"""

    question: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=200)
    priority: int = Field(ge=1, le=5, description="Qwenが付ける。同じ db_state の中の順序にだけ使う")
    db_state: GapDbState = Field(description="サーバが埋める。Qwenの値は使わない")
    existing_knowledge: list[CitedKnowledge] = Field(
        default_factory=list, description="found_but_unreachable / thin のとき実際に当たったもの"
    )
```

`ChatReviewSummary` は次の形になる。

```python
class ChatReviewSummary(BaseModel):
    summary: str
    topics: list[ReviewTopic]           # understood_points / knowledge_gaps を統合
    teaching_points: list[TeachingPoint]
```

### 3.3 `db_state` はサーバが決める

Qwen には `question` / `reason` / `priority` までを出させ、`db_state` と
`existing_knowledge` はサーバが `services/search.py` を実行して埋める。

```
for each teaching_point:
    hits = search_knowledge(db, question, top_k=3)
    → 判定
```

判定に使うのは `semantic_score`（コサイン類似度）である。
**`score`（RRF）は使わない。** `models/knowledge.py:369` に明記されているとおり
RRF スコアは順位を決めるための内部値で、絶対値として閾値に使えない。

| 条件 | db_state |
|---|---|
| 最上位の `semantic_score` が閾値未満、またはヒット0件 | `missing` |
| 閾値以上で、そのナレッジが**会話の citations に出ていない** | `found_but_unreachable` |
| 閾値以上で、**既に会話で提示済み**だったのに解決しなかった | `thin` |

`thin` の判定に会話中の citations が要るが、`chat_reviews.chat_history` は
`ChatMessage`（role / content のみ）しか持たない。**このため当面 `thin` は使わず、
`missing` / `found_but_unreachable` の2値で運用する。** citations をレビューへ
持ち込むかは未決事項（9章）。

> **閾値は実測で決める。** 初期値は 0.80 とし、`scripts/seed.py` の15件と
> 実際のレビュー数件で当ててから固定する。決めた値と根拠は `docs/decisions.md` に残す。

### 3.4 並び順

画面に出す順は**サーバが決める**。`missing` を上、`found_but_unreachable` を下。
同じ `db_state` の中でだけ Qwen の `priority` を使う。

**Qwen の priority をそのまま信用しない理由。** モデルは DB の状態を知らずに
優先度を付けている。上司の時間を使う価値があるのは `missing` の方であり、
その判断材料はサーバ側にしか無い。

### 3.5 DB への保存

`chat_reviews.understood_points` / `knowledge_gaps` は **JSONB** である
（`docker/initdb/02_schema.sql:287-288`）。オブジェクトの配列をそのまま入れられるため、
**DDL変更は不要。**（保存済みの `list[str]` の行は新スキーマで落ちるため、
DBの作り直しそのものは要る。3.6 を参照）

| カラム | 変更後の中身 |
|---|---|
| `understood_points` | `list[ReviewTopic]`（`level` が `understood` / `shaky` のもの） |
| `knowledge_gaps` | `list[TeachingPoint]` |

`tables.py` の型注釈（`Mapped[list[str]]` → `Mapped[list[dict]]`）は直す。
SQLAlchemy 側は JSONB として扱っているだけなので挙動は変わらない。

> **カラム名は変えない。** 中身の意味が変わるのは事実だが、名前を変えると
> DDL・`tables.py`・既存クエリの3箇所を触ることになり、得られるものが
> 「名前の座りが良くなる」だけになる。docstring で意味を明示する方を取る。

### 3.6 既存データ

`list[str]` で保存済みの行は、新しい Pydantic で検証すると落ちる。
CLAUDE.md 3.1 の「スキーマ変更時はDBを作り直す（データは捨てる前提）」に従い、
**移行コードは書かない。** ただし `docker compose down -v` は
ロープレ履歴も消すため、マージ時にチームへ宣言する（CLAUDE.md 4.10）。

### 3.7 API

エンドポイントの形は変えない。応答の中身だけが変わる。

| メソッド | パス | 変更 |
|---|---|---|
| POST | `/chat-reviews/summarize` | 応答が新しい `ChatReviewSummary` になる |
| POST | `/chat-reviews` | 同上（`ChatReviewDetail` 経由） |
| GET | `/chat-reviews/{id}` | 同上 |

**`POST /chat-reviews` が遅くなる。** 今は要約1回だったところに、
teaching_point の数だけ検索が入る（埋め込み生成を含む）。
上限を4件に切り、超えた分は捨てる。

### 3.8 画面（`SupervisorInbox.tsx`）

レビューを開いたときの並びを次の順にする。**会話ログを一番上に置かない。**
上司が最初に読むべきは診断であって、生ログではない。

```
1. 理解度マップ    understood / shaky / missing を色で分ける
2. あなたに答えてほしいこと   missing の TeachingPoint（優先）
3. ナレッジは有ります        found_but_unreachable。既存ナレッジへのリンク付き
4. 会話ログ（折りたたみ）     既定で閉じる
5. 回答欄
```

3 は上司の作業が「答えを書く」ではなく「既存ナレッジの適用場面を直す」に変わるため、
回答欄ではなく **該当ナレッジへのリンク**を出す。

### 3.9 プロンプト

`_SYSTEM_PROMPT` を書き換える。守らせること:

- `evidence` には**後輩の発言だけ**を引く。AIの発言を根拠にしない
- AIの回答を繰り返しただけの箇所は `shaky` にする
- `db_state` は書かせない（スキーマにも含めない。サーバが後から足す）
- `topics` は最大6件、`teaching_points` は最大4件

---

## 4. 機能B: Pet の「次の一手」提案

### 4.1 原則: LLM を呼ばない

`AgentPet.tsx:11-16` は「セリフはフェーズから選ぶだけ。AIに喋らせると、
待たせている最中にさらにLLMを呼ぶことになり、しかも内容を保証できない」と書いている。
**この判断は正しいので壊さない。**

代わりに `actions.ts` の思想を持ち込む — **提案は画面の状態から機械的に決まる。**
つまり Pet を「NextActions の携帯版」にする。

| | NextActions | Pet の提案 |
|---|---|---|
| どこに出るか | 回答の下 | 画面のどこにいても |
| いくつ出るか | 複数 | **最大1つ** |
| 誰のためか | 次に進みたい人 | **今この画面で止まっている人** |

### 4.2 `frontend/src/lib/suggest.ts`

```ts
export type Suggestion = {
  key: string;
  /** 吹き出しに出す一言。Petの口ぶりで書く */
  line: string;
  /** 押したときの操作。無ければただの一言として出す */
  run?: () => void;
};

export function suggest(state: SuggestState): Suggestion | null;
```

**判定はこのファイルだけに置く。** `phase.ts` / `actions.ts` と同じ理由で、
画面の各所が独自に「今なにを勧めるべきか」を判定し始めると、
同じ状態で別のことを言い出す。

`null` を返したときは、これまでどおり `LINES` の気分セリフを出す。
つまり**既存の挙動は既定のまま**で、言うべきことがあるときだけ提案に切り替わる。

### 4.3 出す提案

| 画面 | 状況 | 一言 | 押すと |
|---|---|---|---|
| 上司レビュー | pending が2件以上 | 「{n}件たまってるよ。古い順に見る？」 | 最古の pending を開く |
| 上司レビュー | 開いた直後 | 「先に『どこが分かってないか』だけ見る？」 | 理解度マップへスクロール |
| 上司レビュー | `missing` の問いがある・回答欄が空 | 「この問いに答えるのが一番効くよ」 | 最優先の問いを回答欄の下敷きに入れる |
| 上司レビュー | `found_but_unreachable` だけ | 「答えは要らないかも。ナレッジを直すだけで済むよ」 | 該当ナレッジを開く |
| ロープレ | 履歴0件 | 「まずは『値引き』が短くて練習しやすいよ」 | その場面で開始 |
| ロープレ | 回答欄が空のまま20秒 | 「詰まった？ヒントいる？」 | ヒント Lv1（機能C） |
| ロープレ | 振り返り後 | 「同じ場面もう一回やると、差が見えるよ」 | 再挑戦 |
| AIチャット / ナレッジ登録 | — | 今回は変更しない（`null` を返す） | — |

「回答欄が空のまま20秒」は `suggest.ts` の中で時間を数えない。
**画面側が「いつから空か」を状態として渡し、`suggest` は純関数に保つ。**
時間を持ち込むとテストが書けなくなる。

### 4.4 `lib/pet.ts` / `AgentPet.tsx` の変更

| ファイル | 変更 |
|---|---|
| `lib/pet.ts` | `PetScene` に `"review"` / `"roleplay"` を足す。`DEFAULT_SKIN` に既定の姿を足す |
| `AgentPet.tsx` | `ANCHORS` / `LINES` に2画面ぶんを足す。`suggestion` prop を受け、**吹き出しを押せるようにする** |

吹き出しは今、表示専用である。押せるようにするのが唯一の構造変更になる。
`pointer-events` の扱いに注意する（Pet 本体は `pointer-events-auto`、
外枠は `pointer-events-none`）。

**「消せる」「掴んで置ける」は一切変えない。** 提案を出すようになったからといって、
邪魔だと感じた人が消せなくなってはならない（`lib/pet.ts` の冒頭の理由）。

---

## 5. 機能C: ロープレの「詰まった」ヒント

### 5.1 段階的開示

`RoleplaySession.tsx` は「模範解答を先に見せない」を原則にしている。
ヒントはこれを破ってはいけない。押すたびに**一段だけ**開く。

| Lv | 出すもの | 例 |
|---|---|---|
| 1 | 観点だけ | 「金額の話に入る前に、確かめることがあるよ」 |
| 2 | 組み立ての型 | 「共感 → 背景を聞く → 代替案 の順に組んでみて」 |
| 3 | 言い出しだけ | 「『差し支えなければ』から始めてみる？」 |

**Lv3 でも文全体は出さない。** 出すのは書き出しの一句までとする。
全文を出すと写して送るだけになり、練習が成立しない。

### 5.2 API

```
POST /roleplay/sessions/{session_id}/hint
{ "level": 1 }        → { "level": 1, "hint": "..." }
```

- `level` は `1 | 2 | 3`
- `ensure_can_answer()` を先に通す。発言できない状態でヒントだけ出しても意味が無い
- 生成には `_complete_structured()`（`services/roleplay.py:193`）を再利用する
- 文脈は `scenario_of()` ＋ これまでの `turns` ＋ `_selected_from_links()` の社内事例
- タイムアウトは `_CUSTOMER_TIMEOUT`（60秒）に合わせる

**社内事例をプロンプトに入れるが、レベルごとに出してよい粒度を厳しく縛る。**
Lv1 で事例の言い回しが漏れると、その時点で模範解答を見せたことになる。
system prompt に「Lv1では固有名詞・具体的な言い回しを一切出さない」を明記する。

### 5.3 契約（`models/roleplay.py`）

```python
class HintLevel(IntEnum):
    PERSPECTIVE = 1   # 観点だけ
    STRUCTURE = 2     # 組み立ての型
    OPENING = 3       # 言い出しの一句


class RoleplayHintRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    level: HintLevel


class RoleplayHint(BaseModel):
    """出したヒント1つ。**保存しない**（5.4）。"""
    model_config = ConfigDict(extra="forbid")
    level: HintLevel
    hint: str = Field(min_length=1, max_length=200)
```

### 5.4 記録しない

「Lv3まで使った」を振り返りに出せると成長が見えるが、それには
`roleplay_turns` への列追加が要り、DB作り直しが発生する（CLAUDE.md 3.1）。
今回は入れない。

**副作用として、再読込でヒントのレベルが1に戻る。**
どこまで開いたかはクライアントの state にしか無いため。
これは許容する（練習中に再読込する頻度は低く、戻っても損はしない）。

将来入れるなら、`roleplay_turns` に `hint_level_used SMALLINT` を足すのが素直である。

---

## 6. スキーマ影響のまとめ

| 項目 | DDL変更 | DB作り直し |
|---|---|---|
| A. 理解度マップ（JSONBの中身を構造化） | 不要 | **要**（既存行が新スキーマで落ちるため） |
| A. gap のDB照合 | 不要 | 不要 |
| B. Pet の提案 | 不要 | 不要 |
| C. ロープレのヒント | 不要 | 不要 |

**A のマージ時は「`docker compose down -v && docker compose up -d` が必要」を
チームに宣言する**（CLAUDE.md 4.10）。ロープレ履歴とナレッジも消える。

---

## 7. 実装順とPR分割

**1つのPRにまとめない。** 触るファイルのオーナーが分かれており
（CLAUDE.md 1.1）、まとめるとレビューが止まる。

| # | ブランチ | 内容 | 触る主なファイル | 依存 |
|---|---|---|---|---|
| 1 | `feat/review-understanding-map` | A（理解度マップ＋DB照合） | `models/chat_review.py` / `services/chat_review.py` / `api/chat_review.py` / `SupervisorInbox.tsx` / `types/api.ts` | — |
| 2 | `feat/roleplay-hint` | C（ヒント） | `models/roleplay.py` / `services/roleplay.py` / `api/roleplay.py` / `RoleplaySession.tsx` | — |
| 3 | `feat/pet-suggestions` | B（Petの提案） | `lib/suggest.ts`（新規） / `lib/pet.ts` / `AgentPet.tsx` / 2画面 | 1・2 |

**B を最後にする理由。** Pet の提案は A の `db_state` と C のヒント呼び出しを
参照する。先に作ると、提案の中身を仮置きしたまま2回書き直すことになる。

`models/` は共有ファイル（CLAUDE.md 1.1）である。**1 と 2 の着手前に、
スキーマ担当へ変更内容を共有する。**

---

## 8. 動作確認

### 機能A

- [ ] 蓄積に無い話題でチャット → まとめる → `missing` として出る
- [ ] 蓄積に有る話題を、わざと遠回しに聞いてチャット → `found_but_unreachable` として出て、既存ナレッジへのリンクが出る
- [ ] AIの回答を繰り返しただけの返しをすると `shaky` になる
- [ ] `evidence` に引かれているのが後輩の発言だけであること
- [ ] `missing` が `found_but_unreachable` より上に並ぶこと
- [ ] `POST /chat-reviews` の所要時間（検索が4回増える）を実測して記録する

### 機能B

- [ ] pending 2件で「たまってるよ」が出て、押すと最古が開く
- [ ] 提案が無い状態では、これまでどおり気分セリフが出る
- [ ] 吹き出しを押せる。Pet 本体を掴んで動かす操作は壊れていない
- [ ] 「アシスタントをしまう」で消え、ヘッダーから呼び戻せる

### 機能C

- [ ] Lv1 に社内事例の固有名詞・具体的な言い回しが出ていない
- [ ] Lv3 が書き出しの一句までで、文全体になっていない
- [ ] 発言回数を使い切った後にヒントを押しても 409 で弾かれる
- [ ] 再読込するとレベルが1に戻る（既知の挙動として確認する）

### commit 前（CLAUDE.md 4.6）

```bash
cd backend  && uv run ruff check . && uv run ruff format --check . && uv run pytest -q
cd frontend && npm run lint && npm run build
```

---

## 9. 未決事項

| 項目 | 決める人 | いつまで |
|---|---|---|
| `semantic_score` の閾値（初期値 0.80） | 検索担当 | 機能A の実装中に実測して決める |
| `chat_reviews` に citations を持たせるか（`thin` 判定に要る） | スキーマ担当 | 機能A の着手前 |
| Pet の既定の姿（review / roleplay の scene） | フロント担当 | 機能B の着手前 |
| Lv3 で出す量の線引き（「一句」の具体的な長さ） | ロープレ担当 | 機能C の実装中 |

決めた内容と理由は `docs/decisions.md` に残す（CLAUDE.md 8章）。

---

## 10. 実装時に計画から変えたところ（2026-08-27）

この計画のあと、**上司⇄AIの対話ヒアリング**（2章で「今回やらない」としていたもの）を
後輩側から先に入れた。理由と差分を残す。詳細は `docs/decisions.md` を参照。

| 計画 | 実装 | なぜ変えたか |
|---|---|---|
| `UnderstandingLevel` は Qwen が判定する（3.2） | **本人が答える**（送信前のヒアリング） | 「言えてはいるが根拠が自分の中に無い」は会話ログに現れない。モデルに推測させるより本人に聞く方が正確で、しかも速い（LLMを1回も増やさない） |
| `ReviewTopic.evidence` / `why_it_matters` をモデルに書かせる | 入れていない | 判定を本人がするなら、モデルの根拠は要らない。プロンプトを伸ばさずに済む |
| `TeachingPoint.priority` をモデルに付けさせる | 入れていない | 並び順は `db_state` で決まる（3.4 の趣旨は残した） |
| 理解度マップの導入にDB作り直しが要る（6章） | **作り直さない** | 旧形式（`list[str]`）を検証前に寄せた。デモ用に貯めたレビューとロープレ履歴を捨てずに済む |
| Pet は `suggest.ts` で「次の一手」を出す（機能B） | 上司レビューでは **診断そのものを喋る**（`lib/reviewBriefing.ts`） | 上司がこの画面で欲しいのは次の一手ではなく「後輩がどこまで分かっているか」。LLMを呼ばない原則（4.1）はそのまま |
| — | ロープレの顧客役を Pet が演じる | 相手に顔があると、練習が「フォームを埋める作業」でなくなる |

**機能C（ロープレのヒント）はまだ入っていない。** この計画のまま着手できる。
