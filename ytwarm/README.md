# ytwarm

Warms up YouTube accounts on this machine so each channel's feed learns its own
niche. One persistent Chrome profile folder per channel, a signed-in session that
survives between runs, and a browsing session that searches, watches and swipes
Shorts the way a person would.

Four channels are set up out of the box: `ch1_tech` and `ch2_tech` share niche A,
`ch3_gaming` is niche B, `ch4_finance` is niche C. Niches and profiles are both
config, so a fifth channel or a fourth niche is a form in the GUI, not a code
change.

**One thing worth knowing before you start:** automating a signed-in Google
account is against YouTube's Terms of Service, whoever owns the account. The
risk lands on the four channels themselves. The defaults here are deliberately
small for that reason - a couple of sessions a day, a few actions each, gaps
between profiles - and nothing in it likes, subscribes or comments.

## Install

Three commands, once:

```bash
cd ytwarm
python -m pip install -r requirements.txt
python -m playwright install chromium
```

The third one is only there to set the driver up - the browser this actually
drives is your own installed Google Chrome, not a downloaded Chromium.

## Step by step, first time

1. **Start the workbench.** Double-click `YT Warmer.bat` in the repo root, or:

   ```bash
   python ytwarm/app.py
   ```

   It opens `http://127.0.0.1:8650` by itself.

2. **Sign each channel in, once.** On the Profiles tab press **Set up** on
   Channel 1. A **plain** Chrome window opens on a fresh profile folder - no
   automation attached to it. In that window: sign in to that channel's Google
   account, pass 2FA, and accept the cookie and consent screens. Then **close the
   Chrome window**. Closing it is what makes Chrome write the session to disk.

   It has to be a plain Chrome. Google's sign-in refuses a browser it can see is
   being driven, and dead-ends on *"this browser or app may not be secure"*. That
   check fires on the DevTools connection Playwright needs in order to exist, so
   no stealth flag gets past it. The cookies a plain Chrome leaves behind are
   ordinary cookies, and the warming sessions use them without signing in again.

   When you close it, the profile is reopened for a few seconds under automation
   and the log says **which channel it is signed in as**. That is the real check:
   a cookie file only proves Chrome ran. If no name can be read, that profile is
   not signed in.

   Repeat for the other three. The dot next to each profile turns green once its
   folder has a session in it.

   From a terminal the same thing is:

   ```bash
   python ytwarm/main.py --setup ch1_tech
   ```

3. **Warm one profile** with **Warm now**, and watch the log pane. A session is
   8-18 minutes: homepage, a typed search from the niche list, a 40-120 second
   watch, then 4-8 Shorts with mixed retention.

4. **Warm all four** with **Warm all**. They run one at a time with a random gap
   of 4-20 minutes between them - never together. Four Chrome windows starting
   at once on one IP is the exact pattern this is avoiding.

5. **Leave it alone after that.** Two sessions per profile per day is the
   default cap, and a profile already at its cap is skipped rather than run.

## Two channels on one Google account

One Google account can own several channels, and a channel is not a login. What
belongs to the **account** is the sign-in. What belongs to the **channel** is the
feed, the subscriptions and the watch history - which is the whole thing being
warmed here.

So two channels on one account are still **two profile folders**, each signed
into the same account, and each switched to its own channel:

1. Run **Set up** for that profile.
2. In the Chrome window, click your avatar (top right) → **Switch account** →
   pick the channel.
3. Check the avatar is now that channel, then close the window. The log then
   reports the channel it read back.

The choice is a cookie, so it stays in that folder. The other folder keeps its
own channel.

**Fill in `channel_name` for each profile** (Settings tab, or the field in
`config.json`), spelled exactly as YouTube prints it in the account menu. Then:

- Setup reports which channel the profile landed on, and says whether it matches.
- Every warming session reads the channel *before it watches anything*, and
  stops with `wrong_channel` if it is not the right one.

Without it nothing checks, and a folder left on the wrong channel trains the
wrong feed for weeks without a single visible symptom.

