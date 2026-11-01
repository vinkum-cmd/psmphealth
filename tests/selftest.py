#!/usr/bin/env python3
"""Offline self-test: runs the real runner against a mock engine and a stub SMTP server. Opens no session, needs no network.  usage: python3 tests/selftest.py"""
import csv, json, os, shutil, socket, subprocess, sys, tempfile, threading, time, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib", "py"))
import psmcfg, psmstate                                                     # noqa: E402

class SmtpStub(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True); self.sock = socket.socket(); self.sock.bind(("127.0.0.1", 0)); self.sock.listen(5); self.port = self.sock.getsockname()[1]; self.mails = []
    def run(self):
        while True:
            try: c, _ = self.sock.accept()
            except OSError: return
            f = c.makefile("rwb", buffering=0); f.write(b"220 stub\r\n"); data = False; buf = []
            while True:
                ln = f.readline()
                if not ln: break
                if data:
                    if ln == b".\r\n": data = False; self.mails.append(b"".join(buf).decode("utf-8", "replace")); f.write(b"250 ok\r\n"); buf = []
                    else: buf.append(ln)
                    continue
                cmd = ln[:4].upper()
                if cmd == b"DATA": data = True; f.write(b"354 go\r\n")
                elif cmd == b"QUIT": f.write(b"221 bye\r\n"); break
                else: f.write(b"250 ok\r\n")
            c.close()

def w(path, text):
    with open(path, "w") as f: f.write(text)

