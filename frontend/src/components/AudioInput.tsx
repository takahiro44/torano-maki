/**
 * 商談音声を文字起こしし、確認・修正のうえでナレッジ化する。
 *
 * **文字起こしとナレッジ化を分けている。** 文字起こしの欠落や幻覚は
 * 後段のLLMでは直せない（誤りに見えないまま自然な文で埋められる）ため、
 * 必ず人が目を通す段を挟む。詳細は experiments/stt/README.md。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { getConfigHealth, transcribeAudio } from "../api/client";
import { formatClock } from "../lib/time";
import type { AudioTranscribeResponse } from "../types/api";

/** バックエンドの _ALLOWED_SUFFIXES と揃える */
const ACCEPT = ".wav,.mp3,.m4a,.mp4,.flac,.ogg,.webm,.aac";
const MAX_BYTES = 200 * 1024 * 1024;

/**
 * 文字起こしにかかる実測倍率。8分50秒の音声が31秒だった（約17倍速）。
 * 待たせる以上は見込みを出したいが、外すと不信感になるので
 * 実測より遅めの15倍で見積もる。
 */
const REALTIME_FACTOR = 15;

function formatSize(bytes: number): string {
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`;
}

/** 音声の長さはブラウザ側で取れる。取れなくても致命的ではないので null を返す */
function readDuration(file: File): Promise<number | null> {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const audio = new Audio();
    const done = (value: number | null) => {
      URL.revokeObjectURL(url);
      resolve(value);
    };
    audio.addEventListener("loadedmetadata", () =>
      done(Number.isFinite(audio.duration) ? audio.duration : null),
    );
    audio.addEventListener("error", () => done(null));
    audio.src = url;
  });
}

type Props = { onTranscribed: (result: AudioTranscribeResponse) => void };

export function AudioInput({ onTranscribed }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [duration, setDuration] = useState<number | null>(null);
  const [running, setRunning] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [sttReady, setSttReady] = useState<boolean | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // STT未設定は「音声タブだけが動かない」状態になる。押してから
  // 503で気づくより、開いた時点で原因が分かる方が早い
  useEffect(() => {
    getConfigHealth()
      .then((c) => setSttReady(c.stt_configured))
      .catch(() => setSttReady(null));
  }, []);

  // 30秒以上なにも動かないと止まって見えるため、経過秒数を出し続ける
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => setElapsed((n) => n + 1), 1000);
    return () => window.clearInterval(timer);
  }, [running]);

  const pick = useCallback(async (picked: File) => {
    setError(null);
    if (picked.size > MAX_BYTES) {
      setError(`ファイルが大きすぎます（${formatSize(picked.size)} / 上限 200MB）`);
      return;
    }
    setFile(picked);
    setDuration(await readDuration(picked));
  }, []);

  async function run() {
    if (!file) return;
    setRunning(true);
    setElapsed(0);
    setError(null);
    try {
      const result = await transcribeAudio(file);
      onTranscribed(result);
      setFile(null);
      setDuration(null);
      if (inputRef.current) inputRef.current.value = "";
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  }

  const estimate = duration === null ? null : Math.max(5, Math.round(duration / REALTIME_FACTOR));

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold">商談音声から登録</h2>
        <p className="mt-1 text-sm text-slate-500">
          音声をアップロードすると文字起こしします。内容を確認・修正してからナレッジ化するので、
          聞き取りの誤りをそのまま貯めずに済みます。
        </p>
      </div>

      {sttReady === false && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
          音声認識サーバが未設定です。リポジトリ直下の <code>.env</code> に{" "}
          <code>STT_BASE_URL</code> を設定してバックエンドを再起動してください。
        </div>
      )}

      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          const dropped = e.dataTransfer.files[0];
          if (dropped) void pick(dropped);
        }}
        className={
          "rounded-lg border-2 border-dashed p-8 text-center transition-colors " +
          (dragging ? "border-slate-500 bg-slate-50" : "border-slate-300 bg-white")
        }
      >
        <p className="text-sm text-slate-600">
          音声ファイルをここにドラッグするか、
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            disabled={running}
            className="mx-1 underline underline-offset-2 hover:text-slate-900 disabled:text-slate-400"
          >
            選択してください
          </button>
        </p>
        <p className="mt-2 text-xs text-slate-400">
          wav / mp3 / m4a / flac / ogg など・上限 200MB
        </p>
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          className="hidden"
          onChange={(e) => {
            const picked = e.target.files?.[0];
            if (picked) void pick(picked);
          }}
        />
      </div>

      {file && (
        <div className="rounded-lg border border-slate-200 bg-white p-4">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <span className="text-sm font-medium text-slate-800">{file.name}</span>
            <span className="text-xs text-slate-500">
              {formatSize(file.size)}
              {duration !== null && ` ・ ${formatClock(duration)}`}
            </span>
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-3">
            <button
              onClick={() => void run()}
              disabled={running}
              className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white
                         hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-300"
            >
              {running ? `文字起こし中… ${elapsed}秒` : "文字起こしする"}
            </button>
            {running ? (
              <span className="text-xs text-slate-500">
                {estimate !== null && `目安 約${estimate}秒。`}
                完了するまでこのタブを閉じないでください
              </span>
            ) : (
              estimate !== null && (
                <span className="text-xs text-slate-400">目安 約{estimate}秒</span>
              )
            )}
          </div>
        </div>
      )}

      {error && (
        <p className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          {error}
        </p>
      )}
    </div>
  );
}
