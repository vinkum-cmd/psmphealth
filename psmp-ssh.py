#!/usr/bin/env python3
"""Run `ssh <args>` and answer the PSMP prompts from env vars: PSMP_PASSWORD (Vault Password) and PSMP_REASON.
Output goes to stdout (password redacted). Exit code = ssh exit code; 3 = prompt problem (bad password / missing reason)."""
import os, sys, pexpect

pw = os.environ.get("PSMP_PASSWORD", "")
reason = os.environ.get("PSMP_REASON", "")
timeout = int(os.environ.get("PSMP_TIMEOUT", "300"))

child = pexpect.spawn("ssh", sys.argv[1:], encoding="utf-8", timeout=timeout)
sent_pw = False
sent_reason = False

def emit(text):
    text = text.replace("\r", "")
    if pw:
        text = text.replace(pw, "********")
    sys.stdout.write(text)
    sys.stdout.flush()

PATTERNS = [r"[Pp]assword[^\n]*:\s*$", r"specify a reason[^\n]*:\s*$", pexpect.EOF]
while True:
    i = child.expect(PATTERNS)
    emit((child.before or "") + (child.after if isinstance(child.after, str) else ""))
    if i == 0:
        if not pw:
            emit("\n[psmp-ssh] password prompt but PSMP_PASSWORD is not set\n"); child.close(force=True); sys.exit(3)
        if sent_pw:
            emit("\n[psmp-ssh] password prompted again: login rejected\n"); child.close(force=True); sys.exit(3)
        sent_pw = True
        child.sendline(pw)
    elif i == 1:
        if not reason:
            emit("\n[psmp-ssh] reason prompt but PSMP_REASON is not set\n"); child.close(force=True); sys.exit(3)
        if sent_reason:
            emit("\n[psmp-ssh] reason prompted again\n"); child.close(force=True); sys.exit(3)
        sent_reason = True
        child.sendline(reason)
    else:
        break
child.close()
sys.exit(child.exitstatus if child.exitstatus is not None else 255)