class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.smtp = SmtpStub(); cls.smtp.start()
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="psmph-"); self.base = os.path.join(self.home, "envs", "t"); os.makedirs(self.base)
        self.env = {"schema": 2, "ccp_url": "https://ccp.test", "enduser": {"name": "u1", "ccp": {"app_id": "A", "safe": "S", "object": "O"}},
                    "defaults": {"psmp_server": "psmp.test"}, "test": {"hold_seconds": 60},
                    "reliability": {"retries": 1, "retry_wait_sec": 0, "alert_after_failures": 1, "max_run_minutes": 60, "heartbeat_hours": 1, "parallel": 1},
                    "prechecks": {"enabled": False}, "outputs": {"keep_days": 30},
                    "mail": {"smtp_host": "127.0.0.1", "smtp_port": self.smtp.port, "from": "a@t", "to": ["b@t"], "repeat_hours": 6, "subject_prefix": "[T]"}}
        self.csv = "TargetUser,TargetServer\nroot,srv1\n"; self.seq = os.path.join(self.home, "seq"); self.smtp.mails.clear()
    def tearDown(self): shutil.rmtree(self.home, ignore_errors=True)
    def write(self): w(os.path.join(self.base, "env.json"), json.dumps(self.env)); w(os.path.join(self.base, "tests.csv"), self.csv)
    def run_tool(self, *args, seq=(), no_mail=False, sleep="0"):
        self.write(); w(self.seq, "".join(s + "\n" for s in seq)); w(self.seq + ".calls", "") if not os.path.exists(self.seq + ".calls") else None
        e = dict(os.environ, PSMP_HOME=self.home, PSMP_ENV="t", PSMP_ENGINE=os.path.join(ROOT, "tests", "mock-engine.sh"), MOCK_SEQ=self.seq, MOCK_SLEEP=sleep)
        p = subprocess.run([sys.executable, os.path.join(ROOT, "run-psmp-tests.py"), *(["--no-mail"] if no_mail else []), *args], env=e, capture_output=True, text=True, timeout=120)
        return p.returncode, p.stdout + p.stderr
    def calls(self): return len(open(self.seq + ".calls").read().splitlines())
    def rows(self, csvtext):
        self.csv = csvtext; self.write(); return psmcfg.read_rows(os.path.join(self.base, "tests.csv"), psmcfg.defaults_for(self.env))

    def test_csv_defaults_and_rejections(self):
        r = self.rows("TargetUser,TargetServer\nroot,srv1\n")[0]
        self.assertEqual(r["err"], ""); self.assertEqual(r["env"]["PSMP_HOST"], "psmp.test"); self.assertEqual(r["env"]["PSMP_USER"], "u1"); self.assertEqual(r["env"]["CP_OBJECT"], "O")
        for cols, vals, msg in [(",HoldSec", ",5", "HoldSec"), (",Retries", ",9", "Retries"), (",Enabled", ",maybe", "Enabled"), (",PSMPPort", ",99999", "PSMPPort"), (",TargetPSMPServer", ",bad host!", "characters"),
                                (",TargetPSMPServer", ",a;b", "characters")]:
            self.assertIn(msg, self.rows(f"TargetUser,TargetServer{cols}\nroot,srv1{vals}\n")[0]["err"])
        self.assertEqual(self.rows("TargetUser,TargetServer,HoldSec,Retries,Enabled,PSMPPort\nroot,srv1,60,2,no,2222\n")[0]["err"], "")
        self.assertIn("space", self.rows("TargetUser,TargetServer\nro ot,srv1\n")[0]["err"])
        self.csv = "TargetUser,TargetServer,Command\nroot,srv1,id\n"; self.write()
        with self.assertRaises(SystemExit): psmcfg.read_rows(os.path.join(self.base, "tests.csv"), {})        # no column can ever carry a command
    def test_row_with_own_hold_runs(self):
        self.csv = "TargetUser,TargetServer,HoldSec,Retries,Enabled\nroot,srv1,30,0,yes\n"
        rc, out = self.run_tool(seq=["PASS"], no_mail=True); self.assertEqual(rc, 0, out); self.assertEqual(self.calls(), 1)
    def test_pass_files(self):
        rc, out = self.run_tool(seq=["PASS"]); self.assertEqual(rc, 0, out); self.assertEqual(self.smtp.mails, [])
        for f in ("status.json", "history.csv"): self.assertTrue(os.path.exists(os.path.join(self.base, f)), f)
        res = os.listdir(os.path.join(self.base, "results")); j = [x for x in res if x.endswith(".json")][0]
        d = json.load(open(os.path.join(self.base, "results", j))); self.assertEqual((d["pass"], d["fail"]), (1, 0))
    def test_retry_then_pass(self):
        rc, out = self.run_tool(seq=["FAIL:login:x", "PASS"]); self.assertEqual(rc, 0, out); self.assertEqual(self.calls(), 2); self.assertEqual(self.smtp.mails, [])
    def test_fail_alert_repeat_recover(self):
        rc, out = self.run_tool(seq=["FAIL:login:boom"] * 2); self.assertEqual(rc, 1); self.assertEqual(len(self.smtp.mails), 1); self.assertIn("FAILED", self.smtp.mails[0]); self.assertIn("boom", self.smtp.mails[0])
        self.run_tool(seq=["FAIL:login:boom"] * 2); self.assertEqual(len(self.smtp.mails), 1)
        self.run_tool(seq=["PASS"]); self.assertEqual(len(self.smtp.mails), 2); self.assertIn("RECOVERED", self.smtp.mails[1])
        self.run_tool(seq=["PASS"]); self.assertEqual(len(self.smtp.mails), 2)
    def test_alert_after_two_and_silent_recovery(self):
        self.env["reliability"].update(alert_after_failures=2, retries=0)
        self.run_tool(seq=["FAIL:login:x"]); self.assertEqual(self.smtp.mails, []); self.run_tool(seq=["FAIL:login:x"]); self.assertEqual(len(self.smtp.mails), 1)
        shutil.rmtree(os.path.join(self.base, "logs")); self.smtp.mails.clear()
        self.env["reliability"]["alert_after_failures"] = 3; self.run_tool(seq=["FAIL:login:x"]); self.run_tool(seq=["PASS"]); self.assertEqual(self.smtp.mails, [])
    def test_disabled_row_and_per_row_retries(self):
        self.csv = "TargetUser,TargetServer,Enabled,Retries\noff,s,no,\non,s,yes,0\n"
        rc, out = self.run_tool(seq=["FAIL:login:x"], no_mail=True); self.assertEqual(self.calls(), 1, out); self.assertIn("disabled - skipped", out)
    def test_precheck_fails_fast(self):
        self.env["prechecks"] = {"enabled": True}; self.csv = "TargetUser,TargetServer,TargetPSMPServer\nroot,s,nonexistent.invalid\n"
        rc, out = self.run_tool(seq=["PASS"], no_mail=True); self.assertEqual(rc, 1); self.assertEqual(self.calls(), 0); self.assertIn("precheck", out)
    def test_parallel_keeps_order_and_is_faster(self):
        self.env["reliability"]["parallel"] = 4; self.csv = "TargetUser,TargetServer\n" + "".join(f"u{i},s{i}\n" for i in range(1, 5))
        t0 = time.time(); rc, out = self.run_tool(seq=["PASS"] * 4, no_mail=True, sleep="2"); dt = time.time() - t0
        self.assertEqual(rc, 0, out); self.assertLess(dt, 7, "4 x 2s in parallel must take well under 8s"); self.assertIn("4 at a time", out)
        f = [x for x in os.listdir(os.path.join(self.base, "results")) if x.endswith(".csv")][0]
        rows = list(csv.DictReader(open(os.path.join(self.base, "results", f)))); self.assertEqual([r["TargetUser"] for r in rows], ["u1", "u2", "u3", "u4"])
    def test_lock_pause_heartbeat_rotation(self):
        os.makedirs(self.base, exist_ok=True); lk = os.path.join(self.base, "run.lock")
        w(lk, f"other|1|{int(time.time())}"); rc, out = self.run_tool(seq=["PASS"], no_mail=True); self.assertEqual(rc, 3, out)
        w(lk, f"other|1|{int(time.time()) - 9000}"); rc, out = self.run_tool(seq=["PASS"], no_mail=True); self.assertEqual(rc, 0, out); self.assertFalse(os.path.exists(lk))
        self.run_tool("--pause"); rc, out = self.run_tool(seq=["PASS"], no_mail=True); self.assertEqual(rc, 4); self.run_tool("--resume"); rc, out = self.run_tool(seq=["PASS"], no_mail=True); self.assertEqual(rc, 0)
        rc, out = self.run_tool("--heartbeat-check", no_mail=True); self.assertEqual(rc, 0, out)
        st = json.load(open(os.path.join(self.base, "status.json"))); st["time"] -= 3 * 3600; w(os.path.join(self.base, "status.json"), json.dumps(st))
        rc, out = self.run_tool("--heartbeat-check"); self.assertEqual(rc, 2); self.assertTrue(any("HEARTBEAT" in m for m in self.smtp.mails))
        old = os.path.join(self.base, "results", "psmp-results-old.csv"); w(old, "x"); os.utime(old, (1, 1)); self.run_tool(seq=["PASS"], no_mail=True)
        self.assertFalse(os.path.exists(old)); self.assertTrue(os.path.exists(os.path.join(self.base, "history.csv")))
    def test_no_secret_words_in_outputs(self):
        self.run_tool(seq=["PASS"], no_mail=True)
        for root, _, fs in os.walk(self.base):
            for f in fs:
                if not f.endswith(".lock"): self.assertNotIn("password", open(os.path.join(root, f), errors="ignore").read().lower(), f)

    def test_screenshots_and_all_ok_mail(self):
        self.env["mail"]["send_on"] = "always"; self.csv = "TargetUser,TargetServer\nroot,srv1\nadmin,srv2\n"
        rc, out = self.run_tool(seq=["PASS", "FAIL:login:boom", "FAIL:login:boom"], no_mail=False); self.assertEqual(rc, 1, out)
        pngs = []
        for root, _, fs in os.walk(os.path.join(self.base, "logs")): pngs += [os.path.join(root, f) for f in fs if f.endswith(".screen.png")]
        self.assertEqual(len(pngs), 2, pngs)
        for p in pngs: self.assertEqual(open(p, "rb").read(8), b"\x89PNG\r\n\x1a\n"); self.assertEqual(oct(os.stat(p).st_mode & 0o777), "0o600")
        m = self.smtp.mails[0]; self.assertEqual(m.count("Content-ID: <shot"), 2); self.assertIn("cid:shot1", m); self.assertIn("Session screenshots", m)
        self.smtp.mails.clear(); rc, out = self.run_tool(seq=["PASS", "PASS"]); self.assertEqual(rc, 0, out)
        self.assertEqual(len(self.smtp.mails), 1); self.assertIn("ALL OK (2 tests)", self.smtp.mails[0])
    def test_screenshot_modes(self):
        self.env["outputs"]["screenshots"] = "off"; self.run_tool(seq=["PASS"], no_mail=True)
        self.assertFalse([f for _, _, fs in os.walk(self.base) for f in fs if f.endswith(".png")])
        self.env["outputs"]["screenshots"] = "failures"; self.run_tool(seq=["PASS"], no_mail=True)
        self.assertFalse([f for _, _, fs in os.walk(self.base) for f in fs if f.endswith(".png")])
        self.run_tool(seq=["FAIL:login:x"] * 2, no_mail=True); self.assertTrue([f for _, _, fs in os.walk(self.base) for f in fs if f.endswith(".png")])
    def test_user_csv_format(self):
        r = self.rows("TargetPSMPServer,PSMPPort,TargetUser,TargetServer,PSMPEnduser,EndUserSafe,EndUserApp-ID,EndUserObjectName,HoldSec,Retries,Enabled.\n192.0.2.10,22,psmp-user-01,192.0.2.20,testenduser,PAMEndUser,PSMP-Testing,enduser-object-01,60,1,\n")[0]
        self.assertEqual(r["err"], ""); self.assertTrue(r["enabled"]); self.assertEqual(r["retries"], 1)

    def run_manual(self, typed, seq, *args, user="typed-user"):
        """run the tool on a real terminal (pexpect) so getpass works; returns (exit code, screen text)"""
        import pexpect
        self.write(); w(self.seq, "".join(x + "\n" for x in seq)); w(self.seq + ".calls", "")
        e = dict(os.environ, PSMP_HOME=self.home, PSMP_ENV="t", PSMP_ENGINE=os.path.join(ROOT, "tests", "mock-engine.sh"), MOCK_SEQ=self.seq, MOCK_SLEEP="0")
        c = pexpect.spawn(sys.executable, [os.path.join(ROOT, "run-psmp-tests.py"), "--manual", "--no-mail", *args], env=e, encoding="utf-8", timeout=60)
        i = c.expect(["End user name", pexpect.EOF])
        if i == 0:
            c.sendline(user); i = c.expect(["Password for", pexpect.EOF])
            if i == 0: c.sendline(typed); c.expect(pexpect.EOF)
        c.close(); return c.exitstatus, c.before or ""
    def test_manual_skips_ccp_and_reuses_password(self):
        self.env.pop("ccp_url"); self.env.pop("enduser"); self.csv = "TargetUser,TargetServer\nroot,s1\nadmin,s2\n"      # no CCP values and no end user at all
        rc, out = self.run_manual("S3cretPw!", ["PASS", "PASS"]); self.assertEqual(rc, 0, out)
        calls = open(self.seq + ".calls").read(); self.assertEqual(calls.count("pwlen=9"), 2, calls); self.assertIn("ccp=none", calls)
        for root, _, fs in os.walk(self.home):
            for f in fs:
                if not f.endswith(".png"): self.assertNotIn("S3cretPw!", open(os.path.join(root, f), errors="ignore").read(), f)
        self.assertNotIn("S3cretPw!", out)
        f = [x for x in os.listdir(os.path.join(self.base, "results")) if x.endswith(".csv")][0]
        self.assertEqual({r["PSMPEnduser"] for r in csv.DictReader(open(os.path.join(self.base, "results", f)))}, {"typed-user"})
    def test_manual_user_overrides_csv_and_rejects_bad_names(self):
        self.csv = "TargetUser,TargetServer,PSMPEnduser\nroot,s1,alice\nroot,s2,bob\n"
        rc, out = self.run_manual("pw1", ["PASS", "PASS"]); self.assertEqual(rc, 0, out)
        f = [x for x in os.listdir(os.path.join(self.base, "results")) if x.endswith(".csv")][0]
        self.assertEqual({r["PSMPEnduser"] for r in csv.DictReader(open(os.path.join(self.base, "results", f)))}, {"typed-user"})
        rc, out = self.run_manual("pw1", ["PASS"], user="bad name;rm"); self.assertNotEqual(rc, 0); self.assertIn("not allowed", out)
    def test_manual_stops_after_rejection(self):
        self.env["retries"] = 0; self.csv = "TargetUser,TargetServer\nroot,s1\nadmin,s2\nops,s3\n"
        rc, out = self.run_manual("wrong", ["FAIL:login:password prompted again: login rejected", "PASS", "PASS"]); self.assertEqual(rc, 1, out)
        self.assertEqual(len(open(self.seq + ".calls").read().splitlines()), 1, "must not send the rejected password to rows 2 and 3 or retry"); self.assertIn("skipped", out)
    def test_manual_needs_a_terminal(self):
        self.csv = "TargetUser,TargetServer\nroot,s1\n"
        rc, out = self.run_tool("--manual", seq=["PASS"], no_mail=True); self.assertNotEqual(rc, 0); self.assertIn("terminal", out)

if __name__ == "__main__": unittest.main(verbosity=2)
