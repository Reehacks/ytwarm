"""One warming session: search, watch, Shorts, small interactions, clean exit.

The shape of a session is deliberate and is the only editorial judgement in the
project:

1. The homepage first, and a pause on it. Landing straight on a search URL is
   not how anyone opens YouTube, and the homepage load is also the cheapest
   place to find out the profile is signed out or challenged.
2. A search from the niche keyword list, typed into the box - not a
   `?search_query=` URL. The typed search is the event that tells YouTube what
   this account is interested in.
3. A real watch on a result: 40-120 seconds by default, which is long enough to
   register as watch time rather than a bounce.
4. Shorts. Retention is mixed on purpose - some swiped away after 2-4 seconds,
   some watched out - because a feed where every Short is watched to the end is
   not a person.

**Anything that looks like a challenge stops the session.** A bot check, a
consent wall or a signed-out profile all raise, and the handler screenshots and
returns rather than clicking through. Clicking a consent wall or a verification
box automatically is how a warm profile turns into a flagged one, and a session
that browses signed out builds nothing while looking like it worked.
"""
from __future__ import annotations

import random
import time

from playwright.sync_api import Error as PWError

import config as C
import human

HOME = "https://www.youtube.com/"
SHORTS = "https://www.youtube.com/shorts/"

# Text that means "YouTube wants something from the human". Matched
# case-insensitively against the page body.
BOT_TEXT = [
    "sign in to confirm you're not a bot",
    "sign in to confirm you’re not a bot",
    "i'm not a robot",
    "unusual traffic",
    "verify it's you",
    "verify it’s you",
    "before you continue to youtube",
    "confirm your identity",
]
BOT_URL = ["/sorry/", "consent.youtube.com", "consent.google.com",
           "accounts.google.com/v3/signin/challenge"]

SEARCH_BOX = ["input#search", "input[name='search_query']",
              "ytd-searchbox input", "#search-input input"]
RESULTS = ["ytd-video-renderer a#video-title",
           "a#video-title-link[href*='/watch']",
           "a#video-title[href*='/watch']"]
HOME_VIDEOS = ["ytd-rich-item-renderer a#video-title-link",
               "ytd-rich-grid-media a#video-title-link"]
AVATAR = ["#avatar-btn", "ytd-topbar-menu-button-renderer img"]
SIGNIN = ["a[href*='accounts.google.com/ServiceLogin']",
          "ytd-button-renderer a[href*='ServiceLogin']"]
# The channel name, as the account menu prints it. Reading it needs the menu
# open - the avatar image's alt text is "Avatar image" on current YouTube, so
# there is nothing to read from the closed top bar.
ACCOUNT_NAME = ["ytd-active-account-header-renderer #account-name", "#account-name"]


class BotCheck(RuntimeError):
    """YouTube is asking the human something. Stop, do not answer it."""


class SignedOut(RuntimeError):
    """The profile has no live Google session, so nothing would be recorded."""


class WrongChannel(RuntimeError):
    """The profile is on a different channel than the one it is supposed to warm.

    One Google account can own several channels, and YouTube keeps the feed, the
    subscriptions and the history PER CHANNEL while the login is shared. So two
    channels on one account are two profile folders with the same sign-in, and
    the only thing telling them apart is which channel is selected inside each.
    That selection is a cookie: it survives in the folder, and it can be changed
    by hand at any time without anything saying so.

    Warming the wrong channel is invisible for weeks - the feed it trains is not
    the feed anyone looks at - so a mismatch stops the session.
    """


# ---------------------------------------------------------------- small helpers


def first(page, selectors, timeout: int = 6000):
    """The first selector that matches at least one visible element, or None."""
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        for sel in selectors:
            loc = page.locator(sel).first
            try:
                if loc.count() and loc.is_visible():
                    return loc
            except PWError:
                continue
        time.sleep(0.25)
    return None


def guard(page) -> None:
    """Raise if the page is a challenge rather than YouTube."""
    url = (page.url or "").lower()
    for marker in BOT_URL:
        if marker in url:
            raise BotCheck(f"redirected to a challenge page: {page.url}")
    try:
        body = (page.locator("body").inner_text(timeout=4000) or "")[:4000].lower()
    except PWError:
        return
    for marker in BOT_TEXT:
        if marker in body:
            raise BotCheck(f"page says: {marker!r}")


