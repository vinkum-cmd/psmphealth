#!/usr/bin/env python3
"""PSMP health test runner. One CSV row = one PSMP test (end user -> PSMP server -> target user@target server); runs the engine (psmp-engine.sh) per row,
optionally several rows in parallel, writes results (CSV + JSON), history and status, and sends ONE summary mail per run per the alert policy.
Safety: CSV values are validated (strict charset) and handed to the engine only as named environment variables - nothing from the CSV or JSON is ever
executed or placed on a command line; the command run on the target is fixed inside the engine. No secrets are read or written here.
usage: run-psmp-tests.py [--env NAME] [--csv FILE] [--only 1,3] [--parallel N] [--no-mail] [--dry-run] [--validate] [--pause|--resume]
                         [--heartbeat-check] [--test-mail] [--resend RESULTS.csv] [--version]
"""
import argparse, csv, json, os, subprocess, sys, tempfile, threading, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from email.message import EmailMessage
import smtplib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib", "py"))
import psmcfg, psmstate, psmchecks, report, shot                      # noqa: E402
from report import STEPS, render_report, send_mail               # noqa: E402

VERSION = open(os.path.join(HERE, "VERSION")).read().strip() if os.path.exists(os.path.join(HERE, "VERSION")) else "dev"
ENGINE = os.environ.get("PSMP_ENGINE") or os.path.join(HERE, "psmp-engine.sh")      # tests point this at a mock
PRINT = threading.Lock()
def say(*a): 
    with PRINT: print(*a, flush=True)

def eff(row): return {c: row["env"].get(v, "") for c, v in psmcfg.STD_COLS}

def run_attempt(row, settings, paths, outdir, attempt, hold):
    res = {"result": "FAIL", "failed_step": "runner", "reason": "", "steps": {}, "fixes": "", "gap_sec": 0, "detail_log": ""}
    rj = tempfile.mktemp(prefix="psmpres-")
    env = dict(os.environ); env.update(row["env"])
    env.update(RESULT_JSON=rj, KNOWN_HOSTS=paths["known_hosts"], WAIT=str(row["env"].get("WAIT") or hold), PSMP_REASON=str(psmcfg.rel(settings, "test.reason", "automated psmp health test")))
    tmo = int(psmcfg.rel(settings, "reliability.row_timeout_sec", 600))
    try:
        p = subprocess.run([ENGINE], env=env, capture_output=True, text=True, timeout=tmo); out = p.stdout + p.stderr
        try: res = json.loads(open(rj).read().strip().splitlines()[-1])
        except Exception: res.update(reason="engine produced no result: " + out.strip()[-200:])
    except subprocess.TimeoutExpired as e:
        out = (e.stdout.decode() if isinstance(e.stdout, bytes) else e.stdout) or ""; res.update(reason=f"test timed out after {tmo}s")
    except OSError as e:
        out = ""; res.update(reason=f"cannot start the test engine {ENGINE}: {e}")
    finally:
        if os.path.exists(rj): os.remove(rj)
    lf = os.path.join(outdir, f"row-{row['n']}" + (f"-try{attempt}" if attempt > 1 else "") + ".out")
    with open(lf, "w") as f: f.write(out)
    os.chmod(lf, 0o600); res["detail_log"] = lf
    return res

EXTRA_FIX = {"input": " - Fix the CSV row named in the reason (letters, digits and . _ : @ / - space only; hosts as names or IPv4; HoldSec 10-900; Retries 0-2; Enabled yes/no).",
             "runner": " - The engine did not return a result: run psmp-engine.sh by hand with the same values and read the detail log.",
             "precheck": " - The PSMP server is not reachable from this host: check DNS for the name, firewalls (TCP 22 or the PSMP port) and that the PSMP service is running."}

