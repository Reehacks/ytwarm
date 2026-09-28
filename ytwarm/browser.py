"""Launching real Chrome against a persistent profile folder, with the automation
markers taken off.

`launch_persistent_context` with `channel="chrome"` is the whole point of the
project. The alternatives both fail at the thing being built:

- A normal `launch()` context is thrown away at exit, so the Google session, the
  watch history and the cookies go with it, and every run starts as a stranger.
- Playwright's bundled Chromium is not Chrome. It has no widevine, a different
  user agent and no branding, and Google's sign-in flow treats it accordingly.

So: the installed Chrome binary, one folder per channel on disk, and everything
Chrome writes - cookies, history, local storage, the sign-in token - stays there
between runs.

Two failure modes are worth the error message they get here:

- **The folder is already open in a Chrome window.** Chrome locks a profile
  directory. Playwright's own error for this is a wall of stderr; the raised one
  says to close the window.
- **Closing has to be graceful.** Cookies live in a SQLite file that Chrome
  flushes on exit. Killing the process (or letting a traceback escape) can leave
  the session half-written, which reads later as "it logged itself out".
"""
from __future__ import annotations

import contextlib
import time
from pathlib import Path

from playwright.sync_api import Error as PWError
from playwright.sync_api import sync_playwright

import human

# Everything real Chrome does NOT pass on its own. Kept short on purpose: a long
# flag list is its own fingerprint, and most "stealth" flags in circulation only
# matter for headless Chromium, which this never runs.
STEALTH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-features=Translate",
    # Chrome blocks autoplay until a real gesture, and a video that never starts
    # is a session that watches nothing while looking like it worked.
    "--autoplay-policy=no-user-gesture-required",
]

# Playwright adds these itself. --enable-automation is the switch behind
# navigator.webdriver and the "controlled by automated test software" bar;
# --disable-extensions would strip the extensions a real profile carries.
DROP_DEFAULT_ARGS = ["--enable-automation", "--disable-extensions"]

# Real Chrome supplies window.chrome, the plugin list and the permissions API by
# itself, so none of the usual patches are needed. webdriver is the one property
# the flag above does not always clear, depending on the Chrome build.
INIT_JS = """
if (navigator.webdriver) {
  Object.defineProperty(Object.getPrototypeOf(navigator), 'webdriver',
                        {get: () => undefined});
}
"""


class ProfileBusy(RuntimeError):
    """The profile folder is locked, almost always by an open Chrome window."""


# Where Windows installers actually put Chrome. Same idea as gallery.py's
# _find_tool: without this the only symptom is [WinError 2] out of subprocess,
# which reads as "something is broken" and sends you looking in the wrong place.
CHROME_PLACES = [
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
]


def chrome_exe() -> str:
    """The installed Chrome binary, or a message saying where it was looked for."""
    import os
    import shutil
    for raw in CHROME_PLACES:
        p = Path(os.path.expandvars(raw))
        if p.exists():
            return str(p)
    found = shutil.which("chrome") or shutil.which("google-chrome")
    if found:
        return found
    raise FileNotFoundError(
        "Google Chrome was not found. Looked in:\n  " + "\n  ".join(CHROME_PLACES))


def open_plain(profile, url: str, log):
    """Start Chrome on this profile folder as a NORMAL browser, no automation.

    This is how the manual sign-in has to happen. Google refuses to sign in
    inside a browser it can see is being driven - the flow dead-ends on "this
    browser or app may not be secure", and no amount of stealth flags moves it,
    because the tell is the DevTools connection Playwright needs to exist at all.

    So the sign-in is done by a plain Chrome with nothing attached to it. The
    cookies it writes into the profile folder are ordinary cookies, and the
    warming session picks them up afterwards without ever signing in again.
    """
    import subprocess
    exe = chrome_exe()
    udd = Path(profile.user_data_dir)
    udd.mkdir(parents=True, exist_ok=True)
    cmd = [exe, f"--user-data-dir={udd}", "--no-first-run",
           "--no-default-browser-check", url]
    log.info("starting plain Chrome: %s", exe)
    return subprocess.Popen(cmd)


@contextlib.contextmanager
def launched(profile, cfg, log, headless: bool = False):
    """Yield a persistent browser context for one profile, and flush it on exit.

    `headless` is offered for an unattended run, but a headless Chrome is far
    easier to detect than a visible one - it reports a different user agent, has
    no window and fails several media checks. Leave it off unless the machine has
    no desktop.
    """
    udd = Path(profile.user_data_dir)
    udd.mkdir(parents=True, exist_ok=True)
    vp = human.viewport()
    args = list(STEALTH_ARGS)
    if headless or cfg.hide_window:
        # Chrome lets --window-size beat --start-maximized, so it is only
        # passed where the window is not maximised.
        args.append(f"--window-size={vp['width']},{vp['height']}")
    if cfg.mute_audio:
        args.append("--mute-audio")
    if not headless and cfg.hide_window:
        # Off the edge of the desktop. NOT minimised: Windows treats a minimised
        # window as occluded, Chrome then marks the page hidden, throttles its
        # timers and stops drawing frames - and a watch nobody is drawing is a
        # watch that stops counting. Off-screen keeps the page fully awake while
        # putting it somewhere you cannot click by accident.
        args.append("--window-position=-32000,-32000")
    elif not headless:
        # Maximised, the way most people keep a browser. A random size from the
        # list is a window hanging off the edge of a 1920x1080 monitor, and a
        # different window size every day on one machine is its own pattern.
        args.append("--start-maximized")

    log.info("launching Chrome  profile=%s  window=%s  proxy=%s",
             profile.profile_id,
             "maximised" if "--start-maximized" in args
             else "%dx%d" % (vp["width"], vp["height"]),
             (profile.proxy or {}).get("server", "direct"))

    with sync_playwright() as pw:
        try:
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=str(udd),
                channel=cfg.chrome_channel,
                headless=headless,
                args=args,
                ignore_default_args=DROP_DEFAULT_ARGS,
                # NO viewport. Setting one makes Playwright emulate the screen
                # too: measured here, screen.width/height came back equal to the
                # page (a monitor that changed resolution every session), with no
                # taskbar in availHeight and a window taller than the screen.
                # None of that happens on a real Windows PC.
                no_viewport=True,
                proxy=profile.proxy or None,
                accept_downloads=False,
            )
        except PWError as e:
            msg = str(e)
            if "ProcessSingleton" in msg or "already running" in msg or "SingletonLock" in msg:
                raise ProfileBusy(
                    f"{profile.profile_id}: the profile folder {udd} is already "
                    "open in a Chrome window. Close that window and run again."
                ) from e
            raise
        ctx.set_default_timeout(20000)
        ctx.add_init_script(INIT_JS)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            yield ctx, page
        finally:
            _flush(ctx, log)


def _flush(ctx, log) -> None:
    """Close the context so Chrome writes its session out, and say if it did not."""
    try:
        ctx.close()
        time.sleep(1.2)     # Chrome's own exit write of Cookies / History
        log.info("browser closed, profile flushed to disk")
    except Exception as e:  # noqa: BLE001
        log.warning("browser did not close cleanly: %s", e)
