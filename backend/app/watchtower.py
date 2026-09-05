"""The autonomous agent.

It runs on a loop with nobody watching it, so it is built to be boring: a
fixed set of evaluators, each a pure read over the database returning events,
and one dispatcher that decides whether an event may become a message.

    tick() ── for each ACTIVE watch ── evaluate() ── events
                                            │
                                    consent.allow()
                                            │
                              SENT / SUPPRESSED(reason)

Design decisions that matter when this runs unattended for months:

  Deduplication is structural. Every event carries a fingerprint and the
  column is UNIQUE, so a duplicate is refused by the database rather than by
  logic somebody might later edit. Ticking twice a second changes nothing.

  Evaluators never write. They read state, compare it to the watch's snapshot
  and return events. Only the dispatcher writes. A bug in an evaluator can
  therefore spam, but cannot corrupt.

  A failing watch cannot stop the loop. One bad criteria dict must not stop
  every other person's alerts, so failures are caught per watch and recorded.

The two watch kinds worth having are VERIFICATION_CHANGE and DEPOSIT_RISK.
Everything else here is a saved search; those two only work because this
platform actually checks titles and knows the statutory deposit cap.
"""

import datetime as dt
import traceback
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from . import consent as consent_mod
from . import whatsapp_send as wa_send
from .models import (AgentRun, Lead, Notification, Property, RentalListing,
                     Watch, as_utc, utcnow)

KINDS = ("NEW_MATCH", "PRICE_DROP", "VERIFICATION_CHANGE", "DEPOSIT_RISK",
         "LEAD_IDLE")

# Model Tenancy Act 2021 — mirrors rental_agent, restated here so the agent
# does not depend on the intake module's import graph.
DEPOSIT_CAP_MONTHS = 2
DEPOSIT_CAP_MONTHS_COMMERCIAL = 6


def _rupees(v: Optional[float]) -> str:
    if not v:
        return "—"
    lakh = v / 1e5
    if lakh >= 100:
        return f"₹{lakh / 100:.2f} Cr".replace(".00 Cr", " Cr")
    if lakh >= 1:
        return f"₹{lakh:.0f} L"
    return "₹" + f"{int(v):,}"


# --------------------------------------------------------------- evaluators
# Each returns (events, new_seen_state). None of them write to the session.

def _eval_new_match(db: Session, watch: Watch) -> tuple:
    """Verified stock matching a saved brief that the watcher has not seen."""
    from .search import search

    brief = (watch.criteria or {}).get("brief") or ""
    if not brief:
        return [], watch.seen_state or {}

    found = search(db, prompt=brief, limit=10)
    seen = set((watch.seen_state or {}).get("seen_ids") or [])

    events = []
    for m in found["matches"]:
        if m["id"] in seen:
            continue
        events.append({
            "fingerprint": f"{watch.id}:NEW_MATCH:{m['id']}",
            "title": "New verified match",
            "body": (f"{m['title']} — {_rupees(m.get('price_inr'))} in "
                     f"{m.get('location_name')}. Title verified. "
                     f"{m.get('why') or ''}").strip(),
            "severity": "info",
            "property_id": m["id"],
        })

    all_ids = sorted(seen | {m["id"] for m in found["matches"]})
    return events, {"seen_ids": all_ids}


def _eval_price_drop(db: Session, watch: Watch) -> tuple:
    """A watched property listed lower than when it was last looked at."""
    pid = (watch.criteria or {}).get("property_id")
    if not pid:
        return [], watch.seen_state or {}

    prop = db.get(Property, int(pid))
    if prop is None or prop.price_inr is None:
        return [], watch.seen_state or {}

    last = (watch.seen_state or {}).get("last_price")
    state = {"last_price": prop.price_inr}
    if last is None or prop.price_inr >= last:
        return [], state

    drop = last - prop.price_inr
    pct = drop / last * 100
    floor = (watch.criteria or {}).get("min_drop_percent", 0)
    if pct < floor:
        return [], state

    return [{
        # The price is in the fingerprint: a further drop is a new event, the
        # same drop seen again is not.
        "fingerprint": f"{watch.id}:PRICE_DROP:{prop.id}:{int(prop.price_inr)}",
        "title": f"Price dropped {pct:.1f}%",
        "body": (f"{prop.title} is now {_rupees(prop.price_inr)}, down "
                 f"{_rupees(drop)} from {_rupees(last)}."),
        "severity": "info",
        "property_id": prop.id,
    }], state


