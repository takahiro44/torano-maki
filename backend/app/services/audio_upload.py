"""アップロードされた音声を一時ファイルへ落とし、必ず消す。

**なぜ切り出すか。**
`api/ingest.py` にも同じ処理があるが、あちらは商談1本（最大200MB）を
前提にした受け口で、ロープレの短い回答とは上限も待ち時間も違う。
とはいえ「上限を超えたら書くのをやめる」「例外が出ても消す」という
安全条件まで書き分けると、片方だけ直したときに消し忘れが残る。

**生の練習音声をサーバへ残さないことは仕様である**（計画書15章）。
保存するなら同意・保持期間・削除方法を別途決める必要があるため、
MVPでは「必ず消える形」以外を選べないようにしておく。
そのため関数ではなくコンテキストマネージャを公開する。
呼び出し側が `finally` を書き忘れても消える。

FastAPI の `UploadFile` を引数に取らないのは、services 層を
Webフレームワークに依存させないため。呼び出し側が `file.file` を渡す。
将来 `api/ingest.py` もこちらへ寄せられるが、担当領域が違うため
今回は触らない（CLAUDE.md 4.5）。
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

# 音声のデコードは faster-whisper（PyAV）が行うため、ここは受け口の絞り込み。
# 想定外の拡張子を先に弾かないと、待たされた末にデコードエラーになり
# 原因が分かりにくい。`api/ingest.py` の _ALLOWED_SUFFIXES と揃えること。
ALLOWED_SUFFIXES = frozenset({".wav", ".mp3", ".m4a", ".mp4", ".flac", ".ogg", ".webm", ".aac"})

# ブラウザの MediaRecorder は Blob を送るため、ファイル名も拡張子も付かない。
# 拡張子が無いというだけで弾くと、マイク録音が一切通らなくなる。
_CONTENT_TYPE_SUFFIXES: dict[str, str] = {
    "audio/webm": ".webm",
    "video/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/aac": ".aac",
    "audio/flac": ".flac",
}

# 一度に読む量。音声全体をメモリに載せないために分割して書き出す。
_COPY_CHUNK_BYTES = 1024 * 1024


class AudioUploadError(RuntimeError):
    """受け取れない音声。

    `code` で HTTP ステータスへ振り分ける。利用者の操作で直せる問題
    （形式・サイズ・空ファイル）だけをここに集める。
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def resolve_suffix(file_name: str | None, content_type: str | None) -> str:
    """拡張子を決める。ファイル名に無ければ Content-Type から補う。"""
    suffix = Path(file_name).suffix.lower() if file_name else ""
    if suffix in ALLOWED_SUFFIXES:
        return suffix

    # "audio/webm;codecs=opus" のようにパラメータが付く
    base_type = (content_type or "").split(";")[0].strip().lower()
    fallback = _CONTENT_TYPE_SUFFIXES.get(base_type)
    if fallback is not None:
        return fallback

    raise AudioUploadError(
        "unsupported_format",
        f"対応していない音声形式です（{suffix or content_type or '不明'}）。"
        f"対応: {', '.join(sorted(ALLOWED_SUFFIXES))}",
    )


@contextmanager
def temporary_audio(stream: BinaryIO, *, suffix: str, max_bytes: int) -> Iterator[Path]:
    """音声を一時ファイルへ書き出し、抜けるときに必ず消す。

    上限を超えた時点で書くのをやめるのは、`Content-Length` を信用すると
    詐称でディスクを埋められるため。実際に読んだ量で判定する。
    """
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    path = Path(tmp.name)
    try:
        total = 0
        try:
            while chunk := stream.read(_COPY_CHUNK_BYTES):
                total += len(chunk)
                if total > max_bytes:
                    raise AudioUploadError(
                        "too_large",
                        f"音声が大きすぎます（上限 {max_bytes // (1024 * 1024)}MB）",
                    )
                tmp.write(chunk)
        finally:
            tmp.close()

        if total == 0:
            raise AudioUploadError("empty_file", "音声が空です")
        yield path
    finally:
        # 成功・失敗のどちらでも消す。生の音声はサーバに残さない
        path.unlink(missing_ok=True)