def check_signed_in(page) -> None:
    if first(page, AVATAR, timeout=4000):
        return
    if first(page, SIGNIN, timeout=1500):
        raise SignedOut("no Google session in this profile - run --setup again")


def channel_matches(expected: str, seen: str) -> bool:
    """Is the channel on screen the one this profile is for?

    Kept as a plain function with no page in it so the comparison can be tested.
    An empty `expected` means the profile never said which channel it is, so
    nothing is checked - that is what keeps the guard opt-in.
    """
    if not expected.strip():
        return True
    return " ".join(expected.split()).casefold() == " ".join(seen.split()).casefold()


def account_name(page, log) -> str:
    """Open the account menu, read the channel name, close it again.

    The menu has to be opened: on current YouTube the avatar in the top bar
    carries no name, and the account menu is the only place the active channel
    is written down. Escape closes it, and a click on the avatar is a thing
    people do anyway.
    """
    btn = first(page, AVATAR, timeout=8000)
    if not btn:
        return ""
    human.click(page, btn)
    human.nap(1.0, 0.35, 0.4, 2.5)
    node = first(page, ACCOUNT_NAME, timeout=6000)
    name = ""
    if node:
        try:
            name = (node.inner_text(timeout=4000) or "").strip()
        except PWError as e:
            log.debug("could not read the account name: %s", e)
    page.keyboard.press("Escape")
    human.nap(0.6, 0.25, 0.2, 1.8)
    return name


def check_channel(page, profile, log) -> str:
    """Raise unless the active channel is the one this profile is for."""
    if not profile.channel_name:
        return ""
    seen = account_name(page, log)
    if not seen:
        log.warning("could not read the channel name - carrying on without the check")
        return ""
    if not channel_matches(profile.channel_name, seen):
        raise WrongChannel(
            f"this profile is on {seen!r}, but {profile.profile_id} is for "
            f"{profile.channel_name!r}. Open it with --setup, switch channel "
            "from the avatar menu, and close the window.")
    log.info("channel confirmed: %s", seen)
    return seen


def goto(page, url: str, log) -> None:
    log.info("goto %s", url)
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    human.nap(1.8, 0.7, 0.6, 5.0)
    guard(page)


# ---------------------------------------------------------------- actions


def home_browse(page, cfg, log) -> dict:
    """Open the homepage, look down the feed, maybe open one thing from it."""
    goto(page, HOME, log)
    human.nap(2.6, 1.0, 1.0, 7.0)
    human.scroll(page, int(human.gauss(1400, 500, 400, 3000)))
    human.drift(page)
    if human.chance(0.35):
        link = first(page, HOME_VIDEOS, timeout=5000)
        if link:
            human.click(page, link)
            watched = watch(page, cfg, log)
            return {"kind": "home_watch", "watched": watched}
    return {"kind": "home_browse", "watched": 0}


def search_and_watch(page, profile, cfg, log) -> dict:
    """Type one niche keyword, scroll the results, watch one video properly."""
    keyword = C.next_keyword(profile)
    if page.url.rstrip("/") != HOME.rstrip("/"):
        goto(page, HOME, log)
    box = first(page, SEARCH_BOX, timeout=10000)
    if not box:
        log.warning("no search box found - skipping this search")
        return {"kind": "search", "watched": 0, "keyword": keyword}

    log.info("search %r", keyword)
    human.click(page, box)
    human.nap(0.6, 0.25, 0.2, 1.8)
    human.type_text(page, keyword)
    human.nap(0.7, 0.3, 0.2, 2.2)
    page.keyboard.press("Enter")
    page.wait_for_load_state("domcontentloaded")
    human.nap(2.4, 0.9, 0.9, 6.0)
    guard(page)

    human.scroll(page, int(human.gauss(700, 300, 150, 1800)))
    links = [page.locator(sel) for sel in RESULTS]
    pool = next((loc for loc in links if loc.count()), None)
    if not pool or not pool.count():
        log.warning("no results matched for %r", keyword)
        return {"kind": "search", "watched": 0, "keyword": keyword}

    # Somewhere in the first handful, not always the top hit - the first result
    # every single time is a pattern, and the top hit is often an ad.
    idx = min(pool.count() - 1, random.choices([0, 1, 2, 3, 4, 5],
                                               weights=[22, 22, 18, 14, 12, 12])[0])
    log.info("opening result #%d of %d", idx + 1, pool.count())
    human.click(page, pool.nth(idx))
    watched = watch(page, cfg, log)
    return {"kind": "search", "watched": watched, "keyword": keyword}


