# Verify — AI Real Estate

An AI property platform for India. Buyers describe what they want in plain
language; every match comes back with its title already checked.

## Architecture

Two runtimes, one product. The split is deliberate: the verification,
document-triage and agent logic is tested Python that predates the dashboard
and should not be rewritten to change how the UI looks.

```
frontend/          Next.js 15 + TypeScript + Tailwind — the dashboard
       │  REST, /api/v1/*
       ▼
backend/           FastAPI + SQLAlchemy — services, verification, agents
```

- **Frontend** — Next.js (App Router), TypeScript, Tailwind. Presentation only.
  It holds no business rules: pricing, risk and verification verdicts are
  computed server-side so the browser cannot be the authority on them.
- **Backend** — FastAPI, SQLAlchemy 2.0, Pydantic v2. SQLite in dev, Postgres
  + pgvector in production.
- **A zero-build console** also lives at `backend/static/index.html` and is
  served by the API. It stays: it works with no toolchain and is the fastest
  way to exercise the API on a machine with no Node.

## Market: India

This is not a generic listing platform and must not drift into one.

- Money is **lakh and crore**, never thousands. ₹1.24 Cr, ₹85 L.
- **Khata** (A vs B), **RERA** registration and the **encumbrance certificate**
  are first-class concepts. B-Khata is a real valuation discount, not a label —
  most banks will not lend against it.
- Tenancy follows the **Model Tenancy Act 2021**: deposits capped at 2 months
  residential, 6 commercial; leases of 12+ months need registration.
- Citations belong in the code. `Suraj Lamp v. State of Haryana (2011)` for GPA
  sales, `Registration Act 1908 s.17` for lease registration.

## Rules

**Types.** Strict TypeScript, no `any`, no non-null `!` to silence the checker.
Python is fully annotated. Validate every input at the boundary — Zod on the
frontend, Pydantic on the backend — and never trust a body because an earlier
screen produced it.

**Layers.** Routes parse and authorise; services decide; models persist. A route
holding business logic is a bug. Services take a session and return plain dicts,
so they are testable without HTTP.

**Verification gate.** Unverified stock never reaches a buyer. Every catalogue
query filters `is_verified` and `listing_type`. There is no route into buyer
search that bypasses it — adding one is a defect, not a feature.

**Say what is real.** A number the system estimated is labelled an estimate. A
check that is a keyword scan is not called a clearance. Document triage narrows
what an advocate looks at; only the advocate's opinion clears a title. Where a
value is a placeholder, the response says so in a field the UI renders.

**Money.** Amounts are computed server-side from stored rows and never asserted
by a caller. A settlement reports what the ledger actually moved.

**Errors.** Fail with the specific reason and what to do next. Never swallow an
exception to keep a screen looking clean.

**Tests.** Every bug fixed gets a test that fails against the old code. Verify
that by reverting the fix and watching it fail — a regression test that has
never failed is decoration.

## Commands

```bash
# backend  (from backend/, with ../.venv active)
python -m uvicorn app.main:app --reload --port 8732
python seed.py --reset          # 10 pilot listings
python -m pytest tests/ -q      # full suite

# frontend (from frontend/)
npm run dev                     # :3000, proxies /api to :8732
npm run build                   # must pass clean
npm run lint
npm run typecheck               # tsc --noEmit, no errors
```

## Layout

```
backend/app/
  config.py          settings, provider selection
  models.py          SQLAlchemy models
  search.py          hybrid SQL + vector matching
  valuation.py       comps, growth, sunlight        [feature 2]
  renovation.py      staging + repair estimates      [feature 1]
  documents.py       title/EC/khata parsing          [feature 4]
  evidence.py        cross-document fraud checks     [feature 4]
  verification.py    seller fraud screen
  seller_agent.py    Jarvis — sale intake            [feature 5]
  rental_agent.py    Ultron — rental intake          [feature 5]
  whatsapp_agent.py  buyer qualification             [feature 5]
  buyer_safety.py    money rules, pressure scanner
  tenant_safety.py   deposit rules, tenant rights
  playbook.py        build phases measured from data
  revenue.py         Premier Agent lead fees

frontend/src/
  app/               App Router pages
  components/        dashboard widgets
  lib/               API client
  types/             Zod schemas shared with the API shape
```