def make_shot(row, res, settings, outdir):
    """Terminal-style PNG of this row's session (last attempt). mode off|failures|all from env.json outputs.screenshots. Never raises."""
    mode = str(psmcfg.rel(settings, "outputs.screenshots", "all")).lower()
    if mode == "off" or (mode == "failures" and res["result"] == "PASS"): return ""
    try:
        e = row["env"]; lines = []
        if res.get("detail_log") and os.path.exists(res["detail_log"]): lines = shot.transcript_from_log(open(res["detail_log"], errors="replace").read())
        if not lines: lines = [f"No session output. Failed step: {res.get('failed_step','')}", f"Reason: {res.get('reason','')}"]
        c = psmcfg.rel(settings, "screenshots", {}) or {}
        title = f"Row {row['n']}: {e.get('PSMP_USER','?')} -> {e.get('PSMP_HOST','?')} -> {e.get('TARGET_USER','?')}@{e.get('TARGET_HOST','?')}"
        out = os.path.join(outdir, f"row-{row['n']}.screen.png")
        shot.render(lines, out, title, res["result"], int(c.get("font_size", 14)), int(c.get("max_lines", 40)), int(c.get("width_chars", 110)))
        os.chmod(out, 0o600); return out
    except Exception as ex:
        say(f"           row {row['n']}: screenshot not created ({type(ex).__name__}: {ex})"); return ""

def run_row(row, settings, paths, outdir, hold):
    t0 = time.time(); attempts = 0
    if row["err"]:
        res = {"result": "FAIL", "failed_step": "input", "reason": row["err"], "steps": {}, "gap_sec": 0, "detail_log": ""}
    else:
        pre_ok, pre_msg = True, ""
        if psmcfg.rel(settings, "prechecks.enabled", True): pre_ok, pre_msg = psmchecks.precheck_psmp(row["env"]["PSMP_HOST"], row["env"].get("PSMP_PORT", "22"))
        if not pre_ok: res = {"result": "FAIL", "failed_step": "precheck", "reason": "precheck: " + pre_msg, "steps": {}, "gap_sec": 0, "detail_log": ""}
        else:
            maxtry = 1 + (row["retries"] if row["retries"] is not None else int(psmcfg.rel(settings, "reliability.retries", 1)))
            wait = int(psmcfg.rel(settings, "reliability.retry_wait_sec", 30))
            for attempts in range(1, maxtry + 1):
                res = run_attempt(row, settings, paths, outdir, attempts, hold)
                if res["result"] == "PASS" or attempts == maxtry: break
                say(f"           row {row['n']}: attempt {attempts} failed at {res.get('failed_step')} - retrying in {wait}s"); time.sleep(wait)
    if not res.get("fixes") and res["result"] != "PASS": res["fixes"] = EXTRA_FIX.get(res.get("failed_step"), "")
    res["secs"] = int(time.time() - t0); res["attempts"] = max(attempts, 1)
    res["screenshot"] = make_shot(row, res, settings, outdir)
    return res

def test_mail(mailcfg):
    msg = EmailMessage(); msg["From"] = mailcfg["from"]; msg["To"] = ", ".join(mailcfg["to"]); msg["Subject"] = f"{mailcfg.get('subject_prefix', '[PSMP health]')} TEST MAIL - please ignore"
    msg.set_content(f"This is a TEST message from the PSMP health test on {os.uname().nodename} at {datetime.now():%F %T}.\nIf you can read this, mail delivery from the test host to this mailbox works. No action needed.")
    if mailcfg.get("dry_run"): print(msg); return 0
    try:
        with smtplib.SMTP(mailcfg["smtp_host"], int(mailcfg.get("smtp_port", 25)), timeout=20) as sm: sm.send_message(msg)
    except Exception as ex: print(f"test mail NOT sent: cannot reach {mailcfg['smtp_host']}:{mailcfg.get('smtp_port', 25)} ({type(ex).__name__}: {ex})"); return 1
    print("test mail sent to", msg["To"]); return 0

