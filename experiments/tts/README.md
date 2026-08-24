# TTS 検証（商談サンプル音声の生成）

文字起こしライブラリの検証（CLAUDE.md 3.4）に使うサンプル音声を、
Google Cloud Text-to-Speech の Gemini TTS で生成する。

**backend とは独立した uv プロジェクト。** 検証用の依存を `backend/uv.lock` に
入れないため分離している。

## 前提

1. Google Cloud プロジェクトで Cloud Text-to-Speech API を有効化する
2. ADC（アプリケーションのデフォルト認証情報）でログインする

   ```bash
   gcloud auth application-default login
   gcloud auth application-default set-quota-project <PROJECT_ID>
   ```

## 実行

```bash
cd experiments/tts
uv sync
uv run test_tts.py
```

`output/test_sales_dialogue.wav` が生成される（`output/` は git 管理外）。

## メモ

- モデル: `gemini-3.1-flash-tts-preview`（プレビュー。提供リージョン・料金に注意）
- 話者は `MultiSpeakerVoiceConfig` で `Sales` / `Customer` の2名
- 出力は LINEAR16 / 22050Hz。文字起こし側の要件が決まったら合わせて見直す
