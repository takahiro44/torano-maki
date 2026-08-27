# 音声由来ナレッジを班員のDBへ移す

音声ファイルは各自のPCからアップロードする。音声本体はサーバやGitに残さず、
DBに保存された時刻付き文字起こし・ナレッジ・根拠だけをJSONで受け渡す。

## 作成者側

1. 画面の「音声」タブから音声をアップロードする。
2. 文字起こしを確認し、「この内容でナレッジ化する」を押す。
3. 抽出結果を確認し、「この内容で承認する」を押す。
4. 最新の音声をJSONへ書き出す。

```bash
cd backend
uv run python scripts/export_audio_knowledge.py
```

既定の出力先は `data/audio_knowledge_export.json`。`data/` はGit管理外なので、
JSONはチャットや共有ドライブなど、チームで合意した安全な経路で渡す。
同名ファイルが複数ある場合は、文字起こし時に返されたIDを指定すると確実。

```bash
uv run python scripts/export_audio_knowledge.py --source-id 00000000-0000-0000-0000-000000000000 --output ../data/audio_knowledge_export.json
```

## 班員側

受け取ったJSONをリポジトリ直下の `data/` に置き、次を実行する。

```bash
cd backend
uv run python scripts/load_extraction_json.py --file ../data/audio_knowledge_export.json
```

同じデータを入れ直す場合だけ `--replace` を付ける。既存行は1商談分まとめて
物理削除してから復元されるため、通常の初回投入では付けない。

```bash
uv run python scripts/load_extraction_json.py --file ../data/audio_knowledge_export.json --replace
```

復元時に各PCの設定で埋め込みを再生成するため、DGXのLLMや元の音声ファイルは不要。
一方、JSONには文字起こし全文が含まれるため、実商談データの共有先には注意する。
