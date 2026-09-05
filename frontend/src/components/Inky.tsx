"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { AgentStatus, Notification } from "@/types";

/**
 * Inky — the agent's presence in the dashboard.
 *
 * `position: fixed` is the whole trick: it survives scrolling and swiping
 * without a single scroll listener, so there is nothing to run on every frame
 * and nothing to fall out of sync on a fast flick.
 */
export function Inky() {
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<AgentStatus | null>(null);
  const [notes, setNotes] = useState<Notification[]>([]);
  const [brief, setBrief] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [subject, setSubject] = useState<string>("");

  // A stable per-browser identity, so watches survive a reload. Not an
  // account — the agent needs something to address, not someone to bill.
  useEffect(() => {
    let id: string | null = null;
    try {
      id = localStorage.getItem("verify_subject");
      if (!id) {
        id = `+9198${Math.floor(10_000_000 + Math.random() * 89_999_999)}`;
        localStorage.setItem("verify_subject", id);
      }
    } catch {
      // Private browsing: fall back to a per-session identity.
      id = `+9198${Math.floor(10_000_000 + Math.random() * 89_999_999)}`;
    }
    setSubject(id);
  }, []);

  const refresh = useCallback(async () => {
    if (!subject) return;
    try {
      const [s, n] = await Promise.all([
        api.agentStatus(),
        api.notifications(subject),
      ]);
      setStatus(s);
      setNotes(n.notifications);
    } catch {
      // The agent being unreachable must not break the page it sits on.
      setStatus(null);
    }
  }, [subject]);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 30_000);
    return () => clearInterval(timer);
  }, [refresh]);

  async function watchThis(): Promise<void> {
    if (!brief.trim() || !subject) return;
    setBusy(true);
    setError(null);
    try {
      await api.createWatch(subject, "NEW_MATCH", { brief: brief.trim() });
      setBrief("");
      await refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not save that watch.");
    } finally {
      setBusy(false);
    }
  }

  const unread = notes.filter((n) => !n.read_at).length;
  const urgent = notes.some((n) => !n.read_at && n.severity === "urgent");
  const running = status?.running === true;

  return (
    <>
      <button
        aria-label={
          unread > 0 ? `Your agent — ${unread} unread` : "Your agent"
        }
        aria-expanded={open}
        onClick={() => {
          setOpen((v) => !v);
          void refresh();
        }}
        className="fixed bottom-6 right-6 z-50 grid h-14 w-14 place-items-center rounded-full border shadow-lg transition hover:scale-105 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2"
        style={{
          background: urgent ? "#A6362B" : "#2C3E72",
          borderColor: urgent ? "#A6362B" : "#2C3E72",
          color: "#fff",
        }}
      >
        <Octopus />
        {unread > 0 && (
          <span
            className="absolute -right-1 -top-1 grid h-5 min-w-5 place-items-center rounded-full px-1 text-[11px] font-semibold text-white"
            style={{ background: urgent ? "#7d251c" : "#1F6B4A" }}
          >
            {unread}
          </span>
        )}
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="Your agent"
          className="fixed bottom-24 right-6 z-50 flex max-h-[70vh] w-[min(23rem,calc(100vw-3rem))] flex-col overflow-hidden rounded-lg border border-neutral-200 bg-white shadow-2xl dark:border-neutral-700 dark:bg-neutral-900"
        >
          <header className="flex items-center justify-between border-b border-neutral-200 px-4 py-3 dark:border-neutral-800">
            <div>
              <p className="font-medium">Your agent</p>
              <p className="font-mono text-[10px] uppercase tracking-wider text-neutral-500">
                {running
                  ? `watching · ${status?.active_watches ?? 0} watches`
                  : "loop not running"}
              </p>
            </div>
            <button
              onClick={() => setOpen(false)}
              className="rounded px-2 py-1 text-sm text-neutral-500 hover:text-neutral-900 dark:hover:text-neutral-100"
            >
              Close
            </button>
          </header>

          {!running && (
            <p className="border-l-2 border-amber-500 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:bg-amber-950/40 dark:text-amber-300">
              <b className="block">The agent process is not up</b>
              Watches are saved but nothing is being checked. Start it with{" "}
              <code className="font-mono text-xs">python agent.py</code> in the
              backend directory.
            </p>
          )}

          <div className="flex-1 overflow-y-auto px-4 py-3">
            {notes.length === 0 ? (
              <p className="text-sm text-neutral-500">
                Nothing yet. Ask me to watch something and I&apos;ll keep
                looking after you close the tab.
              </p>
            ) : (
              <ul className="space-y-3">
                {notes.map((n) => (
                  <li
                    key={n.id}
                    className={
                      n.severity === "urgent"
                        ? "border-l-2 border-red-500 pl-3"
                        : "border-l-2 border-neutral-300 pl-3 dark:border-neutral-700"
                    }
                  >
                    <p className="text-sm font-medium">{n.title}</p>
                    <p className="mt-0.5 text-sm text-neutral-600 dark:text-neutral-400">
                      {n.body}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <footer className="border-t border-neutral-200 px-4 py-3 dark:border-neutral-800">
            <label
              htmlFor="inky-brief"
              className="font-mono text-[10px] uppercase tracking-wider text-neutral-500"
            >
              Ask me to watch
            </label>
            <input
              id="inky-brief"
              value={brief}
              onChange={(e) => setBrief(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void watchThis();
              }}
              placeholder="3BHK in Whitefield under 1.3 crore"
              className="mt-1 w-full rounded border border-neutral-300 bg-neutral-50 p-2 text-sm outline-none focus:border-accent dark:border-neutral-700 dark:bg-neutral-950"
            />
            <button
              onClick={() => void watchThis()}
              disabled={busy || !brief.trim()}
              className="mt-2 w-full rounded bg-accent px-3 py-2 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50"
            >
              {busy ? "Saving…" : "Tell me when one appears"}
            </button>
            {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
            <p className="mt-2 text-xs leading-relaxed text-neutral-500">
              Alerts wait here in the app. To get them on WhatsApp you have to
              grant permission, and you can withdraw it at any time.
            </p>
          </footer>
        </div>
      )}
    </>
  );
}

function Octopus() {
  return (
    <svg
      viewBox="0 0 32 32"
      className="h-7 w-7"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      aria-hidden="true"
    >
      {/* head */}
      <path
        d="M9 14a7 7 0 0 1 14 0v3H9z"
        fill="currentColor"
        stroke="none"
        opacity={0.95}
      />
      {/* eyes, knocked out of the head */}
      <circle cx="13" cy="13" r="1.5" fill="#2C3E72" stroke="none" />
      <circle cx="19" cy="13" r="1.5" fill="#2C3E72" stroke="none" />
      {/* tentacles */}
      <g className="inky-tentacles">
        <path d="M10 17c-1 3 .5 5-1.5 7" />
        <path d="M13.5 17c-.6 3.5.4 5.5-.6 7.5" />
        <path d="M18.5 17c.6 3.5-.4 5.5.6 7.5" />
        <path d="M22 17c1 3-.5 5 1.5 7" />
      </g>
      <style>{`
        .inky-tentacles path { transform-origin: top center; animation: inky-sway 2.6s ease-in-out infinite; }
        .inky-tentacles path:nth-child(2) { animation-delay: .2s }
        .inky-tentacles path:nth-child(3) { animation-delay: .4s }
        .inky-tentacles path:nth-child(4) { animation-delay: .6s }
        @keyframes inky-sway { 0%,100% { transform: rotate(-4deg) } 50% { transform: rotate(4deg) } }
        @media (prefers-reduced-motion: reduce) {
          .inky-tentacles path { animation: none }
        }
      `}</style>
    </svg>
  );
}
