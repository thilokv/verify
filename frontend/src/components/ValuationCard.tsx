"use client";

import { useEffect, useState } from "react";
import { api, ApiError, rupees } from "@/lib/api";
import type { Valuation } from "@/types";

const CONFIDENCE_COPY: Record<Valuation["confidence"], string> = {
  high: "Three or more comparables in the same locality.",
  medium: "One or two comparables in the same locality.",
  low: "No comparable in this locality — priced off the wider city.",
  none: "Nothing comparable to price against.",
};

const CONFIDENCE_STYLE: Record<Valuation["confidence"], string> = {
  high: "text-verified border-verified",
  medium: "text-amber-600 border-amber-500",
  low: "text-amber-700 border-amber-600",
  none: "text-neutral-500 border-neutral-400",
};

export function ValuationCard({ propertyId }: { propertyId: number | null }) {
  const [data, setData] = useState<Valuation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (propertyId === null) {
      setData(null);
      return;
    }
    let cancelled = false;
    setBusy(true);
    setError(null);
    api
      .valuation(propertyId)
      .then((v) => {
        if (!cancelled) setData(v);
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          setError(e instanceof ApiError ? e.message : "Valuation failed.");
          setData(null);
        }
      })
      .finally(() => {
        if (!cancelled) setBusy(false);
      });
    return () => {
      cancelled = true;
    };
  }, [propertyId]);

  if (propertyId === null) {
    return (
      <section className="rounded-lg border border-dashed border-neutral-300 p-5 text-sm text-neutral-500 dark:border-neutral-700">
        Pick a property from the search results to value it.
      </section>
    );
  }

  if (busy) {
    return (
      <section className="rounded-lg border border-neutral-200 p-5 text-sm text-neutral-500 dark:border-neutral-800">
        Pricing against comparables…
      </section>
    );
  }

  if (error) {
    return (
      <section className="rounded-lg border border-red-300 p-5 text-sm text-red-600 dark:border-red-900">
        {error}
      </section>
    );
  }

  if (!data) return null;

  const { sunlight } = data;

  return (
    <section className="rounded-lg border border-neutral-200 bg-white p-5 dark:border-neutral-800 dark:bg-neutral-900">
      <h2 className="font-mono text-[11px] uppercase tracking-widest text-neutral-500">
        Valuation · estimate
      </h2>
      <p className="mt-2 font-medium">{data.title}</p>

      <div className="mt-4 flex flex-wrap items-end gap-x-8 gap-y-3">
        <div>
          <p className="font-mono text-[10px] uppercase tracking-wider text-neutral-500">
            Our estimate
          </p>
          <p className="text-3xl font-semibold">
            {data.estimate_display ?? "—"}
          </p>
        </div>
        <div>
          <p className="font-mono text-[10px] uppercase tracking-wider text-neutral-500">
            Listed
          </p>
          <p className="text-xl">{data.listed_display ?? rupees(data.listed_price_inr)}</p>
        </div>
        {data.verdict && (
          <p className="text-sm text-neutral-600 dark:text-neutral-400">
            {data.verdict}
            {data.gap_percent !== null && data.gap_percent !== undefined
              ? ` (${data.gap_percent > 0 ? "+" : ""}${data.gap_percent}%)`
              : ""}
          </p>
        )}
      </div>

      <p
        className={`mt-3 inline-block border-l-2 pl-2 font-mono text-[11px] uppercase tracking-wider ${CONFIDENCE_STYLE[data.confidence]}`}
      >
        {data.confidence} confidence — {CONFIDENCE_COPY[data.confidence]}
      </p>

      {data.adjustments.length > 0 && (
        <div className="mt-5">
          <p className="font-mono text-[10px] uppercase tracking-wider text-neutral-500">
            How we got there
          </p>
          {data.rate_per_sqft !== undefined && (
            <p className="mt-2 text-sm">
              Base: ₹{data.rate_per_sqft.toLocaleString("en-IN")}/sq ft ·{" "}
              {rupees(data.base_inr ?? null)}
            </p>
          )}
          <ul className="mt-2 divide-y divide-neutral-200 dark:divide-neutral-800">
            {data.adjustments.map((a) => (
              <li key={a.factor} className="py-2">
                <div className="flex items-baseline justify-between gap-3">
                  <span className="text-sm">{a.factor}</span>
                  <span
                    className={
                      a.percent < 0
                        ? "font-mono text-sm text-red-600"
                        : "font-mono text-sm text-verified"
                    }
                  >
                    {a.percent > 0 ? "+" : ""}
                    {a.percent}%
                  </span>
                </div>
                <p className="mt-0.5 text-xs text-neutral-500">{a.why}</p>
              </li>
            ))}
          </ul>
        </div>
      )}

      {data.projected_3yr_growth_percent !== undefined &&
        data.projected_3yr_growth_percent > 0 && (
          <p className="mt-4 text-sm">
            <span className="font-medium">
              {data.projected_3yr_growth_percent}%
            </span>{" "}
            of the estimate is committed infrastructure already priced in.
          </p>
        )}

      <div className="mt-5 rounded border border-neutral-200 p-3 dark:border-neutral-800">
        <p className="font-mono text-[10px] uppercase tracking-wider text-neutral-500">
          Sunlight · {sunlight.facing}
        </p>
        <p className="mt-1 text-sm">
          <span className="font-medium">
            {sunlight.annual_direct_sun_hours.toLocaleString("en-IN")} hours
          </span>{" "}
          of direct sun a year · peak {sunlight.peak_exposure}
        </p>
        <p className="mt-1 text-sm text-neutral-600 dark:text-neutral-400">
          {sunlight.note}
        </p>
        <p className="mt-2 font-mono text-[10px] leading-relaxed text-neutral-500">
          {sunlight.method}
        </p>
      </div>

      <p className="mt-4 text-xs leading-relaxed text-neutral-500">
        {data.basis}
      </p>
    </section>
  );
}
