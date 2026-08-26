# AIチャット ストリーミングAPI 契約

`feat/chat-ux` でバックエンドとフロントを並行実装するための取り決め。
**実装の正はここではなく `backend/app/models/chat.py`（Pydantic）**。
このファイルは両者が同時に着手するための先出し合意であり、
実装後は OpenAPI スキーマが正になる。

## エンドポイント

```
POST /chat/stream
Content-Type: application/json
Accept: text/event-stream
```

リクエストボディは既存の `POST /chat` と同一（`ChatRequest`）。
**既存の `POST /chat` は変更しない。** 追加のみ。既存テストと、
ストリーミング非対応な呼び出し元のために残す。

## レスポンス

`text/event-stream`。1イベント = `data: <JSON>\n\n` の1行。
`event:` フィールドは使わない（JSON側の `type` で判別する。
フロントで `addEventListener` を型ごとに生やす必要がなくなる）。

**HTTPステータスは接続確立時点で 200 になる。** そのあとに起きた失敗は
ステータスコードで表現できないため、必ず `error` イベントで流すこと。

### イベント種別

| type | いつ出るか | 目的 |
|---|---|---|
| `tool_call` | Tool を実行する**直前** | 検索に数秒かかる間、画面を無言にしない |
| `tool_result` | Tool の実行後 | 何件見つかったかを即座に出す |
| `citations` | 出典が増えたとき | 回答が書かれる前に参照元を出せる |
| `text` | 最終回答のトークンが来るたび | **体感待ち時間を32秒→1〜2秒にする本体** |
| `answer_reset` | 流した `text` を破棄させるとき | 前置きを回答として確定させない |
| `done` | 正常終了 | 確定値。途中経過と食い違ったらこちらを採用 |
| `error` | 異常終了 | 以降イベントは来ない |

### 各イベントの形

```jsonc
// Tool を呼ぶと決めた時点。実行前に出す
{"type":"tool_call","step":1,"tool":"search_knowledge",
 "label":"ナレッジを検索しています","arguments":{"query":"...","top_k":5}}

// label は**サーバが日本語で決める**。フロントにtool名の対応表を持たせない
// （Toolが増えたときにフロントを直さずに済むため）

// Tool の実行結果
{"type":"tool_result","step":1,"tool":"search_knowledge","ok":true,
 "summary":"ナレッジを検索しました（5件）","error_code":null}

// summary は既存 ToolTraceStep.summary と同じ文字列。画面にそのまま出せる1行

// 出典。既存 models/chat.py の Citation をそのまま使う
{"type":"citations","citations":[{"knowledge_id":"...","title":"...",
 "data_source_id":null,"source_type":null,"file_name":null,"utterances":[]}]}

// **差分ではなく毎回すべて**を送る。フロントは置き換えるだけでよい

// 最終回答のトークン
{"type":"text","delta":"在庫が"}

// ここまでの text を破棄する指示。フロントは回答本文を空に戻す
{"type":"answer_reset","reason":"tool_call"}

// **なぜ要るか。** 最終回答のラウンドかどうかは投げてみるまで分からない。
// Agent は「根拠の発言を確認します」と前置きしてから tool_calls を出すことがあり、
// その時点で前置きは既に流れている。取り消せないと、前置きが回答として
// 確定し、本当の回答が返らない（実際にその不具合が出た）。

// 正常終了。usage は既存 ChatUsage
{"type":"done","usage":{"iterations":2,"prompt_tokens":7293,
 "completion_tokens":561,"hit_max_iterations":false}}

// 異常終了
{"type":"error","code":"llm_unreachable","message":"vLLM に接続できません"}
```

### error の code

| code | 元の例外 | 画面での意味 |
|---|---|---|
| `llm_not_configured` | `LlmNotConfiguredError` | `.env` の BASE_URL / MODEL_NAME 未設定 |
| `llm_unreachable` | `LlmRequestError` | DGXが落ちている・届かない |
| `internal` | その他 | 想定外 |

## 守ること

- **`text` として残るのは最終回答だけ。** Tool 呼び出しのラウンドの前置きが
  流れてしまった場合は、`answer_reset` を送って破棄させること
  （サーバ側はシステムプロンプトでも前置きを書かないよう指示しているが、
  守られない前提で取り消せるようにしておく）
- **`done` は必ず最後に1回出す**（`error` で終わる場合を除く）
- 出典は Tool の実行結果からのみ組み立てる。本文からは拾わない
  （既存 `agent_loop.py` の方針を踏襲。LLMに書かせるとIDを捏造する）
- クライアントは途中で切断しうる（中止ボタン）。切断時にサーバ側が
  例外で落ちてログを汚さないこと