def watch(page, cfg, log, seconds: float | None = None) -> float:
    """Sit on a watch page for a randomised span, checking it is really playing.

    The span is spent in short slices rather than one sleep, so the bot check is
    re-run, the odd micro-interaction can land, and a video that quietly paused
    itself (an ad ending, a stalled buffer) gets a keypress rather than being
    "watched" while frozen.
    """
    lo, hi = cfg.watch_seconds
    target = seconds if seconds else random.uniform(lo, hi)
    try:
        page.wait_for_selector("video", timeout=25000)
    except PWError:
        log.warning("no video element appeared - leaving this one")
        return 0.0
    guard(page)
    human.nap(1.6, 0.6, 0.5, 4.0)
    log.info("watching for %.0fs", target)

    spent = 0.0
    while spent < target:
        slice_len = min(human.gauss(9, 3, 3, 18), target - spent)
        time.sleep(slice_len)
        spent += slice_len
        guard(page)
        if _paused(page) and human.chance(0.8):
            log.debug("video was paused - resuming")
            page.keyboard.press("k")
        if human.chance(cfg.interaction_chance):
            micro(page, cfg, log)
    log.info("watched %.0fs", spent)
    return round(spent, 1)


def _paused(page) -> bool:
    try:
        return bool(page.evaluate(
            "() => { const v = document.querySelector('video');"
            " return v ? v.paused : false; }"))
    except PWError:
        return False


def _media(page) -> tuple:
    """(currentTime, duration) of the page's video, zeros if there is none."""
    try:
        got = page.evaluate(
            "() => { const v = document.querySelector('video');"
            " return v ? [v.currentTime || 0, v.duration || 0] : [0, 0]; }")
        return float(got[0] or 0), float(got[1] or 0)
    except PWError:
        return 0.0, 0.0


def micro(page, cfg, log) -> str:
    """One small human thing: pause, mute, glance at the comments, nudge back.

    No likes, no subscribes, no comments posted. Those are public actions on
    someone else's video from a real account, and none of them are needed to
    teach the algorithm a niche.
    """
    what = random.choices(
        ["pause", "sound", "comments", "rewind", "drift"],
        weights=[22, 18, 22, 13, 25])[0]
    log.debug("micro-interaction: %s", what)
    try:
        if what == "pause":
            page.keyboard.press("k")
            human.nap(2.6, 1.4, 0.6, 9.0)
            page.keyboard.press("k")
        elif what == "sound":
            page.keyboard.press("m")
            human.nap(3.0, 1.5, 0.8, 10.0)
            page.keyboard.press("m")
        elif what == "comments":
            human.scroll(page, int(human.gauss(900, 300, 350, 1700)))
            human.nap(3.2, 1.6, 0.9, 9.0)
            human.scroll_back(page, int(human.gauss(900, 300, 350, 1700)))
        elif what == "rewind":
            page.keyboard.press("ArrowLeft")
            human.nap(0.8, 0.4, 0.2, 2.5)
        else:
            human.drift(page)
    except PWError as e:
        log.debug("micro-interaction %s did not land: %s", what, e)
    return what


def shorts_session(page, cfg, log) -> dict:
    """Walk the Shorts feed with mixed retention.

    Roughly a third are swiped after 2-4 seconds. The rest are watched for a
    slice of their own length, and a short one is sometimes allowed to loop once
    - the loop is what a Short that landed actually looks like.
    """
    goto(page, SHORTS, log)
    lo, hi = cfg.shorts_per_session
    count = random.randint(int(lo), int(hi))
    log.info("shorts: %d planned", count)
    seen = 0
    for i in range(count):
        try:
            page.wait_for_selector("video", timeout=20000)
        except PWError:
            log.warning("shorts feed did not load a video - stopping shorts")
            break
        guard(page)
        _, dur = _media(page)
        if human.chance(0.33):
            held = human.gauss(3.0, 0.7, 2.0, 4.0)
            time.sleep(held)
            log.debug("short %d: swiped after %.1fs", i + 1, held)
        else:
            if dur and dur > 1:
                held = min(dur * random.uniform(0.7, 1.0), 60.0)
                if dur < 20 and human.chance(0.4):
                    held = min(dur * random.uniform(1.5, 2.0), 60.0)   # one loop
            else:
                held = human.gauss(18, 7, 6, 45)
            end = time.time() + held
            while time.time() < end:
                time.sleep(min(6.0, max(0.5, end - time.time())))
                guard(page)
            log.debug("short %d: held %.1fs of a %.1fs short", i + 1, held, dur)
            if human.chance(cfg.interaction_chance):
                micro(page, cfg, log)
        seen += 1
        page.keyboard.press("ArrowDown")
        human.nap(1.1, 0.5, 0.35, 3.0)
    return {"kind": "shorts", "shorts": seen}