## Getting the window out of the way

A session opens a real Chrome window on your desktop. Tick **keep the Chrome
window off-screen** in Settings (`hide_window`) and it opens at -32000,-32000
instead - nowhere you can click. It still has a taskbar button, so it is findable
if you want it.

**Off-screen, not headless and not minimised**, and the difference matters:

| | what the page thinks | frames drawn |
| --- | --- | --- |
| normal window | visible | 146 / second |
| **off-screen** | **visible** | **145 / second** |
| minimised | hidden - Chrome throttles it | almost none |
| headless | visible, but easy for YouTube to spot | - |

Those are measured numbers from this machine. A minimised window is treated as
occluded: Chrome marks the page hidden, throttles its timers and stops drawing,
so the watch that is the whole point stops counting. Off-screen is a fully awake
window that happens to be nowhere.

Leave it off for your first few runs. Watching one session go past is the fastest
way to see that it works.

## VPNs

Turn it off for this, or keep the same server every time. A commercial VPN exits
through a datacenter address, which is one of the strongest "this is a bot"
signals Google has - shared with thousands of other users, and the reason a
`/sorry/` page or an endless sign-in loop shows up. A home connection is a
residential address and is treated as an ordinary person. Whichever you pick,
**keep each channel on the same connection**: an account whose location jumps
around between sessions is its own flag. The per-profile `proxy` setting exists
for the other case - a dedicated residential proxy per channel - not for a
consumer VPN shared by everyone.

## Every day after that

One click on **Warm all** in the workbench, or:

```bash
python ytwarm/main.py --warm-all
```

Once a day is enough. Twice per channel is the hard cap.

## The daily schedule

`Warming Schedule.bat` in the repo root drives a Windows scheduled task. Install
it once:

```bash
"Warming Schedule.bat" install
```

That fires daily at 10:00 **plus a random delay of up to 4 hours**, so no two
days start at the same minute. Another time and window:

```bash
"Warming Schedule.bat" install -At 19:30 -WindowHours 3
```

Then:

| command | what happens |
| --- | --- |
| `"Warming Schedule.bat"` | shows the state, next run and last result |
| `"Warming Schedule.bat" pause` | stops it running, keeps the task |
| `"Warming Schedule.bat" resume` | starts it running again |
| `"Warming Schedule.bat" run-now` | one round this second, for testing |
| `"Warming Schedule.bat" remove` | deletes the task |

The workbench shows the same state at the top of the Profiles tab, with a
**Pause / Resume** button. Installing and removing stay on the command line,
because both need a time and a decision.

Three things the task does on purpose:

- **It runs as you, only while you are signed in to Windows.** A warming session
  opens a real Chrome window, and a task running with nobody logged on would have
  no desktop to draw it on.
- **It may take the day off.** `skip_day_chance` (0.2 by default) means roughly
  one run in five is skipped. A run every single day without fail is a machine.
  Only scheduled runs roll this - the **Warm all** button always runs.
- **A missed day is caught up, not lost.** If the PC was off at the trigger time,
  the run happens once it is back.

If Windows refuses to create the task, run the same command from a window opened
with **Run as administrator**.

## The CLI

```bash
python main.py --list                 # profiles, whether set up, sessions today
python main.py --setup ch3_gaming     # visible Chrome, manual sign-in
python main.py --warm ch3_gaming      # one session
python main.py --warm-all             # all enabled, one at a time, with gaps
python main.py --warm ch1_tech --force    # ignore the daily cap and the on/off flag
python main.py --config other.json --list # a different config file
```

## Config

`config.json` sits next to the code and the GUI's Settings tab edits it. Nothing
in it needs editing by hand.

