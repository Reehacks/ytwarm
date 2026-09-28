"""Config loader, validator and per-profile state for ytwarm.

One JSON file, no PyYAML - the config is four objects and a keyword list, and a
parser dependency for that is a dependency to keep alive for nothing.

Two things in here are guards rather than parsing, and both exist because the
failure is only noticed weeks later:

- **Two profiles may never share a `user_data_dir`.** One cookie jar holding two
  Google accounts means both channels are whichever signed in last, and the
  warming history all lands on that one.
- **State is per profile, never one shared file.** Every channel has its own
  `state/<profile_id>.json`, so a session cap on one cannot silently spend
  another's.

`niche` is the sharing mechanism: profiles pointing at the same niche draw from
the same keyword pool, which is what makes Channel 1 and Channel 2 grow the same
interest profile. A profile may also carry its own `keywords` list, which wins
over the niche - that is the escape hatch for a one-off channel that fits no
niche, and it is why nothing here invents a niche per channel.
"""
from __future__ import annotations

import json
import logging
import os
import random
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.environ.get("YTWARM_CONFIG") or (ROOT / "config.json"))
STATE_DIR = ROOT / "state"
LOG_DIR = ROOT / "logs"
SHOT_DIR = ROOT / "screenshots"

BUDGET_KEYS = ("min_minutes", "max_minutes", "min_actions", "max_actions")

# Where Chrome keeps the cookie jar inside a user-data-dir. Newest layout first;
# the bare ones are for a profile written by a much older Chrome.
COOKIE_FILES = ("Default/Network/Cookies", "Default/Cookies",
                "Profile 1/Network/Cookies", "Network/Cookies", "Cookies")
DEFAULT_BUDGET = {"min_minutes": 8, "max_minutes": 16, "min_actions": 3,
                  "max_actions": 5, "sessions_per_day": 2}


class ConfigError(Exception):
    """The config file is wrong in a way that would waste a whole session."""


@dataclass
class Profile:
    profile_id: str
    user_data_dir: Path
    keywords: list
    daily_budget: dict
    label: str = ""
    niche: str = ""
    channel_name: str = ""
    proxy: dict | None = None
    enabled: bool = True

    @property
    def name(self) -> str:
        return self.label or self.profile_id

    @property
    def signed_in_once(self) -> bool:
        """True once Chrome has written a cookie jar into this profile folder.

        Not proof of a live Google session - a logged-out profile has a cookie
        file too, and an empty one is 48KB - but it is the difference between
        "never set up" and "set up", which is what the GUI dot shows.

        **Chrome 96 moved the jar to `Default/Network/Cookies`.** Checking only
        the old `Default/Cookies` reported "no cookies written" after a perfectly
        good sign-in, which reads as "it failed" when nothing had.
        """
        d = self.user_data_dir
        return any((d / rel).exists() for rel in COOKIE_FILES)


@dataclass
class Config:
    path: Path
    raw: dict
    profiles: list = field(default_factory=list)

    @property
    def niches(self) -> dict:
        return self.raw.get("niches") or {}

    @property
    def chrome_channel(self) -> str:
        return self.raw.get("chrome_channel") or "chrome"

    @property
    def mute_audio(self) -> bool:
        return bool(self.raw.get("mute_audio", True))

    @property
    def interaction_chance(self) -> float:
        return float(self.raw.get("interaction_chance", 0.12))

    @property
    def gap_minutes(self) -> tuple:
        lo, hi = (self.raw.get("gap_minutes") or [4, 20])[:2]
        return float(lo), float(hi)

    @property
    def hide_window(self) -> bool:
        """Put the Chrome window off the edge of the desktop instead of on it.

        Not headless, and not minimised. Both of those stop the watch counting:
        headless is easy for YouTube to spot, and a minimised window is treated
        as occluded, so Chrome marks the page hidden and throttles it. Off-screen
        leaves the page fully visible to itself while being nowhere you can click.
        """
        return bool(self.raw.get("hide_window", False))

    @property
    def skip_day_chance(self) -> float:
        """How often an UNATTENDED run takes the day off.

        Only `--scheduled` runs roll this. A run every single day without fail is
        a machine, and the schedule is the one part of this nobody is watching.
        A button press in the GUI always runs, because a button that sometimes
        does nothing is a broken button.
        """
        return float(self.raw.get("skip_day_chance", 0.2))

    @property
    def watch_seconds(self) -> tuple:
        lo, hi = (self.raw.get("watch_seconds") or [40, 120])[:2]
        return float(lo), float(hi)

    @property
    def shorts_per_session(self) -> tuple:
        lo, hi = (self.raw.get("shorts_per_session") or [4, 8])[:2]
        return int(lo), int(hi)

    def profile(self, profile_id: str) -> Profile:
        for p in self.profiles:
            if p.profile_id == profile_id:
                return p
        known = ", ".join(p.profile_id for p in self.profiles) or "none"
        raise ConfigError(f"no profile {profile_id!r}. Known: {known}")

    def enabled(self) -> list:
        return [p for p in self.profiles if p.enabled]


