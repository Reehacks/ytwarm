# ytwarm - YouTube channel warmer

Warms up YouTube channels so each channel's feed learns its own niche. Every
channel gets its own Chrome profile folder. A session searches, watches videos
and swipes Shorts the way a person would. It never likes, subscribes or comments.

> **Read this first.** Automating a signed-in Google account is against
> YouTube's Terms of Service. Any risk lands on your own channels. The defaults
> are small on purpose. Use it at your own risk.

This is the setup guide. The full manual, with every option explained, is in
[`ytwarm/README.md`](ytwarm/README.md).

## What you need

- **Windows.** The launchers are `.bat` files and the daily schedule is a
  Windows scheduled task.
- **Python 3.10 or newer**, with "Add Python to PATH" ticked when you install it.
- **Google Chrome**, installed normally. ytwarm drives your real Chrome, not a
  downloaded Chromium.
- One YouTube channel (or more) that you want to warm.

## 1. Install

```bash
git clone https://github.com/Reehacks/ytwarm.git
cd ytwarm/ytwarm
python -m pip install -r requirements.txt
python -m playwright install chromium
```

The last line only sets up the Playwright driver.

## 2. Configure

Open the workbench: double-click **`YT Warmer.bat`** in the repo folder. It opens
`http://127.0.0.1:8650` in your browser. Everything below can be set on its
**Settings** tab, or by editing `ytwarm/config.json` by hand.

The repo ships with four example profiles (`ch1_tech` ... `ch4_finance`) and
three example niches (A, B, C). Change them to fit your channels.

### Niches - what each channel should learn

A niche is a list of search keywords. Write 15-25 phrases that a real viewer of
your channel's topic would type into YouTube.

```json
"niches": {
  "A": {
    "label": "Niche A - cooking",
    "keywords": ["easy pasta recipes", "meal prep for the week", "..."]
  }
}
```

Two channels on the same niche build the same interest profile.

### Profiles - one per channel

| field | what to put there |
| --- | --- |
| `profile_id` | a short name for the command line, like `cooking1`. |
| `label` | the name shown in the workbench. |
| `channel_name` | **your channel's name, spelled exactly as YouTube shows it** in the account menu. Each session checks it before it watches anything and stops if the browser is on a different channel. Leave it empty to turn the check off (not recommended). |
| `niche` | the key of a niche, like `"A"`. |
| `user_data_dir` | the Chrome profile folder, like `profiles/cooking1`. **Every profile needs its own folder.** |
| `enabled` | `true` to include this profile in "Warm all". Turn off the example profiles you do not use. |
| `proxy` | `null` for your normal connection. Only set this if you have a dedicated residential proxy for this channel. |
| `daily_budget` | minutes and actions per session, and `sessions_per_day`. The defaults are fine. |

The global settings (`watch_seconds`, `gap_minutes`, `mute_audio`,
`hide_window`, `skip_day_chance` ...) are explained in
[`ytwarm/README.md`](ytwarm/README.md#config). The defaults are fine to start.

## 3. Sign in each channel, once

1. In the workbench, on the **Profiles** tab, press **Set up** on a profile.
2. A normal Chrome window opens. Sign in to the Google account that owns the
   channel. Accept the cookie screens.
3. If the account has more than one channel: click your avatar, then
   **Switch account**, then pick the right channel.
4. **Close the Chrome window.** Chrome saves the login when the window closes.
5. The log shows which channel the profile is signed in as. Check that it is the
   right one.

Do this for every enabled profile. The dot next to a profile turns green when it
is signed in.

Your login lives only in `ytwarm/profiles/` on your PC. That folder is in
`.gitignore`. **Never commit it or share it** - it holds live Google session
cookies.

## 4. Warm

- **Warm now** runs one session for one profile. Watch the first one to see it
  work.
- **Warm all** runs every enabled profile, one at a time, with a random gap
  between them.

From a terminal, in the `ytwarm/ytwarm` folder:

```bash
python main.py --list
python main.py --warm cooking1
python main.py --warm-all
```

Each profile runs at most `sessions_per_day` times a day.

## 5. Run it every day (optional)

```bash
"Warming Schedule.bat" install
```

This makes a Windows task that runs "Warm all" once a day at 10:00, plus a
random delay of up to 4 hours. Some days it skips on purpose. Use
`"Warming Schedule.bat" pause`, `resume` or `remove` to control it. Your PC must
be on and you must be signed in to Windows.

## Tips

- **No VPN**, or always the same server. A VPN address looks like a bot to
  Google. Keep each channel on the same connection every day.
- **Don't open a profile's folder in your own Chrome while a session runs.**
  Chrome locks the folder and the session will refuse to start.
- If a session stops with a bot check, a screenshot is saved in
  `ytwarm/screenshots/` and the reason is in `ytwarm/logs/<profile_id>.log`.
  Let the channel rest for a day.

## Check that it works

```bash
python ytwarm/test_ytwarm.py
```

No browser and no network needed.
