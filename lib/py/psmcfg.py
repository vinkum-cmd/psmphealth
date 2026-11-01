"""Environment + test-case loading for the PSMP health test. Config files are only READ (JSON/CSV), never executed.
An environment is a folder envs/<name>/ with env.json (all site-specific values), tests.csv, known_hosts, logs and results."""
import csv, json, os, re

HERE = os.environ.get("PSMP_HOME") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
NAME_OK = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
SAFE_VALUE = re.compile(r"^[A-Za-z0-9._:@/\- ]*$")
HOST_OK = re.compile(r"^[A-Za-z0-9._:-]{1,253}$")
# normalised CSV header -> (variable handed to the engine, required if no environment default)
COLS = {
    "targetpsmpserver": ("PSMP_HOST", True), "psmpport": ("PSMP_PORT", False), "targetccpurl": ("CCP_URL", True),
    "psmpenduser": ("PSMP_USER", True), "endusersafe": ("CP_SAFE", True), "enduserappid": ("CP_APPID", True), "enduserobjectname": ("CP_OBJECT", True),
    "targetuser": ("TARGET_USER", True), "targetserver": ("TARGET_HOST", True),
    "holdsec": ("WAIT", False), "retries": ("_RETRIES", False), "enabled": ("_ENABLED", False),
}
STD_COLS = [("TargetPSMPServer", "PSMP_HOST"), ("PSMPPort", "PSMP_PORT"), ("TargetCCPUrl", "CCP_URL"), ("PSMPEnduser", "PSMP_USER"), ("EndUserSafe", "CP_SAFE"),
            ("EndUserApp-ID", "CP_APPID"), ("EndUserObjectName", "CP_OBJECT"), ("TargetUser", "TARGET_USER"), ("TargetServer", "TARGET_HOST")]

def norm(h): return re.sub(r"[^a-z0-9]", "", h.lower())
def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8-sig") as f: return json.load(f)
    except FileNotFoundError: return {} if default is None else default
def url(v):
    v = v.strip()
    return v if v.startswith("https://") else "https://" + v.replace("http://", "")

def pick_env(arg=None):
    name = arg or os.environ.get("PSMP_ENV") or ""
    if not name:
        d = os.path.join(HERE, "envs")
        names = [n for n in sorted(os.listdir(d)) if n != "example" and os.path.isdir(os.path.join(d, n))] if os.path.isdir(d) else []
        name = names[0] if len(names) == 1 else ""
    if not name: raise SystemExit("no environment selected: use --env <name> (folders under envs/, besides example)")
    if not NAME_OK.match(name): raise SystemExit(f"environment name '{name}' not allowed (letters, digits, _ -)")
    return name

def load_env(name):
    base = os.path.join(HERE, "envs", name); conf = os.path.join(base, "env.json")
    if not os.path.isfile(conf): raise SystemExit(f"environment '{name}' not found: {conf}")
    settings = load_json(conf); mail = settings.get("mail") or {}
    paths = dict(base=base, conf=conf, tests=os.path.join(base, "tests.csv"), logs=os.path.join(base, "logs"), results=os.path.join(base, "results"), known_hosts=os.path.join(base, "known_hosts"))
    for k in ("logs", "results"): os.makedirs(paths[k], mode=0o700, exist_ok=True)
    return settings, mail, paths

def rel(settings, path, default=None):
    o = settings
    for k in path.split("."):
        if not isinstance(o, dict) or k not in o: return default
        o = o[k]
    return default if o is None else o

def defaults_for(settings):
    e = settings.get("enduser", {}); c = e.get("ccp", {}); d = settings.get("defaults", {})
    out = {"PSMP_HOST": d.get("psmp_server", ""), "PSMP_PORT": str(d.get("psmp_port", "")), "CCP_URL": settings.get("ccp_url", ""), "PSMP_USER": e.get("name", ""),
           "CP_SAFE": c.get("safe", ""), "CP_APPID": c.get("app_id", ""), "CP_OBJECT": c.get("object", ""), "TARGET_USER": d.get("target_user", ""), "TARGET_HOST": d.get("target_server", "")}
    return {k: v for k, v in out.items() if v}

CCP_ONLY = {"CCP_URL", "CP_SAFE", "CP_APPID", "CP_OBJECT", "PSMP_USER"}      # not needed with --manual
def read_rows(path, defaults=None, manual=False):
    defaults = defaults or {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        rd = csv.reader(f); header = next(rd)
        keys = [norm(h) for h in header]
        unknown = [header[i] for i, k in enumerate(keys) if k not in COLS]
        if unknown: raise SystemExit(f"{os.path.basename(path)}: unknown column(s) {unknown} - refusing to run")
        rows = []
        for n, r in enumerate(rd, 1):
            if not any(c.strip() for c in r): continue
            vals = {keys[i]: r[i].strip() if i < len(r) else "" for i in range(len(keys))}
            env, err = {}, ""
            for k, (var, req) in COLS.items():
                v = vals.get(k, "")
                if not SAFE_VALUE.match(v): err = f"column {k}: characters not allowed"; break
                if v == "" and var in defaults: v = defaults[var]
                if req and not v and not (manual and var in CCP_ONLY): err = f"column {k}: required (no value and no default in env.json)"; break
                if v != "": env[var] = v
            retries, enabled = None, True
            if not err:
                if "CCP_URL" in env: env["CCP_URL"] = url(env["CCP_URL"])
                if not HOST_OK.match(env["PSMP_HOST"]): err = "TargetPSMPServer must be a host name or IPv4 address"
                elif not HOST_OK.match(env["TARGET_HOST"]): err = "TargetServer must be a host name or IPv4 address"
                elif " " in env.get("PSMP_USER", "") or " " in env["TARGET_USER"]: err = "PSMPEnduser and TargetUser must not contain spaces"
                elif env.get("PSMP_PORT", "22") and not (env.get("PSMP_PORT", "22").isdigit() and 1 <= int(env.get("PSMP_PORT", "22")) <= 65535): err = "PSMPPort must be 1-65535"
                elif "WAIT" in env and not (env["WAIT"].isdigit() and 10 <= int(env["WAIT"]) <= 900): err = "HoldSec must be a whole number from 10 to 900"
                rv = env.pop("_RETRIES", ""); ev = env.pop("_ENABLED", "yes").lower()
                if not err and rv != "":
                    if rv in ("0", "1", "2"): retries = int(rv)
                    else: err = "Retries must be 0, 1 or 2"
                if not err and ev not in ("yes", "no", "true", "false", "1", "0"): err = "Enabled must be yes or no"
                enabled = ev in ("yes", "true", "1")
            rows.append({"n": n, "env": env, "err": err, "retries": retries, "enabled": enabled})
        return rows