def load(path: Path | str | None = None) -> Config:
    path = Path(path) if path else CONFIG_PATH
    if not path.exists():
        raise ConfigError(f"config not found: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ConfigError(f"{path.name} is not valid JSON: {e}") from e
    cfg = Config(path=path, raw=raw, profiles=_parse_profiles(raw, path))
    for d in (STATE_DIR, LOG_DIR, SHOT_DIR):
        d.mkdir(parents=True, exist_ok=True)
    return cfg


def save(cfg_raw: dict, path: Path | str | None = None) -> Config:
    """Validate a whole config dict, then write it. Used by the GUI's editor.

    Validation runs BEFORE the write, so a bad edit in the browser cannot leave
    the file on disk unparseable.
    """
    path = Path(path) if path else CONFIG_PATH
    _parse_profiles(cfg_raw, path)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg_raw, indent=2), encoding="utf-8")
    tmp.replace(path)
    return load(path)


def _parse_profiles(raw: dict, path: Path) -> list:
    if not isinstance(raw, dict):
        raise ConfigError("the config must be a JSON object")
    niches = raw.get("niches") or {}
    rows = raw.get("profiles")
    if not isinstance(rows, list) or not rows:
        raise ConfigError("'profiles' must be a non-empty list")

    out, seen_ids, seen_dirs = [], {}, {}
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ConfigError(f"profile #{i + 1} is not an object")
        pid = str(row.get("profile_id") or "").strip()
        if not pid:
            raise ConfigError(f"profile #{i + 1} has no profile_id")
        if pid in seen_ids:
            raise ConfigError(f"duplicate profile_id {pid!r}")
        seen_ids[pid] = True

        niche = str(row.get("niche") or "").strip()
        keywords = [str(k).strip() for k in (row.get("keywords") or []) if str(k).strip()]
        if not keywords:
            if niche and niche in niches:
                keywords = [str(k).strip() for k in (niches[niche].get("keywords") or [])
                            if str(k).strip()]
            elif niche:
                raise ConfigError(f"{pid}: niche {niche!r} is not in 'niches'")
        if not keywords:
            raise ConfigError(f"{pid}: no keywords - set 'niche' or a 'keywords' list")

        udd = str(row.get("user_data_dir") or "").strip()
        if not udd:
            raise ConfigError(f"{pid}: no user_data_dir")
        udd_path = Path(udd)
        if not udd_path.is_absolute():
            udd_path = (path.parent / udd_path).resolve()
        key = str(udd_path).lower()
        if key in seen_dirs:
            raise ConfigError(
                f"{pid} and {seen_dirs[key]} share user_data_dir {udd_path}. "
                "One Chrome profile holds one Google account - give every "
                "channel its own folder.")
        seen_dirs[key] = pid

        budget = dict(DEFAULT_BUDGET)
        budget.update({k: v for k, v in (row.get("daily_budget") or {}).items()
                       if v is not None})
        for k in BUDGET_KEYS:
            try:
                budget[k] = float(budget[k]) if "minutes" in k else int(budget[k])
            except (TypeError, ValueError):
                raise ConfigError(f"{pid}: daily_budget.{k} must be a number") from None
            if budget[k] <= 0:
                raise ConfigError(f"{pid}: daily_budget.{k} must be above 0")
        if budget["min_minutes"] > budget["max_minutes"]:
            raise ConfigError(f"{pid}: min_minutes is above max_minutes")
        if budget["min_actions"] > budget["max_actions"]:
            raise ConfigError(f"{pid}: min_actions is above max_actions")
        budget["sessions_per_day"] = int(budget.get("sessions_per_day") or 0)

        proxy = row.get("proxy") or None
        if proxy is not None:
            if not isinstance(proxy, dict) or not str(proxy.get("server") or "").strip():
                raise ConfigError(
                    f"{pid}: proxy needs a 'server' like "
                    '{"server": "http://host:port", "username": "", "password": ""}')
            proxy = {k: v for k, v in proxy.items() if v not in ("", None)}

        out.append(Profile(
            profile_id=pid,
            user_data_dir=udd_path,
            keywords=keywords,
            daily_budget=budget,
            label=str(row.get("label") or "").strip(),
            niche=niche,
            channel_name=str(row.get("channel_name") or "").strip(),
            proxy=proxy,
            enabled=bool(row.get("enabled", True)),
        ))
    return out


