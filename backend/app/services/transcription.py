"""音声認識。

音声認識ライブラリは差し替えの可能性が高いため（ARM64+CUDAで動くものが未確定）、
呼び出し口をこの1関数に集約する（CLAUDE.md 6章）。
利用側はライブラリを直接importせず、必ず transcribe() を通すこと。
そうしておけば、撤退が必要になってもこのファイルだけ差し替えれば済む。
"""

from pathlib import Path

from pydantic import BaseModel


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str


class Transcript(BaseModel):
    text: str
    segments: list[TranscriptSegment] = []
    language: str | None = None


def transcribe(audio_path: Path) -> Transcript:
    """音声ファイルを文字起こしする。

    実装は未確定。docs/decisions.md の「音声認識の候補と注意点」を参照。
    """
    raise NotImplementedError(
        "音声認識ライブラリが未確定。docs/decisions.md を参照して決定すること"
    )
