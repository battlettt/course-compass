"""Hub commands for the `course-compass` CLI: login, sync, serve, demo, today, digest, calendar, grades,
index, timer and schedule."""

from __future__ import annotations

import getpass
import os
import plistlib
import shutil
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

from .d2l import D2LClient, D2LError, Session, browser_login, check_base, load_session, parse_cookie_header, \
    save_session
from .db import Hub, default_home
from .timeutil import countdown, local_tz, parse_iso, to_iso, utcnow

LAUNCH_LABEL = "ca.coursecompass.sync"


def _hub(args) -> Hub:
    home = Path(args.home).expanduser() if getattr(args, "home", None) else default_home()
    return Hub(home)


def cmd_login(args, out) -> int:
    hub = _hub(args)
    base = check_base(args.base or hub.get_setting("d2l_base"))
    hub.set_setting("d2l_base", base)
    if args.cookie:
        print("Paste the Cookie header from a signed-in LEARN request (input is hidden), then press Enter:", file=out)
        cookies = parse_cookie_header(getpass.getpass(""))
        session = Session(base=base, cookies=cookies)
    else:
        session = browser_login(base, hub.browser_profile, headless=False, out=out)
    me = D2LClient(session).whoami()
    session.user = {"first": me.get("FirstName", ""), "id": me.get("Identifier", "")}
    session.saved_at = to_iso(utcnow())
    save_session(hub.session_file, session)
    hub.set_setting("demo", False)
    print(f"Connected to {base} as {me.get('FirstName', '')} {me.get('LastName', '')}. "
          f"Session saved to {hub.session_file} (readable only by you). Next: course-compass sync", file=out)
    return 0


def cmd_logout(args, out) -> int:
    hub = _hub(args)
    removed = []
    if hub.session_file.exists():
        hub.session_file.unlink()
        removed.append("session")
    if args.browser and hub.browser_profile.exists():
        shutil.rmtree(hub.browser_profile)
        removed.append("browser profile")
    print("Removed: " + (", ".join(removed) if removed else "nothing (not signed in)"), file=out)
    return 0


def cmd_sync(args, out) -> int:
    from .jobs import full_refresh

    hub = _hub(args)
    result = full_refresh(hub, download=not args.no_files, notify=not args.quiet,
                          log=lambda message: print(message, file=out))
    return 0 if result.ok else 2


def cmd_serve(args, out) -> int:
    from .server import serve

    hub = _hub(args)
    serve(hub, host=args.host, port=args.port, open_browser=not args.no_browser, out=out,
          extra_hosts=args.allow_host)
    return 0


def cmd_demo(args, out) -> int:
    from .demo import seed

    home = Path(args.home).expanduser() if args.home else Path.home() / ".course-compass-demo"
    if home.exists() and args.reset:
        shutil.rmtree(home)
    hub = Hub(home)
    if not hub.courses() or args.reset:
        seed(hub)
        print(f"Demo data written to {home}.", file=out)
    if args.no_serve:
        return 0
    from .server import serve
    serve(hub, port=args.port, open_browser=not args.no_browser, out=out)
    return 0


def cmd_today(args, out) -> int:
    hub = _hub(args)
    now = utcnow()
    tz = local_tz()
    items = [i for i in hub.items(start=to_iso(now - timedelta(days=1)), end=to_iso(now + timedelta(days=args.days)),
                                  kinds=("assignment", "quiz", "exam", "manual")) if i["status"] == "open"]
    print(f"Due in the next {args.days} days ({len(items)}):", file=out)
    for item in items:
        due = parse_iso(item["due"])
        print(f"  {due.astimezone(tz).strftime('%a %b %d %I:%M %p'):<20} {countdown(due, now):<12} "
              f"{item['course_code'] or '':<9} {item['title']}", file=out)
    if not items:
        print("  nothing due", file=out)
    return 0


def cmd_digest(args, out) -> int:
    from .notify import digest, macos_notify

    hub = _hub(args)
    text = digest(hub, days=args.days)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text, file=out, end="")
    if args.notify:
        due = text.split("## Due in the next", 1)[1].split("\n")[0] if "## Due in the next" in text else ""
        macos_notify("Course Compass: your day", f"Due in the next{due}. Open the hub for the plan.")
    return 0