# ---------------------------------------------------------------- state


def state_path(profile_id: str) -> Path:
    return STATE_DIR / f"{profile_id}.json"


def load_state(profile_id: str) -> dict:
    p = state_path(profile_id)
    if not p.exists():
        return {"runs": []}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"runs": []}


def save_state(profile_id: str, st: dict) -> dict:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state_path(profile_id).write_text(json.dumps(st, indent=2), encoding="utf-8")
    return st


def record_run(profile_id: str, summary: dict) -> dict:
    """Append one session to this profile's own state file, newest last."""
    st = load_state(profile_id)
    runs = st.setdefault("runs", [])
    runs.append({"at": datetime.now().isoformat(timespec="seconds"), **summary})
    del runs[:-60]
    return save_state(profile_id, st)


def next_keyword(profile: Profile) -> str:
    """One keyword, chosen so none repeats until the whole list has been used.

    `random.choice` over the list every time was the old behaviour, and with a
    short list it typed the same phrase several times a week - which teaches the
    feed nothing after the first couple of times, and is not a shape a person's
    search history has.

    The used list is PER PROFILE, so two channels sharing a niche work through
    the same words in different orders rather than typing identical queries in
    identical order from one address.

    The order is reshuffled every cycle rather than fixed, and the used list is
    filtered against the CURRENT keywords, so editing the niche in the GUI
    neither repeats a word nor strands the rotation on words that are gone.
    """
    st = load_state(profile.profile_id)
    used = [k for k in (st.get("used_keywords") or []) if k in profile.keywords]
    left = [k for k in profile.keywords if k not in used]
    if not left:                       # a full cycle is done - start a new one
        used, left = [], list(profile.keywords)
    pick = random.choice(left)
    st["used_keywords"] = used + [pick]
    save_state(profile.profile_id, st)
    return pick


def sessions_today(profile_id: str) -> int:
    today = date.today().isoformat()
    runs = load_state(profile_id).get("runs") or []
    return sum(1 for r in runs if str(r.get("at", "")).startswith(today)
               and r.get("result") != "error")


def over_daily_cap(profile: Profile) -> bool:
    cap = int(profile.daily_budget.get("sessions_per_day") or 0)
    return bool(cap) and sessions_today(profile.profile_id) >= cap


# ---------------------------------------------------------------- logging


def setup_logging(profile_id: str = "ytwarm", verbose: bool = True) -> logging.Logger:
    """Timestamped logging to the console and to logs/<profile_id>.log."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger(f"ytwarm.{profile_id}")
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    log.propagate = False
    if log.handlers:
        return log
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    for h in (logging.StreamHandler(), logging.FileHandler(LOG_DIR / f"{profile_id}.log",
                                                           encoding="utf-8")):
        h.setFormatter(fmt)
        log.addHandler(h)
    return log


def shot_path(profile_id: str, tag: str) -> Path:
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return SHOT_DIR / f"{profile_id}_{tag}_{stamp}.png"


def random_gap_seconds(cfg: Config) -> float:
    lo, hi = cfg.gap_minutes
    return random.uniform(lo, hi) * 60
