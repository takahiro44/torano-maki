"""商談台本（scripts/*.json）から Gemini TTS で2話者の商談音声を1本生成する。

営業ナレッジ抽出の検証には実際の商談音声が要るが、本物の商談録音は持ち出せない。
そのため合成音声でデモデータを用意する。

Cloud Text-to-Speech は1リクエストあたり4000 bytesの制限があり、台本全文は
1回で送れない。合成モードが2つあるのはこのため（--mode を参照）。

話者の性別・年代は台本ごとに異なるため、音声IDと人物像はコードに持たず
台本JSONの `speakers` から読む。台本を増やすときはJSONを1つ足すだけでよい。
"""

from __future__ import annotations

import argparse
import array
import json
import math
import wave
from dataclasses import dataclass
from pathlib import Path

from google.api_core.exceptions import InvalidArgument
from google.cloud import texttospeech

MODEL = "gemini-3.1-flash-tts-preview"
LANGUAGE = "ja-JP"
SAMPLE_RATE_HERTZ = 22050

# APIの実際の上限。超えると 400 InvalidArgument:
#   "Either `input.text` or `input.prompt` is longer than the limit of 4000 bytes."
# 台本を少し足しただけで落ちないよう、既定では SAFETY_MARGIN_BYTES を引いて使う
MAX_INPUT_BYTES = 4000
SAFETY_MARGIN_BYTES = 200

# 発話の切り替わりに入れる無音。詰まって聞こえるのを防ぐ
GAP_MS = 250

BASE_DIR = Path(__file__).parent
SCRIPTS_DIR = BASE_DIR / "scripts"
OUTPUT_ROOT = BASE_DIR / "output"

# 16bit PCM の最大振幅。正規化でクリップさせないための上限
PCM16_PEAK = 32767

# Multi-speaker 合成に添えるプロンプト。人物像は台本JSONから差し込む
MULTI_SPEAKER_PROMPT = """
これは日本企業で行われている法人営業の商談です。

{personas}
二人とも過度に演技的にはせず、
実際に会議室で対面して会話しているようにしてください。

これは長い商談の一部です。
声のトーン、声量、話す速度は、最初から最後まで一定に保ってください。
"""

# 1発話ずつ合成するときは話者が1人しかいないため、その人物像だけを渡す
SINGLE_SPEAKER_PROMPT = """
これは日本企業で行われている法人営業の商談での、{role}の発話です。
話し手は{persona}
台本の朗読ではなく、実際の商談のような自然な話し方にしてください。
過度に演技的にはせず、会議室で対面して話しているようにしてください。
"""

# 本文が短すぎて上のプロンプトが通らないときの代替。
# 人物設定を落とすと声の印象が変わってしまうため、そこは残したまま短くする。
# 拒否の判定は長さではなく内容依存で、命令形の指示を含む短い文ほど弾かれやすい。
PERSONA_ONLY_PROMPT = "これは法人営業の商談での{role}の発話です。話し手は{persona}"

# 人物設定を含む上の代替でも通らなかったときの最後の砦
SHORT_PROMPT = "商談での自然な話し方で、落ち着いた口調にしてください。"


@dataclass(frozen=True)
class Turn:
    """台本の1発話。dictのまま持ち回すとキー名の打ち間違いに気づけないため型を与える。"""

    speaker: str
    text: str

    def to_line(self) -> str:
        """Multi-speaker 合成が話者を判別できる `話者: 本文` 形式にする。"""
        return f"{self.speaker}: {self.text}"

    def line_bytes(self) -> int:
        """UTF-8のbyte数。日本語は1文字3byteのため、文字数で判断すると3倍近く見誤る。"""
        return len(self.to_line().encode("utf-8")) + 1  # 連結時の改行ぶん


@dataclass(frozen=True)
class Speaker:
    """台本ごとの登場人物。音声IDと人物像は必ず対で使うため1つにまとめる。"""

    voice: str
    role: str
    persona: str


@dataclass(frozen=True)
class Script:
    """1本の商談台本。出力先を名前から決めるため stem を持たせる。"""

    stem: str
    title: str
    speakers: dict[str, Speaker]
    turns: list[Turn]

    @property
    def output_dir(self) -> Path:
        """台本ごとにpart置き場を分ける。共有すると別台本のpartを消してしまう。"""
        return OUTPUT_ROOT / self.stem

    @property
    def default_output(self) -> Path:
        return OUTPUT_ROOT / f"{self.stem}.wav"