def _eval_verification_change(db: Session, watch: Watch) -> tuple:
    """The title status of a property someone is interested in moved.

    This is the alert that justifies the whole agent. A buyer who liked a
    listing last month has no way of learning that its title check has since
    failed — and that is exactly the moment they are about to pay a token.
    """
    pid = (watch.criteria or {}).get("property_id")
    if not pid:
        return [], watch.seen_state or {}

    prop = db.get(Property, int(pid))
    if prop is None:
        return [], watch.seen_state or {}

    now_state = {"is_verified": bool(prop.is_verified),
                 "risk_status": prop.risk_status}
    prior = watch.seen_state or {}
    if not prior:
        return [], now_state          # first sight is a baseline, not an event
    if prior == now_state:
        return [], now_state

    was_verified = bool(prior.get("is_verified"))
    became_verified = bool(prop.is_verified) and not was_verified
    lost_verified = was_verified and not prop.is_verified
    flagged = (prop.risk_status == "FLAGGED"
               and prior.get("risk_status") != "FLAGGED")

    if flagged or lost_verified:
        title = "Title check failed on a property you follow"
        body = (f"{prop.title} is no longer clear: status is "
                f"{prop.risk_status or 'unverified'}. Do not pay a token or "
                f"advance on this until an advocate has explained why.")
        severity = "urgent"
    elif became_verified:
        title = "Title opinion signed"
        body = (f"{prop.title} has passed verification and is now visible to "
                f"buyers. {prop.verification_note or ''}").strip()
        severity = "info"
    else:
        title = "Verification status changed"
        body = (f"{prop.title}: {prior.get('risk_status') or 'unknown'} → "
                f"{prop.risk_status or 'unknown'}.")
        severity = "warning"

    return [{
        "fingerprint": (f"{watch.id}:VERIFICATION_CHANGE:{prop.id}:"
                        f"{prop.risk_status}:{int(bool(prop.is_verified))}"),
        "title": title,
        "body": body,
        "severity": severity,
        "property_id": prop.id,
    }], now_state


def _eval_deposit_risk(db: Session, watch: Watch) -> tuple:
    """New lettings asking more deposit than the Act allows."""
    seen = set((watch.seen_state or {}).get("seen_ids") or [])
    locality = ((watch.criteria or {}).get("locality") or "").strip().lower()

    rows = (db.query(RentalListing)
            .filter(RentalListing.status == "ACTIVE",
                    RentalListing.deposit_inr.isnot(None),
                    RentalListing.monthly_rent_inr.isnot(None)).all())

    events, all_ids = [], set(seen)
    for r in rows:
        all_ids.add(r.id)
        if r.id in seen or not r.monthly_rent_inr:
            continue
        prop = db.get(Property, r.property_id)
        if locality and (prop is None or
                         (prop.location_name or "").strip().lower() != locality):
            continue
        commercial = bool(prop and prop.property_type == "Commercial")
        cap = DEPOSIT_CAP_MONTHS_COMMERCIAL if commercial else DEPOSIT_CAP_MONTHS
        months = r.deposit_inr / r.monthly_rent_inr
        if months <= cap + 0.01:
            continue
        lawful = cap * r.monthly_rent_inr
        events.append({
            "fingerprint": f"{watch.id}:DEPOSIT_RISK:{r.id}",
            "title": f"Deposit {months:.0f}× rent on a new letting",
            "body": (f"{prop.title if prop else 'A listing'} asks "
                     f"{_rupees(r.deposit_inr)} where the Model Tenancy Act "
                     f"caps it at {cap} months — {_rupees(lawful)}. The excess "
                     f"of {_rupees(r.deposit_inr - lawful)} is contestable."),
            "severity": "warning",
            "property_id": r.property_id,
        })

    return events, {"seen_ids": sorted(all_ids)}


