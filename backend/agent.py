#!/usr/bin/env python3
"""The agent daemon. Run this and close the laptop lid.

    python agent.py                 # loop, default 60s
    python agent.py --interval 300  # every 5 minutes
    python agent.py --once          # a single tick, for cron or a smoke test

This is a separate process from the API on purpose. The API must answer a
buyer in milliseconds; the agent walks every watch and may call an LLM. Sharing
a process means one slow evaluation blocks a page load, and a crash in either
takes down both.

It exits non-zero only when it cannot start. Once the loop is up, a failure in
any single tick is logged and the loop continues — an agent that dies on the
first transient database error is not an agent, it is a script.
"""

import argparse
import logging
import os
import signal
import sys
import time
from types import FrameType
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.db import SessionLocal, init_db          # noqa: E402
from app.watchtower import tick                   # noqa: E402

log = logging.getLogger("agent")

_stop = False


def _handle_signal(signum: int, _frame: Optional[FrameType]) -> None:
    """Finish the tick in progress, then stop. Never abandon a half-written run."""
    global _stop
    _stop = True
    log.info("signal %s received — finishing this tick, then exiting", signum)


def run_once() -> dict:
    session = SessionLocal()
    try:
        return tick(session)
    finally:
        session.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify property watch agent")
    parser.add_argument("--interval", type=int, default=60,
                        help="seconds between ticks (default 60)")
    parser.add_argument("--once", action="store_true",
                        help="run a single tick and exit")
    parser.add_argument("--quiet", action="store_true",
                        help="log only warnings and errors")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s")

    try:
        init_db()
    except Exception as exc:                     # cannot start — say why, loudly
        log.error("could not open the database: %s", exc)
        return 1

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    if args.once:
        result = run_once()
        log.info("tick: %s", result)
        return 0

    log.info("agent up — ticking every %ss. Ctrl-C to stop.", args.interval)
    consecutive_failures = 0

    while not _stop:
        started = time.monotonic()
        try:
            result = run_once()
            consecutive_failures = 0
            if result["events_found"] or result["errors"]:
                log.info(
                    "checked %s watches · %s events · %s sent · %s suppressed%s",
                    result["watches_checked"], result["events_found"],
                    result["notifications_sent"],
                    result["notifications_suppressed"],
                    f" · {len(result['errors'])} errors" if result["errors"] else "")
            for err in result["errors"]:
                log.warning("watch error: %s", err.strip().splitlines()[-1])
        except Exception:
            consecutive_failures += 1
            log.exception("tick failed (%s in a row)", consecutive_failures)
            # Back off so a database that has gone away is not hammered, but
            # never give up entirely — the whole point is to still be here.
            time.sleep(min(300, 5 * consecutive_failures))

        elapsed = time.monotonic() - started
        remaining = max(0.0, args.interval - elapsed)
        # Sleep in slices so Ctrl-C is felt immediately rather than after a
        # full interval.
        while remaining > 0 and not _stop:
            nap = min(1.0, remaining)
            time.sleep(nap)
            remaining -= nap

    log.info("agent stopped cleanly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
