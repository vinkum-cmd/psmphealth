# psmphealth (Linux/Unix) - v1

Tests PSMP the way an end user uses it: log on through a PSMP server to a target account on a target server, run `whoami`, stay connected for the hold time, run `whoami` again.
One CSV row = one test, so you can list many PSMP servers, each with its own target accounts and target servers. One summary mail per run with a terminal-style screenshot of every session embedded in it. `mail.send_on` = `always` (mail every run; subject "ALL OK (n tests)" when all pass) or `failure` (only FAILED / STILL FAILING / RECOVERED).

## Install (Linux with bash, python3 >= 3.9)
    sudo apt install jq curl openssh-client python3-pexpect python3-pil
    cp -r envs/example envs/prod
    $EDITOR envs/prod/env.json envs/prod/tests.csv
    python3 run-psmp-tests.py --env prod --validate     # tools, CSV, DNS, ports, CCP, mail - opens no session
    python3 run-psmp-tests.py --env prod --only 1 --no-mail
    python3 tests/selftest.py                           # offline self-test of the tool (no PSMP needed)

## tests.csv (one row per test; blank cells take the default from env.json)
| Column | Meaning |
|---|---|
| `TargetPSMPServer` | PSMP server (name or IPv4). Different rows can use different PSMP servers |
| `PSMPPort` | SSH port of the PSMP server (default 22) |
| `TargetUser` | target account the end user connects as (checked with `whoami`) |
| `TargetServer` | target server behind PSMP |
| `PSMPEnduser`, `EndUserSafe`, `EndUserApp-ID`, `EndUserObjectName`, `TargetCCPUrl` | the end user and where CCP finds its vault password; defaults from env.json, override per row (e.g. a different end user for another PSMP) |
| `HoldSec` (10-900), `Retries` (0-2), `Enabled` (yes/no) | per-row options |
Allowed characters: letters, digits and `. _ : @ / - space`; hosts as names or IPv4. There is no column for commands: the command run on the target is fixed in `psmp-engine.sh`.

## Commands
| Command | What it does |
|---|---|
| `run-psmp-tests.py --env prod` | full run, mail per policy |
| `--only 1,3` / `--no-mail` / `--dry-run` | subset / no mail / show rows only |
| `--parallel N` | run N tests at once (1-10; or `reliability.parallel`) - keeps long lists fast |
| `--validate` | environment checklist, no session |
| `--pause` / `--resume` | kill switch |
| `--heartbeat-check` | alert if no run finished within `reliability.heartbeat_hours` |
| `--test-mail` / `--resend results.csv` / `--version` | mail checks / re-send a report / version |

## Screenshots
After each row the tool draws the session output (our notes plus what the target returned: both `whoami` lines with their times) as a PNG, green title bar for PASS, red for FAIL, saved as `envs/<name>/logs/runs/<run>/row-N.screen.png` (mode 600), column `Screenshot` in the results CSV, inline in the mail. env.json `outputs.screenshots`: `all` (default) | `failures` | `off`; `screenshots.font_size/max_lines/width_chars` tune the look. Needs python3-pil. The image is made from output the engine already redacted, so the vault password never appears.

## What each step proves
CCP password (the end user's vault password was fetched) -> PSMP login (PSMP accepted login + reason, shell on the target reached) -> Target user (`whoami` is the expected account) -> Session held (second `whoami` after the hold time).

## Reliability built in
Pre-check of each PSMP server (DNS + TCP, IPv4) before logging in; retry per row; alert after `alert_after_failures` failed runs in a row; one run at a time per environment; kill switch; heartbeat; old files deleted after `outputs.keep_days`. Alerts always go to `mail.to` in env.json.

## Security
* The end user's password comes from CCP at run time, lives only in memory (handed to one ssh child process) and is redacted from output.
* The remote command is fixed in the code; CSV/JSON are read, validated and never executed. Host keys are pinned in `envs/<name>/known_hosts` on first use (accept-new).
* A release carries a SHA256SUMS file (`make-release.sh`). Restrict write access to the folder to admins.
More: docs/TROUBLESHOOTING.md, docs/SCHEDULE.md. The older tools ~/psmp-test and ~/psmp-health are not changed by this tool.
