"""Pre-checks (DNS + TCP, IPv4 only, no logon, no secrets) and the --validate report."""
import shutil, socket, ssl, urllib.request, urllib.error

def dns_addrs(host):
    try: return sorted({a[4][0] for a in socket.getaddrinfo(host, None, socket.AF_INET)})
    except OSError: return []
def tcp_ok(host, port, timeout=4):
    try:
        ip = socket.getaddrinfo(host, int(port), socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
        with socket.create_connection((ip, int(port)), timeout=timeout): return True
    except OSError: return False
def http_code(u, timeout=8):
    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE      # reachability probe only
    try:
        with urllib.request.urlopen(urllib.request.Request(u, method="GET"), timeout=timeout, context=ctx) as r: return r.status
    except urllib.error.HTTPError as e: return e.code
    except Exception: return 0
def precheck_psmp(host, port=22):
    ips = dns_addrs(host)
    if not ips: return False, f"PSMP server name '{host}' does not resolve in DNS"
    if not tcp_ok(host, port): return False, f"PSMP server {host} ({', '.join(ips)}) not reachable on TCP {port}"
    return True, f"{host}:{port} -> {', '.join(ips)}"

def validate(settings, mail, rows, paths, tools=("jq", "curl", "ssh", "python3"), manual=False):
    bad = 0
    def line(ok, what, extra=""):
        nonlocal bad
        print(f"  [{'ok' if ok else 'FAIL'}] {what}{' - ' + extra if extra else ''}"); bad += 0 if ok else 1
    print("Tools"); [line(shutil.which(t) is not None, t) for t in tools]
    try: import pexpect; line(True, "python3 pexpect", pexpect.__version__)
    except ImportError: line(False, "python3 pexpect", "install it (apt install python3-pexpect)")
    print("Test rows")
    for r in rows: line(not r["err"], f"row {r['n']} {r['env'].get('PSMP_HOST','?')} -> {r['env'].get('TARGET_USER','?')}@{r['env'].get('TARGET_HOST','?')}", r["err"] + ("" if r["enabled"] else "  (disabled)"))
    ok_rows = [r for r in rows if r["enabled"] and not r["err"]]
    print("PSMP servers (DNS + TCP port)")
    for h, p in sorted({(r["env"]["PSMP_HOST"], r["env"].get("PSMP_PORT", "22")) for r in ok_rows}): ok, msg = precheck_psmp(h, p); line(ok, f"{h}:{p}", msg)
    print("CCP (reachable, no logon)")
    if manual: print("  skipped (--manual: the password is typed, CCP is not used)")
    for u in [] if manual else sorted({r["env"]["CCP_URL"] for r in ok_rows}): c = http_code(u); line(c != 0, u, f"http {c}" if c else "no answer")
    print("Files")
    try: open(os.path.join(paths["base"], ".w"), "w").close(); os.remove(os.path.join(paths["base"], ".w")); line(True, "environment folder writable")
    except Exception as ex: line(False, "environment folder writable", str(ex))
    if mail.get("smtp_host"): print("Mail"); line(tcp_ok(mail["smtp_host"], mail.get("smtp_port", 25)), f"{mail['smtp_host']}:{mail.get('smtp_port', 25)}", "to " + ", ".join(mail.get("to", [])))
    return bad
import os      # noqa: E402  (used by validate)
