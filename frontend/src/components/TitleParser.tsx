"use client";

import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { DocumentCheck } from "@/types";

const SAMPLES: ReadonlyArray<readonly [string, string]> = [
  [
    "Mortgage found",
    "ENCUMBRANCE CERTIFICATE Form 15. Survey No: 42/1. A mortgage was created in favour of the bank over the schedule property on 12-03-2019.",
  ],
  [
    "Clean EC",
    "ENCUMBRANCE CERTIFICATE Form 15. The property bearing Survey No: 42/1 is free from all encumbrance for the period 1994 to 2026.",
  ],
  [
    "B-Khata",
    "This is a B-Khata property under BBMP limits. Survey No: 88/3, extent 1200 sq ft.",
  ],
  [
    "Litigation",
    "SALE DEED. Survey No: 17/2. O.S. No 441/2021 is pending before the civil court with a stay order.",
  ],
];

const VERDICT_STYLE: Record<DocumentCheck["verdict"], string> = {
  PASSED: "border-verified text-verified",
  PENDING: "border-amber-500 text-amber-700 dark:text-amber-400",
  RED_FLAGGED: "border-red-500 text-red-600",
};

export function TitleParser() {
  const [text, setText] = useState<string>(SAMPLES[1][1]);
  const [result, setResult] = useState<DocumentCheck | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      setResult(await api.checkDocument(text));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Check failed.");
      setResult(null);
    } finally {
      setBusy(false);
    }
  }

  const extracted = result
    ? Object.entries(result.extracted).filter(([, v]) => Boolean(v))
    : [];

  return (
    <section className="rounded-lg border border-neutral-200 bg-white p-5 dark:border-neutral-800 dark:bg-neutral-900">
      <h2 className="font-mono text-[11px] uppercase tracking-widest text-neutral-500">
        Title &amp; legal parser
      </h2>

      <div className="mt-3 flex flex-wrap gap-2">
        {SAMPLES.map(([label, sample]) => (
          <button
            key={label}
            onClick={() => setText(sample)}
            className="rounded-full border border-neutral-300 px-3 py-1 text-xs text-neutral-600 transition hover:border-accent hover:text-accent dark:border-neutral-700 dark:text-neutral-400"
          >
            {label}
          </button>
        ))}
      </div>

      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={5}
        placeholder="Paste an encumbrance certificate, sale deed or khata extract…"
        className="mt-3 w-full resize-y rounded border border-neutral-300 bg-neutral-50 p-3 font-mono text-xs outline-none focus:border-accent dark:border-neutral-700 dark:bg-neutral-950"
      />

      <div className="mt-3 flex items-center gap-3">
        <button
          onClick={run}
          disabled={busy}
          className="rounded bg-accent px-4 py-2 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50"
        >
          {busy ? "Reading…" : "Check this document"}
        </button>
        {error && <p className="text-sm text-red-600">{error}</p>}
      </div>

      {result && (
        <div className="mt-4">
          <p
            className={`border-l-2 pl-3 font-mono text-sm uppercase tracking-wider ${VERDICT_STYLE[result.verdict]}`}
          >
            {result.verdict.replace(/_/g, " ")} · {result.document_type}
          </p>

          {result.issues.length > 0 ? (
            <ul className="mt-3 space-y-2">
              {result.issues.map((i) => (
                <li
                  key={i.code}
                  className="rounded border border-neutral-200 p-3 text-sm dark:border-neutral-800"
                >
                  <code className="font-mono text-[10px] uppercase tracking-wider text-neutral-500">
                    {i.code}
                  </code>
                  <p className="mt-1">{i.detail}</p>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-3 text-sm text-neutral-500">
              No detectable problems in this screen.
            </p>
          )}

          {extracted.length > 0 && (
            <p className="mt-3 font-mono text-[11px] text-neutral-500">
              extracted →{" "}
              {extracted.map(([k, v]) => `${k}=${String(v)}`).join("   ")}
            </p>
          )}

          {result.disclaimer && (
            <p className="mt-3 text-xs leading-relaxed text-neutral-500">
              {result.disclaimer}
            </p>
          )}
        </div>
      )}
    </section>
  );
}