# ---------------------------------------------------------------- the session


class Budget:
    """A session's ceiling: whichever of runtime or action count runs out first."""

    def __init__(self, cfg: dict):
        self.minutes = random.uniform(cfg["min_minutes"], cfg["max_minutes"])
        self.deadline = time.time() + self.minutes * 60
        self.target = random.randint(int(cfg["min_actions"]), int(cfg["max_actions"]))
        self.done = 0

    def left(self) -> bool:
        return self.done < self.target and time.time() < self.deadline

    def spend(self) -> None:
        self.done += 1

    @property
    def minutes_left(self) -> float:
        return max(0.0, (self.deadline - time.time()) / 60)


def warm(profile, cfg, log, headless: bool = False) -> dict:
    """Run one whole warming session for one profile. Never raises.

    The first two actions are fixed - one typed search with a watch, then the
    Shorts feed - because those are the two events that actually shape the
    recommendation profile. Anything left in the budget is spent at random.
    """
    import browser                     # local: keeps `import workflows` cheap

    started = time.time()
    out = {"result": "ok", "actions": 0, "watched": 0.0, "shorts": 0,
           "minutes": 0.0, "note": ""}
    try:
        with browser.launched(profile, cfg, log, headless=headless) as (ctx, page):
            # The screenshot has to be taken while the page is still open, so a
            # challenge is caught in HERE and only then re-raised - the outer
            # handler runs after the context manager has closed the browser.
            try:
                budget = Budget(profile.daily_budget)
                log.info("session plan: %d actions, up to %.1f minutes",
                         budget.target, budget.minutes)
                goto(page, HOME, log)
                check_signed_in(page)
                # Before anything is watched. A wrong-channel session that has
                # already spent ten minutes has already trained the wrong feed.
                check_channel(page, profile, log)
                human.nap(3.0, 1.2, 1.0, 8.0)
                human.scroll(page, int(human.gauss(800, 300, 250, 1800)))

                plan = [search_and_watch, shorts_session]
                while budget.left():
                    action = plan.pop(0) if plan else random.choices(
                        [search_and_watch, shorts_session, home_browse],
                        weights=[50, 30, 20])[0]
                    try:
                        got = action(page, profile, cfg, log) \
                            if action is search_and_watch else action(page, cfg, log)
                    except (BotCheck, SignedOut):
                        raise
                    except PWError as e:
                        log.warning("action %s failed: %s", action.__name__, e)
                        got = {}
                    out["watched"] += float(got.get("watched") or 0)
                    out["shorts"] += int(got.get("shorts") or 0)
                    budget.spend()
                    out["actions"] = budget.done
                    log.info("action %d/%d done, %.1f minutes of budget left",
                             budget.done, budget.target, budget.minutes_left)
                    if budget.left():
                        human.nap(6.0, 3.0, 1.5, 20.0)
            except BotCheck:
                _shot(page, profile, log, "botcheck")
                raise
            except SignedOut:
                _shot(page, profile, log, "signedout")
                raise
            except WrongChannel:
                _shot(page, profile, log, "wrongchannel")
                raise
    except BotCheck as e:
        out.update(result="blocked", note=str(e))
        log.warning("BOT CHECK: %s", e)
    except SignedOut as e:
        out.update(result="signed_out", note=str(e))
        log.warning("SIGNED OUT: %s", e)
    except WrongChannel as e:
        out.update(result="wrong_channel", note=str(e))
        log.warning("WRONG CHANNEL: %s", e)
    except Exception as e:                                  # noqa: BLE001
        out.update(result="error", note=f"{type(e).__name__}: {e}")
        log.exception("session failed")
    out["minutes"] = round((time.time() - started) / 60, 1)
    log.info("session %s: %d actions, %.0fs watched, %d shorts, %.1f minutes",
             out["result"], out["actions"], out["watched"], out["shorts"],
             out["minutes"])
    C.record_run(profile.profile_id, out)
    return out


