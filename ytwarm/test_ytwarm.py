"""Self-checks for the parts that can be wrong silently. No browser, no network.

    python test_ytwarm.py

The config guards and the daily cap are here because both fail QUIETLY: a shared
`user_data_dir` looks fine until two channels turn out to be one account, and a
cap that does not count only shows up as a profile warmed six times in a day.

`human` is checked against a stub page rather than a real one. The point is not
that Playwright works - it is that the scroll really varies its steps and that
typing emits every character even when the typo branch fires.
"""
from __future__ import annotations

import json
import signal
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config as C          # noqa: E402
import human                # noqa: E402

GOOD = {
    "niches": {"A": {"keywords": ["one thing", "two thing"]}},
    "profiles": [
        {"profile_id": "a", "niche": "A", "user_data_dir": "profiles/a"},
        {"profile_id": "b", "niche": "A", "user_data_dir": "profiles/b"},
    ],
}


def write(tmp: Path, raw: dict) -> Path:
    p = tmp / "config.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    return p


def refused(tmp: Path, raw: dict, needle: str) -> None:
    try:
        C.load(write(tmp, raw))
    except C.ConfigError as e:
        assert needle.lower() in str(e).lower(), f"wrong reason: {e}"
        return
    raise AssertionError(f"should have been refused: {needle}")


def test_config(tmp: Path) -> None:
    cfg = C.load(write(tmp, GOOD))
    assert [p.profile_id for p in cfg.profiles] == ["a", "b"]
    # Two profiles on one niche share the keyword pool - this is how channel 1
    # and channel 2 grow the same interest profile.
    assert cfg.profile("a").keywords == cfg.profile("b").keywords == ["one thing", "two thing"]
    assert cfg.profile("a").user_data_dir.is_absolute()
    assert cfg.profile("a").daily_budget["min_minutes"] == C.DEFAULT_BUDGET["min_minutes"]
    assert cfg.watch_seconds == (40, 120) and cfg.shorts_per_session == (4, 8)

    own = json.loads(json.dumps(GOOD))
    own["profiles"][0]["keywords"] = ["its own"]
    assert C.load(write(tmp, own)).profile("a").keywords == ["its own"]

    same = json.loads(json.dumps(GOOD))
    same["profiles"][1]["user_data_dir"] = "profiles/a"
    refused(tmp, same, "share user_data_dir")

    dup = json.loads(json.dumps(GOOD))
    dup["profiles"][1]["profile_id"] = "a"
    refused(tmp, dup, "duplicate")

    ghost = json.loads(json.dumps(GOOD))
    ghost["profiles"][0]["niche"] = "Z"
    refused(tmp, ghost, "not in 'niches'")

    backwards = json.loads(json.dumps(GOOD))
    backwards["profiles"][0]["daily_budget"] = {"min_minutes": 20, "max_minutes": 5}
    refused(tmp, backwards, "above max_minutes")

    badproxy = json.loads(json.dumps(GOOD))
    badproxy["profiles"][0]["proxy"] = {"username": "u"}
    refused(tmp, badproxy, "proxy needs a 'server'")

    refused(tmp, {"profiles": []}, "non-empty list")
    print("config guards: ok")


def test_cookie_paths(tmp: Path) -> None:
    """Chrome 96 moved the cookie jar, and the old path reported a good sign-in
    as "no cookies written" - a failure message for something that worked."""
    p = C.load(write(tmp, GOOD)).profile("a")
    assert not p.signed_in_once, "an empty folder is not a set-up profile"
    jar = p.user_data_dir / "Default" / "Network" / "Cookies"
    jar.parent.mkdir(parents=True, exist_ok=True)
    jar.write_bytes(b"not a real jar")
    assert p.signed_in_once, "Default/Network/Cookies is where Chrome 96+ writes"
    print("cookie jar detection: ok")


def test_keyword_rotation(tmp: Path) -> None:
    """No keyword may come back until the whole list has been used.

    The old `random.choice` typed the same phrase several times a week on a short
    list, which teaches the feed nothing after the first couple of times.
    """
    C.STATE_DIR = tmp / "rot"
    words = [f"w{i}" for i in range(25)]
    raw = json.loads(json.dumps(GOOD))
    raw["niches"]["A"]["keywords"] = words
    cfg = C.load(write(tmp, raw))
    a, b = cfg.profile("a"), cfg.profile("b")

    cycle = [C.next_keyword(a) for _ in range(25)]
    assert sorted(cycle) == sorted(words), "one full cycle must use every word once"
    assert cycle != words, "the order must be shuffled, not the list order"
    assert C.next_keyword(a) in words, "the next cycle starts over"

    # Two profiles on ONE niche must not walk the same order from one address.
    other = [C.next_keyword(b) for _ in range(25)]
    assert sorted(other) == sorted(words)
    assert other != cycle, "profiles sharing a niche must rotate independently"

    # Editing the niche mid-cycle drops the words that went away, and does not
    # strand the rotation or repeat what is left.
    raw["niches"]["A"]["keywords"] = words[:5] + ["brand new"]
    small = C.load(write(tmp, raw)).profile("a")
    got = {C.next_keyword(small) for _ in range(6)}
    assert got == set(words[:5] + ["brand new"]), got
    print("keyword rotation: ok")


