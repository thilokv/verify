"use client";

import { useState } from "react";
import { api, ApiError, rupees } from "@/lib/api";
import type { Match, SearchResponse } from "@/types";

const EXAMPLES = [
  "3BHK flat in Whitefield under 1.3 crore, ready to move, clean title",
  "plot near Devanahalli under 1 crore",
  "villa in Hebbal, budget 3.5 crore",
] as const;

export function SearchBar({
  onSelect,
}: {
  onSelect: (property: Match) => void;
}) {
  const [prompt, setPrompt] = useState<string>(EXAMPLES[0]);
  const [result, setResult] = useState<SearchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      setResult(await api.search(prompt));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Search failed.");
      setResult(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded-lg border border-neutral-200 bg-white p-5 dark:border-neutral-800 dark:bg-neutral-900">
      <h2 className="font-mono text-[11px] uppercase tracking-widest text-neutral-500">
        Describe what you want
      </h2>

      <textarea
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        rows={2}
        className="mt-3 w-full resize-y rounded border border-neutral-300 bg-neutral-50 p-3 text-[15px] outline-none focus:border-accent dark:border-neutral-700 dark:bg-neutral-950"
      />

      <div className="mt-2 flex flex-wrap gap-2">
        {EXAMPLES.map((ex) => (
          <button
            key={ex}
            onClick={() => setPrompt(ex)}
            className="rounded-full border border-neutral-300 px-3 py-1 text-xs text-neutral-600 transition hover:border-accent hover:text-accent dark:border-neutral-700 dark:text-neutral-400"
          >
            {ex.length > 44 ? `${ex.slice(0, 44)}…` : ex}
          </button>
        ))}
      </div>

      <div className="mt-4 flex items-center gap-3">
        <button
          onClick={run}
          disabled={busy}
          className="rounded bg-accent px-4 py-2 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50"
        >
          {busy ? "Searching…" : "Find my matches"}
        </button>
        {error && <p className="text-sm text-red-600">{error}</p>}
      </div>

      {result && (
        <div className="mt-5">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-t border-neutral-200 pt-4 dark:border-neutral-800">
            <span className="text-sm font-medium">
              {result.count} {result.count === 1 ? "match" : "matches"}
            </span>
            <span className="font-mono text-[11px] text-neutral-500">
              {result.semantic ? "semantic" : "lexical"} · rationale:{" "}
              {result.rationale_source}
            </span>
          </div>

          {result.notice && (
            <p className="mt-3 border-l-2 border-amber-500 bg-amber-50 p-3 text-sm text-amber-800 dark:bg-amber-950/40 dark:text-amber-300">
              {result.notice}
            </p>
          )}

          <ul className="mt-3 space-y-3">
            {result.matches.map((m) => (
              <li
                key={m.id}
                className="rounded border border-neutral-200 p-4 dark:border-neutral-800"
              >
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <p className="font-medium">{m.title}</p>
                    <p className="text-sm text-neutral-500">
                      {m.location_name}
                      {m.city ? `, ${m.city}` : ""}
                    </p>
                  </div>
                  <div className="text-right">
                    <p className="font-medium">{rupees(m.price_inr)}</p>
                    <span
                      className={
                        m.is_verified
                          ? "font-mono text-[10px] uppercase tracking-wider text-verified"
                          : "font-mono text-[10px] uppercase tracking-wider text-amber-600"
                      }
                    >
                      {m.is_verified ? "Title verified" : "Unverified"}
                    </span>
                  </div>
                </div>

                {m.why && <p className="mt-2 text-sm">{m.why}</p>}
                {m.concern && (
                  <p className="mt-1 text-sm text-amber-700 dark:text-amber-400">
                    {m.concern}
                  </p>
                )}

                <button
                  onClick={() => onSelect(m)}
                  className="mt-3 text-sm text-accent underline underline-offset-4 dark:text-blue-300"
                >
                  Value this property →
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
