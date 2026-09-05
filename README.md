# Verify — AI Real Estate

One app: plain-language property search, floor-plan matching, a conversational
seller agent, title-document triage, and a WhatsApp lead-qualification bot —
Pins 1–5 of the master blueprint, running over a real backend.

Five surfaces, one server at <http://localhost:8000>:

| Tab | What it does |
|---|---|
| **Find a home** | Plain-language brief → ranked verified matches with reasons |
| **Match a plan** | Upload a 2D/3D floor plan → read it → search against it |
| **List with Jarvis** | Conversational seller intake → creates a pending listing |
| **Title check** | Paste an EC / deed / khata → triage verdict and flags |
| **Team** | Staff-gated CRM: leads, intent scores, booked site visits |

```
backend/
  app/config.py           runtime modes (dev vs production providers)
  app/models.py           Pin 1 — SQLAlchemy schema, pgvector or SQLite
  app/embeddings.py       pluggable embedding providers
  app/search.py           Pin 2 — hybrid SQL + vector search
  app/whatsapp_agent.py   Pin 3 — buyer qualification state machine
  app/seller_agent.py     Jarvis — seller intake state machine
  app/documents.py        Pin 4 — title & document checks
  app/vision.py           floor-plan reading (needs a vision model)
  app/llm.py              match rationale and conversational replies
  app/auth.py             staff API-key gate (secure by default)
  app/whatsapp_send.py    outbound delivery via the Graph API
  app/main.py             FastAPI application
  static/index.html       the app — zero build step
  seed.py                 10 realistic Bengaluru listings
  tests/                  48 tests
frontend/                 Next.js client (alternative to static/)
```

## Run it in 30 seconds

No Docker, no Postgres, no API key required.

```bash
cd backend
python3 -m venv ../.venv && ../.venv/bin/pip install -r requirements-dev.txt
../.venv/bin/python seed.py --reset
../.venv/bin/python -m uvicorn app.main:app --port 8000
```

Open <http://localhost:8000> for the console, or <http://localhost:8000/docs>
for the API. Run the tests with `../.venv/bin/python -m pytest tests/ -q`.

## The two modes

| | dev (default) | production |
|---|---|---|
| Storage | SQLite, cosine in Python | PostgreSQL + pgvector, HNSW index |
| Embeddings | `hash` — **lexical, not semantic** | `openai` / `voyage` |
| Rationale | rule-based, derived from real fields | Claude or GPT |
| Needs | nothing | Postgres, an API key |

Dev mode exists so the pipeline is runnable and testable without
infrastructure. It is honest about itself: `/api/v1/health` returns
`semantic_search: false` and the console shows a warning banner while the
`hash` provider is active. **The hash embedder scores word overlap — it does
not understand that "villa" and "bungalow" are related.** Do not put it in
front of buyers.

### Switching to production

```bash
docker run -d --name pgvector -p 5432:5432 \
  -e POSTGRES_PASSWORD=postgres pgvector/pgvector:pg16

../.venv/bin/pip install psycopg2-binary pgvector openai anthropic

export VECTOR_BACKEND=pgvector
export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/realestate_db
export EMBEDDING_PROVIDER=openai   OPENAI_API_KEY=sk-...
export LLM_PROVIDER=anthropic      ANTHROPIC_API_KEY=sk-ant-...

python seed.py --reset      # re-embeds with the new provider
```

**Vectors from different providers are not comparable.** After changing
`EMBEDDING_PROVIDER` you must re-embed everything — `seed.py --reset`, or
`POST /api/v1/reindex` to keep the data and rebuild only the vectors.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/health` | active providers; warns when search is not semantic |
| POST | `/api/v1/search` | Pin 2 — hybrid intent search |
| GET | `/api/v1/properties` | inventory listing |
| POST | `/api/v1/properties` | 🔒 add stock (always enters **unverified**) |
| POST | `/api/v1/properties/{id}/verify` | 🔒 advocate sign-off |
| POST | `/api/v1/reindex` | 🔒 recompute every embedding |
| POST | `/api/v1/documents/check` | Pin 4 — title triage |
| POST | `/api/v1/properties/{id}/documents` | 🔒 triage and attach to a listing |
| GET/POST | `/api/v1/whatsapp/webhook` | Pin 3 — Meta handshake + inbound, replies sent |
| POST | `/api/v1/chat` | same agent, testable without Meta |
| POST | `/api/v1/seller/chat` | Jarvis seller intake (client holds the state) |
| GET | `/api/v1/plan/status` | whether plan reading is available |
| POST | `/api/v1/plan/match` | upload a floor plan → reading + matches |
| GET | `/api/v1/leads`, `/api/v1/site-visits` | 🔒 CRM read — buyer PII |

🔒 = staff key required: `Authorization: Bearer <key>` or `X-API-Key: <key>`.

## Auth

Secure by default. With `STAFF_API_KEYS` unset the service **does not fall
open** — it generates a key at boot and prints it once:

```
WARNING: No STAFF_API_KEYS configured — generated an ephemeral staff key
         for this process only:

    staff_Eb0wXWf3svpd4C2nhwhCswR73NSATJVD
