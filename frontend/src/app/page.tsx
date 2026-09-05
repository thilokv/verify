"use client";

import { useEffect, useState } from "react";
import { Inky } from "@/components/Inky";
import { SearchBar } from "@/components/SearchBar";
import { StagingStudio } from "@/components/StagingStudio";
import { TitleParser } from "@/components/TitleParser";
import { ValuationCard } from "@/components/ValuationCard";
import { api } from "@/lib/api";
import type { Health, Match } from "@/types";

export default function Dashboard() {
  const [selected, setSelected] = useState<Match | null>(null);
  const [health, setHealth] = useState<Health | null>(null);

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null));
  }, []);

  const warnings = health
    ? [health.warning, health.auth_warning].filter(
        (w): w is string => typeof w === "string",
      )
    : [];

  return (
    <main className="mx-auto max-w-6xl px-5 py-8">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            Verify <span className="text-accent">Real Estate</span>
          </h1>
          <p className="mt-1 max-w-xl text-sm text-neutral-600 dark:text-neutral-400">
            Describe the home you want in plain language, or upload its plan.
            Every match returns with its title already checked — khata, RERA and
            encumbrance history.
          </p>
        </div>

        {health && (
          <ul className="font-mono text-[10px] uppercase tracking-wider text-neutral-500">
            <li>
              {health.semantic_search ? "semantic" : "lexical"} search
            </li>
            <li>{health.auth_required ? "auth on" : "auth off"}</li>
            <li>whatsapp {health.whatsapp_outbound}</li>
          </ul>
        )}
      </header>

      {warnings.map((w) => (
        <p
          key={w}
          className="mt-4 border-l-2 border-amber-500 bg-amber-50 p-3 text-sm text-amber-800 dark:bg-amber-950/40 dark:text-amber-300"
        >
          {w}
        </p>
      ))}

      <div className="mt-6 grid gap-5 lg:grid-cols-2">
        <div className="space-y-5">
          <SearchBar onSelect={setSelected} />
          <TitleParser />
        </div>
        <div className="space-y-5">
          <ValuationCard propertyId={selected?.id ?? null} />
          <StagingStudio />
        </div>
      </div>

      <footer className="mt-10 border-t border-neutral-200 pt-5 text-xs leading-relaxed text-neutral-500 dark:border-neutral-800">
        Unverified stock never reaches a buyer — a new listing enters pending and
        becomes visible only after an advocate signs the title opinion. Valuation
        and renovation figures are estimates, not a valuation report or a
        quotation. Document checks triage; they are not a title opinion.
      </footer>
      <Inky />
    </main>
  );
}
