#!/bin/bash
# psmp-engine.sh - ONE PSMP test (called by run-psmp-tests.py, or by hand with the env vars below).
# Flow: CCP (end user's vault password, memory only) -> ONE ssh session through PSMP as <PSMP_USER>@<TARGET_USER>@<TARGET_HOST>
#       running the FIXED command "whoami; sleep WAIT; whoami" -> checks. No setting, CSV column or env var can change what runs on the target.
# Required env: PSMP_HOST PSMP_USER TARGET_USER TARGET_HOST CP_APPID CP_SAFE CP_OBJECT CCP_URL
# Optional env: PSMP_PORT(22) WAIT(60) PSMP_REASON CP_FOLDER(Root) CCP_CERT CCP_KEY PSMP_KEY KNOWN_HOSTS RESULT_JSON
set -uo pipefail
HERE=$(dirname "$(readlink -f "$0")")
for v in PSMP_HOST PSMP_USER TARGET_USER TARGET_HOST CP_APPID CP_SAFE CP_OBJECT CCP_URL; do [ -n "${!v:-}" ] || { echo "missing env var: $v"; exit 2; }; done
PORT=${PSMP_PORT:-22}; WAIT=${WAIT:-60}; export PSMP_REASON=${PSMP_REASON:-automated psmp health test}
OUT=$(mktemp); trap 'rm -f "$OUT"; unset PW' EXIT
declare -A ST=([ccp]="not run" [login]="not run" [identity]="not run" [hold]="not run")
problems=(); failed=""; PW=""; rc=NA; gap=0
note() { echo "[$(date +%T)] $*"; }
soft() { ST[$1]=FAIL; problems+=("$1: $2"); [ -n "$failed" ] || failed=$1; }

hints() { case "$1" in
  ccp) cat <<T
 - Check the CCP URL is reachable from this host and returns HTTP 200.
 - Check the AppID's Allowed Machine includes this host's IP and the AppID may read the safe.
 - Check the safe/object names, and that the CCP account's user name equals the PSMP end user.
T
;;
  login) cat <<T
 - Check the PSMP server is up and reachable on its SSH port; check the PSMP service on that server.
 - Check the end user's vault password in the CCP object is current (not rotated/expired) and the user is not suspended.
 - Check the end user may use the target account and the target is reachable from the PSMP server (target user and server in the CSV).
 - If a prompt appeared twice, the login was rejected: see the detail log.
 - "Failed to obtain account ... 076E Password object was not found": the target account (user name + address as in the CSV) is not found in the Vault for this end user. Check the account's user name and address in PVWA, that the end user is a member of its safe with Use/Retrieve rights, and that the PSM for SSH proxy user can read that safe.
T
;;
  identity) cat <<T
 - The session reached a shell but as a different user than TargetUser: check the account mapping on the PSMP server and the CSV value.
T
;;
  hold) cat <<T
 - The session did not stay open for the whole hold time: check PSMP/target idle or session time limits, firewalls dropping SSH, and the PSMP log for the session.
T
;;
  *) echo " - See the detail log." ;; esac; }