```

It changes every restart; set `STAFF_API_KEYS` for anything persistent.
`REQUIRE_AUTH=false` disables the gate deliberately — local dev only, and
`/api/v1/health` reports it as `auth_warning` when you do.

Buyer contact data sits behind this gate because lead rows carry phone numbers
and DPDP Act 2023 obligations attach from the first stored lead. Note this
authenticates the *caller as staff* — it does not record *which person* acted.
Put a real IdP in front once more than a few people hold keys.

## Floor-plan matching

Reading a drawing is the one feature with no rule-based substitute, so it
needs a vision-capable provider. With `LLM_PROVIDER=stub` the tab disables
itself and says why rather than pretending to have read the image. The prompt
forbids estimating a dimension that is not drawn, and unreadable regions come
back in an `unreadable` list that the UI shows.

## WhatsApp delivery

Inbound webhooks are signature-verified (HMAC-SHA256 against
`WHATSAPP_APP_SECRET`) and replies go back through the Graph API. With no
`WHATSAPP_TOKEN` it runs in **dry-run**: the message is logged and reported as
`delivery: dry_run` rather than silently vanishing.

Send failures are logged and swallowed so the webhook still returns 200 — a
non-200 makes Meta retry, which would re-run the turn and double-send. Watch
for HTTP 131047: outside the 24-hour service window free-form text is rejected
and you need an approved template.

## Design decisions worth knowing

**Verification gates search (FR-5).** Unverified and flagged stock never
reaches a buyer. New listings always enter unverified, whatever the caller
sends. A test asserts no unverified row leaks across six query shapes.

**Locality is a SQL constraint, not a similarity hint.** Leaving it to the
vector scorer ranked Electronic City above the Whitefield property a buyer
explicitly asked for, because the two descriptions shared generic words.
Locality, price, type and configuration are all hard filters; the vector
handles the rest. If a locality has no matching stock the search widens and
**says so** in `notice` rather than quietly showing a different neighbourhood.

**The match rationale is derived, never echoed.** The blueprint's draft
returned `f"Top match based on criteria: '{prompt}'"` — the buyer's own words
dressed up as reasoning. Every clause the stub emits is computed from the
listing row against the parsed constraints; with a real `LLM_PROVIDER` it is a
model call whose output falls back to the rule-based text if it fails to parse.

**Both agents are state machines, not free LLM loops.** Buyer qualification
and seller intake are fixed, auditable, cheap paths that cannot invent a price
or a possession date. The optional LLM layer only rephrases what the state
machine already decided. Jarvis holds its state client-side, so an abandoned
half-finished listing leaves nothing behind on the server.

**There is no route into the catalogue that skips verification.** The REST
endpoint and Jarvis both force `is_verified = False` on entry, whatever the
caller sends.

**Document checks triage; they do not clear title.** `PASSED` means "nothing
this screen can detect is wrong" — never "the title is clear". Every response
carries that disclaimer. A signed advocate opinion is what has legal weight.

## Before this goes near production

- **OCR.** `documents.extract_text()` raises `NotImplementedError` for file
  input by design; wire Textract, Vision or Tesseract. It fails loudly rather
  than passing every document as clean.
- **Rate limiting.** No throttle anywhere. `/chat`, `/search` and
  `/plan/match` are public and each does real work; plan reading costs money.
- **Per-user identity.** The API-key gate is not an audit trail — you cannot
  tell who marked a listing verified.
- **WhatsApp templates.** Only free-form text, which works inside the 24-hour
  window. Re-engagement needs approved templates.
- **Migrations.** `create_all` is fine for a pilot; use Alembic before the
  schema has data you care about.
