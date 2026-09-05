# Frontend (Pin 5)

Next.js 15 App Router client. Not installed in this repo — run:

    npm install
    npm run dev          # http://localhost:3000

Requires the API on :8000 (`next.config.mjs` proxies /api/* there; override
with API_URL). For a zero-build alternative the FastAPI app serves an
equivalent console at http://localhost:8000 — same endpoints, no npm.
