"""The ytwarm workbench: a browser front end for the warmer. Port 8650.

Standard library only, the same choice `gallery.py` and the Reddit Shorts
workbench make - it then runs on whichever Python has Playwright in it, with no
second environment to keep alive.

Ports here: 8000 ClipRank, 8188 ComfyUI, 8420 the review gallery, 8500 Studio,
8600 Reddit Shorts, 8650 this, 8750 Longform.

**Every run is a subprocess, not a thread.** Playwright's sync API refuses to run
inside a thread that already owns an event loop, and a crash in a session would
otherwise take the web server down with it. So the GUI shells out to
`main.py` and streams its stdout into the log pane - the CLI stays the one code
path that actually warms anything.

**One job at a time, refused rather than queued.** Two Chrome windows on one
profile is impossible (Chrome locks the folder) and on two profiles is the
simultaneous-session pattern the whole project avoids.

The setup job ends when the human CLOSES the Chrome window, not when a button
here is pressed - Chrome writes its cookie jar on exit, so waiting for the
process is the only honest signal that the session was saved.
"""
from __future__ import annotations

import json
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import config as C

HERE = Path(__file__).resolve().parent
PORT = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8650

JOB = {"running": False, "kind": "", "profile": "", "log": [], "code": None}
LOCK = threading.Lock()
PROC: subprocess.Popen | None = None


def _log(line: str) -> None:
    line = str(line).rstrip()
    if not line:
        return
    with LOCK:
        JOB["log"].append(line)
        del JOB["log"][:-500]
    print(line, flush=True)


def _pump(proc: subprocess.Popen) -> None:
    for line in proc.stdout:
        _log(line)
    proc.wait()
    with LOCK:
        JOB["running"] = False
        JOB["code"] = proc.returncode
    _log(f"--- finished, exit code {proc.returncode} ---")