def load_script(path: Path, limit: int | None = None) -> Script:
    """台本を読み込む。未知の話者はここで弾かないと合成時まで気づけない。"""
    raw = json.loads(path.read_text(encoding="utf-8"))

    speakers = {
        alias: Speaker(
            voice=config["voice"],
            role=config["role"],
            persona=config["persona"],
        )
        for alias, config in raw["speakers"].items()
    }

    turns: list[Turn] = []
    for index, item in enumerate(raw["turns"], start=1):
        speaker = item["speaker"]
        if speaker not in speakers:
            msg = f"{path.name} の {index} 番目に未知の話者 '{speaker}' がある"
            raise ValueError(msg)
        turns.append(Turn(speaker=speaker, text=item["text"]))

    return Script(
        stem=path.stem,
        title=raw.get("title", path.stem),
        speakers=speakers,
        turns=turns[:limit] if limit else turns,
    )


def split_into_chunks(turns: list[Turn], limit_bytes: int) -> list[str]:
    """発話の途中で切らず、最小のチャンク数へできるだけ均等に分ける。

    分割の境界ごとに声質が変わるため、境界の数は少ないほどよい。
    さらに、極端に短いチャンクは声の揺れが大きくなるので均等化する。
    """
    if not turns:
        return []

    sizes = [turn.line_bytes() for turn in turns]
    for turn, size in zip(turns, sizes, strict=True):
        if size > limit_bytes:
            msg = (
                f"1発話が上限 {limit_bytes} bytes を超えている"
                f"（{size} bytes / {turn.speaker}）。台本側で分割すること"
            )
            raise ValueError(msg)

    total = sum(sizes)
    chunk_count = math.ceil(total / limit_bytes)
    target = total / chunk_count

    chunks: list[str] = []
    current: list[str] = []
    current_bytes = 0

    for turn, size in zip(turns, sizes, strict=True):
        over_limit = current_bytes + size > limit_bytes
        over_target = current_bytes + size > target and len(chunks) < chunk_count - 1
        if current and (over_limit or over_target):
            chunks.append("\n".join(current))
            current = []
            current_bytes = 0
        current.append(turn.to_line())
        current_bytes += size

    if current:
        chunks.append("\n".join(current))
    return chunks


def multi_speaker_voice(script: Script) -> texttospeech.VoiceSelectionParams:
    """話者エイリアスへ音声を割り当てた Multi-speaker の設定。"""
    config = texttospeech.MultiSpeakerVoiceConfig(
        speaker_voice_configs=[
            texttospeech.MultispeakerPrebuiltVoice(
                speaker_alias=alias,
                speaker_id=speaker.voice,
            )
            for alias, speaker in script.speakers.items()
        ]
    )
    return texttospeech.VoiceSelectionParams(
        language_code=LANGUAGE,
        model_name=MODEL,
        multi_speaker_voice_config=config,
    )


def single_speaker_voice(speaker: Speaker) -> texttospeech.VoiceSelectionParams:
    """プリセット音声を名前で直接指定する。声のIDが固定されるため人物が入れ替わらない。"""
    return texttospeech.VoiceSelectionParams(
        language_code=LANGUAGE,
        name=speaker.voice,
        model_name=MODEL,
    )


def multi_speaker_prompt(script: Script) -> str:
    """登場人物の紹介を台本から組み立てる。話者名はJSONのエイリアスに揃える。"""
    personas = "".join(
        f"{alias}は{speaker.persona}\n\n" for alias, speaker in script.speakers.items()
    )
    return MULTI_SPEAKER_PROMPT.format(personas=personas)


def synthesize(
    client: texttospeech.TextToSpeechClient,
    text: str,
    voice: texttospeech.VoiceSelectionParams,
    prompt: str,
) -> bytes:
    """1リクエストぶんを合成する。認証情報はADCから読むためコードには持たせない。"""
    response = client.synthesize_speech(
        input=texttospeech.SynthesisInput(text=text, prompt=prompt),
        voice=voice,
        audio_config=texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.LINEAR16,
            sample_rate_hertz=SAMPLE_RATE_HERTZ,
        ),
    )
    return response.audio_content