def cmd_calendar(args, out) -> int:
    from .ics import build_feed
    from .server import FEEDS, feed_items

    hub = _hub(args)
    target = Path(args.out_dir).expanduser()
    target.mkdir(parents=True, exist_ok=True)
    for name in (args.feed or list(FEEDS)):
        path = target / f"course-compass-{name}.ics"
        path.write_text(build_feed(feed_items(hub, name), FEEDS[name], alarms=hub.get_setting("alarms_minutes")),
                        encoding="utf-8", newline="")
        print(f"Wrote {path}", file=out)
    print("Double-click a file to import it into Calendar once, or run `course-compass serve` and use "
          "Calendar > Add to Apple Calendar for live, auto-updating subscriptions.", file=out)
    return 0


def cmd_grades(args, out) -> int:
    import json

    from .grades import standing
    from .syllabus import Syllabus

    hub = _hub(args)
    for course in hub.courses():
        row = hub.one("SELECT parsed FROM syllabus WHERE course_id=?", (course["id"],))
        parsed = Syllabus.from_dict(json.loads(row["parsed"])) if row else None
        whatif = {r["component"]: r["percent"] for r in hub.query("SELECT * FROM whatif WHERE course_id=?",
                                                                    (course["id"],))}
        st = standing(hub.query("SELECT * FROM grades WHERE course_id=?", (course["id"],)),
                      parsed.components if parsed else [], whatif, args.target or course.get("target"))
        current = f"{st.current_percent:.1f}%" if st.current_percent is not None else "no grades yet"
        print(f"{course['code']:<10} {current:<14} graded {st.graded_weight:g}% of {st.total_weight:g}%", file=out)
        need = st.needed()
        if need and need["needed_percent"] is not None:
            line = f"           for {need['target']:g}%: {need['needed_percent']}% average on the rest ({need['status']})"
            if "final" in need:
                line += f"; {need['final']['needed_percent']}% on the {need['final']['name'].lower()}"
            print(line, file=out)
    return 0


def cmd_index(args, out) -> int:
    from .notes import build_index

    report = build_index(_hub(args))
    print(f"Indexed {report.chunks} passages from {report.extracted} of {report.files} file(s).", file=out)
    for line in report.skipped[:20]:
        print(f"  skipped {line}", file=out)
    return 0


def cmd_timer(args, out) -> int:
    from .planner import running_timer, start_timer, stop_timer

    hub = _hub(args)
    if args.action == "start":
        course_id = None
        if args.course:
            match = [c for c in hub.courses() if c["code"].replace(" ", "").lower() == args.course.replace(" ", "").lower()]
            if not match:
                print(f"error: no course {args.course!r}", file=sys.stderr)
                return 2
            course_id = match[0]["id"]
        start_timer(hub, course_id, None, note=args.note or "")
        print("Timer started.", file=out)
    elif args.action == "stop":
        stopped = stop_timer(hub)
        if stopped:
            minutes = (parse_iso(stopped["end"]) - parse_iso(stopped["start"])).total_seconds() / 60
            print(f"Logged {minutes:.0f} minutes.", file=out)
        else:
            print("No timer running.", file=out)
    else:
        running = running_timer(hub)
        print(f"Running since {running['start']} ({running['course_code'] or 'general'})" if running
              else "No timer running.", file=out)
    return 0


