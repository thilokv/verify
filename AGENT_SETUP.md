# The agent — what it does, and what you have to do

There is now a real autonomous agent in this platform. It is a **separate
process** that walks every saved watch on a loop and decides, on its own,
whether something has happened that a person needs to know about.

Nobody has to be at a keyboard. Nothing calls Claude.

---

## 1. What it actually watches

| Watch | Fires when | Who it is for |
|---|---|---|
| `VERIFICATION_CHANGE` | A property's title status moves — **especially when it fails** | Buyers |
| `DEPOSIT_RISK` | A new letting asks more deposit than the Model Tenancy Act allows | Tenants |
| `NEW_MATCH` | Verified stock matching a saved brief appears | Buyers |
| `PRICE_DROP` | A watched property is relisted lower | Buyers |
| `LEAD_IDLE` | A lead went quiet part-way through qualification | You and your agents |

The first two are the reason this is worth running. Everything else is a saved
search that any portal could copy in a week. But **"the flat you were about to
pay a token on just failed its title check"** is a message only a platform that
actually re-checks titles can send — and it is the single most valuable
sentence in the product. A buyer who liked a listing last month has no way of
learning this on their own, and that is precisely the moment their money moves.

---

## 2. Run it

```bash
cd backend
python agent.py                  # loop, ticks every 60s
python agent.py --interval 300   # every 5 minutes
python agent.py --once           # single pass, for cron
```

It is deliberately **not** part of the API process. The API must answer a buyer
in milliseconds; the agent walks every watch and may call a model. Sharing a
process means one slow evaluation blocks a page load, and a crash in either
kills both.

**Keep it running after you log out** — pick one:

```bash
# macOS, launchd  (~/Library/LaunchAgents/com.verify.agent.plist)
launchctl load ~/Library/LaunchAgents/com.verify.agent.plist

# Linux, systemd
sudo systemctl enable --now verify-agent

# Anywhere, crontab — no daemon to supervise
*/5 * * * * cd /path/to/backend && /path/to/.venv/bin/python agent.py --once
```

Check it is alive: `GET /api/v1/agent/status` → `"running": true`. That flag is
computed from the last completed run, so it tells you the truth even if the
process died an hour ago. The octopus in the UI says **"loop not running"**
rather than pretending to watch.

---

## 3. What you have to configure

Everything below is optional except the first line — the agent runs today with
in-app notifications and no credentials at all.

### Nothing (works right now)
In-app alerts. They wait in the octopus panel. No consent needed, because they
interrupt nobody.

### WhatsApp — to reach people who are not in the app
The highest-value channel in India, and the one that needs the most care.

1. Go to **developers.facebook.com** → create a Business app → add
   **WhatsApp**.
2. You need a **Meta Business Account** with a verified business. Verification
   takes days and needs registration documents — start it early.
3. Get a **permanent** token (System User token), not the 24-hour test token.
4. From the WhatsApp → API Setup page, copy the **Phone number ID**.

```bash
WHATSAPP_TOKEN=EAAG…          # System User token, permanent
WHATSAPP_PHONE_ID=1234567890  # not the phone number — the ID
WHATSAPP_APP_SECRET=…         # App Settings → Basic. Signs inbound webhooks.
WHATSAPP_VERIFY_TOKEN=…       # any string you invent; Meta echoes it back
```

**Template messages.** Meta only allows free-form replies inside a 24-hour
window after the user messages you. Outside it you must use a
**pre-approved template**. Approval takes 1–2 days. Until you have one
approved, the agent can only reach people who have messaged you recently.
Budget for this — it is the thing that surprises people.

Cost: Meta bills per conversation. Utility conversations in India are roughly
₹0.10–0.35 each at the time of writing. Check current pricing.

### Semantic search — the biggest single quality win
Matching is currently **lexical**: it scores word overlap, not meaning. "close
to a good school" matches nothing.

```bash
EMBEDDING_PROVIDER=openai
OPENAI_API_KEY=sk-…           # platform.openai.com → API keys
```
Then **reindex** — vectors from different providers are not comparable:
```bash
curl -X POST localhost:8732/api/v1/reindex -H "Authorization: Bearer $STAFF_KEY"
```

### Better rationales
```bash
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-…    # console.anthropic.com
```

### Virtual staging — to actually render images
Without this the staging tool returns the plan and says plainly that no image
was produced. It never returns your photo pretending to be a result.
```bash
REPLICATE_API_TOKEN=r8_…      # replicate.com/account/api-tokens
```

