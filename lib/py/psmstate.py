"""Run lock, kill switch, alert state machine, history/status files, rotation, optional webhook/syslog. No secrets pass through here."""
import atexit, csv, json, os, subprocess, time, urllib.request

def now(): return int(time.time())

# ---- run lock (one run at a time per environment; stale after max_run_minutes) and kill switch
def acquire_lock(path, stale_minutes):
    """True = acquired, False = another run is active (a message is returned via the second value)."""
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(fd, f"{os.uname().nodename}|{os.getpid()}|{now()}".encode()); os.close(fd)
            atexit.register(lambda: os.path.exists(path) and os.remove(path))
            return True, ""
        except FileExistsError:
            try: owner = open(path).read().strip(); ts = int(owner.split("|")[2])
            except Exception: owner, ts = "?", int(os.path.getmtime(path))
            if now() - ts < stale_minutes * 60: return False, f"another run is active (lock {path}: {owner})"
            os.remove(path)          # stale: the previous run died
    return False, f"cannot take lock {path}"

def paused(base): return os.path.exists(os.path.join(base, "PAUSE"))
def set_pause(base, on):
    p = os.path.join(base, "PAUSE")
    if on: open(p, "w").write(time.strftime("paused %F %T\n"))
    elif os.path.exists(p): os.remove(p)

# ---- alert state machine (per test: status, consecutive fails, whether an alert was sent, when)
def migrate(entry):
    if not entry: return {"status": "PASS", "fails": 0, "alerted": False, "sent": 0}
    if "fails" in entry: return entry
    failing = entry.get("status") == "FAIL"        # state written by the first version
    return {"status": entry.get("status", "PASS"), "fails": 1 if failing else 0, "alerted": failing and entry.get("sent", 0) > 0, "sent": entry.get("sent", 0)}

def decide(prev, passed, ts, repeat_secs, alert_after):
    """-> (kind, new_state). kind: '', FAILED, STILL FAILING, RECOVERED. alerted/sent are only updated after a mail really went out (mark_sent)."""
    p = migrate(prev)
    if passed: return ("RECOVERED" if p["alerted"] else ""), {"status": "PASS", "fails": 0, "alerted": p["alerted"], "sent": p["sent"]}
    fails = p["fails"] + 1
    if not p["alerted"]: kind = "FAILED" if fails >= alert_after else ""
    else: kind = "STILL FAILING" if ts - p["sent"] >= repeat_secs else ""
    return kind, {"status": "FAIL", "fails": fails, "alerted": p["alerted"], "sent": p["sent"]}

def mark_sent(state, kind, ts):
    if kind in ("FAILED", "STILL FAILING"): state.update(alerted=True, sent=ts)
    elif kind == "RECOVERED": state.update(alerted=False, fails=0, sent=ts)

# ---- files
def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f: json.dump(obj, f, indent=1)
    os.chmod(tmp, 0o600); os.replace(tmp, path)

HIST_COLS = ["Time", "RunId", "Row", "Component", "PSMServer", "Account", "Result", "FailedStep", "Seconds", "RecordingSec", "Attempts"]
def append_history(path, runid, rows):
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new: w.writerow(HIST_COLS)
        for r in rows: w.writerow([time.strftime("%F %T"), runid] + [r.get(c, "") for c in HIST_COLS[2:]])
    os.chmod(path, 0o600)

def rotate(dirs, keep_days):
    """delete files older than keep_days under the given directories; returns how many."""
    if not keep_days: return 0
    cut = time.time() - keep_days * 86400; n = 0
    for d in dirs:
        for root, _, files in os.walk(d):
            for fn in files:
                p = os.path.join(root, fn)
                if fn in ("mail-state.json", "history.csv", "status.json") : continue
                try:
                    if os.path.getmtime(p) < cut: os.remove(p); n += 1
                except OSError: pass
    return n

# ---- optional outputs (off unless configured). Payloads contain results only - no passwords, no tokens.
def syslog(msg, tag="psm-test"):
    try: subprocess.run(["logger", "-t", tag, "-p", "user.notice", msg], timeout=5, check=False)
    except Exception: pass

def webhook(urlstr, payload):
    if not urlstr.startswith(("https://", "http://")): return False, "webhook url must start with http(s)://"
    try:
        req = urllib.request.Request(urlstr, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r: return 200 <= r.status < 300, f"http {r.status}"
    except Exception as ex: return False, f"{type(ex).__name__}: {ex}"
