"""The full refresh: LEARN API -> calendar feed fallback -> notes index -> estimates and plan -> alerts.

Shared by `course-compass sync`, the web app's Sync button and the
scheduled background run, so they all behave the same way.
"""

from __future__ import annotations

import io
from argparse import Namespace
from dataclasses import dataclass, field
from typing import Callable

from .d2l import D2LClient, D2LError, browser_login, load_session, save_session
from .db import Hub
from .notes import build_index
from .notify import send_pending
from .planner import replan
from .sync import Syncer, fetch_ical, sync_ical
from .timeutil import to_iso, utcnow


@dataclass
class RefreshResult:
    lines: list[str] = field(default_factory=list)
    ok: bool = True
    login_needed: bool = False

    def log(self, message: str) -> None:
        self.lines.append(message)


def refresh_session(hub: Hub, log: Callable[[str], None]) -> bool:
    """Try a silent (headless) LEARN sign-in with the saved browser profile. True if it worked."""
    if not hub.browser_profile.exists():
        return False
    try:
        session = browser_login(hub.get_setting("d2l_base"), hub.browser_profile, headless=True, timeout_s=45)
    except D2LError as exc:
        log(f"Silent sign-in did not work ({exc}).")
        return False
    session.saved_at = to_iso(utcnow())
    save_session(hub.session_file, session)
    log("Signed in to LEARN again with the saved browser profile.")
    return True


def full_refresh(hub: Hub, download: bool = True, notify: bool = True, opener=None,
                 log: Callable[[str], None] | None = None) -> RefreshResult:
    result = RefreshResult()
    say = log or result.log
    if log:
        say = lambda m: (result.log(m), log(m))   # noqa: E731
    if hub.get_setting("demo"):
        say("Demo data: nothing to sync from LEARN.")
    else:
        session = load_session(hub.session_file)
        if session is None:
            say("Not connected to LEARN yet: run `course-compass login` (or Settings > Connect LEARN).")
        else:
            report = Syncer(hub, D2LClient(session, opener=opener), download=download, log=say).run()
            if report.login_needed and refresh_session(hub, say):
                report = Syncer(hub, D2LClient(load_session(hub.session_file), opener=opener),
                                download=download, log=say).run()
            say("LEARN: " + report.summary())
            result.login_needed = report.login_needed
            result.ok = not report.errors
        feed = hub.get_setting("d2l_ical_url")
        if feed:
            try:
                saved = sync_ical(hub, fetch_ical(feed, hub.get_setting("d2l_base"), opener=None))
                say(f"LEARN calendar feed: {saved} event(s) added or changed.")
            except (D2LError, OSError, ValueError) as exc:
                say(f"LEARN calendar feed failed: {exc}")
                result.ok = False
    index = build_index(hub)
    say(f"Notes index: {index.chunks} passages from {index.extracted} of {index.files} file(s)."
        + (f" {len(index.skipped)} skipped." if index.skipped else ""))
    plan = replan(hub)
    say(f"Planner: {len(plan.blocks)} study block(s) scheduled"
        + (f", {len(plan.at_risk)} item(s) at risk." if plan.at_risk else "."))
    if notify:
        sent = send_pending(hub)
        if sent:
            say(f"Alerts: {sent} change(s) notified.")
    return result


def llm_client(hub: Hub, env=None):
    """The LLM client chosen in Settings (auto / claude / openai / local), via the CLI's own selection rules."""
    from ..cli import pick_client

    provider = hub.get_setting("provider") or "auto"
    args = Namespace(provider=None if provider == "auto" else provider, local=False, llm=False)
    return pick_client(args, io.StringIO(), env)