def test_state(tmp: Path) -> None:
    C.STATE_DIR = tmp / "state"
    cfg = C.load(write(tmp, GOOD))
    p = cfg.profile("a")
    p.daily_budget["sessions_per_day"] = 2
    assert C.sessions_today("a") == 0 and not C.over_daily_cap(p)
    C.record_run("a", {"result": "ok", "actions": 3})
    assert C.sessions_today("a") == 1 and not C.over_daily_cap(p)
    C.record_run("a", {"result": "ok", "actions": 2})
    assert C.over_daily_cap(p), "the cap must bite at sessions_per_day"
    # A failed session must not spend the day's budget.
    C.record_run("b", {"result": "error", "actions": 0})
    assert C.sessions_today("b") == 0
    # State is per profile: a's runs cannot show up under b.
    assert C.load_state("b")["runs"][0]["result"] == "error"
    assert len(C.load_state("a")["runs"]) == 2
    print("per-profile state and the daily cap: ok")


class FakeMouse:
    def __init__(self, rec): self.rec = rec
    def move(self, x, y): self.rec["moves"].append((x, y))
    def click(self, x, y): self.rec["clicks"].append((x, y))
    def wheel(self, dx, dy): self.rec["wheel"].append(dy)


class FakeKeyboard:
    def __init__(self, rec): self.rec = rec
    def type(self, ch): self.rec["typed"].append(ch)
    def press(self, key): self.rec["keys"].append(key)


class FakePage:
    def __init__(self):
        self.rec = {"moves": [], "clicks": [], "wheel": [], "typed": [], "keys": []}
        self.mouse, self.keyboard = FakeMouse(self.rec), FakeKeyboard(self.rec)
    viewport_size = {"width": 1440, "height": 900}


def test_human() -> None:
    draws = [human.gauss(1.0, 5.0) for _ in range(4000)]
    assert min(draws) >= 0.35 and max(draws) <= 2.2, "gauss must stay inside its clamps"
    assert 0.02 < statistics.stdev(draws) , "a clamped draw is still a draw, not a constant"

    pts = human.bezier((0, 0), (300, 200), 20)
    assert len(pts) == 20
    assert abs(pts[-1][0] - 300) < 1e-6 and abs(pts[-1][1] - 200) < 1e-6
    off = max(abs(y - (2 / 3) * x) for x, y in pts)
    assert off > 1.0, "the path must bow off the straight line"

    page = FakePage()
    moved = human.scroll(page, 600)
    assert moved >= 600 and len(page.rec["wheel"]) > 3
    assert len(set(page.rec["wheel"])) > 2, "every wheel step the same size is a script"

    page = FakePage()
    text = "best budget laptop 2026"
    human.type_text(page, text)
    # Every typo costs one extra character and exactly one Backspace, so the
    # arithmetic has to balance - a dropped correction would leave the query
    # misspelled in the search box and nothing would say so.
    backspaces = [k for k in page.rec["keys"] if k == "Backspace"]
    assert page.rec["keys"] == backspaces, "typing presses nothing but Backspace"
    assert len(page.rec["typed"]) == len(text) + len(backspaces)
    print("human timing, path and typing: ok")


def test_channel_match() -> None:
    try:
        import workflows
    except ImportError as e:
        print(f"channel guard: SKIPPED (playwright not installed: {e})")
        return
    m = workflows.channel_matches
    assert m("ChannelOne", "ChannelOne")
    assert m("ChannelOne", "  channelone ")      # case and padding
    assert m("Channel Two Name", "Channel  Two\nName")       # YouTube's own spacing
    assert not m("ChannelOne", "ChannelTwo")
    assert not m("ChannelOne", "")                    # unread name is not a pass
    # No channel set on the profile means the guard is off, not that it fails.
    assert m("", "anything at all") and m("   ", "")
    print("channel guard: ok")


def test_budget() -> None:
    try:
        import workflows
    except ImportError as e:
        print(f"budget: SKIPPED (playwright not installed: {e})")
        return
    b = workflows.Budget({"min_minutes": 10, "max_minutes": 10,
                          "min_actions": 3, "max_actions": 3})
    assert b.left()
    for _ in range(3):
        b.spend()
    assert not b.left(), "the action count must end the session"
    b2 = workflows.Budget({"min_minutes": 0.0001, "max_minutes": 0.0001,
                           "min_actions": 99, "max_actions": 99})
    import time
    time.sleep(0.02)
    assert not b2.left(), "the clock must end the session too"
    print("session budget: ok")


def test_stop_unwinds() -> None:
    """Stop must let the session close Chrome, not kill it where it stands.

    A killed session leaves Chrome orphaned, and an orphaned Chrome keeps the
    profile folder locked - so the NEXT session refuses to start and nothing on
    screen says why. The child here sleeps in slices the way a watch loop does.
    """
    import subprocess
    if not hasattr(signal, "SIGBREAK"):
        print("stop path: SKIPPED (not Windows)")
        return
    import app
    child = ("import sys, time\n"
             f"sys.path.insert(0, r'{Path(__file__).resolve().parent}')\n"
             "import main\n"
             "main._catch_break()\n"
             "try:\n"
             "    print('working', flush=True)\n"
             "    [time.sleep(3) for _ in range(30)]\n"
             "except KeyboardInterrupt:\n"
             "    print('CLEANUP RAN', flush=True)\n"
             "    sys.exit(7)\n")
    p = subprocess.Popen([sys.executable, "-u", "-c", child],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    time.sleep(1.5)
    app._stop(p)
    out = p.stdout.read()
    assert "CLEANUP RAN" in out, "the child was killed without unwinding"
    assert p.returncode == 7, f"expected a clean exit, got {p.returncode}"
    print("stop path: ok")


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        test_config(tmp)
        test_cookie_paths(tmp)
        test_keyword_rotation(tmp)
        test_state(tmp)
    test_human()
    test_channel_match()
    test_budget()
    test_stop_unwinds()
    print("all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
