# Troubleshooting
| Message | Meaning | Fix |
|---|---|---|
| `CCP http=...` / `CCP account user name is 'x', expected 'y'` | CCP refused, or the CCP object belongs to another user | AppID allowed machine / safe rights; object name = the PSMP end user |
| `no shell reached through PSMP (... password prompted again)` | PSMP rejected the vault password | rotate/reconcile the end user's password in the CCP object; check the user is not suspended |
| `... Permission denied` | PSMP refused the login or the target account mapping | end user rights on the target account; PSMP policy |
| `... Connection refused / timed out` | PSMP server down or firewalled | PSMP service, TCP port, firewall |
| `Host key verification failed` | the PSMP host key changed | confirm it is expected, then remove its line from `envs/<name>/known_hosts` |
| `whoami is 'x' ..., expected 'y'` | login mapped to a different account | CSV `TargetUser` or the PSMP mapping |
| `second whoami missing: session dropped` / `gap ... expected >=` | the session ended before the hold time | PSMP/target idle limits, firewall dropping SSH, PSMP log |
| `precheck: ... not reachable on TCP` | PSMP server down, DNS or firewall | fix before the next run; the test never logged in |