def _spawn(kind: str, profile_id: str, args: list) -> dict:
    global PROC
    with LOCK:
        if JOB["running"]:
            return {"ok": False, "error": f"{JOB['kind']} is still running"}
        JOB.update(running=True, kind=kind, profile=profile_id, log=[], code=None)
    cmd = [sys.executable, "-u", str(HERE / "main.py")] + args
    _log("$ " + " ".join(cmd))
    PROC = subprocess.Popen(
        cmd, cwd=str(HERE), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
        bufsize=1,
        # Its own process group, so Stop can send it a Ctrl-Break. See _stop().
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    threading.Thread(target=_pump, args=(PROC,), daemon=True).start()
    return {"ok": True}


# The daily task, as schedule.ps1 registers it. Read through PowerShell because
# schtasks.exe cannot report RandomDelay, which is the setting that matters.
# Never polled: the state changes when a button here is pressed, and a
# PowerShell launch every couple of seconds would cost more than the whole page.
TASK_NAME = "ytwarm daily warm"
PS = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command"]


def _schedule(enable=None) -> dict:
    verb = ""
    if enable is True:
        verb = f"Enable-ScheduledTask -TaskName '{TASK_NAME}' | Out-Null; "
    elif enable is False:
        verb = f"Disable-ScheduledTask -TaskName '{TASK_NAME}' | Out-Null; "
    script = (
        verb +
        f"$t = Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction SilentlyContinue; "
        "if (-not $t) { '{\"installed\":false}' } else { "
        "$i = Get-ScheduledTaskInfo $t; "
        "@{installed=$true; enabled=($t.State -ne 'Disabled'); "
        "state=[string]$t.State; next=[string]$i.NextRunTime; "
        "last=[string]$i.LastRunTime} | ConvertTo-Json -Compress }")
    try:
        got = subprocess.run(PS + [script], capture_output=True, text=True, timeout=40)
        return json.loads((got.stdout or "").strip() or '{"installed": false}')
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        return {"installed": False, "error": str(e)}


def _stop(proc) -> None:
    """Ctrl-Break first, kill only if that is ignored.

    `terminate()` on its own kills Python where it stands. The browser context
    manager's close never runs, Chrome is left orphaned, and an orphaned Chrome
    keeps the profile folder LOCKED - so the next session refuses to start with
    "already open in a Chrome window", and nothing on screen explains why.

    Ctrl-Break raises KeyboardInterrupt inside the child instead. That unwinds
    through the `with`, closes the browser properly, and the cookie jar is
    written the same way a finished session writes it.
    """
    try:
        proc.send_signal(signal.CTRL_BREAK_EVENT)
    except (OSError, ValueError, AttributeError) as e:
        _log(f"--- could not ask it to stop ({e}), killing it ---")
        proc.terminate()
        return
    # Windows does not interrupt time.sleep for a Ctrl-Break, so the stop lands
    # at the END of the current sleep - up to 20 seconds in a watch loop - and
    # then Chrome takes a second or two to close. 45s is that with room to spare.
    for _ in range(90):
        if proc.poll() is not None:
            return
        time.sleep(0.5)
    _log("--- it did not stop on its own, killing it ---")
    proc.terminate()


def _state() -> dict:
    try:
        cfg = C.load()
        err = ""
        profiles = [{
            "profile_id": p.profile_id,
            "label": p.label,
            "niche": p.niche,
            "channel_name": p.channel_name,
            "enabled": p.enabled,
            "keywords": p.keywords,
            "user_data_dir": str(p.user_data_dir),
            "proxy": (p.proxy or {}).get("server", ""),
            "budget": p.daily_budget,
            "signed_in_once": p.signed_in_once,
            "sessions_today": C.sessions_today(p.profile_id),
            "runs": (C.load_state(p.profile_id).get("runs") or [])[-6:][::-1],
        } for p in cfg.profiles]
        raw = cfg.raw
    except C.ConfigError as e:
        profiles, raw, err = [], {}, str(e)
    with LOCK:
        job = {k: JOB[k] for k in ("running", "kind", "profile", "code")}
        job["log"] = JOB["log"][-220:]
    return {"profiles": profiles, "config": raw, "job": job, "error": err}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):            # the job log is the interesting one
        pass

    def _send(self, code: int, body, ctype="application/json"):
        if not isinstance(body, (bytes, bytearray)):
            body = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self._send(200, (HERE / "index.html").read_bytes(),
                              "text/html; charset=utf-8")
        if path == "/api/state":
            return self._send(200, _state())
        if path == "/api/schedule":
            return self._send(200, _schedule())
        return self._send(404, {"error": "no such path"})

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._send(400, {"error": "body is not JSON"})
        pid = str(body.get("profile_id") or "")

        if path == "/api/warm":
            extra = ["--force"] if body.get("force") else []
            return self._send(200, _spawn("warm", pid, ["--warm", pid] + extra))
        if path == "/api/warm-all":
            extra = ["--force"] if body.get("force") else []
            return self._send(200, _spawn("warm-all", "", ["--warm-all"] + extra))
        if path == "/api/setup":
            return self._send(200, _spawn("setup", pid, ["--setup", pid]))
        if path == "/api/schedule":
            return self._send(200, _schedule(enable=bool(body.get("enable"))))
        if path == "/api/stop":
            if PROC and PROC.poll() is None:
                _log("--- stop requested - it finishes the step it is on first "
                     "(up to about 20 seconds), then closes the browser ---")
                threading.Thread(target=_stop, args=(PROC,), daemon=True).start()
                return self._send(200, {"ok": True})
            return self._send(200, {"ok": False, "error": "nothing is running"})
        if path == "/api/config":
            try:
                C.save(body.get("config") or {})
            except C.ConfigError as e:
                return self._send(200, {"ok": False, "error": str(e)})
            except OSError as e:
                return self._send(200, {"ok": False, "error": f"could not write: {e}"})
            _log("config saved")
            return self._send(200, {"ok": True})
        return self._send(404, {"error": "no such path"})


def main() -> None:
    C.setup_logging("gui")
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}"
    print(f"ytwarm workbench on {url}", flush=True)
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if PROC and PROC.poll() is None:
            print("closing the running session first...", flush=True)
            _stop(PROC)
        srv.server_close()
        print("stopped", flush=True)


if __name__ == "__main__":
    main()