def synthesize_with_fallback(
    client: texttospeech.TextToSpeechClient,
    text: str,
    voice: texttospeech.VoiceSelectionParams,
    speaker: Speaker,
) -> tuple[bytes, str]:
    """プロンプトを段階的に差し替えながら合成する。

    「お願いします。」のような極端に短い発話に長いプロンプトを添えると、
    `400 ... violates Vertex AI's usage guidelines` で確定的に拒否される
    （プロンプトを外せば同じ本文が通る）。

    ここで人物設定まで落とすと**その発話だけ声の印象が変わる**ため、
    まず人物設定を残した短いプロンプトを試す。
    """
    fields = {"role": speaker.role, "persona": speaker.persona}
    attempts = [
        (SINGLE_SPEAKER_PROMPT.format(**fields), "指定どおり"),
        (PERSONA_ONLY_PROMPT.format(**fields), "人物設定のみ"),
        (SHORT_PROMPT, "短縮プロンプト"),
        ("", "プロンプト無し"),
    ]
    last_error: Exception | None = None
    for candidate, label in attempts:
        try:
            return synthesize(client, text, voice, candidate), label
        except InvalidArgument as error:
            last_error = error
    raise RuntimeError(f"合成に失敗した: {text[:20]}") from last_error


def read_samples(path: Path) -> tuple[array.array, tuple[int, int, int]]:
    """WAVを16bitサンプル列として読む。正規化のために生の振幅が要る。"""
    with wave.open(str(path), "rb") as src:
        spec = (src.getnchannels(), src.getsampwidth(), src.getframerate())
        frames = src.readframes(src.getnframes())
    if spec[1] != 2:
        msg = f"{path.name} は16bitではない（sampwidth={spec[1]}）"
        raise ValueError(msg)
    samples = array.array("h")
    samples.frombytes(frames)
    return samples, spec


def rms(samples: array.array) -> float:
    """実効値。人が感じる音量に近く、ピーク値より継ぎ目の差を捉えやすい。"""
    if not samples:
        return 0.0
    return math.sqrt(sum(float(s) * s for s in samples) / len(samples))


def apply_gain(samples: array.array, gain: float) -> array.array:
    """クリップさせずに音量を揃える。歪みは音量差より耳につく。"""
    peak = max(abs(min(samples)), abs(max(samples))) or 1
    gain = min(gain, PCM16_PEAK / peak)
    return array.array(
        "h",
        (int(max(-PCM16_PEAK, min(PCM16_PEAK, s * gain))) for s in samples),
    )


