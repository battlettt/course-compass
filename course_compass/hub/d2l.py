"""Read-only client for a D2L Brightspace site (Waterloo LEARN by default), using your own browser session.

D2L's web pages load their data from the Brightspace REST API (`/d2l/api/...`)
with the signed-in user's session cookies. Course Compass makes the same GET
requests with the same cookies, so it sees exactly what you see and nothing
more. It never sends a write request, never stores your password, and never
follows a redirect (a redirect means the session expired, and following it
could hand the session cookies to another host).

Endpoints used (Brightspace API reference, lp/le product versions):
  GET /d2l/api/lp/{v}/users/whoami
  GET /d2l/api/lp/{v}/enrollments/myenrollments/?orgUnitTypeId=3
  GET /d2l/api/le/{v}/{ou}/news/
  GET /d2l/api/le/{v}/{ou}/dropbox/folders/
  GET /d2l/api/le/{v}/{ou}/dropbox/folders/{id}/submissions/mysubmissions/
  GET /d2l/api/le/{v}/{ou}/quizzes/
  GET /d2l/api/le/{v}/{ou}/calendar/events/myEvents/
  GET /d2l/api/le/{v}/{ou}/content/toc
  GET /d2l/api/le/{v}/{ou}/content/topics/{id}/file
  GET /d2l/api/le/{v}/{ou}/grades/values/myGradeValues/
  GET /d2l/api/le/{v}/{ou}/grades/final/values/myGradeValue
  GET /d2l/api/le/{v}/{ou}/overview
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode, urljoin, urlparse

# Versions Waterloo LEARN reported from its public /d2l/api/versions/ endpoint on 2026-11-23.
DEFAULT_VERSIONS = {"lp": "1.64", "le": "1.100"}
USER_AGENT = "CourseCompass/0.2 (personal read-only sync)"
MAX_JSON_BYTES = 10_000_000
MAX_FILE_BYTES = 60_000_000
SESSION_COOKIE_HINTS = ("d2lSessionVal", "d2lSecureSessionVal")


class D2LError(Exception):
    pass


class SessionExpiredError(D2LError):
    """D2L answered 401/403 or redirected to the login page: sign in again with `course-compass login`."""


class NotFoundError(D2LError):
    pass


class ForbiddenError(D2LError):
    """403 on a course tool: you have no access to it (common for co-op and PD courses), not a dead session."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401 - urllib API
        return None   # surface the 3xx as an HTTPError instead of following it


def default_opener() -> Callable:
    return urllib.request.build_opener(_NoRedirect()).open