finish() {
  local res=PASS; [ ${#problems[@]} -eq 0 ] || res=FAIL
  [ ${#problems[@]} -eq 0 ] || printf 'problem: %s\n' "${problems[@]}"
  for s in ccp login identity hold; do printf '  %-9s %s\n' "$s" "${ST[$s]}"; done
  echo "RESULT: $res"
  if [ -n "${RESULT_JSON:-}" ]; then
    jq -nc --arg r "$res" --arg f "$failed" --arg why "${problems[0]:-}" --arg fx "$([ "$res" = FAIL ] && hints "$failed")" --argjson gap "${gap:-0}" \
      --arg ccp "${ST[ccp]}" --arg login "${ST[login]}" --arg identity "${ST[identity]}" --arg hold "${ST[hold]}" \
      '{result:$r,failed_step:$f,reason:$why,detail_log:"",gap_sec:$gap,steps:{ccp:$ccp,login:$login,identity:$identity,hold:$hold},fixes:$fx}' >> "$RESULT_JSON"
  fi
  [ "$res" = PASS ]; exit $?
}

# ---- 1) CCP: the end user's password, in memory only
command -v curl >/dev/null && command -v jq >/dev/null && command -v python3 >/dev/null || { soft ccp "curl, jq or python3 not installed"; ST[ccp]=FAIL; finish; }
note "fetching the end user's password from CCP ($CCP_URL AppID=$CP_APPID Safe=$CP_SAFE)"
copts=(-sS --max-time 20 -G -w '\n%{http_code}'); [ -n "${CCP_CERT:-}" ] && copts+=(--cert "$CCP_CERT"); [ -n "${CCP_KEY:-}" ] && copts+=(--key "$CCP_KEY")
resp=$(curl "${copts[@]}" "$CCP_URL/AIMWebService/api/Accounts" --data-urlencode "AppID=$CP_APPID" --data-urlencode "Safe=$CP_SAFE" --data-urlencode "Folder=${CP_FOLDER:-Root}" \
  --data-urlencode "Object=$CP_OBJECT" --data-urlencode "Reason=$PSMP_REASON" 2>&1); crc=$?
http=${resp##*$'\n'}; body=${resp%$'\n'*}
if [ $crc -ne 0 ]; then soft ccp "CCP call failed (curl rc=$crc): $(tr '\n' ' ' <<<"$resp" | cut -c1-160)"; unset resp body; finish
elif [ "$http" != 200 ]; then soft ccp "CCP http=$http $(printf '%s' "$body" | jq -r '"\(.ErrorCode // "") \(.ErrorMsg // "")"' 2>/dev/null | cut -c1-200)"; unset resp body; finish; fi
cp_user=$(printf '%s' "$body" | jq -r '.UserName // empty'); PW=$(printf '%s' "$body" | jq -r '.Content // empty'); unset resp body
[ -n "$PW" ] || { soft ccp "CCP returned no password"; finish; }
[ "$cp_user" = "$PSMP_USER" ] || { soft ccp "CCP account user name is '$cp_user', expected '$PSMP_USER' (wrong object?)"; unset PW; finish; }
ST[ccp]=OK; note "CCP OK (password length ${#PW}, not shown)"

# ---- 2) one PSMP session; the remote command is FIXED here
ssh_opts=(-p "$PORT" -o ServerAliveInterval=15 -o ServerAliveCountMax=4 -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new)
[ -n "${KNOWN_HOSTS:-}" ] && ssh_opts+=(-o "UserKnownHostsFile=$KNOWN_HOSTS")
[ -n "${PSMP_KEY:-}" ] && ssh_opts+=(-i "$PSMP_KEY")
remote="echo \"[1] \$(date +%s) \$(whoami)\"; sleep ${WAIT}; echo \"[2] \$(date +%s) \$(whoami)\""
note "connecting: ${PSMP_USER}@${TARGET_USER}@${TARGET_HOST} via ${PSMP_HOST}:${PORT} (hold ${WAIT}s)"
start=$(date +%s)
PSMP_PASSWORD="$PW" PSMP_TIMEOUT=$(( WAIT + 120 )) python3 -I "$HERE/psmp-ssh.py" "${ssh_opts[@]}" -l "${PSMP_USER}@${TARGET_USER}@${TARGET_HOST}" "$PSMP_HOST" "$remote" >"$OUT" 2>&1
rc=$?; unset PW; elapsed=$(( $(date +%s) - start ))
sed 's/^/    ssh: /' "$OUT" | head -40

# ---- 3) evaluate
l1=$(grep -E '^\[1\] [0-9]+ ' "$OUT" | tail -1); l2=$(grep -E '^\[2\] [0-9]+ ' "$OUT" | tail -1)
t1=$(awk '{print $2}' <<<"$l1"); u1=$(awk '{print $3}' <<<"$l1"); t2=$(awk '{print $2}' <<<"$l2"); u2=$(awk '{print $3}' <<<"$l2")
if [ -z "$l1" ]; then
  why=$(grep -a -o -i -E 'Failed to obtain account[^\n]{0,260}|permission denied[^.]*|password prompted again[^.]*|reason prompted again|Connection (refused|timed out|reset)[^.]*|No route to host|Could not resolve[^.]*|Host key verification failed|Too many authentication failures' "$OUT" | head -1)
  soft login "no shell reached through PSMP (ssh rc=$rc${why:+, $why})"
else
  ST[login]=OK
  if [ "$u1" = "$TARGET_USER" ] && { [ -z "$l2" ] || [ "$u2" = "$TARGET_USER" ]; }; then ST[identity]=OK
  else soft identity "whoami is '${u1}'${l2:+ then '${u2}'}, expected '$TARGET_USER'"; fi
  if [ -z "$l2" ]; then soft hold "second whoami missing: session dropped before ${WAIT}s (ssh rc=$rc, elapsed ${elapsed}s)"
  else gap=$(( t2 - t1 )); if [ $gap -ge $(( WAIT - 2 )) ] && [ $rc -eq 0 ]; then ST[hold]=OK; else soft hold "gap between whoami calls ${gap}s (expected >= ${WAIT}s), ssh rc=$rc"; fi; fi
fi
note "elapsed ${elapsed}s, gap ${gap}s"
finish