def _eval_lead_idle(db: Session, watch: Watch) -> tuple:
    """Leads that went quiet part-way through qualification."""
    hours = int((watch.criteria or {}).get("idle_hours", 48))
    cutoff = utcnow() - dt.timedelta(hours=hours)
    seen = set((watch.seen_state or {}).get("seen_ids") or [])

    rows = (db.query(Lead)
            .filter(Lead.status.in_(("NEW", "QUALIFIED")),
                    Lead.updated_at < cutoff).all())

    events, all_ids = [], set(seen)
    for lead in rows:
        all_ids.add(lead.id)
        if lead.id in seen:
            continue
        events.append({
            "fingerprint": f"{watch.id}:LEAD_IDLE:{lead.id}",
            "title": f"Lead idle {hours}h",
            "body": (f"{lead.buyer_phone} stopped at {lead.stage} — "
                     f"{lead.preferred_location or 'no locality'}, budget "
                     f"{_rupees(lead.budget_max)}, intent {lead.intent_score}."),
            "severity": "info",
            "property_id": None,
        })

    return events, {"seen_ids": sorted(all_ids)}


EVALUATORS = {
    "NEW_MATCH": _eval_new_match,
    "PRICE_DROP": _eval_price_drop,
    "VERIFICATION_CHANGE": _eval_verification_change,
    "DEPOSIT_RISK": _eval_deposit_risk,
    "LEAD_IDLE": _eval_lead_idle,
}


# --------------------------------------------------------------- dispatch

def _deliver(db: Session, watch: Watch, event: Dict[str, Any],
             now: Optional[dt.datetime] = None) -> Optional[Notification]:
    """Turn one event into a notification, subject to permission.

    Returns None when the event was already delivered — the unique fingerprint
    is what makes the whole loop safe to run as often as you like.
    """
    existing = (db.query(Notification)
                .filter_by(fingerprint=event["fingerprint"]).first())
    if existing is not None:
        return None

    allowed, reason = consent_mod.allow(db, watch.subject, watch.channel, now)

    note = Notification(
        watch_id=watch.id,
        subject=watch.subject,
        channel=watch.channel,
        title=event["title"],
        body=event["body"],
        severity=event.get("severity", "info"),
        property_id=event.get("property_id"),
        fingerprint=event["fingerprint"],
        status="QUEUED" if allowed else "SUPPRESSED",
        suppressed_reason=None if allowed else reason,
    )
    db.add(note)
    db.flush()

    if not allowed:
        return note

    if watch.channel == "whatsapp":
        result = wa_send.send_text(watch.subject, f"{note.title}\n\n{note.body}")
        # Dry-run counts as sent: the decision was made and the cap must
        # still apply, otherwise testing silently exhausts nobody's quota.
        note.status = "SENT" if result.get("status") != "error" else "FAILED"
    else:
        # in-app waits to be read; email would go here once SMTP is configured.
        note.status = "SENT"

    note.sent_at = utcnow()
    return note


