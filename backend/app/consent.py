"""Permission to contact someone. The agent asks this before every message.

This module exists because an autonomous agent that messages people is a
liability unless it is provably restrained. Under the DPDP Act 2023 consent
must be informed, purpose-limited, and as easy to withdraw as to give.

Three rules, and all three fail closed:

  No row, no send      Absence of consent is refusal, never permission.
  Quiet hours          Local IST. A property alert at 03:00 costs you the user.
  A daily ceiling      An agent that can message without limit will, the day
                       a rule is written badly. The cap is the blast radius.

`allow()` returns a reason when it says no, and that reason is stored on the
suppressed notification — so "why did this not send" is answerable months
later without re-running anything.
"""

import datetime as dt
from typing import Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from .models import Consent, Notification, utcnow

# Bengaluru. Quiet hours are meaningless in UTC.
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

CHANNELS = ("inapp", "whatsapp", "email")

# In-app is a pull channel: the user opens the app and looks. It interrupts
# nobody, so it needs no consent row, no quiet hours and no cap. Everything
# that pushes into someone's day does.
PUSH_CHANNELS = ("whatsapp", "email")


def grant(session: Session, subject: str, channel: str, purpose: str,
          quiet_from_hour: int = 21, quiet_to_hour: int = 8,
          max_per_day: int = 6) -> Consent:
    """Record consent to contact `subject` on `channel` for `purpose`."""
    if channel not in CHANNELS:
        raise ValueError(f"channel must be one of {CHANNELS}")
    if not subject or not subject.strip():
        raise ValueError("A subject (phone or email) is required.")
    if not purpose or not purpose.strip():
        raise ValueError(
            "A purpose is required — consent that does not say what it is for "
            "is not consent.")

    row = (session.query(Consent)
           .filter_by(subject=subject.strip(), channel=channel).first())
    if row is None:
        row = Consent(subject=subject.strip(), channel=channel)
        session.add(row)

    row.purpose = purpose
    row.granted = True
    row.granted_at = utcnow()
    row.revoked_at = None
    row.quiet_from_hour = quiet_from_hour
    row.quiet_to_hour = quiet_to_hour
    row.max_per_day = max_per_day
    session.commit()
    return row


def revoke(session: Session, subject: str, channel: Optional[str] = None) -> int:
    """Withdraw consent. Takes effect on the next evaluation, not eventually.

    The row is kept and stamped rather than deleted: we must be able to show
    when someone opted out, and a deleted row proves nothing.
    """
    q = session.query(Consent).filter_by(subject=(subject or "").strip())
    if channel:
        q = q.filter_by(channel=channel)
    rows = q.all()
    for row in rows:
        row.granted = False
        row.revoked_at = utcnow()
    session.commit()
    return len(rows)


def sent_today(session: Session, subject: str, channel: str) -> int:
    """Messages already pushed to this subject today, in IST."""
    start_ist = dt.datetime.now(IST).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return (session.query(func.count(Notification.id))
            .filter(Notification.subject == subject,
                    Notification.channel == channel,
                    Notification.status == "SENT",
                    Notification.created_at >= start_ist.astimezone(dt.timezone.utc))
            .scalar() or 0)


def in_quiet_hours(row: Consent, now: Optional[dt.datetime] = None) -> bool:
    """Whether local time falls inside this subject's quiet window."""
    local = (now or dt.datetime.now(dt.timezone.utc)).astimezone(IST)
    start, end = row.quiet_from_hour, row.quiet_to_hour
    if start == end:
        return False
    if start < end:                      # e.g. 01:00 -> 06:00, same day
        return start <= local.hour < end
    return local.hour >= start or local.hour < end   # e.g. 21:00 -> 08:00


def allow(session: Session, subject: str, channel: str,
          now: Optional[dt.datetime] = None) -> Tuple[bool, Optional[str]]:
    """May the agent send to this subject on this channel right now?

    Returns (allowed, reason_if_not). The reason is written onto the
    suppressed notification so the decision stays auditable.
    """
    if channel not in CHANNELS:
        return False, f"unknown channel {channel!r}"

    # In-app waits to be read; it never interrupts anyone.
    if channel not in PUSH_CHANNELS:
        return True, None

    row = (session.query(Consent)
           .filter_by(subject=(subject or "").strip(), channel=channel).first())
    if row is None:
        return False, "no consent on file for this channel"
    if not row.granted:
        return False, "consent withdrawn"
    if in_quiet_hours(row, now):
        return False, (f"quiet hours ({row.quiet_from_hour}:00–"
                       f"{row.quiet_to_hour}:00 IST)")
    used = sent_today(session, subject, channel)
    if used >= (row.max_per_day or 0):
        return False, f"daily cap reached ({used}/{row.max_per_day})"
    return True, None