def _shot(page, profile, log, tag: str) -> None:
    """Screenshot the challenge, so the reason is visible after the fact."""
    try:
        path = C.shot_path(profile.profile_id, tag)
        page.screenshot(path=str(path), full_page=False)
        log.warning("screenshot: %s", path)
    except Exception as e:                                  # noqa: BLE001
        log.debug("could not screenshot: %s", e)


def setup(profile, cfg, log) -> dict:
    """Open the profile in a PLAIN Chrome and wait for the human to sign in.

    Deliberately not a Playwright browser. Google's sign-in refuses a browser it
    can see is being driven - it dead-ends on "this browser or app may not be
    secure" - and that check fires on the DevTools connection Playwright needs to
    exist at all, so there is no flag that gets past it. A plain Chrome pointed at
    the same profile folder writes ordinary cookies, and the warming sessions read
    them from then on without ever signing in again.

    Finished means the window is CLOSED, not a button here: Chrome flushes its
    cookie jar on exit, and waiting for the process is the only way to know it did.
    """
    import browser

    started = time.time()
    proc = browser.open_plain(profile, HOME, log)
    log.info("Chrome is open for %s", profile.name)
    log.info("1. Sign in to this channel's Google account, and pass 2FA.")
    log.info("2. Accept the cookie and consent screens YouTube shows.")
    log.info("3. CLOSE the Chrome window. That saves the session to disk.")
    proc.wait()
    if time.time() - started < 5:
        log.warning("Chrome exited straight away. That usually means this profile "
                    "folder is already open in another window - close it and retry.")
    signed = profile.signed_in_once
    log.info("Chrome closed - profile folder %s",
             "has a session in it" if signed else "still looks EMPTY")

    # Read the channel back through Playwright. This runs whether or not a
    # channel name is configured, because it is the only thing that actually
    # answers "did that work" - a cookie file proves Chrome ran, nothing more.
    # It also proves the saved session survives under automation, which the
    # plain-Chrome sign-in cannot tell you on its own.
    seen, ok = "", True
    if signed:
        log.info("reopening the profile to see which channel it is on...")
        seen, ok = _peek_channel(profile, cfg, log)
        if not seen:
            log.warning("no channel name could be read. This profile is probably "
                        "NOT signed in - open it again and check the avatar.")
        elif not profile.channel_name:
            log.info("signed in as %r.", seen)
            log.info("Put that name in this profile's channel name field, and a "
                     "session on the wrong channel will be refused.")
        elif ok:
            log.info("OK - this profile is on %r", seen)
        else:
            log.warning("WRONG CHANNEL: this profile is on %r, but %s is for %r. "
                        "Run --setup again, switch channel from the avatar menu, "
                        "then close the window.", seen, profile.profile_id,
                        profile.channel_name)
    note = "signed in" if signed else "no cookies written"
    if seen:
        note = f"on {seen}" + ("" if ok else " - WRONG CHANNEL")
    elif signed:
        note = "cookies written, but no channel could be read"
    C.record_run(profile.profile_id,
                 {"result": "setup", "actions": 0, "watched": 0.0, "shorts": 0,
                  "minutes": round((time.time() - started) / 60, 1), "note": note})
    return {"result": "setup", "signed_in": signed and ok, "channel": seen}


def _peek_channel(profile, cfg, log) -> tuple:
    """(channel name, does it match) - a short Playwright visit, nothing watched."""
    import browser

    try:
        with browser.launched(profile, cfg, log, headless=False) as (ctx, page):
            page.goto(HOME, wait_until="domcontentloaded", timeout=45000)
            human.nap(2.2, 0.8, 1.0, 5.0)
            guard(page)
            seen = account_name(page, log)
    except Exception as e:                                  # noqa: BLE001
        log.warning("could not check the channel: %s", e)
        return "", True
    return seen, channel_matches(profile.channel_name, seen)