def check_base(base: str) -> str:
    parsed = urlparse(base.strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.path not in ("", "/"):
        raise D2LError(f"D2L base URL must look like https://learn.example.edu, got {base!r}.")
    return f"https://{parsed.netloc}"


# ---------------------------------------------------------------- session file

@dataclass
class Session:
    base: str
    cookies: dict[str, str]
    saved_at: str = ""
    user: dict = field(default_factory=dict)

    def __repr__(self) -> str:   # never print cookie values
        return f"Session(base={self.base!r}, cookies={sorted(self.cookies)}, saved_at={self.saved_at!r})"

    def cookie_header(self) -> str:
        return "; ".join(f"{name}={value}" for name, value in self.cookies.items())


def save_session(path: Path, session: Session) -> None:
    """Write the session with 0600 permissions (it is as sensitive as being signed in)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"base": session.base, "cookies": session.cookies, "saved_at": session.saved_at,
                   "user": session.user}, handle)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def load_session(path: Path) -> Session | None:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return Session(base=check_base(raw["base"]), cookies=dict(raw["cookies"]),
                       saved_at=raw.get("saved_at", ""), user=raw.get("user") or {})
    except (ValueError, KeyError, TypeError, D2LError):
        raise D2LError(f"{path} is not a valid session file. Run `course-compass login` again.") from None


def parse_cookie_header(text: str) -> dict[str, str]:
    """Parse a pasted `Cookie:` header (from browser dev tools) into a dict."""
    text = text.strip()
    if text.lower().startswith("cookie:"):
        text = text[7:]
    cookies = {}
    for part in text.split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name:
            cookies[name.strip()] = value.strip()
    if not cookies:
        raise D2LError("No cookies found. Paste the whole Cookie header value from a signed-in LEARN request.")
    return cookies


# ---------------------------------------------------------------- client

def _utc_ms(iso: str) -> str:
    return iso[:19] + ".000Z" if iso.endswith("Z") and len(iso) == 20 else iso


class D2LClient:
    def __init__(self, session: Session, opener: Callable | None = None, delay: float = 0.35,
                 versions: dict[str, str] | None = None, sleep: Callable[[float], None] = time.sleep):
        self.base = check_base(session.base)
        self.session = session
        self._open = opener or default_opener()
        self.delay = delay
        self.versions = {**DEFAULT_VERSIONS, **(versions or {})}
        self._sleep = sleep
        self.requests = 0

    # transport -------------------------------------------------------------
    def _url(self, path: str) -> str:
        url = urljoin(self.base + "/", path.lstrip("/"))
        if urlparse(url).netloc != urlparse(self.base).netloc:
            raise D2LError(f"Refusing to send the D2L session to another host: {url}")
        return url

    def _request(self, path: str, accept: str, limit: int) -> bytes:
        url = self._url(path)
        request = urllib.request.Request(url, headers={
            "Cookie": self.session.cookie_header(), "Accept": accept, "User-Agent": USER_AGENT})
        for attempt in (1, 2):
            if self.requests:
                self._sleep(self.delay)        # be gentle: one request at a time, spaced out
            self.requests += 1
            try:
                with self._open(request, timeout=30) as response:
                    data = response.read(limit + 1)
                    if len(data) > limit:
                        raise D2LError(f"Response from {path} is larger than {limit} bytes; skipped.")
                    return data
            except urllib.error.HTTPError as exc:
                if exc.code == 403 and "/users/whoami" not in path:
                    raise ForbiddenError(path) from None
                if exc.code in (301, 302, 303, 307, 308, 401, 403):
                    raise SessionExpiredError(
                        "LEARN session expired or not signed in. Run `course-compass login`.") from None
                if exc.code == 404:
                    raise NotFoundError(path) from None
                if exc.code in (429, 500, 502, 503, 504) and attempt == 1:
                    self._sleep(3.0)
                    continue
                raise D2LError(f"LEARN returned HTTP {exc.code} for {path}") from None
            except urllib.error.URLError as exc:
                if attempt == 1:
                    self._sleep(2.0)
                    continue
                raise D2LError(f"Could not reach LEARN ({exc.reason}).") from None
            except TimeoutError:
                if attempt == 1:
                    continue
                raise D2LError(f"LEARN timed out on {path}.") from None
        raise D2LError(f"LEARN request failed: {path}")   # pragma: no cover

    def get_json(self, path: str):
        data = self._request(path, "application/json", MAX_JSON_BYTES)
        try:
            return json.loads(data.decode("utf-8-sig"))
        except ValueError:
            # A signed-out request often gets the HTML login page with a 200.
            if data.lstrip()[:1] == b"<":
                raise SessionExpiredError("LEARN sent a web page instead of data: sign in again.") from None
            raise D2LError(f"LEARN sent invalid JSON for {path}") from None

    def get_bytes(self, path: str, limit: int = MAX_FILE_BYTES) -> bytes:
        return self._request(path, "*/*", limit)

    def lp(self, path: str) -> str:
        return f"/d2l/api/lp/{self.versions['lp']}/{path.lstrip('/')}"

    def le(self, ou: int, path: str) -> str:
        return f"/d2l/api/le/{self.versions['le']}/{int(ou)}/{path.lstrip('/')}"

    # endpoints ---------------------------------------------------------------
    def whoami(self) -> dict:
        return self.get_json(self.lp("users/whoami"))

    def enrollments(self) -> list[dict]:
        """All course-offering enrollments, following the bookmark paging."""
        items, bookmark = [], None
        for _ in range(50):
            query = {"orgUnitTypeId": 3}
            if bookmark:
                query["bookmark"] = bookmark
            page = self.get_json(self.lp("enrollments/myenrollments/") + "?" + urlencode(query))
            items.extend(page.get("Items") or [])
            info = page.get("PagingInfo") or {}
            if not info.get("HasMoreItems"):
                break
            bookmark = info.get("Bookmark")
        return items

    def _object_list(self, path: str) -> list[dict]:
        """Follow an ObjectListPage ({"Objects": [...], "Next": url}) or accept a plain list."""
        out, url = [], path
        for _ in range(50):
            page = self.get_json(url)
            if isinstance(page, list):
                return out + page
            out.extend(page.get("Objects") or [])
            nxt = page.get("Next")
            if not nxt:
                break
            url = urlparse(nxt)._replace(scheme="", netloc="").geturl() if nxt.startswith("http") else nxt
        return out

    def news(self, ou: int) -> list[dict]:
        return self.get_json(self.le(ou, "news/"))

    def dropbox_folders(self, ou: int) -> list[dict]:
        return self.get_json(self.le(ou, "dropbox/folders/"))

    def my_submissions(self, ou: int, folder_id: int) -> list[dict]:
        return self.get_json(self.le(ou, f"dropbox/folders/{int(folder_id)}/submissions/mysubmissions/"))

    def quizzes(self, ou: int) -> list[dict]:
        return self._object_list(self.le(ou, "quizzes/"))

    def calendar_events(self, ou: int, start: str, end: str) -> list[dict]:
        # Brightspace wants UTCDateTime as yyyy-MM-ddTHH:mm:ss.fffZ; without milliseconds it answers 400
        query = urlencode({"startDateTime": _utc_ms(start), "endDateTime": _utc_ms(end)})
        return self._object_list(self.le(ou, "calendar/events/myEvents/") + "?" + query)

    def toc(self, ou: int) -> dict:
        return self.get_json(self.le(ou, "content/toc"))

    def topic_file(self, ou: int, topic_id: int) -> bytes:
        return self.get_bytes(self.le(ou, f"content/topics/{int(topic_id)}/file"))

    def grade_values(self, ou: int) -> list[dict]:
        return self.get_json(self.le(ou, "grades/values/myGradeValues/"))

    def final_grade(self, ou: int) -> dict | None:
        try:
            return self.get_json(self.le(ou, "grades/final/values/myGradeValue"))
        except NotFoundError:
            return None

    def overview(self, ou: int) -> dict | None:
        try:
            return self.get_json(self.le(ou, "overview"))
        except NotFoundError:
            return None


# ---------------------------------------------------------------- browser login (optional Playwright)

def browser_login(base: str, profile_dir: Path, headless: bool = False, timeout_s: int = 300,
                  out=None) -> Session:
    """Open a real browser window on LEARN, let the student sign in (SSO + Duo), then keep its cookies.

    The browser profile is kept in the Course Compass home folder, so the
    university's "remember me" and the Duo device trust survive. A later
    `headless=True` call can then often refresh an expired LEARN session without
    any typing. The password is typed into the university's own page and never
    passes through Course Compass.
    """
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError:
        raise D2LError("Browser login needs Playwright: `pip install playwright && playwright install chromium`. "
                       "Or use `course-compass login --cookie` to paste a session cookie.") from None
    base = check_base(base)
    host = urlparse(base).netloc
    profile_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(profile_dir, 0o700)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(str(profile_dir), headless=headless)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(base + "/d2l/home", wait_until="domcontentloaded")
            if out and not headless:
                print("A browser window is open on LEARN. Sign in there (Duo included); "
                      "this window closes by itself once you reach the LEARN home page.", file=out)
            deadline = time.monotonic() + timeout_s
            while time.monotonic() < deadline:
                url = urlparse(page.url)
                if url.netloc == host and url.path.startswith("/d2l/home"):
                    break
                page.wait_for_timeout(1000)
            else:
                raise D2LError("Timed out waiting for the LEARN home page" +
                               (" (headless refresh needs a fresh sign-in)." if headless else "."))
            cookies = {c["name"]: c["value"] for c in context.cookies(base)
                       if c.get("domain", "").lstrip(".") in (host, host.split(".", 1)[-1])}
        finally:
            context.close()
    if not any(name in cookies for name in SESSION_COOKIE_HINTS):
        raise D2LError("Signed in, but no D2L session cookie was found.")
    return Session(base=base, cookies=cookies)