### Two you should set before any real user touches this
```bash
STAFF_API_KEYS=…              # else a new key is generated every restart
DOCUMENT_KEY=…                # else uploaded title documents sit unencrypted
```
Generate the document key with:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

---

## 4. Permissions — read this before switching WhatsApp on

An agent that messages people is a liability unless it is provably restrained.
Under the **DPDP Act 2023** consent must be informed, purpose-limited, and as
easy to withdraw as to give.

Three rules, and **all three fail closed**:

- **No consent row, no send.** Absence of permission is refusal, never
  permission. There is a test that fails if anyone changes this.
- **Quiet hours**, 21:00–08:00 IST by default. A property alert at 03:00 costs
  you the user permanently.
- **A daily ceiling**, 6 by default. An agent that *can* message without limit
  eventually *will*, the first day a rule is written badly. The cap is your
  blast radius.

```bash
POST /api/v1/consent/grant?subject=+9198…&channel=whatsapp&purpose=…
POST /api/v1/consent/revoke?subject=+9198…
GET  /api/v1/consent/status?subject=+9198…
```

Every suppressed message stores **why** it was suppressed, so "why did this not
send" is answerable months later without re-running anything.

### STOP works

`STOP` is checked **before anything else reads an inbound message** — it does
not queue behind qualification logic and does not depend on the conversational
agent understanding it. The revocation reaches the autonomous loop on its very
next tick, where the alert becomes `SUPPRESSED: consent withdrawn`.

Recognised: `STOP`, `STOP ALL`, `UNSUBSCRIBE`, `CANCEL`, `QUIT`, `OPT OUT`,
`REMOVE ME`, and romanised Hindi — `BAND KARO`, `ROKO`, `HATAO`, `MAT BHEJO`.
Punctuation and casing are tolerated: `stop.`, `  Stop!  `, `STOP ALL`.

Three decisions worth knowing about:

- **It matches the whole message, never a substring.** "a flat near the bus
  stop" is an enquiry from someone who *wants* to hear from you. Unsubscribing
  them because their sentence contained "stop" is a silent, unrecoverable
  failure — they would simply never hear from you again and never know why.
  There are eight tests pinning exactly this.
- **It revokes only the channel it arrived on.** STOP on WhatsApp stops
  WhatsApp. In-app alerts wait to be read and interrupt nobody, so silencing
  those too would take away something they did not ask to lose. Their saved
  watches survive.
- **A confirmation is sent even though consent is now withdrawn.** It is a
  receipt for an action they just took, not a message we chose to send —
  without it they cannot tell whether STOP worked. It also tells them `START`
  brings them back, and START restores their original quiet hours and cap.

`START` from someone who never opted out is left alone and passed to the
conversational agent, because "start looking for a flat" is an ordinary opening
line and hijacking it would break the conversation.

---

## 5. The octopus

Inky sits bottom-right and is `position: fixed`, so it survives scrolling and
swiping without a single scroll listener. It shows an unread count, turns red
when something urgent is waiting, and opens the agent's inbox. From there you
can create a watch in one line. Tentacles animate, and stop entirely for anyone
whose system asks for reduced motion.

---

## 6. Verify it works

```bash
# 1. a buyer follows a listing
curl -X POST "localhost:8732/api/v1/watches?subject=+919845012345\
&kind=VERIFICATION_CHANGE&property_id=1&channel=inapp"

# 2. baseline — must NOT alert
curl -X POST localhost:8732/api/v1/agent/tick -H "Authorization: Bearer $STAFF_KEY"

# 3. the title check fails, then tick again — must alert, once
curl "localhost:8732/api/v1/notifications?subject=+919845012345"
```

Verified end to end on this machine: baseline silent → fires urgent on failure →
never repeats however many times it ticks. The daemon caught a change with
nobody watching, and shut down cleanly on SIGTERM.

---

## 7. What is not real yet

- **Email is not implemented.** The channel exists and is gated; there is no
  SMTP behind it.
- **Rate limits are per process.** Four workers means four times the written
  limit. Move the counters to Redis before scaling out.
- **The agent has no LLM in the loop.** Every evaluator is deterministic
  Python. That is a deliberate choice — a rule that fires at 04:00 unattended
  should be one you can read, not one you have to prompt. Add a model where it
  helps *phrase* an alert, not where it decides whether to send one.