def tick(db: Session, now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """One pass of the agent. Safe to call as often as you like.

    This is the entire agent step, and it is deliberately callable from the
    daemon, from an endpoint and from a test — the same code path in all
    three, so what is tested is what runs at 04:00 unattended.
    """
    run = AgentRun()
    db.add(run)
    db.flush()

    checked = found = sent = suppressed = 0
    errors: List[str] = []

    watches = db.query(Watch).filter_by(status="ACTIVE").all()
    for watch in watches:
        checked += 1
        evaluator = EVALUATORS.get(watch.kind)
        if evaluator is None:
            errors.append(f"watch {watch.id}: unknown kind {watch.kind!r}")
            continue
        try:
            events, new_state = evaluator(db, watch)
        except Exception:
            # One malformed watch must not stop everybody else's alerts.
            errors.append(f"watch {watch.id}: {traceback.format_exc(limit=2)}")
            continue

        for event in events:
            note = _deliver(db, watch, event, now)
            if note is None:
                continue
            found += 1
            if note.status == "SENT":
                sent += 1
            elif note.status == "SUPPRESSED":
                suppressed += 1

        watch.seen_state = new_state
        watch.last_checked_at = utcnow()
        watch.fired_count = (watch.fired_count or 0) + len(events)

    run.finished_at = utcnow()
    run.watches_checked = checked
    run.events_found = found
    run.notifications_sent = sent
    run.notifications_suppressed = suppressed
    run.error = "\n".join(errors) if errors else None
    db.commit()

    return {
        "run_id": run.id,
        "watches_checked": checked,
        "events_found": found,
        "notifications_sent": sent,
        "notifications_suppressed": suppressed,
        "errors": errors,
    }


# ------------------------------------------------------------------ watches

def create_watch(db: Session, subject: str, kind: str,
                 criteria: Optional[Dict[str, Any]] = None,
                 channel: str = "inapp", label: str = "") -> Watch:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    if channel not in consent_mod.CHANNELS:
        raise ValueError(f"channel must be one of {consent_mod.CHANNELS}")
    if not subject or not subject.strip():
        raise ValueError("A subject (phone or email) is required.")

    criteria = criteria or {}
    if kind in ("PRICE_DROP", "VERIFICATION_CHANGE") and not criteria.get("property_id"):
        raise ValueError(f"{kind} needs a property_id in criteria.")
    if kind == "NEW_MATCH" and not (criteria.get("brief") or "").strip():
        raise ValueError("NEW_MATCH needs a brief in criteria.")

    watch = Watch(subject=subject.strip(), kind=kind, criteria=criteria,
                  channel=channel, label=label or kind.replace("_", " ").title(),
                  seen_state={})
    db.add(watch)
    db.commit()
    db.refresh(watch)
    return watch


def watch_to_dict(w: Watch) -> Dict[str, Any]:
    return {
        "id": w.id,
        "subject": w.subject,
        "kind": w.kind,
        "label": w.label,
        "channel": w.channel,
        "criteria": w.criteria or {},
        "status": w.status,
        "fired_count": w.fired_count or 0,
        "last_checked_at": (w.last_checked_at.isoformat()
                            if w.last_checked_at else None),
    }


def note_to_dict(n: Notification) -> Dict[str, Any]:
    return {
        "id": n.id,
        "watch_id": n.watch_id,
        "title": n.title,
        "body": n.body,
        "severity": n.severity,
        "property_id": n.property_id,
        "channel": n.channel,
        "status": n.status,
        "suppressed_reason": n.suppressed_reason,
        "read": n.read_at is not None,
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }


def agent_status(db: Session) -> Dict[str, Any]:
    """Is the agent alive, and what has it been doing?"""
    last = (db.query(AgentRun).order_by(AgentRun.started_at.desc()).first())
    active = db.query(Watch).filter_by(status="ACTIVE").count()
    queued = db.query(Notification).filter_by(status="QUEUED").count()
    suppressed = db.query(Notification).filter_by(status="SUPPRESSED").count()

    stale = True
    if last is not None and last.finished_at is not None:
        stale = (utcnow() - as_utc(last.finished_at)) > dt.timedelta(minutes=15)

    return {
        "active_watches": active,
        "queued": queued,
        "suppressed_total": suppressed,
        "last_run": (
            None if last is None else {
                "id": last.id,
                "started_at": last.started_at.isoformat() if last.started_at else None,
                "watches_checked": last.watches_checked,
                "events_found": last.events_found,
                "notifications_sent": last.notifications_sent,
                "notifications_suppressed": last.notifications_suppressed,
                "error": last.error,
            }),
        "running": not stale,
        "note": ("The agent is a separate process: `python agent.py`. If "
                 "'running' is false the loop is not up, and nothing is being "
                 "watched no matter how many watches exist."),
        "whatsapp": "live" if wa_send.is_live() else "dry_run",
    }