# Opt-out keywords. TRAI and Meta both expect STOP to work; the rest are what
# people actually send, including romanised Hindi, which is the majority of
# WhatsApp traffic in this market.
OPT_OUT_WORDS = frozenset({
    "stop", "stopall", "stop all", "unsubscribe", "unsub", "cancel", "end",
    "quit", "optout", "opt out", "revoke", "remove me", "delete me",
    "band", "band karo", "band kro", "roko", "rokiye", "hatao", "mat bhejo",
    "no more", "leave me alone",
})

OPT_IN_WORDS = frozenset({
    "start", "resume", "subscribe", "unstop", "opt in", "optin", "chalu",
    "chalu karo", "shuru", "shuru karo",
})

# Punctuation people put around a keyword: "STOP.", "stop!", "-stop-".
_EDGE = " \t\r\n.!,;:'\"()[]{}<>-–—*_/\\"


def _normalise(text: str) -> str:
    """Reduce a message to a bare comparable phrase."""
    cleaned = (text or "").strip().strip(_EDGE).lower()
    return " ".join(cleaned.split())          # collapse internal whitespace


def keyword_intent(text: str) -> Optional[str]:
    """Classify a message as an opt-out, an opt-in, or neither.

    Matching is against the WHOLE message, never a substring. "a flat near the
    bus stop" is a property enquiry from someone who wants to hear from you;
    unsubscribing them because their sentence contained "stop" would be a
    silent, unrecoverable failure — they would simply never hear from you
    again and would never know why.
    """
    phrase = _normalise(text)
    if not phrase:
        return None
    if phrase in OPT_OUT_WORDS:
        return "OPT_OUT"
    if phrase in OPT_IN_WORDS:
        return "OPT_IN"
    return None


def handle_keyword(session: Session, subject: str, text: str,
                   channel: str = "whatsapp") -> Optional[dict]:
    """Act on STOP/START before anything else reads the message.

    Returns None when the message is ordinary traffic, so the caller can pass
    it on to the conversational agent untouched.
    """
    intent = keyword_intent(text)
    if intent is None:
        return None

    if intent == "OPT_OUT":
        # Only the channel they are speaking on. In-app alerts wait to be read
        # and interrupt nobody, so silencing those too would take away
        # something they did not ask to lose.
        revoked = revoke(session, subject, channel)
        return {
            "intent": "OPT_OUT",
            "channel": channel,
            "revoked": revoked,
            # Sent even though consent is now withdrawn: this is a receipt for
            # an action they just took, not a message we chose to send. Without
            # it they cannot tell whether STOP worked.
            "reply": ("You're unsubscribed. We won't message you on WhatsApp "
                      "again.\n\nAnything you asked us to watch is still saved "
                      "and waiting in the app. Send START to turn WhatsApp "
                      "messages back on."),
        }

    row = (session.query(Consent)
           .filter_by(subject=(subject or "").strip(), channel=channel).first())
    if row is None or row.granted:
        # They never opted out, so START is not an opt-in — it is probably the
        # start of a sentence. Let the conversational agent have it.
        return None

    grant(session, subject, channel, row.purpose or "Alerts you asked for",
          row.quiet_from_hour, row.quiet_to_hour, row.max_per_day)
    return {
        "intent": "OPT_IN",
        "channel": channel,
        "reply": ("Welcome back — WhatsApp alerts are on again. Send STOP at "
                  "any time to turn them off."),
    }


def status(session: Session, subject: str) -> dict:
    """What this person has agreed to, for showing back to them."""
    rows = (session.query(Consent)
            .filter_by(subject=(subject or "").strip()).all())
    return {
        "subject": subject,
        "channels": [
            {
                "channel": r.channel,
                "granted": bool(r.granted),
                "purpose": r.purpose,
                "quiet_hours": f"{r.quiet_from_hour}:00–{r.quiet_to_hour}:00 IST",
                "max_per_day": r.max_per_day,
                "sent_today": sent_today(session, subject, r.channel),
                "revoked_at": r.revoked_at.isoformat() if r.revoked_at else None,
            }
            for r in rows
        ],
        "note": ("In-app notifications need no consent — they wait in the app "
                 "rather than interrupting you. WhatsApp and email always do."),
    }
