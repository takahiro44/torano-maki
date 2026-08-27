/**
 * どの入力欄にも付けられるマイク。話した内容を文字にして入力欄へ入れる。
 *
 * **文字起こしを直接送信しない。** 結果は入力欄に入るだけで、送るかどうかは
 * 人が決める。STTは誤認識するので、確認の段を挟まないと誤りがそのまま
 * 蓄積される（MicAnswer と同じ判断）。
 *
 * **録音した音声はブラウザにもサーバにも残さない。** 送信後に参照を切る。
 *
 * **既存の入力欄を置き換えない。** キーボードで打てる場所を減らさない。
 * 音声はあくまで打つ代わりの選択肢で、唯一の手段になってはいけない。
 */

import { useEffect, useRef, useState } from "react";
import { transcribeAudio } from "../api/client";
import { Spinner } from "./chat/AgentTimeline";

type Phase = "idle" | "recording" | "transcribing";

type Props = {
  /** 文字起こし結果。呼び出し側が入力欄へ入れて、人が直してから送る */
  onTranscribed: (text: string) => void;
  disabled?: boolean;
  /** 入力欄の中に置くか、単独で置くか。中に置くときは枠を持たない */
  variant?: "inline" | "standalone";
  /**
   * 文字起こしをどのAPIで行うか。既定はナレッジ取り込み用の
   * `/ingest/audio/transcribe` で、話した内容が `data_sources` に残る。
   *
   * **残したくない画面は必ず差し替えること。** AIチャットの質問のように
   * ナレッジの材料ではない音声を既定のまま通すと、出典一覧に
   * 「質問だけの商談音声」が積み上がる（`transcribeChatQuestion` を渡す）。
   */
  transcribe?: (audio: Blob) => Promise<{ text: string }>;
};

/** 既定の文字起こし。話した内容を商談音声として `data_sources` に記録する */
function transcribeAsDataSource(audio: Blob): Promise<{ text: string }> {
  const type = audio.type || "audio/webm";
  return transcribeAudio(new File([audio], fileNameFor(type), { type }));
}

/** ブラウザが実際に出せる形式を選ぶ。指定が通らないと録音自体が始まらない */
function pickMimeType(): string | undefined {
  const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg"];
  return candidates.find((type) => MediaRecorder.isTypeSupported(type));
}

/** バックエンドは拡張子で形式を判定する。MIMEと合わない名前を付けると弾かれる */
function fileNameFor(mimeType: string): string {
  if (mimeType.includes("mp4")) return "recording.mp4";
  if (mimeType.includes("ogg")) return "recording.ogg";
  return "recording.webm";
}

export function MicButton({
  onTranscribed,
  disabled = false,
  variant = "inline",
  transcribe: transcribeAudioBlob = transcribeAsDataSource,
}: Props) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [seconds, setSeconds] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  useEffect(() => {
    if (phase !== "recording") return;
    const started = Date.now();
    const timer = window.setInterval(
      () => setSeconds(Math.floor((Date.now() - started) / 1000)),
      500,
    );
    return () => window.clearInterval(timer);
  }, [phase]);

  // 画面を離れるときにマイクを掴んだままにしない。解放しないと
  // ブラウザの録音インジケータが出続け、不信感になる
  useEffect(() => {
    return () => {
      recorderRef.current?.stream.getTracks().forEach((track) => track.stop());
    };
  }, []);

  async function start() {
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream, { mimeType: pickMimeType() });
      chunksRef.current = [];
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      recorder.onstop = () => {
        stream.getTracks().forEach((track) => track.stop());
        void transcribe(new Blob(chunksRef.current, { type: recorder.mimeType }));
      };
      recorderRef.current = recorder;
      recorder.start();
      setSeconds(0);
      setPhase("recording");
    } catch {
      // 権限拒否とマイク未接続を区別できないため、両方を案内する
      setError("マイクを使えませんでした。ブラウザの権限とマイクの接続を確認してください。");
      setPhase("idle");
    }
  }

  function stop() {
    setPhase("transcribing");
    recorderRef.current?.stop();
  }

  async function transcribe(audio: Blob) {
    try {
      const result = await transcribeAudioBlob(audio);
      onTranscribed(result.text);
    } catch (e) {
      setError(describeSttError(e));
    } finally {
      // 音声はここで参照を切る。ブラウザにも残さない
      chunksRef.current = [];
      recorderRef.current = null;
      setPhase("idle");
      setSeconds(0);
    }
  }

  const label =
    phase === "recording"
      ? `録音を止める（${seconds}秒）`
      : phase === "transcribing"
        ? "文字起こし中"
        : "音声で入力する";

  return (
    <span className="relative inline-flex shrink-0">
      <button
        type="button"
        onClick={phase === "recording" ? stop : () => void start()}
        disabled={disabled || phase === "transcribing"}
        aria-label={label}
        title={label}
        className={
          "flex items-center justify-center rounded-xl transition-colors " +
          (variant === "inline" ? "size-9 " : "size-10 ring-1 ring-slate-200 ") +
          (phase === "recording"
            ? "bg-rose-500 text-white hover:bg-rose-400"
            : "bg-white text-slate-400 hover:bg-slate-100 hover:text-slate-600 disabled:text-slate-300")
        }
      >
        {phase === "transcribing" ? (
          <Spinner className="size-4 text-slate-400" />
        ) : (
          <MicIcon recording={phase === "recording"} />
        )}
      </button>

      {phase === "recording" && (
        <span className="pointer-events-none absolute -top-1 -right-1 flex size-2.5">
          <span className="absolute inline-flex size-full animate-ping rounded-full bg-rose-400 opacity-75" />
          <span className="relative inline-flex size-2.5 rounded-full bg-rose-500" />
        </span>
      )}

      {error && (
        <span
          role="alert"
          className="absolute bottom-full left-1/2 z-20 mb-2 w-60 -translate-x-1/2 rounded-xl
                     bg-rose-50 px-3 py-2 text-[11px] leading-relaxed text-rose-800
                     ring-1 ring-rose-200/70"
        >
          {error}
          <button
            type="button"
            onClick={() => setError(null)}
            className="mt-1 block text-rose-600 underline underline-offset-2"
          >
            閉じる
          </button>
        </span>
      )}
    </span>
  );
}

function MicIcon({ recording }: { recording: boolean }) {
  return (
    <svg viewBox="0 0 20 20" className="size-[18px]" aria-hidden="true">
      <rect
        x="7"
        y="2.5"
        width="6"
        height="9"
        rx="3"
        fill={recording ? "currentColor" : "none"}
        stroke="currentColor"
        strokeWidth="1.6"
      />
      <path
        d="M4.5 9.5a5.5 5.5 0 0 0 11 0M10 15v2.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
    </svg>
  );
}

function describeSttError(e: unknown): string {
  const message = e instanceof Error ? e.message : String(e);
  if (e instanceof Error && e.name === "TimeoutError") {
    return "文字起こしが終わりませんでした。もう一度お試しください。";
  }
  if (message.includes("無音") || message.includes("空")) {
    return "音声を聞き取れませんでした。マイクに近づいてもう一度話してください。";
  }
  if (message.includes("未設定")) {
    return "音声認識サーバが未設定です。.env の STT_BASE_URL を確認してください。";
  }
  if (message.includes("接続できません")) {
    return "音声認識サーバ（DGX）に接続できません。キーボードで入力できます。";
  }
  return message;
}
