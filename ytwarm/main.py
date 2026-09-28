"""ytwarm CLI - warm YouTube profiles so each channel's feed learns its niche.

    python main.py --list
    python main.py --setup ch1_tech          # sign in by hand, once per channel
    python main.py --warm ch1_tech
    python main.py --warm-all                # every enabled profile, one at a time

`--warm-all` runs profiles SEQUENTIALLY with a random gap between them. Not for
politeness: four Chrome windows on one machine and one IP, starting within a
second of each other, is the pattern being avoided in the first place. The gap
comes from `gap_minutes` in the config.
"""
from __future__ import annotations

import argparse
import random
import sys
import time

import config as C


def _require_playwright() -> None:
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        sys.exit("Playwright is not installed. Run:\n"
                 "    pip install playwright\n"
                 "    playwright install chromium\n"
                 "(Chrome itself is your own installed one - nothing to download "
                 "for that, but the install step is what sets up the driver.)")


def cmd_list(cfg) -> int:
    print(f"{'profile':<14} {'niche':<6} {'on':<4} {'set up':<7} {'today':<6} last")
    for p in cfg.profiles:
        runs = (C.load_state(p.profile_id).get("runs") or [])
        last = runs[-1] if runs else None
        last_txt = f"{last['at']}  {last.get('result')}" if last else "-"
        print(f"{p.profile_id:<14} {p.niche or '-':<6} "
              f"{'yes' if p.enabled else 'no':<4} "
              f"{'yes' if p.signed_in_once else 'NO':<7} "
              f"{C.sessions_today(p.profile_id):<6} {last_txt}")
        print(f"{'':<14} {len(p.keywords)} keywords, {p.user_data_dir}")
    return 0


def cmd_setup(cfg, profile_id: str) -> int:
    import workflows
    p = cfg.profile(profile_id)
    log = C.setup_logging(p.profile_id)
    log.info("SETUP %s - a visible Chrome window is opening", p.name)
    got = workflows.setup(p, cfg, log)
    return 0 if got.get("signed_in") else 1


def cmd_warm(cfg, profile_id: str, headless: bool, force: bool) -> int:
    import workflows
    p = cfg.profile(profile_id)
    log = C.setup_logging(p.profile_id)
    if not p.enabled and not force:
        log.info("%s is disabled in the config - skipping", p.profile_id)
        return 0
    if not p.signed_in_once:
        log.error("%s has no Chrome profile yet. Run:  python main.py --setup %s",
                  p.profile_id, p.profile_id)
        return 1
    if C.over_daily_cap(p) and not force:
        log.info("%s already had %d sessions today (cap %d) - skipping",
                 p.profile_id, C.sessions_today(p.profile_id),
                 p.daily_budget["sessions_per_day"])
        return 0
    got = workflows.warm(p, cfg, log, headless=headless)
    return 0 if got["result"] == "ok" else 1


def cmd_warm_all(cfg, headless: bool, force: bool, scheduled: bool = False) -> int:
    log = C.setup_logging("warm-all")
    if scheduled and not force and random.random() < cfg.skip_day_chance:
        log.info("taking today off (%.0f%% chance) - a run every single day "
                 "without fail is a machine", cfg.skip_day_chance * 100)
        return 0
    todo = [p for p in cfg.enabled() if force or not C.over_daily_cap(p)]
    todo = [p for p in todo if p.signed_in_once] or todo
    random.shuffle(todo)                      # a fixed order is a pattern too
    if not todo:
        log.info("nothing to do - every profile is capped, disabled or not set up")
        return 0
    log.info("warming %d profiles: %s", len(todo),
             ", ".join(p.profile_id for p in todo))
    worst = 0
    for i, p in enumerate(todo):
        worst |= cmd_warm(cfg, p.profile_id, headless, force)
        if i < len(todo) - 1:
            gap = C.random_gap_seconds(cfg)
            log.info("waiting %.1f minutes before the next profile", gap / 60)
            time.sleep(gap)
    return worst


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="ytwarm", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="a config.json other than the one next to this file")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true", help="show profiles and their state")
    g.add_argument("--setup", metavar="PROFILE_ID",
                   help="open a visible Chrome to sign in by hand, once per channel")
    g.add_argument("--warm", metavar="PROFILE_ID", help="run one warming session")
    g.add_argument("--warm-all", action="store_true",
                   help="every enabled profile, one at a time, with gaps")
    ap.add_argument("--headless", action="store_true",
                    help="no window (easier to detect - only for a machine with no desktop)")
    ap.add_argument("--force", action="store_true",
                    help="ignore the per-day session cap and the enabled flag")
    ap.add_argument("--scheduled", action="store_true",
                    help="this run came from the daily task, not a person - it may "
                         "take the day off (skip_day_chance)")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = C.load(args.config)
    except C.ConfigError as e:
        sys.exit(f"config problem: {e}")
    try:
        if args.list:
            return cmd_list(cfg)
        _require_playwright()
        if args.setup:
            return cmd_setup(cfg, args.setup)
        if args.warm:
            return cmd_warm(cfg, args.warm, args.headless, args.force)
        return cmd_warm_all(cfg, args.headless, args.force, args.scheduled)
    except C.ConfigError as e:
        # An unknown profile id is the common one, and its message already lists
        # the known ids - a traceback would bury that.
        sys.exit(str(e))


def _catch_break() -> None:
    """Turn a Ctrl-Break into a KeyboardInterrupt, so a stop can unwind.

    Windows' Ctrl-Break is how the workbench asks a session to stop - Ctrl-C
    cannot be used, because a child in its own process group ignores it. But
    Python does NOT convert Ctrl-Break by itself: with no handler the process
    dies on the spot with 0xC000013A, the `with` blocks never run their finally,
    and Chrome is left orphaned holding the profile folder's lock.

    KeyboardInterrupt lands in the main thread, which is where the session runs,
    so it unwinds through `browser.launched` and closes the browser properly.
    """
    import signal
    if not hasattr(signal, "SIGBREAK"):          # not Windows
        return

    def _raise(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGBREAK, _raise)


if __name__ == "__main__":
    _catch_break()
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        # Ctrl-C during a session: the browser context manager still runs its
        # close, which is what flushes the cookies. Say so, do not just die.
        print("\ninterrupted - closing down", file=sys.stderr)
        raise SystemExit(130) from None
