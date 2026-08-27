/**
 * 登録の材料になるファイルを受け取り、本文のテキストにして返す。
 *
 * **音声もテキストも同じ入口にする。** 抽出の入力は結局テキストであり、
 * 経路を2本持つとプロンプトやチャンク分割の改善を両方に当て続けることに
 * なる（旧 AudioIngest の判断）。画面も同じ理由で分けない。
 * 利用者から見ても「登録するもの」は1つで、それが音声かテキストかは
 * 入口の違いでしかない。
 *
 * **文字起こしとナレッジ化は分ける。** 文字起こしの欠落や幻覚は後段のLLMでは
 * 直せない（誤りに見えないまま自然な文で埋められる）ため、必ず人が目を通す
 * 段を挟む。ここは本文を渡すところまでで、抽出は呼び出し側が行う。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { getConfigHealth, transcribeAudio } from "../api/client";
import { formatClock } from "../lib/time";
import type { AudioTranscribeResponse } from "../types/api";
import { Spinner } from "./chat/AgentTimeline";

/** バックエンドの _ALLOWED_SUFFIXES と揃える */
const AUDIO_SUFFIXES = [".wav", ".mp3", ".m4a", ".mp4", ".flac", ".ogg", ".webm", ".aac"];
/** そのまま本文にできるもの。文字起こしを挟まない */
const TEXT_SUFFIXES = [".txt", ".md", ".csv", ".vtt", ".srt"];
const ACCEPT = [...AUDIO_SUFFIXES, ...TEXT_SUFFIXES].join(",");

const MAX_BYTES = 200 * 1024 * 1024;

/**
 * 文字起こしにかかる実測倍率。8分50秒の音声が31秒だった（約17倍速）。
 * 待たせる以上は見込みを出したいが、外すと不信感になるので
 * 実測より遅めの15倍で見積もる。
 */
const REALTIME_FACTOR = 15;

export type PickedSource = {
  text: string;
  fileName: string;
  /** 音声から来た場合のみ。抽出時に渡すと、ナレッジがこの音声を出典に持つ */
  transcript: AudioTranscribeResponse | null;
};

function suffixOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot === -1 ? "" : name.slice(dot).toLowerCase();
}

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

type Props = {
  onPicked: (source: PickedSource) => void;
  disabled?: boolean;
};

export function SourcePicker({ onPicked, disabled = false }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [duration, setDuration] = useState<number | null>(null);
  const [running, setRunning] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [sttReady, setSttReady] = useState<boolean | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // STT未設定は「音声だけが動かない」状態になる。押してから503で気づくより、
  // 開いた時点で原因が分かる方が早い
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

  const pick = useCallback(
    async (picked: File) => {
      setError(null);
      const suffix = suffixOf(picked.name);

      if (TEXT_SUFFIXES.includes(suffix)) {
        // テキストは文字起こしを挟まない。読んですぐ本文になる
        try {
          onPicked({ text: await picked.text(), fileName: picked.name, transcript: null });
        } catch {
          setError("ファイルを読み込めませんでした。文字コードを確認してください。");
        }
        return;
      }

      if (!AUDIO_SUFFIXES.includes(suffix)) {
        setError(`対応していない形式です（${suffix || "拡張子なし"}）。${ACCEPT} に対応しています`);
        return;
      }
      if (picked.size > MAX_BYTES) {
        setError(`ファイルが大きすぎます（${formatSize(picked.size)} / 上限 200MB）`);
        return;
      }
      setFile(picked);
      setDuration(await readDuration(picked));
    },
    [onPicked],
  );

  async function run() {
    if (!file) return;
    setRunning(true);
    setElapsed(0);
    setError(null);
    try {
      const result = await transcribeAudio(file);
      onPicked({ text: result.text, fileName: result.file_name, transcript: result });
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
    <div className="space-y-3">
      {sttReady === false && (
        <p className="rounded-xl bg-amber-50 px-3.5 py-2.5 text-xs leading-relaxed text-amber-900 ring-1 ring-amber-200/70">
          音声認識サーバが未設定です。リポジトリ直下の <code>.env</code> に{" "}
          <code>STT_BASE_URL</code> を設定してバックエンドを再起動してください。
          テキストファイルは設定なしでも使えます。
        </p>
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
          "rounded-2xl border border-dashed px-4 py-6 text-center transition-colors " +
          (dragging ? "border-indigo-400 bg-indigo-50/60" : "border-slate-300 bg-white")
        }
      >
        <p className="text-sm text-slate-600">
          商談の録音やメモをここにドラッグするか、
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            disabled={running || disabled}
            className="mx-1 font-medium text-indigo-600 underline underline-offset-2 hover:text-indigo-500 disabled:text-slate-400"
          >
            ファイルを選ぶ
          </button>
        </p>
        <p className="mt-1.5 text-[11px] text-slate-400">
          音声 wav / mp3 / m4a など・テキスト txt / md / csv・上限 200MB
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
        <div className="rounded-2xl bg-white p-3.5 ring-1 ring-slate-200/80">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <span className="text-sm font-medium text-slate-800">{file.name}</span>
            <span className="text-[11px] text-slate-400">
              {formatSize(file.size)}
              {duration !== null && ` ・ ${formatClock(duration)}`}
            </span>
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-3">
            <button
              onClick={() => void run()}
              disabled={running || disabled}
              className="flex items-center gap-2 rounded-xl bg-indigo-600 px-3.5 py-2 text-sm
                         font-medium text-white transition-colors hover:bg-indigo-500
                         disabled:cursor-not-allowed disabled:bg-slate-200 disabled:text-slate-400"
            >
              {running && <Spinner className="size-4 text-white" />}
              {running ? `文字起こし中… ${elapsed}秒` : "文字起こしする"}
            </button>
            {running ? (
              <span className="text-[11px] text-slate-500">
                {estimate !== null && `目安 約${estimate}秒。`}
                完了するまでこの画面を閉じないでください
              </span>
            ) : (
              estimate !== null && (
                <span className="text-[11px] text-slate-400">目安 約{estimate}秒</span>
              )
            )}
          </div>
        </div>
      )}

      {error && (
        <p className="rounded-xl bg-rose-50 px-3.5 py-2.5 text-sm text-rose-800 ring-1 ring-rose-200/70">
          {error}
        </p>
      )}
    </div>
  );
}