| key | what it does |
| --- | --- |
| `niches` | keyword pools. Two profiles pointing at one niche build the same interest profile - that is how Channel 1 and Channel 2 stay matched. Keywords **rotate**: none comes back until the whole list has been used, so 25 words at about two searches a day means a phrase returns roughly every two weeks rather than three times a week. Each profile keeps its own place in the rotation, so two channels on one niche never type the same query in the same order. |
| `profiles[].profile_id` | the name used on the command line. |
| `profiles[].user_data_dir` | the Chrome profile folder. **Never shared between two profiles** - the config refuses it, because one folder holds one Google account. |
| `profiles[].niche` | which keyword pool. A `keywords` list on the profile itself overrides it. |
| `profiles[].channel_name` | the YouTube channel this profile is for, spelled exactly as YouTube shows it. Every session reads the channel off the account menu and **stops** if it is a different one. Leave it blank and nothing is checked. |
| `profiles[].proxy` | `{"server": "http://host:port", "username": "...", "password": "..."}`, or `null` for a direct connection. |
| `profiles[].daily_budget` | `min/max_minutes` and `min/max_actions` per session, and `sessions_per_day` as the cap. Whichever of time or actions runs out first ends the session. |
| `watch_seconds` | how long one video is watched, drawn between the two. |
| `shorts_per_session` | how many Shorts per Shorts visit. |
| `interaction_chance` | how often a small thing happens: pause, mute, a glance at the comments. |
| `gap_minutes` | the wait between profiles in `--warm-all`. |
| `mute_audio` | on by default. Four warming sessions a day with sound is not something you want. |
| `hide_window` | puts the Chrome window off the edge of the desktop, so it cannot be clicked or closed by accident. See below. |

## What it does, in order

1. Homepage, a real pause, a scroll.
2. One keyword from the niche list, **typed into the search box** - not a
   `?search_query=` URL. The typed search is the event that teaches the account.
3. A result from the first six, watched 40-120 seconds, with the odd pause,
   mute or scroll into the comments.
4. The Shorts feed. About a third are swiped away after 2-4 seconds; the rest
   are watched most of the way, and a short one is sometimes allowed to loop.
5. More of the same until the session's time or action budget runs out.
6. A clean close, so Chrome flushes cookies and history to the profile folder.

**It never likes, subscribes or comments.** None of those are needed to teach a
recommendation profile, and all three are public actions from a real account.

## When it stops early

- **Bot check** - anything that says "confirm you're not a bot", a `/sorry/`
  redirect, a consent wall. The session stops, saves a screenshot in
  `screenshots/`, and the profile is left exactly as it was. Nothing tries to
  answer the challenge; clicking through a verification automatically is how a
  warm profile becomes a flagged one.
- **Signed out** - no avatar in the top bar. Warming a signed-out profile
  records nothing at all, so it stops and asks for `--setup` again.
- **Wrong channel** - the account menu names a different channel than
  `channel_name`. It stops before watching anything, because a wrong-channel
  session trains a feed nobody looks at and says nothing about it.
- **Profile busy** - Chrome locks a profile folder, so the session refuses to
  start while that folder is open in a window of your own.

All three land in `logs/<profile_id>.log` with timestamps, and in the GUI's log
pane.

## Files

```
config.json        profiles, niches, budgets - the GUI edits this
config.py          loading, validating, per-profile state, logging
browser.py         persistent Chrome, stealth flags, graceful close
human.py           clamped normal delays, bezier mouse paths, scrolling, typing
workflows.py       the session: search, watch, shorts, micro-interactions, guards
main.py            the CLI
app.py             the workbench web server (port 8650)
index.html         the workbench page
test_ytwarm.py     self-checks: config guards, the daily cap, the human engine
profiles/          one Chrome profile folder per channel   (gitignored)
state/             one run history per channel             (gitignored)
logs/, screenshots/                                        (gitignored)
```

`profiles/` grows to a few hundred MB per channel once Chrome has run. It holds
live Google session cookies, so it is gitignored and should stay that way.

## Checking it still works

```bash
python test_ytwarm.py
```

No browser and no network. It covers the two guards that fail silently - two
profiles sharing a Chrome folder, and the daily cap - plus the timing, path and
typing maths in `human.py`.
