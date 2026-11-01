"""Report rendering (HTML + text) and mail sending for the PSMP health test. Pure functions: no secrets, nothing is executed."""
import html, os, re, smtplib
from email.message import EmailMessage

STEPS = ["ccp", "login", "identity", "hold"]
STEP_LABEL = {"ccp": "CCP password", "login": "PSMP login", "identity": "Target user", "hold": "Session held"}

def fixes_list(t): return [x.strip(" -") for x in re.split(r"\s\|\s|\n", t or "") if x.strip(" -")]

def render_report(table, runid, when, host, secs, csvpath, kinds=None, note="", shots=None):
    """-> (plain text, html) for the whole run: all tests, all steps, and for failures the reason, fixes and detail log."""
    kinds = kinds or {}; shots = shots or {}
    nfail = sum(1 for r in table if r["Result"] != "PASS"); nok = len(table) - nfail
    head = f"{nfail} FAILED, {nok} OK" if nfail else f"ALL OK ({nok} tests)"
    T = [f"PSMP HEALTH TEST - {head}", f"Run {runid} | {when} | host {host} | {secs}s", ""]
    if note: T += [note, ""]
    T.append(f"{'#':<3}{'PSMP server':<20}{'Target user@server':<34}{'Result':<7}{'Gap':<7}Failed step")
    for r in table:
        T.append(f"{r['Row']:<3}{r['TargetPSMPServer']:<20}{(r['TargetUser'] + '@' + r['TargetServer']):<34}{r['Result']:<7}{(r.get('GapSec') or '-'):<7}{r['FailedStep']}")
    col = "#b3261e" if nfail else "#2e7d32"
    H = [f'<html><body style="font-family:Segoe UI,Arial,sans-serif;font-size:13px;color:#222">',
         f'<div style="padding:10px 14px;color:#fff;background:{col};font-size:16px"><b>PSMP health test - {html.escape(head)}</b></div>',
         f'<p style="color:#555">Run <b>{html.escape(runid)}</b> &middot; {html.escape(when)} &middot; host {html.escape(host)} &middot; {secs}s' + (f'<br><i>{html.escape(note)}</i>' if note else '') + '</p>',
         '<table cellpadding="5" style="border-collapse:collapse;border:1px solid #ccc"><tr style="background:#eee"><th>#</th><th>PSMP server</th><th>End user</th><th>Target</th><th>Result</th>'
         + "".join(f"<th>{STEP_LABEL[k]}</th>" for k in STEPS) + '<th>Gap</th><th>Time</th></tr>']
    for r in table:
        ok = r["Result"] == "PASS"; cells = ""
        for k in STEPS:
            v = r.get(k, "not run")
            sym, c = ("&#10003;", "#2e7d32") if v == "OK" else (("&#10007;", "#b3261e") if v == "FAIL" else ("&ndash;", "#999"))
            cells += f'<td align="center" style="color:{c};font-weight:bold">{sym}</td>'
        H.append(f'<tr style="border-top:1px solid #ddd"><td>{r["Row"]}</td><td>{html.escape(r["TargetPSMPServer"])}</td><td>{html.escape(r["PSMPEnduser"])}</td>'
                 f'<td>{html.escape(r["TargetUser"])}@{html.escape(r["TargetServer"])}</td><td style="color:#fff;background:{"#2e7d32" if ok else "#b3261e"}" align="center"><b>{r["Result"]}</b></td>'
                 f'{cells}<td>{html.escape(str(r.get("GapSec") or "-"))}s</td><td>{r.get("Seconds","")}s</td></tr>')
    H.append("</table>")
    if shots:
        H.append('<h3>Session screenshots</h3>')
        for r in table:
            if str(r["Row"]) in shots:
                H.append(f'<p style="margin:10px 0 2px"><b>Row {r["Row"]}</b> &middot; {html.escape(r["TargetPSMPServer"])} &rarr; {html.escape(r["TargetUser"])}@{html.escape(r["TargetServer"])} &middot; {r["Result"]}</p><img src="cid:shot{r["Row"]}" style="max-width:100%;border:1px solid #ccc">')
    fails = [r for r in table if r["Result"] != "PASS"]
    if fails:
        T += ["", "FAILURES - what failed and what to try", "=" * 40]; H.append('<h3 style="color:#b3261e">Failures: what failed and what to try</h3>')
        for r in fails:
            kind = kinds.get(str(r["Row"]), "") or "FAILED"
            steps = ", ".join(f"{STEP_LABEL[k]}={r.get(k, 'not run')}" for k in STEPS); fx = fixes_list(r.get("Fixes", ""))
            T += ["", f"Row {r['Row']} [{kind}]: {r['TargetPSMPServer']} / {r['PSMPEnduser']} -> {r['TargetUser']}@{r['TargetServer']}", f"  Failed step: {r['FailedStep']}",
                  f"  Reason:      {r['Reason']}", f"  Steps:       {steps}", "  High-level fixes:"] + [f"    - {x}" for x in fx] + [f"  Detail log on {host}: {r['DetailLog']}"]
            H.append(f'<div style="border-left:4px solid #b3261e;padding:4px 12px;margin:10px 0;background:#fdf3f2"><b>Row {r["Row"]} &middot; {html.escape(kind)}</b>: '
                     f'{html.escape(r["TargetPSMPServer"])} / {html.escape(r["PSMPEnduser"])} &rarr; {html.escape(r["TargetUser"])}@{html.escape(r["TargetServer"])}'
                     f'<br><b>Failed step:</b> {html.escape(r["FailedStep"])}<br><b>Reason:</b> {html.escape(r["Reason"])}<br><b>Steps:</b> {html.escape(steps)}'
                     f'<br><b>High-level fixes to try:</b><ul style="margin:4px 0">' + "".join(f"<li>{html.escape(x)}</li>" for x in fx) + f'</ul><span style="color:#666">Detail log on {html.escape(host)}: {html.escape(r["DetailLog"])}</span></div>')
    T += ["", "WHAT EACH STEP MEANS", "  CCP password = end user's vault password fetched from CCP | PSMP login = PSMP accepted the login and a shell on the target was reached",
          "  Target user = whoami on the target is the expected account | Session held = second whoami after the hold time, session stayed open", f"Results CSV attached: {os.path.basename(csvpath)}"]
    H.append('<p style="color:#555;font-size:12px"><b>Steps:</b> CCP password = vault password fetched from CCP &middot; PSMP login = login accepted, shell reached on the target &middot; Target user = whoami is the expected account &middot; Session held = still open after the hold time.'
             f'<br>Full results CSV attached: {html.escape(os.path.basename(csvpath))}</p></body></html>')
    return "\n".join(T), "\n".join(H)

def send_mail(cfg, subject, text, htmlbody, attach, shots=None):
    msg = EmailMessage(); msg["From"] = cfg["from"]; msg["To"] = ", ".join(cfg["to"]); msg["Subject"] = subject
    msg.set_content(text); msg.add_alternative(htmlbody, subtype="html")
    for n, p in sorted((shots or {}).items()):
        try: msg.get_payload()[1].add_related(open(p, "rb").read(), maintype="image", subtype="png", cid=f"<shot{n}>", filename=os.path.basename(p))
        except OSError: pass
    if attach: msg.add_attachment(open(attach, "rb").read(), maintype="text", subtype="csv", filename=os.path.basename(attach))
    if cfg.get("dry_run"):
        print("--- MAIL DRY RUN (not sent) ---"); print(f"To: {msg['To']}\nSubject: {subject}\n"); print(text); return True
    with smtplib.SMTP(cfg["smtp_host"], int(cfg.get("smtp_port", 25)), timeout=20) as s: s.send_message(msg)
    return True
