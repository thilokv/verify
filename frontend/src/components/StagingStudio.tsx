"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { Renovation, Scope, Staging } from "@/types";

export function StagingStudio() {
  const [scopes, setScopes] = useState<Scope[]>([]);
  const [rooms, setRooms] = useState<string[]>([]);
  const [styles, setStyles] = useState<string[]>([]);

  const [chosen, setChosen] = useState<string[]>(["deep_clean", "paint"]);
  const [area, setArea] = useState(1500);
  const [grade, setGrade] = useState("standard");
  const [room, setRoom] = useState("living");
  const [style, setStyle] = useState("contemporary");
  const [file, setFile] = useState<File | null>(null);

  const [costing, setCosting] = useState<Renovation | null>(null);
  const [staging, setStaging] = useState<Staging | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .renovationScopes()
      .then((d) => {
        setScopes(d.scopes);
        setRooms(d.rooms);
        setStyles(d.styles);
      })
      .catch(() => setError("Could not load renovation options."));
  }, []);

  function toggle(key: string): void {
    setChosen((prev) =>
      prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key],
    );
  }

  async function estimate(): Promise<void> {
    setError(null);
    try {
      setCosting(await api.renovationEstimate(chosen, area, grade));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Estimate failed.");
      setCosting(null);
    }
  }

  async function runStaging(): Promise<void> {
    setError(null);
    try {
      setStaging(await api.stage(room, style, area, file));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Staging failed.");
      setStaging(null);
    }
  }

  return (
    <section className="rounded-lg border border-neutral-200 bg-white p-5 dark:border-neutral-800 dark:bg-neutral-900">
      <h2 className="font-mono text-[11px] uppercase tracking-widest text-neutral-500">
        Renovation &amp; staging
      </h2>

      {/* ---- staging ---- */}
      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        <label className="text-sm">
          <span className="text-neutral-500">Room</span>
          <select
            value={room}
            onChange={(e) => setRoom(e.target.value)}
            className="mt-1 w-full rounded border border-neutral-300 bg-neutral-50 p-2 dark:border-neutral-700 dark:bg-neutral-950"
          >
            {rooms.map((r) => (
              <option key={r} value={r}>
                {r.replace(/_/g, " ")}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          <span className="text-neutral-500">Style</span>
          <select
            value={style}
            onChange={(e) => setStyle(e.target.value)}
            className="mt-1 w-full rounded border border-neutral-300 bg-neutral-50 p-2 dark:border-neutral-700 dark:bg-neutral-950"
          >
            {styles.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          <span className="text-neutral-500">Room photo</span>
          <input
            type="file"
            accept="image/*"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="mt-1 w-full text-xs"
          />
        </label>
      </div>

      <button
        onClick={runStaging}
        className="mt-3 rounded bg-accent px-4 py-2 text-sm font-medium text-white transition hover:opacity-90"
      >
        Stage this room
      </button>

      {staging && (
        <div className="mt-4 rounded border border-neutral-200 p-3 dark:border-neutral-800">
          {staging.rendered && staging.image ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={staging.image}
              alt={`${staging.room} staged in ${staging.style} style`}
              className="w-full rounded"
            />
          ) : (
            <p className="border-l-2 border-amber-500 pl-3 text-sm text-amber-700 dark:text-amber-400">
              {staging.status}
            </p>
          )}
          <p className="mt-3 font-mono text-[10px] uppercase tracking-wider text-neutral-500">
            Prompt
          </p>
          <p className="mt-1 text-xs text-neutral-600 dark:text-neutral-400">
            {staging.prompt}
          </p>
          <p className="mt-3 text-xs text-neutral-500">{staging.honesty_note}</p>
        </div>
      )}

      {/* ---- costing ---- */}
      <div className="mt-6 border-t border-neutral-200 pt-5 dark:border-neutral-800">
        <p className="font-mono text-[10px] uppercase tracking-wider text-neutral-500">
          What the work costs, and what comes back
        </p>

        <div className="mt-3 flex flex-wrap gap-2">
          {scopes.map((s) => (
            <button
              key={s.key}
              onClick={() => toggle(s.key)}
              title={s.note}
              className={
                chosen.includes(s.key)
                  ? "rounded-full border border-accent bg-accent px-3 py-1 text-xs text-white"
                  : "rounded-full border border-neutral-300 px-3 py-1 text-xs text-neutral-600 hover:border-accent dark:border-neutral-700 dark:text-neutral-400"
              }
            >
              {s.label}
            </button>
          ))}
        </div>

        <div className="mt-3 flex flex-wrap items-end gap-3">
          <label className="text-sm">
            <span className="text-neutral-500">Area (sq ft)</span>
            <input
              type="number"
              value={area}
              min={1}
              onChange={(e) => setArea(Number(e.target.value))}
              className="mt-1 w-28 rounded border border-neutral-300 bg-neutral-50 p-2 dark:border-neutral-700 dark:bg-neutral-950"
            />
          </label>
          <label className="text-sm">
            <span className="text-neutral-500">Grade</span>
            <select
              value={grade}
              onChange={(e) => setGrade(e.target.value)}
              className="mt-1 rounded border border-neutral-300 bg-neutral-50 p-2 dark:border-neutral-700 dark:bg-neutral-950"
            >
              <option value="budget">budget</option>
              <option value="standard">standard</option>
              <option value="premium">premium</option>
            </select>
          </label>
          <button
            onClick={estimate}
            className="rounded border border-accent px-4 py-2 text-sm text-accent transition hover:bg-accent hover:text-white dark:text-blue-300"
          >
            Estimate
          </button>
        </div>

        {error && <p className="mt-3 text-sm text-red-600">{error}</p>}

        {costing && (
          <div className="mt-4">
            <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1">
              <p className="text-2xl font-semibold">
                {costing.total_cost_display}
              </p>
              <p className="text-sm text-neutral-500">
                range {costing.range_display} · about {costing.estimated_weeks}{" "}
                {costing.estimated_weeks === 1 ? "week" : "weeks"}
              </p>
            </div>

            <table className="mt-3 w-full text-sm">
              <thead>
                <tr className="border-b border-neutral-200 text-left font-mono text-[10px] uppercase tracking-wider text-neutral-500 dark:border-neutral-800">
                  <th className="pb-1">Work</th>
                  <th className="pb-1 text-right">Cost</th>
                  <th className="pb-1 text-right">Recovers</th>
                  <th className="pb-1 text-right">Net</th>
                </tr>
              </thead>
              <tbody>
                {costing.lines.map((l) => (
                  <tr
                    key={l.scope}
                    className="border-b border-neutral-100 dark:border-neutral-800/60"
                  >
                    <td className="py-2">
                      {l.label}
                      <span className="block text-xs text-neutral-500">
                        {l.note}
                      </span>
                    </td>
                    <td className="py-2 text-right align-top">
                      {l.cost_display}
                    </td>
                    <td className="py-2 text-right align-top">
                      {l.recovery_percent}%
                    </td>
                    <td
                      className={
                        l.net_inr >= 0
                          ? "py-2 text-right align-top font-mono text-verified"
                          : "py-2 text-right align-top font-mono text-red-600"
                      }
                    >
                      {l.net_inr >= 0 ? "+" : "−"}₹
                      {Math.abs(l.net_inr).toLocaleString("en-IN")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            <p
              className={
                costing.pays_for_itself
                  ? "mt-3 border-l-2 border-verified pl-3 text-sm"
                  : "mt-3 border-l-2 border-amber-500 pl-3 text-sm text-amber-800 dark:text-amber-300"
              }
            >
              {costing.recommendation}
            </p>
            <p className="mt-2 text-xs text-neutral-500">
              {costing.disclaimer}
            </p>
          </div>
        )}
      </div>
    </section>
  );
}