def heartbeat_check(settings, mailcfg, paths, no_mail):
    hours = float(psmcfg.rel(settings, "reliability.heartbeat_hours", 0))
    if not hours: print("heartbeat: not configured (reliability.heartbeat_hours)"); return 0
    st = psmcfg.load_json(os.path.join(paths["base"], "status.json")); age = (time.time() - st.get("time", 0)) / 3600 if st else 1e9
    if age <= hours: print(f"heartbeat OK: last run {age:.1f}h ago (limit {hours}h)"); return 0
    msg = f"No PSMP health test run has finished for {age:.1f}h (limit {hours}h) on {os.uname().nodename}. The schedule may have stopped, the host may be down, or the environment is paused ({'yes' if psmstate.paused(paths['base']) else 'no'})."
    print("HEARTBEAT MISSED:", msg)
    hb = os.path.join(paths["base"], "heartbeat-alert.json"); last = psmcfg.load_json(hb).get("sent", 0)
    if no_mail or not mailcfg.get("smtp_host") or time.time() - last < 6 * 3600: return 2
    m = EmailMessage(); m["From"] = mailcfg["from"]; m["To"] = ", ".join(mailcfg["to"]); m["Subject"] = f"{mailcfg.get('subject_prefix', '[PSMP health]')} HEARTBEAT MISSED - no test run for {age:.0f}h"; m.set_content(msg)
    try:
        if mailcfg.get("dry_run"): print(m)
        else:
            with smtplib.SMTP(mailcfg["smtp_host"], int(mailcfg.get("smtp_port", 25)), timeout=20) as s: s.send_message(m)
            psmstate.write_json(hb, {"sent": int(time.time())})
    except Exception as ex: print("heartbeat mail NOT sent:", ex)
    return 2

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--env", default=""); ap.add_argument("--csv", default=""); ap.add_argument("--only", default="")
    ap.add_argument("--parallel", type=int, default=0, help="run N rows at the same time (1-10; default from env.json reliability.parallel)")
    ap.add_argument("--no-mail", action="store_true"); ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--validate", action="store_true", help="check tools, CSV, DNS, ports, CCP and mail reachability - opens no session")
    ap.add_argument("--pause", action="store_true"); ap.add_argument("--resume", action="store_true"); ap.add_argument("--heartbeat-check", action="store_true"); ap.add_argument("--version", action="store_true")
    ap.add_argument("--resend", default=""); ap.add_argument("--test-mail", action="store_true")
    a = ap.parse_args()
    if a.version: print("psmphealth", VERSION); return 0
    envname = psmcfg.pick_env(a.env); settings, mailcfg, paths = psmcfg.load_env(envname)
    if os.environ.get("MAIL_DRYRUN") == "1": mailcfg["dry_run"] = True
    if a.pause or a.resume: psmstate.set_pause(paths["base"], a.pause); print("environment", envname, "PAUSED" if a.pause else "resumed"); return 0
    if a.test_mail: return test_mail(mailcfg)
    if a.heartbeat_check: return heartbeat_check(settings, mailcfg, paths, a.no_mail)
    if a.resend:
        table = list(csv.DictReader(open(a.resend, newline="", encoding="utf-8-sig"))); runid = table[0].get("RunId", "?") if table else "?"
        text, hb = render_report(table, runid, datetime.fromtimestamp(os.path.getmtime(a.resend)).strftime("%F %T"), os.uname().nodename, sum(int(r.get("Seconds") or 0) for r in table), a.resend, note="Re-sent from an existing results file (test of the report format).")
        nf = sum(1 for r in table if r["Result"] != "PASS")
        try: send_mail(mailcfg, f"{mailcfg.get('subject_prefix', '[PSMP health]')} {nf} FAILED, {len(table)-nf} OK - run {runid} (re-sent report)", text, hb, a.resend); print("report mail sent"); return 0
        except Exception as ex: print("report mail NOT sent:", ex); return 1
    reason = str(psmcfg.rel(settings, "test.reason", "automated psmp health test"))
    if not psmcfg.SAFE_VALUE.match(reason): raise SystemExit("test.reason in env.json contains characters that are not allowed")
    csvpath = a.csv or paths["tests"]; rows = psmcfg.read_rows(csvpath, psmcfg.defaults_for(settings))
    if a.only: keep = {int(x) for x in a.only.split(",")}; rows = [r for r in rows if r["n"] in keep]
    if a.validate:
        print(f"psmphealth {VERSION}  environment: {envname}  tests: {csvpath}")
        bad = psmchecks.validate(settings, mailcfg, rows, paths); print(f"\n{'ALL CHECKS PASSED' if not bad else str(bad) + ' PROBLEM(S) FOUND'}"); return 1 if bad else 0
    if a.dry_run:
        for r in rows: print(r["n"], r["err"] or "ok", "(disabled)" if not r["enabled"] else "", {k: v for k, v in r["env"].items()})
        return 0
    if psmstate.paused(paths["base"]): print(f"environment '{envname}' is PAUSED (remove with --resume) - not starting"); return 4
    ok, msg = psmstate.acquire_lock(os.path.join(paths["base"], "run.lock"), int(psmcfg.rel(settings, "reliability.max_run_minutes", 120)))
    if not ok: print(msg, "- not starting"); return 3
    par = max(1, min(10, a.parallel or int(psmcfg.rel(settings, "reliability.parallel", 1)))); hold = int(psmcfg.rel(settings, "test.hold_seconds", 60))
    runid = datetime.now().strftime("%Y%m%d-%H%M%S"); outdir = os.path.join(paths["logs"], "runs", runid); os.makedirs(outdir, mode=0o700, exist_ok=True)
    started = time.time(); todo = [r for r in rows if r["enabled"]]
    for r in rows:
        if not r["enabled"]: say(f"row {r['n']}: disabled - skipped")
    def work(r):
        e = r["env"]; say(f"[{datetime.now():%T}] row {r['n']}: {e.get('PSMP_HOST','?')} -> {e.get('TARGET_USER','?')}@{e.get('TARGET_HOST','?')} ...")
        res = run_row(r, settings, paths, outdir, hold)
        say(f"[{datetime.now():%T}] row {r['n']} -> {res['result']}{' at step ' + res['failed_step'] if res['result'] != 'PASS' else ''} ({res['secs']}s{', ' + str(res['attempts']) + ' attempts' if res['attempts'] > 1 else ''})")
        return r, res
    if par > 1: say(f"running {len(todo)} tests, {par} at a time")
    with ThreadPoolExecutor(max_workers=par) as ex: results = list(ex.map(work, todo))      # map keeps CSV order
    # ---- results CSV + JSON
    cols = [c for c, _ in psmcfg.STD_COLS]
    extra = ["RunId", "Row", "Result", "FailedStep", "Reason"] + STEPS + ["GapSec", "Seconds", "Attempts", "DetailLog", "Screenshot", "Fixes"]
    path = os.path.join(paths["results"], f"psmp-results-{runid}.csv"); jrows = []
    with open(path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(cols + extra)
        for r, s in results:
            st = s.get("steps", {}); fx = " | ".join(x.strip() for x in s.get("fixes", "").splitlines() if x.strip()); ev = eff(r)
            w.writerow([ev[c] for c in cols] + [runid, r["n"], s["result"], s.get("failed_step", ""), s.get("reason", "")] + [st.get(k, "not run") for k in STEPS] + [s.get("gap_sec", 0), s.get("secs", 0), s.get("attempts", 1), s.get("detail_log", ""), s.get("screenshot", ""), fx])
            jrows.append({"Row": r["n"], "PSMPServer": ev["TargetPSMPServer"], "Component": "PSMP", "Account": f"{ev['TargetUser']}@{ev['TargetServer']}", "Result": s["result"], "FailedStep": s.get("failed_step", ""), "Reason": s.get("reason", ""),
                          "Seconds": s.get("secs", 0), "RecordingSec": s.get("gap_sec", 0), "Attempts": s.get("attempts", 1), "Steps": st})
    os.chmod(path, 0o600)
    nfail = sum(1 for _, s in results if s["result"] != "PASS"); npass = len(results) - nfail
    summary = {"schema_version": 1, "tool_version": VERSION, "environment": envname, "run_id": runid, "host": os.uname().nodename, "time": int(time.time()), "pass": npass, "fail": nfail, "seconds": int(time.time() - started), "tests": jrows}
    if psmcfg.rel(settings, "outputs.json", True): psmstate.write_json(os.path.join(paths["results"], f"psmp-results-{runid}.json"), summary)
    if psmcfg.rel(settings, "outputs.history", True): psmstate.append_history(os.path.join(paths["base"], "history.csv"), runid, jrows)
    psmstate.write_json(os.path.join(paths["base"], "status.json"), {k: summary[k] for k in ("tool_version", "environment", "run_id", "host", "time", "pass", "fail", "seconds")})
    if psmcfg.rel(settings, "outputs.syslog", False): psmstate.syslog(f"psmp run {runid} env={envname} pass={npass} fail={nfail}", "psmphealth")
    wh = psmcfg.rel(settings, "outputs.webhook_url", "")
    if wh: print("webhook:", psmstate.webhook(wh, {k: summary[k] for k in ("environment", "run_id", "host", "time", "pass", "fail")})[1])
    n = psmstate.rotate([paths["results"], os.path.join(paths["logs"], "runs")], int(psmcfg.rel(settings, "outputs.keep_days", 30)))
    print(f"\nRESULTS: {npass} PASS, {nfail} FAIL  ->  {path}" + (f"   (rotated {n} old files)" if n else ""))
    # ---- one summary mail per run, per-test policy: FAILED (after alert_after_failures) / STILL FAILING / RECOVERED
    if a.no_mail or not mailcfg.get("smtp_host") or not mailcfg.get("to"):
        print("mail: not sent" + (" (--no-mail)" if a.no_mail else " (mail settings incomplete)")); return 1 if nfail else 0
    spath = os.path.join(paths["logs"], "mail-state.json"); state = psmcfg.load_json(spath); now = int(time.time())
    rep = int(mailcfg.get("repeat_hours", 6)) * 3600; after = int(psmcfg.rel(settings, "reliability.alert_after_failures", 1)); kinds = {}
    for r, s in results:
        ev = eff(r); key = "|".join([ev["TargetPSMPServer"], ev["TargetUser"], ev["TargetServer"], ev["PSMPEnduser"]])
        k, ns = psmstate.decide(state.get(key), s["result"] == "PASS", now, rep, after); kinds[r["n"]] = (key, k, ns)
    notify = any(v[1] for v in kinds.values()) or mailcfg.get("send_on") == "always"; sent = False
    if notify:
        head = sorted({v[1] for v in kinds.values() if v[1]}); pre = mailcfg.get("subject_prefix", "[PSMP health]")
        subj = (f"{pre} ALL OK ({npass} tests)" if not nfail else f"{pre} {nfail} FAILED, {npass} OK") + (f" ({', '.join(head)})" if head else "") + f" - run {runid}"
        with open(path, newline="") as f: table = list(csv.DictReader(f))
        text, hb = render_report(table, runid, f"{datetime.now():%F %T}", os.uname().nodename, int(time.time() - started), path, kinds={str(n): v[1] for n, v in kinds.items()}, shots={str(r["n"]): s2["screenshot"] for r, s2 in results if s2.get("screenshot")}, note=f"psmphealth {VERSION} / environment {envname}")
        try: sent = send_mail(mailcfg, subj, text, hb, path, shots={str(r["n"]): s2["screenshot"] for r, s2 in results if s2.get("screenshot")})
        except Exception as ex: print("WARNING: mail could not be sent:", ex)
    for _, (key, k, ns) in kinds.items():
        if sent: psmstate.mark_sent(ns, k, now)
        state[key] = ns
    if not mailcfg.get("dry_run"): psmstate.write_json(spath, state)
    print("mail: " + ("sent" if sent and not mailcfg.get("dry_run") else "dry run printed" if sent else "nothing to report"))
    return 1 if nfail else 0

if __name__ == "__main__": sys.exit(main())
