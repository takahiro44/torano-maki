"""ChatGPT に書かせた商談台本JSONを、生成スクリプトが読める `scripts/*.json` にする。

台本はモデルに書かせるため、**スキーマは合っていても細部が毎回ぶれる。**
コードブロックのフェンスが付く、話者名が「営業」と `Sales` で混ざる、
1発話が API の上限を超える、といった崩れをそのたび手で直していては自動化にならない。

ここで崩れを吸収して正規化し、**API を1回も呼ばないうちに**弾けるものは弾く。
合成は課金されるため、落ちるなら合成前に落ちる方がよい。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).parent
SCRIPTS_DIR = BASE_DIR / "scripts"
DRAFTS_DIR = BASE_DIR / "drafts"

# generate_tts.py と同じ上限。1発話がこれを超えると合成時に 400 で落ちる
MAX_TURN_BYTES = 3800

# 話者エイリアスは generate_tts.py のプロンプト組み立てが参照するため、
# 台本ごとに違う名前にせず、この2つへ寄せる
SALES = "Sales"
CUSTOMER = "Customer"

# 発話の配列が入っているキー。`turns` と `dialogue` のどちらでも来る
TURNS_KEYS = ("turns", "dialogue", "conversation", "発話")

# 台本の中身として残すキー。合成には使わないが、
# **後工程（抽出結果の答え合わせ）で使うため落とさない**
EXTRA_KEYS = ("id", "product", "sales_stage", "small_talk", "gold_knowledge", "version")

# モデルが返しがちな表記ゆれを正規のエイリアスへ寄せる。
# 1文字違うだけで「未知の話者」として落ちるため、ここで吸収する
SPEAKER_ALIASES: dict[str, str] = {
    "sales": SALES,
    "salesperson": SALES,
    "営業": SALES,
    "営業担当": SALES,
    "営業担当者": SALES,
    "セールス": SALES,
    "自社": SALES,
    "customer": CUSTOMER,
    "client": CUSTOMER,
    "顧客": CUSTOMER,
    "お客様": CUSTOMER,
    "客": CUSTOMER,
    "顧客側": CUSTOMER,
    "先方": CUSTOMER,
    "担当者": CUSTOMER,
}

# `voice` を書き忘れたときの既定。実際に生成して声を確認済みのものだけを置く
DEFAULT_VOICES: dict[str, str] = {SALES: "Kore", CUSTOMER: "Algieba"}

# 人物像の文字列から読み取る性別と年代。台本に音声IDが無いときの割り当てに使う
GENDER_WORDS: dict[str, str] = {"女性": "female", "男性": "male"}
AGE_BUCKETS: dict[str, str] = {
    "10代": "young",
    "20代": "young",
    "30代": "middle",
    "40代": "middle",
    "50代": "senior",
    "60代": "senior",
    "70代": "senior",
}

# 性別・年代ごとの音声。**同じ文を全音声に読ませ、基本周波数(F0)を実測して決めた。**
# 名前から性別は分からない（`Gacrux` は 187Hz で女性だった）ため、耳ではなく数値で判定した。
# 括弧内は実測値。並び順は「営業は先頭・顧客は末尾」から取るため意味を持つ。
# 計測手順と全30音声の値は docs/setup-notes.md を参照。
PROFILE_VOICES: dict[tuple[str, str], tuple[str, ...]] = {
    # 若い女性（20代）。高い方から
    ("female", "young"): ("Sulafat", "Achernar", "Erinome"),  # 213 / 212 / 223Hz
    # 30〜40代女性。先頭の Kore は既存台本②の営業（高橋美咲）と同じ声
    ("female", "middle"): ("Kore", "Callirrhoe", "Despina", "Autonoe", "Aoede"),
    # 50代以上の女性。低い方を後ろに置き、顧客側に回るようにする
    ("female", "senior"): ("Zephyr", "Laomedeia", "Vindemiatrix", "Gacrux"),
    # 若い男性（20代）。男性の中では高い3つ
    ("male", "young"): ("Orus", "Alnilam", "Zubenelgenubi"),  # 151 / 142 / 139Hz
    # 30〜40代男性。先頭の Achird は既存台本①の営業と同じ声
    ("male", "middle"): ("Achird", "Puck", "Enceladus", "Rasalgethi"),
    # 50代以上の男性。末尾の Algieba は既存台本①②の顧客（業務部長）と同じ声
    ("male", "senior"): ("Algenib", "Umbriel", "Schedar", "Charon", "Algieba"),
}

DEFAULT_ROLES: dict[str, str] = {SALES: "営業担当者", CUSTOMER: "顧客側"}

# 人物像が体言止めで終わることが多く、そのままだと
# 「話し手は〜営業」と文が切れる。読み上げの口調も揃えたいので補う
PERSONA_SUFFIX = "です。話す速度は普通で、落ち着いた自然な口調にしてください。"

DEFAULT_PERSONAS: dict[str, str] = {
    SALES: (
        "30代の法人営業担当者で、落ち着いて丁寧です。"
        "話す速度は普通で、信頼感のある口調にしてください。"
    ),
    CUSTOMER: (
        "50代の業務部長で、自社の業務には精通している一方、"
        "ITやシステムの専門家ではありません。"
        "話す速度は普通で、落ち着いた自然な口調にしてください。"
    ),
}

# Gemini TTS のプリセット音声。**一覧に無くても止めない。**
# 提供される音声は増減するため、古い一覧で正しい指定を拒否する方が害が大きい
KNOWN_VOICES = frozenset(
    {
        "Achernar",
        "Achird",
        "Algenib",
        "Algieba",
        "Alnilam",
        "Aoede",
        "Autonoe",
        "Callirrhoe",
        "Charon",
        "Despina",
        "Enceladus",
        "Erinome",
        "Fenrir",
        "Gacrux",
        "Iapetus",
        "Kore",
        "Laomedeia",
        "Leda",
        "Orus",
        "Puck",
        "Pulcherrima",
        "Rasalgethi",
        "Sadachbia",
        "Sadaltager",
        "Schedar",
        "Sulafat",
        "Umbriel",
        "Vindemiatrix",
        "Zephyr",
        "Zubenelgenubi",
    }
)

# コードブロックで包んで返ってくることがあるため、最初にこれを剥がす
FENCE_PATTERN = re.compile(r"^\s*```[a-zA-Z]*\s*|\s*```\s*$")

# 発話を分割してよい位置。句点の直後で切ると意味の途中で切れない
SENTENCE_END = "。？！?!"


class DraftError(ValueError):
    """取り込みを中断する理由。合成前に人が直すべき内容だけをここに入れる。"""


def parse_json_text(text: str, source: str) -> Any:
    """モデル出力の体裁を落としてから JSON として読む。

    台本1本のオブジェクトと、複数本をまとめた配列のどちらも来るため、
    ここでは型を絞らない。振り分けは `split_bundle` が行う。
    """
    stripped = text.lstrip("﻿").strip()
    stripped = FENCE_PATTERN.sub("", stripped).strip()

    # 前置き（「以下が台本です」など）が付いていても、最外の括弧を拾えば読める。
    # 先に現れた方をJSONの開始とみなす（配列で来ることもあるため）
    candidates = [
        (start, stripped.rfind(closing))
        for opening, closing in ("{}", "[]")
        if (start := stripped.find(opening)) != -1
    ]
    if not candidates:
        msg = f"{source}: JSONが見つからない"
        raise DraftError(msg)
    start, end = min(candidates)

    try:
        return json.loads(stripped[start : end + 1])
    except json.JSONDecodeError as error:
        msg = f"{source}: JSONとして読めない（{error}）"
        raise DraftError(msg) from error


def normalize_text(value: str) -> str:
    """読み上げに影響しない体裁だけを落とす。

    改行や全角空白が残ると合成の間が不自然になり、byte数も無駄に増える。
    **触るのは空白だけ。** ここで NFKC をかけると「ですか？」の全角疑問符まで
    半角になり、読み上げの抑揚が変わってしまう。
    """
    return re.sub(r"\s+", " ", value.replace("　", " ")).strip()


def canonical_speaker(name: str) -> str | None:
    """表記ゆれを正規のエイリアスへ寄せる。判断できなければ None を返す。

    話者名は読み上げられないため、本文と違って NFKC まで正規化してよい。
    **同席者は `Customer2` のように末尾の数字で区別する**（商談には
    顧客側が2人出てくることがあり、2人目を1人目と同じ声にはできない）。
    """
    key = unicodedata.normalize("NFKC", normalize_text(name)).lower().rstrip(":：")
    suffix = ""
    if (match := re.fullmatch(r"(.*?)[ _]?(\d+)", key)) and match.group(1):
        key, suffix = match.group(1), match.group(2)
        suffix = "" if suffix == "1" else suffix

    base = (
        SALES
        if key == SALES.lower()
        else CUSTOMER
        if key == CUSTOMER.lower()
        else SPEAKER_ALIASES.get(key)
    )
    return None if base is None else f"{base}{suffix}"


def infer_profile(persona: str) -> tuple[str, str] | None:
    """人物像の文から性別と年代を読む。台本に音声IDが無いとき、これで声を選ぶ。

    書き方が「30代女性の営業」でも「50代男性、業務部長」でも拾えるよう、
    語の出現だけを見る。読み取れなければ None を返して既定値に任せる。
    """
    gender = next((v for word, v in GENDER_WORDS.items() if word in persona), None)
    age = next((v for word, v in AGE_BUCKETS.items() if word in persona), "middle")
    return None if gender is None else (gender, age)


def fallback_voice(base: str) -> str:
    """性別も年代も読み取れなかったときの声。無音にするよりは既定で鳴らす。"""
    return DEFAULT_VOICES.get(base, "Algieba")


def pick_voice(persona: str, alias: str, used: set[str], seed: str = "") -> str:
    """人物像に合う音声を選ぶ。**同じ台本の中で声が重ならないようにする。**

    営業は先頭から取る。営業は同じ人物が何本もの台本に登場するため、
    **台本が変わっても同じ声になる**必要があるからである。

    顧客は末尾から取り、さらに台本ごとに開始位置をずらす。
    顧客は台本ごとに別人なので、固定すると
    **40代女性の顧客が全台本で同じ声**になってしまう。
    """
    base = alias.rstrip("0123456789")
    profile = infer_profile(persona)
    candidates = PROFILE_VOICES.get(profile, ()) if profile else ()
    if base == SALES:
        return next((v for v in candidates if v not in used), fallback_voice(base))

    # 各分類の先頭は営業に予約されている。**同じ人物が何本もの台本に出る**ため、
    # その声を顧客に使うと、別会社の顧客が営業と同じ声で話すことになる
    order = list(reversed(candidates[1:] if len(candidates) > 1 else candidates))
    if order and seed:
        shift = int(hashlib.sha256(seed.encode()).hexdigest(), 16) % len(order)
        order = order[shift:] + order[:shift]

    for voice in order:
        if voice not in used:
            return voice
    return next((v for v in order if v), fallback_voice(base))


def normalize_speakers(
    raw: Any, source: str, seed: str = ""
) -> dict[str, dict[str, str]]:
    """`speakers` を整える。欠けた項目は既定で埋め、判別できない話者だけ拒否する。

    **人物像だけを文字列で書いた形も受ける**（`"Sales": "30代女性の営業"`）。
    音声IDが無ければ人物像から性別・年代を読んで割り当てる。
    """
    if not isinstance(raw, dict) or not raw:
        msg = f"{source}: speakers が無い"
        raise DraftError(msg)

    speakers: dict[str, dict[str, str]] = {}
    used_voices: set[str] = set()
    for name, config in raw.items():
        alias = canonical_speaker(str(name))
        if alias is None:
            msg = (
                f"{source}: 話者 '{name}' を Sales / Customer のどちらとも判断できない。"
                "台本側の話者名を直すこと"
            )
            raise DraftError(msg)
        if isinstance(config, str):
            config = {"persona": config}
        if not isinstance(config, dict):
            msg = f"{source}: speakers.{name} が文字列でもオブジェクトでもない"
            raise DraftError(msg)

        base = alias.rstrip("0123456789")
        persona = normalize_text(
            str(config.get("persona") or DEFAULT_PERSONAS.get(base, ""))
        )
        if persona and not persona.endswith(("。", "！", "？", ".", "!", "?")):
            persona += PERSONA_SUFFIX
        voice = str(config.get("voice") or "").strip() or pick_voice(
            persona, alias, used_voices, seed=seed
        )
        used_voices.add(voice)

        speakers[alias] = {
            "voice": voice,
            "role": normalize_text(
                str(config.get("role") or DEFAULT_ROLES.get(base, "話し手"))
            ),
            "persona": persona,
        }

    if len(speakers) < 2:
        msg = f"{source}: 話者が {len(speakers)} 人しかいない（商談として成立しない）"
        raise DraftError(msg)
    return speakers


def split_long_text(text: str, limit_bytes: int) -> list[str]:
    """上限を超える発話を句点で分ける。

    generate_tts.py は1発話が上限を超えると落ちる。モデルは指示しても稀に
    長い発話を書くため、機械的に分けて通す。語の途中で切ると読み上げが
    不自然になるので、分割点は句点の直後に限る。
    """
    if len(text.encode("utf-8")) <= limit_bytes:
        return [text]

    sentences: list[str] = []
    current = ""
    for char in text:
        current += char
        if char in SENTENCE_END:
            sentences.append(current)
            current = ""
    if current:
        sentences.append(current)

    parts: list[str] = []
    buffer = ""
    for sentence in sentences:
        candidate = buffer + sentence
        if buffer and len(candidate.encode("utf-8")) > limit_bytes:
            parts.append(buffer.strip())
            buffer = sentence
        else:
            buffer = candidate
    if buffer.strip():
        parts.append(buffer.strip())

    for part in parts:
        if len(part.encode("utf-8")) > limit_bytes:
            msg = (
                f"句点が無く {limit_bytes} bytes 以下に分けられない発話がある: "
                f"{part[:30]}…"
            )
            raise DraftError(msg)
    return parts


def normalize_turns(
    raw: Any, speakers: dict[str, dict[str, str]], source: str
) -> tuple[list[dict[str, str]], int]:
    """`turns` を整え、分割が起きた回数と併せて返す。"""
    if not isinstance(raw, list) or not raw:
        msg = f"{source}: turns が空"
        raise DraftError(msg)

    turns: list[dict[str, str]] = []
    split_count = 0
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            msg = f"{source}: turns[{index}] がオブジェクトではない"
            raise DraftError(msg)

        alias = canonical_speaker(str(item.get("speaker", "")))
        if alias is None or alias not in speakers:
            msg = (
                f"{source}: turns[{index}] の話者 '{item.get('speaker')}' が "
                "speakers に無い"
            )
            raise DraftError(msg)

        text = normalize_text(str(item.get("text", "")))
        if not text:
            continue  # 空の発話は無音のpartになるだけなので落とす

        parts = split_long_text(text, MAX_TURN_BYTES)
        split_count += len(parts) - 1
        turns.extend({"speaker": alias, "text": part} for part in parts)

    if not turns:
        msg = f"{source}: 本文のある発話が1つも無い"
        raise DraftError(msg)
    return turns, split_count


def normalize_script(raw: dict[str, Any], stem: str, source: str) -> dict[str, Any]:
    """台本1本ぶんを正規化する。ここを通れば generate_tts.py は必ず読める。"""
    speakers = normalize_speakers(raw.get("speakers"), source, seed=stem)
    turns, split_count = normalize_turns(script_turns(raw), speakers, source)

    unknown = {speaker["voice"] for speaker in speakers.values()} - KNOWN_VOICES
    if unknown:
        print(f"  ⚠ 一覧に無い音声ID: {', '.join(sorted(unknown))}（そのまま使う）")
    if split_count:
        print(f"  ⚠ 上限を超える発話を {split_count} 箇所で分割した")

    extras = {key: raw[key] for key in EXTRA_KEYS if key in raw}
    return {
        "title": normalize_text(str(raw.get("title") or stem)),
        **extras,
        "speakers": speakers,
        "turns": turns,
    }


# 束の中で台本の配列が入っているキー。ChatGPT の書き方が毎回同じとは限らない
BUNDLE_KEYS = ("scripts", "台本", "items", "dialogues", "conversations", "data")

# 台本ごとの名前に使えるキー。**ファイル名になる**ため英数字のものだけ採用する
STEM_KEYS = ("stem", "id", "slug", "name", "file", "filename")

SAFE_STEM = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


def script_turns(value: Any) -> Any:
    """台本の発話配列を取り出す。キー名が `turns` でも `dialogue` でも拾う。"""
    if not isinstance(value, dict):
        return None
    return next((value[key] for key in TURNS_KEYS if key in value), None)


def is_script(value: Any) -> bool:
    """台本1本かどうか。発話配列を持つオブジェクトだけを台本とみなす。"""
    return script_turns(value) is not None


def safe_stem(value: Any) -> str | None:
    """ファイル名に使える名前だけ通す。

    題名は日本語で来るためファイル名にしない。番号や英語のIDがあればそれを使い、
    無ければ呼び出し側が `<束の名前>_01` を振る。
    """
    if not isinstance(value, str):
        return None
    candidate = Path(value.strip()).stem
    return candidate if SAFE_STEM.fullmatch(candidate) else None


def split_bundle(raw: Any, bundle_stem: str, source: str) -> list[tuple[str, Any]]:
    """1ファイルに何本入っていても、`(名前, 台本)` の一覧に開く。

    台本は数本をまとめた1つのJSONで来る。**束ね方は毎回同じとは限らない**ため、
    配列・`scripts` キー・名前をキーにしたオブジェクトのどれでも受ける。
    束の側に `speakers` があれば、持っていない台本へ配る（人物像の書き直しを防ぐ）。
    """
    shared_speakers: Any = None
    items: list[tuple[str | None, Any]]

    if isinstance(raw, list):
        items = [(None, item) for item in raw]
    elif is_script(raw):
        items = [(bundle_stem, raw)]
    elif isinstance(raw, dict):
        shared_speakers = raw.get("speakers")
        key = next((k for k in BUNDLE_KEYS if isinstance(raw.get(k), list)), None)
        if key is not None:
            items = [(None, item) for item in raw[key]]
        elif raw and all(is_script(value) for value in raw.values()):
            items = [(safe_stem(name), value) for name, value in raw.items()]
        else:
            msg = (
                f"{source}: 台本が見つからない。"
                "台本1本のオブジェクト、その配列、または scripts キーで渡すこと"
            )
            raise DraftError(msg)
    else:
        msg = f"{source}: JSONのトップレベルがオブジェクトでも配列でもない"
        raise DraftError(msg)

    if not items:
        msg = f"{source}: 台本が1本も入っていない"
        raise DraftError(msg)

    scripts: list[tuple[str, Any]] = []
    used: set[str] = set()
    for index, (name, item) in enumerate(items, start=1):
        if not is_script(item):
            msg = f"{source}: {index} 本目に turns が無い"
            raise DraftError(msg)
        if shared_speakers is not None and not item.get("speakers"):
            item = {**item, "speakers": shared_speakers}

        stem = name or next(
            (s for key in STEM_KEYS if (s := safe_stem(item.get(key)))),
            f"{bundle_stem}_{index:02d}" if len(items) > 1 else bundle_stem,
        )
        if stem in used:
            msg = f"{source}: 台本の名前 '{stem}' が重複している"
            raise DraftError(msg)
        used.add(stem)
        scripts.append((stem, item))
    return scripts


def write_script(script: dict[str, Any], stem: str, *, force: bool) -> Path:
    """正規化済みの台本を `scripts/<名前>.json` に置く。置いた先を返す。

    **同名の台本が既にあって内容が変わる場合は、`--force` なしでは上書きしない。**
    声や人物像は `scripts/` 側を聴きながら手で調整するため、
    古い下書きを再実行しただけでその調整が消えると気づけない。
    """
    destination = SCRIPTS_DIR / f"{stem}.json"
    content = json.dumps(script, ensure_ascii=False, indent=2) + "\n"

    if destination.exists() and destination.read_text(encoding="utf-8") == content:
        print(f"  取り込み済み（変更なし）: scripts/{destination.name}")
        return destination
    if destination.exists() and not force:
        msg = (
            f"scripts/{destination.name} は既にあり、内容が下書きと違う。"
            "手で調整した台本を消さないよう止めた。上書きするなら --force"
        )
        raise DraftError(msg)

    SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")
    total_bytes = sum(len(turn["text"].encode("utf-8")) for turn in script["turns"])
    print(
        f"  取り込み完了: scripts/{destination.name}"
        f"（{len(script['turns'])} 発話 / 本文 {total_bytes} bytes）"
    )
    return destination


def import_draft(
    path: Path,
    *,
    force: bool = False,
    only: list[str] | None = None,
    prefix: str = "",
) -> list[Path]:
    """下書きJSONを検証して `scripts/` に置く。置いた台本を並び順で返す。

    **1本でも束でも同じ扱いにする。** 呼び出し側が本数を気にしなくてよいよう、
    常に一覧を返す。
    """
    raw = parse_json_text(path.read_text(encoding="utf-8"), path.name)
    entries = split_bundle(raw, bundle_stem=path.stem, source=path.name)
    if prefix:
        entries = [(f"{prefix}{stem}", item) for stem, item in entries]
    if only:
        entries = [(stem, item) for stem, item in entries if stem in only]
        if not entries:
            msg = f"{path.name}: --only で指定した台本が無い"
            raise DraftError(msg)
    if len(entries) > 1:
        print(f"  {len(entries)} 本の台本が入っている")

    # **全部を検証してから書く。** 3本目で落ちたときに1・2本目だけ
    # `scripts/` に残ると、束と取り込み済みの中身が食い違う
    normalized = [
        (
            stem,
            normalize_script(
                item,
                stem=stem,
                source=f"{path.name}[{stem}]" if len(entries) > 1 else path.name,
            ),
        )
        for stem, item in entries
    ]
    return [write_script(script, stem, force=force) for stem, script in normalized]


def collect_inputs(values: list[str]) -> list[Path]:
    """ファイル・ディレクトリ・`drafts/` 配下の名前のいずれでも受ける。"""
    paths: list[Path] = []
    for value in values:
        file_candidates = (
            Path(value),
            DRAFTS_DIR / value,
            DRAFTS_DIR / f"{value}.json",
        )
        found = next((c for c in file_candidates if c.is_file()), None)
        if found is not None:
            paths.append(found)
            continue
        dir_candidates = (Path(value), DRAFTS_DIR / value)
        directory = next((c for c in dir_candidates if c.is_dir()), None)
        if directory is not None:
            paths.extend(sorted(directory.glob("*.json")))
            continue
        raise SystemExit(f"入力 '{value}' が見つからない")
    if not paths:
        raise SystemExit("取り込む台本が無い")
    return paths


def list_bundle(path: Path) -> None:
    """束に何本入っているかだけを出す。取り込む前に中身を確かめるため。"""
    raw = parse_json_text(path.read_text(encoding="utf-8"), path.name)
    for stem, item in split_bundle(raw, bundle_stem=path.stem, source=path.name):
        turns = script_turns(item)
        count = len(turns) if isinstance(turns, list) else 0
        print(f"  {stem}\t{count} 発話\t{item.get('title', '')}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ChatGPTが書いた台本JSONを検証して scripts/ に取り込む"
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        help=f"下書きJSON。ファイル / ディレクトリ / {DRAFTS_DIR.name}/ 配下の名前",
    )
    parser.add_argument(
        "--only",
        nargs="+",
        metavar="名前",
        help="束のうち指定した名前の台本だけを取り込む",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="取り込まず、束に入っている台本の一覧だけ出す",
    )
    parser.add_argument(
        "--prefix",
        default="",
        help="台本の名前の先頭に付ける文字列（例 long_ → long_001.json）",
    )
    parser.add_argument(
        "--force", action="store_true", help="scripts/ にある同名の台本を上書きする"
    )
    args = parser.parse_args()

    for path in collect_inputs(args.inputs):
        print(f"検証中: {path}")
        try:
            if args.list:
                list_bundle(path)
            else:
                import_draft(path, force=args.force, only=args.only, prefix=args.prefix)
        except DraftError as error:
            raise SystemExit(f"  ✗ {error}") from error


if __name__ == "__main__":
    main()