def cmd_schedule(args, out) -> int:
    """Install a macOS LaunchAgent that syncs at set times and sends the morning digest."""
    hub = _hub(args)
    agent = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_LABEL}.plist"
    if args.remove:
        if agent.exists():
            subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(agent)], capture_output=True)
            agent.unlink()
        print("Scheduled sync removed.", file=out)
        return 0
    times = []
    for value in args.times.split(","):
        hour, _, minute = value.strip().partition(":")
        times.append({"Hour": int(hour), "Minute": int(minute or 0)})
    program = [sys.executable, "-m", "course_compass", "sync", "--home", str(hub.home)]
    plist = {"Label": LAUNCH_LABEL, "ProgramArguments": program, "StartCalendarInterval": times,
             "StandardOutPath": str(hub.home / "sync.log"), "StandardErrorPath": str(hub.home / "sync.log"),
             "EnvironmentVariables": {"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
             "WorkingDirectory": str(hub.home)}
    if args.dry_run:
        print(plistlib.dumps(plist).decode(), file=out)
        return 0
    agent.parent.mkdir(parents=True, exist_ok=True)
    agent.write_bytes(plistlib.dumps(plist))
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(agent)], capture_output=True)
    done = subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(agent)], capture_output=True, text=True)
    if done.returncode != 0:
        print(f"Wrote {agent}, but launchctl said: {done.stderr.strip()[:200]}", file=out)
        return 2
    print(f"Sync scheduled at {args.times} every day (log: {hub.home / 'sync.log'}). "
          "Remove with `course-compass schedule --remove`.", file=out)
    return 0


def add_commands(sub, common_home) -> None:
    p = sub.add_parser("login", parents=[common_home], help="connect to LEARN (opens a browser to sign in)")
    p.add_argument("--base", help="D2L site, default https://learn.uwaterloo.ca")
    p.add_argument("--cookie", action="store_true", help="paste a session cookie instead of using a browser")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("logout", parents=[common_home], help="forget the saved LEARN session")
    p.add_argument("--browser", action="store_true", help="also delete the saved browser profile")
    p.set_defaults(func=cmd_logout)

    p = sub.add_parser("sync", parents=[common_home], help="pull everything from LEARN, re-index notes, re-plan")
    p.add_argument("--no-files", action="store_true", help="skip downloading content files")
    p.add_argument("--quiet", action="store_true", help="no notifications")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("serve", parents=[common_home], help="open the hub web app")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--host", default="127.0.0.1", help="interface to listen on (default: this Mac only)")
    p.add_argument("--allow-host", action="append", default=[],
                   help="extra Host header to accept, e.g. mymac.tailnet.ts.net:8765")
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("demo", help="try the hub with made-up sample courses (no LEARN needed)")
    p.add_argument("--home", help="demo folder (default ~/.course-compass-demo)")
    p.add_argument("--reset", action="store_true", help="rebuild the demo data with today's dates")
    p.add_argument("--port", type=int, default=8766)
    p.add_argument("--no-serve", action="store_true")
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("today", parents=[common_home], help="what's due soon, in the terminal")
    p.add_argument("--days", type=int, default=7)
    p.set_defaults(func=cmd_today)

    p = sub.add_parser("digest", parents=[common_home], help="the morning digest (markdown)")
    p.add_argument("--days", type=int, default=3)
    p.add_argument("--out", help="write to a file")
    p.add_argument("--notify", action="store_true", help="also show a Mac notification")
    p.set_defaults(func=cmd_digest)

    p = sub.add_parser("calendar", parents=[common_home], help="write .ics calendar files")
    p.add_argument("--out-dir", default=".", help="folder for the .ics files")
    p.add_argument("--feed", action="append", choices=["all", "deadlines", "exams", "study", "classes"])
    p.set_defaults(func=cmd_calendar)

    p = sub.add_parser("grades", parents=[common_home], help="current standing and what you need, per course")
    p.add_argument("--target", type=float, help="target final grade, e.g. 85")
    p.set_defaults(func=cmd_grades)

    p = sub.add_parser("index", parents=[common_home], help="rebuild the notes index from LEARN files and your notes")
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("timer", parents=[common_home], help="start/stop the study timer")
    p.add_argument("action", choices=["start", "stop", "status"])
    p.add_argument("--course", help="course code, e.g. STAT230")
    p.add_argument("--note")
    p.set_defaults(func=cmd_timer)

    p = sub.add_parser("schedule", parents=[common_home], help="sync automatically at set times (macOS)")
    p.add_argument("--times", default="7:30,12:30,18:00", help="comma-separated HH:MM times")
    p.add_argument("--remove", action="store_true")
    p.add_argument("--dry-run", action="store_true", help="print the LaunchAgent instead of installing it")
    p.set_defaults(func=cmd_schedule)
