# Running Verify from a terminal

## 0. Get it somewhere permanent — do this first

The project currently lives inside a Claude scratch workspace that is
**deleted when that session ends**. Copy it out before anything else:

```bash
mkdir -p ~/code
cp -R "/Users/v.thilokchowdary/Library/Application Support/Claude/scratch-workspaces/d0dd653f-61d5-4707-80a2-706d9b8edac8/dcd09647-8c2d-413c-8e65-61bbdbb766cb/scratch-2026-09-04-6100d4/ai-realestate-platform" ~/code/verify
cd ~/code/verify
```

That copies 500 MB because it includes `.venv` and `node_modules`. To copy only
the 62 source files and rebuild the rest (below), add
`--exclude` — or just use `git`:

```bash
cd ~/code/verify && git init && git add -A && git commit -m "Verify — initial import"
```

`.gitignore` already excludes `.venv/`, `node_modules/`, `.next/`,
`realestate.db` and `.env`.

---

## 1. Three processes

Each wants its own terminal tab. Nothing here daemonises itself.

### The API and the console — port 8732

```bash
cd ~/code/verify/backend
source ../.venv/bin/activate
STAFF_API_KEYS=devkey123 python -m uvicorn app.main:app --reload --port 8732
```

→ **http://localhost:8732** — all 13 tabs, Inky bottom-right.
`--reload` restarts on save. Drop `STAFF_API_KEYS` and a fresh random key is
generated at every boot, which locks you out of Team, Revenue and Playbook
between restarts.

### The Next.js dashboard — port 3100

```bash
cd ~/code/verify/frontend
npm run dev
```

→ **http://localhost:3100**. It proxies `/api/*` to 8732, so the backend must
be up first. Port 3100 rather than 3000 because you have a `python -m
http.server` that has been holding 3000 for weeks.

### The agent

```bash
cd ~/code/verify/backend
source ../.venv/bin/activate
python agent.py --interval 60      # loop
python agent.py --once             # single pass, for cron
```

Deliberately not part of the API: the API must answer a buyer in
milliseconds, and the agent walks every watch. Check it with
`curl -s localhost:8732/api/v1/agent/status | python3 -m json.tool`.

---

## 2. Rebuilding from source

If you copied without `.venv` / `node_modules`:

```bash
cd ~/code/verify
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements-dev.txt
cd frontend && npm install
```

Node 24 and Python 3.9 are what this was built and tested against.

**If `npm install` fails with `EACCES … _cacache`** — your global npm cache has
a permission problem. Use a local cache rather than `--force`, which npm itself
calls reckless:

```bash
npm_config_cache=/tmp/npm-cache npm install
```

---

## 3. Daily commands

```bash
# tests — 266 of them, about a second
cd backend && python -m pytest tests/ -q

# one test, verbose
python -m pytest tests/test_platform.py -q -k "b_khata" -v

# reset the catalogue to the 10 pilot listings
python seed.py --reset

# typecheck and production build
cd frontend && npm run typecheck && npm run build
```

**Ports already in use?**

```bash
lsof -iTCP -sTCP:LISTEN -P -n | grep -E ':8732|:3100'
pkill -f "uvicorn app.main:app"
pkill -f "next dev"
pkill -f "agent.py"
```

---

## 4. Poking the API directly

```bash
export B=http://localhost:8732/api/v1
export H="Authorization: Bearer devkey123"

# what a property really costs, and whether a bank will lend
curl -s "$B/properties/1/affordability?monthly_income_inr=200000" | python3 -m json.tool

# the B-Khata refusal
curl -s "$B/properties/6/affordability?monthly_income_inr=200000" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["loan"]["headline"])'

# compare — two to four ids
curl -s "$B/compare?ids=1&ids=5&ids=6&monthly_income_inr=200000" \
  | python3 -c 'import sys,json;[print(f["title"]) for f in json.load(sys.stdin)["findings"]]'

# the purchase sequence
curl -s "$B/conveyance?property_id=1" | python3 -m json.tool

# search
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"user_prompt":"3BHK in Whitefield under 1.3 crore","limit":5}' $B/search \
  | python3 -m json.tool

# talk to the buyer agent without Meta in the loop
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"phone":"+919845012345","text":"3BHK in Whitefield under 1.3 crore"}' $B/chat

# STOP works here too
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"phone":"+919845012345","text":"STOP"}' $B/chat

# staff-only
curl -s -H "$H" $B/leads | python3 -m json.tool
curl -s -X POST -H "$H" $B/agent/tick | python3 -m json.tool
```

Interactive API docs: **http://localhost:8732/docs**

---

## 5. Before anyone real uses it

```bash
cd ~/code/verify && cp .env.example .env
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set in `.env`:

- `STAFF_API_KEYS` — else your staff key changes every restart.
- `DOCUMENT_KEY` — else uploaded title documents sit on disk unencrypted.
- `EMBEDDING_PROVIDER=openai` + `OPENAI_API_KEY` — matching is currently
  **lexical**: it scores word overlap, not meaning. Then
  `curl -X POST -H "$H" $B/reindex`, because vectors from different providers
  are not comparable.

`AGENT_SETUP.md` covers WhatsApp (Meta business verification takes days —
start early) and the permissions model.

---

## 6. Gotchas that have already bitten

- **Run backend commands from `backend/`.** `SessionLocal` resolves SQLite
  relative to the working directory; from the repo root you silently get a
  different, empty database.
- **Don't `npm run build` while `npm run dev` is running.** They share `.next`
  and the dev server serves a blank page afterwards. `rm -rf .next` and restart.
- **Four consent tests pin a fixed 11:00 IST moment.** They assert consent and
  cap logic, not the clock — without the pin they pass by day and fail after
  21:00, when quiet hours short-circuits the check.
