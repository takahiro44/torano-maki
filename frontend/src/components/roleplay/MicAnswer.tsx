/**
 * マイクで答える。録音してSTTにかけ、結果を回答欄へ流し込む。
 *
 * **文字起こしを直接送信しない。** 結果は入力欄に入るだけで、
 * 送信するかどうかは人が決める。STTの誤認識をそのまま送ると、
 * 顧客役が誤った内容へ答え、フィードバックまでその前提で作られる
 * （計画書8章）。
 *
 * **録音した音声は送信後に破棄する。** 練習音声はサーバにも
 * ブラウザにも残さない（計画書15章）。
 */

import { useEffect, useRef, useState } from "react";
import { transcribeRoleplayAnswer } from "../../api/client";

type Props = {
  sessionId: string;
  disabled: boolean;
  /** 文字起こし結果。呼び出し側が入力欄へ入れて、人が直してから送る */
  onTranscribed: (text: string) => void;
};

type Phase = "idle" | "recording" | "transcribing";

/** ブラウザが実際に出せる形式を選ぶ。指定が通らないと録音自体が始まらない */
function pickMimeType(): string | undefined {
  const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg"];
  return candidates.find((type) => MediaRecorder.isTypeSupported(type));
}

export function MicAnswer({ sessionId, disabled, onTranscribed }: Props) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [seconds, setSeconds] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  useEffect(() => {
    if (phase === "idle") return;
    const started = Date.now();
    const timer = window.setInterval(
      () => setSeconds(Math.floor((Date.now() - started) / 1000)),
      1000,
    );
    return () => window.clearInterval(timer);
  }, [phase]);

  // 画面を離れるときにマイクを掴んだままにしない。
  // 解放しないとブラウザの録音インジケータが出続け、不信感になる
  useEffect(() => {
    return () => {
      recorderRef.current?.stream.getTracks().forEach((track) => track.stop());
    };
  }, []);

  async function startRecording() {
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
      setError("マイクを使えませんでした。ブラウザの権限設定とマイクの接続を確認してください。");
      setPhase("idle");
    }
  }

  function stopRecording() {
    setPhase("transcribing");
    setSeconds(0);
    recorderRef.current?.stop();
  }

  async function transcribe(audio: Blob) {
    try {
      const result = await transcribeRoleplayAnswer(sessionId, audio);
      onTranscribed(result.text);
    } catch (e) {
      setError(describeSttError(e));
    } finally {
      // 音声はここで参照を切る。ブラウザにも残さない
      chunksRef.current = [];
      recorderRef.current = null;
      setPhase("idle");
    }
  }

  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2">
        {phase === "recording" ? (
          <button
            onClick={stopRecording}
            className="flex items-center gap-2 rounded-lg border border-red-300 bg-red-50 px-3 py-2
                       text-sm font-medium text-red-700 hover:bg-red-100"
          >
            <span className="inline-block size-2 animate-pulse rounded-full bg-red-500" />
            録音を止める（{seconds}秒）
          </button>
        ) : (
          <button
            onClick={() => void startRecording()}
            disabled={disabled || phase === "transcribing"}
            className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700
                       hover:border-slate-500 disabled:cursor-not-allowed disabled:text-slate-300"
          >
            {phase === "transcribing" ? `文字起こし中…（${seconds}秒）` : "マイクで答える"}
          </button>
        )}
      </div>

      {phase === "transcribing" && (
        <p className="text-xs text-slate-400">
          文字起こしの結果は入力欄に入ります。誤りがあれば直してから送信してください。
        </p>
      )}

      {error && <p className="text-xs text-red-700">{error}</p>}
    </div>
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
    return "音声認識サーバが設定されていません。.env の STT_BASE_URL を確認してください。";
  }
  if (message.includes("接続できません")) {
    return "音声認識サーバ（DGX）に接続できません。キーボードで回答できます。";
  }
  return message;
}