def concat_wavs(parts: list[Path], dest: Path, gap_ms: int, normalize: bool) -> None:
    """分割生成したWAVを1本に結合する。

    リクエストごとに音量が変わるため（実測で最大4.7dBの差があった）、
    既定で全体の平均音量に揃えてから結合する。
    """
    if not parts:
        msg = "結合対象のWAVがない"
        raise ValueError(msg)

    loaded = [read_samples(path) for path in parts]
    spec = loaded[0][1]
    for path, (_, other) in zip(parts, loaded, strict=True):
        if other != spec:
            msg = f"{path.name} のフォーマットが他と一致しない"
            raise ValueError(msg)

    channels, sample_width, frame_rate = spec
    levels = [rms(samples) for samples, _ in loaded]
    target_level = sum(levels) / len(levels)

    tracks: list[array.array] = []
    for (samples, _), level in zip(loaded, levels, strict=True):
        if normalize and level > 0:
            tracks.append(apply_gain(samples, target_level / level))
        else:
            tracks.append(samples)

    silence = array.array("h", [0] * (int(frame_rate * gap_ms / 1000) * channels))

    with wave.open(str(dest), "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(sample_width)
        out.setframerate(frame_rate)
        for index, track in enumerate(tracks):
            if index > 0:
                out.writeframes(silence.tobytes())
            out.writeframes(track.tobytes())


def duration_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


def clear_parts(output_dir: Path) -> None:
    """前回が今回より多いパート数だと、古い part が結合対象に紛れ込むため消す。"""
    for stale in output_dir.glob("part_*.wav"):
        stale.unlink()


def generate_chunked(
    client: texttospeech.TextToSpeechClient,
    script: Script,
    chunk_bytes: int,
) -> list[Path]:
    """会話をまとめて Multi-speaker で合成する。掛け合いは自然だが継ぎ目で声が変わる。"""
    chunks = split_into_chunks(script.turns, chunk_bytes)
    print(f"{len(script.turns)} 発話 → {len(chunks)} チャンク（multi-speaker）")
    clear_parts(script.output_dir)

    voice = multi_speaker_voice(script)
    prompt = multi_speaker_prompt(script)
    parts: list[Path] = []
    for index, chunk in enumerate(chunks, start=1):
        part_path = script.output_dir / f"part_{index:02d}.wav"
        print(f"  合成中 {part_path.name} ({len(chunk.encode('utf-8'))} bytes)")
        part_path.write_bytes(synthesize(client, chunk, voice, prompt))
        parts.append(part_path)
    return parts


def generate_per_turn(
    client: texttospeech.TextToSpeechClient,
    script: Script,
    resume: bool,
) -> list[Path]:
    """1発話ずつ単一話者で合成する。

    プリセット音声を名前で固定するため、リクエストをまたいでも人物が入れ替わらない。
    そのかわり掛け合いの自然さは multi-speaker に劣る。
    """
    turns = script.turns
    print(f"{len(turns)} 発話 → {len(turns)} リクエスト（per-turn）")
    if not resume:
        clear_parts(script.output_dir)

    parts: list[Path] = []
    for index, turn in enumerate(turns, start=1):
        part_path = script.output_dir / f"part_{index:02d}.wav"
        parts.append(part_path)
        if resume and part_path.exists():
            continue
        speaker = script.speakers[turn.speaker]
        audio, label = synthesize_with_fallback(
            client,
            turn.text,
            single_speaker_voice(speaker),
            speaker,
        )
        note = "" if label == "指定どおり" else f" ← {label}"
        print(f"  合成中 {part_path.name} [{turn.speaker}]{note}")
        part_path.write_bytes(audio)
    return parts


def resolve_script_path(value: str) -> Path:
    """名前とパスの両方を受ける。打ち間違いに気づけるよう候補を添えて落とす。"""
    for candidate in (Path(value), SCRIPTS_DIR / value, SCRIPTS_DIR / f"{value}.json"):
        if candidate.is_file():
            return candidate
    available = ", ".join(sorted(path.stem for path in SCRIPTS_DIR.glob("*.json")))
    msg = f"台本 '{value}' が見つからない。利用できる台本: {available}"
    raise SystemExit(msg)


def main() -> None:
    parser = argparse.ArgumentParser(description="商談台本からデモ音声を生成する")
    parser.add_argument(
        "--script",
        required=True,
        help=f"台本。{SCRIPTS_DIR.name}/ 配下の名前かパスで指定する",
    )
    parser.add_argument(
        "--mode",
        choices=("chunk", "per-turn"),
        default="chunk",
        help="chunk: 会話をまとめて合成 / per-turn: 1発話ずつ合成して声を固定する",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="先頭N発話だけ生成する（動作確認用）",
    )
    parser.add_argument(
        "--chunk-bytes",
        type=int,
        default=MAX_INPUT_BYTES - SAFETY_MARGIN_BYTES,
        help=f"chunkモードの1リクエスト上限byte数（APIの上限は {MAX_INPUT_BYTES}）",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="最終出力先（既定 output/<台本名>.wav）",
    )
    parser.add_argument(
        "--no-normalize",
        action="store_true",
        help="パート間の音量調整を行わない",
    )
    parser.add_argument(
        "--concat-only",
        action="store_true",
        help="合成せず、既存の part_NN.wav を結合し直す",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="既にある part_NN.wav は作り直さない（途中で失敗したときの再開用）",
    )
    args = parser.parse_args()

    if args.chunk_bytes > MAX_INPUT_BYTES:
        raise SystemExit(f"--chunk-bytes は {MAX_INPUT_BYTES} 以下にすること")

    script = load_script(resolve_script_path(args.script), limit=args.limit)
    script.output_dir.mkdir(parents=True, exist_ok=True)
    destination = args.out or script.default_output
    print(f"台本: {script.title}（{script.stem}）")

    if args.concat_only:
        parts = sorted(script.output_dir.glob("part_*.wav"))
    else:
        client = texttospeech.TextToSpeechClient()
        if args.mode == "per-turn":
            parts = generate_per_turn(client, script, resume=args.resume)
        else:
            parts = generate_chunked(client, script, args.chunk_bytes)

    concat_wavs(parts, destination, GAP_MS, normalize=not args.no_normalize)
    print(f"生成完了: {destination} ({duration_seconds(destination):.1f} 秒)")


if __name__ == "__main__":
    main()
