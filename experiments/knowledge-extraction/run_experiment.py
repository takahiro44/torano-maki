"""商談文字起こしをDGX上のLLMへ渡し、ER図に対応するJSONを生成する。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import ValidationError

from schema import (
    CallSummary,
    DataSource,
    ExperimentResult,
    KnowledgeDraft,
    KnowledgeEvidence,
    KnowledgeUnit,
    LlmExtraction,
    TranscriptDocument,
    UtteranceSegment,
)

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[1]
DEFAULT_TRANSCRIPT = _HERE / "input" / "medium_glossary.json"
DEFAULT_AUDIO = _HERE / "input" / "sales_demo_perturn.wav"
DEFAULT_PROMPT = _HERE / "prompt.md"
DEFAULT_OUTPUT = _HERE / "output" / "knowledge_extraction.json"
DEFAULT_ENV = _REPO_ROOT / ".env"


def load_env(path: Path) -> None:
    """検証用の2設定だけを読むため、依存を増やさず単純な.envを扱う。"""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def load_transcript(path: Path) -> TranscriptDocument:
    return TranscriptDocument.model_validate_json(path.read_text(encoding="utf-8"))


def format_transcript(transcript: TranscriptDocument) -> str:
    return "\n".join(
        f"[{sequence_no:04d}] {segment.start:.2f}-{segment.end:.2f} {segment.text}"
        for sequence_no, segment in enumerate(transcript.segments, start=1)
    )


def build_messages(prompt_path: Path, transcript: TranscriptDocument) -> list[dict[str, str]]:
    schema = json.dumps(LlmExtraction.model_json_schema(), ensure_ascii=False, indent=2)
    system_prompt = prompt_path.read_text(encoding="utf-8").rstrip()
    system_prompt += f"\n\nJSON Schema:\n{schema}"
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"文字起こし:\n{format_transcript(transcript)}"},
    ]


def call_chat_completions(
    base_url: str,
    model_name: str,
    messages: list[dict[str, str]],
    max_tokens: int,
    timeout_seconds: float,
) -> dict[str, object]:
    payload = {
        "model": model_name,
        "messages": messages,
        "temperature": 0,
        "max_tokens": max_tokens,
        # Qwen系では思考トークンだけで上限を使い切らないよう、最終JSONを直接出させる。
        "chat_template_kwargs": {"enable_thinking": False},
    }
    headers = {"Content-Type": "application/json"}
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"DGX returned HTTP {error.code}: {body[:2000]}") from error


def response_content(response: dict[str, object]) -> str:
    try:
        choices = response["choices"]
        if not isinstance(choices, list) or not choices:
            raise TypeError
        message = choices[0]["message"]
        if not isinstance(message, dict):
            raise TypeError
        content = message.get("content") or message.get("reasoning_content")
        if not isinstance(content, str) or not content.strip():
            raise TypeError
        return content
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError("chat/completions応答に本文がありません") from error


def parse_json_object(content: str) -> dict[str, object]:
    stripped = content.strip()
    candidates = [stripped]
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", stripped, flags=re.DOTALL)
    if fenced:
        candidates.append(fenced.group(1))
    first_brace, last_brace = stripped.find("{"), stripped.rfind("}")
    if 0 <= first_brace < last_brace:
        candidates.append(stripped[first_brace : last_brace + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("LLM応答からJSONオブジェクトを取り出せません")


def validate_semantics(extraction: LlmExtraction, segment_count: int) -> None:
    expected = set(range(1, segment_count + 1))
    assigned = [item.sequence_no for item in extraction.speaker_assignments]
    if len(assigned) != len(set(assigned)):
        raise ValueError("speaker_assignmentsにsequence_noの重複があります")
    missing, unexpected = expected - set(assigned), set(assigned) - expected
    if missing or unexpected:
        raise ValueError(
            f"speaker_assignmentsが入力と一致しません: missing={sorted(missing)}, "
            f"unexpected={sorted(unexpected)}"
        )
    for draft in extraction.knowledge_units:
        for evidence in draft.evidence:
            if evidence.start_sequence_no > evidence.end_sequence_no:
                raise ValueError(f"evidenceの開始と終了が逆です: {draft.title}")
            if (
                evidence.start_sequence_no not in expected
                or evidence.end_sequence_no not in expected
            ):
                raise ValueError(f"evidenceが存在しない発話を参照しています: {draft.title}")


def _search_text(draft: KnowledgeDraft) -> str:
    values = [
        draft.title,
        draft.situation,
        draft.problem,
        draft.judgment,
        draft.action,
        draft.reasoning,
        draft.outcome,
        draft.lesson,
        draft.applicable_situations,
        draft.limitations,
        draft.industry,
        draft.product,
        draft.sales_stage,
    ]
    return "\n".join(value for value in values if value)


def materialize_result(
    transcript: TranscriptDocument,
    extraction: LlmExtraction,
    transcript_path: Path,
    audio_path: Path,
    now: datetime | None = None,
) -> ExperimentResult:
    validate_semantics(extraction, len(transcript.segments))
    transcript_hash = hashlib.sha256(transcript_path.read_bytes()).hexdigest()
    source_id = uuid5(NAMESPACE_URL, f"torano-maki:{audio_path.name}:{transcript_hash}")
    occurred_at = datetime.fromtimestamp(audio_path.stat().st_mtime, tz=UTC)
    created_at = now or datetime.now(UTC)
    speaker_by_sequence = {
        item.sequence_no: item.speaker for item in extraction.speaker_assignments
    }
    utterance_id_by_sequence: dict[int, UUID] = {}
    utterances: list[UtteranceSegment] = []
    for sequence_no, segment in enumerate(transcript.segments, start=1):
        utterance_id = uuid5(source_id, f"utterance:{sequence_no}")
        utterance_id_by_sequence[sequence_no] = utterance_id
        utterances.append(
            UtteranceSegment(
                id=utterance_id,
                data_source_id=source_id,
                sequence_no=sequence_no,
                speaker=speaker_by_sequence[sequence_no],
                start_sec=segment.start,
                end_sec=segment.end,
                content=segment.text,
            )
        )

    knowledge_units: list[KnowledgeUnit] = []
    evidence_rows: list[KnowledgeEvidence] = []
    for knowledge_index, draft in enumerate(extraction.knowledge_units, start=1):
        knowledge_id = uuid5(source_id, f"knowledge:{knowledge_index}:{draft.title}")
        knowledge_units.append(
            KnowledgeUnit(
                id=knowledge_id,
                data_source_id=source_id,
                knowledge_type=draft.knowledge_type,
                title=draft.title,
                situation=draft.situation,
                problem=draft.problem,
                judgment=draft.judgment,
                action=draft.action,
                reasoning=draft.reasoning,
                outcome=draft.outcome,
                lesson=draft.lesson,
                applicable_situations=draft.applicable_situations,
                limitations=draft.limitations,
                industry=draft.industry,
                product=draft.product,
                sales_stage=draft.sales_stage,
                search_text=_search_text(draft),
                embedding=None,
                embedding_model=None,
                created_at=created_at,
            )
        )
        for evidence_index, evidence in enumerate(draft.evidence, start=1):
            evidence_rows.append(
                KnowledgeEvidence(
                    id=uuid5(knowledge_id, f"evidence:{evidence_index}"),
                    knowledge_id=knowledge_id,
                    start_utterance_id=utterance_id_by_sequence[evidence.start_sequence_no],
                    end_utterance_id=utterance_id_by_sequence[evidence.end_sequence_no],
                )
            )

    summary = extraction.call_summary
    return ExperimentResult(
        data_sources=[
            DataSource(
                id=source_id,
                source_type="audio",
                file_name=audio_path.name,
                occurred_at=occurred_at,
            )
        ],
        utterance_segments=utterances,
        knowledge_units=knowledge_units,
        knowledge_evidence=evidence_rows,
        call_summaries=[
            CallSummary(
                id=uuid5(source_id, "call-summary"),
                data_source_id=source_id,
                summary=summary.summary,
                customer_needs=summary.customer_needs,
                proposals=summary.proposals,
                decisions=summary.decisions,
                next_actions=summary.next_actions,
            )
        ],
    )


def extract_with_retries(
    base_url: str,
    model_name: str,
    messages: list[dict[str, str]],
    segment_count: int,
    output_dir: Path,
    max_attempts: int,
    max_tokens: int,
    timeout_seconds: float,
) -> LlmExtraction:
    current_messages = list(messages)
    last_error: Exception | None = None
    output_dir.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, max_attempts + 1):
        print(f"DGXへ抽出リクエストを送信: attempt {attempt}/{max_attempts}", flush=True)
        response = call_chat_completions(
            base_url,
            model_name,
            current_messages,
            max_tokens,
            timeout_seconds,
        )
        raw_path = output_dir / f"raw_attempt_{attempt}.json"
        raw_path.write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8")
        content = ""
        try:
            content = response_content(response)
            parsed = parse_json_object(content)
            extraction = LlmExtraction.model_validate(parsed)
            validate_semantics(extraction, segment_count)
            return extraction
        except (ValidationError, ValueError) as error:
            last_error = error
            print(f"応答の検証に失敗: {error}", file=sys.stderr, flush=True)
            if attempt < max_attempts:
                current_messages.extend(
                    [
                        {"role": "assistant", "content": content},
                        {
                            "role": "user",
                            "content": (
                                "前のJSONは次の検証エラーになりました。元の文字起こしと"
                                "JSON Schemaに従い、修正後のJSONオブジェクトだけを"
                                "返してください。\n"
                                f"{str(error)[:4000]}"
                            ),
                        },
                    ]
                )
    raise RuntimeError(f"{max_attempts}回とも構造検証に失敗しました: {last_error}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transcript", type=Path, default=DEFAULT_TRANSCRIPT)
    parser.add_argument("--audio", type=Path, default=DEFAULT_AUDIO)
    parser.add_argument("--prompt", type=Path, default=DEFAULT_PROMPT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--base-url", help=".envのBASE_URLをこの実行だけ上書きする")
    parser.add_argument("--model-name", help=".envのMODEL_NAMEをこの実行だけ上書きする")
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--max-tokens", type=int, default=12000)
    parser.add_argument("--timeout", type=float, default=600)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    for required_path in (args.transcript, args.audio, args.prompt):
        if not required_path.exists():
            raise SystemExit(f"必要なファイルがありません: {required_path}")
    load_env(args.env_file)
    base_url = (args.base_url or os.getenv("BASE_URL", "")).strip()
    model_name = (args.model_name or os.getenv("MODEL_NAME", "")).strip()
    if not base_url or not model_name:
        raise SystemExit("BASE_URL / MODEL_NAME が未設定です。.envを確認してください")

    transcript = load_transcript(args.transcript)
    print(
        f"入力: {args.audio.name} / {len(transcript.segments)} segments / model={model_name}",
        flush=True,
    )
    messages = build_messages(args.prompt, transcript)
    extraction = extract_with_retries(
        base_url=base_url,
        model_name=model_name,
        messages=messages,
        segment_count=len(transcript.segments),
        output_dir=args.output.parent,
        max_attempts=args.max_attempts,
        max_tokens=args.max_tokens,
        timeout_seconds=args.timeout,
    )
    result = materialize_result(
        transcript=transcript,
        extraction=extraction,
        transcript_path=args.transcript,
        audio_path=args.audio,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    print(
        f"完了: knowledge={len(result.knowledge_units)}, "
        f"evidence={len(result.knowledge_evidence)}, output={args.output}",
        flush=True,
    )


if __name__ == "__main__":
    main()
