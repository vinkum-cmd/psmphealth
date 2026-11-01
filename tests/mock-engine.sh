#!/bin/bash
# Mock engine for the self-test: pops the first line of $MOCK_SEQ ("PASS" or "FAIL:<step>:<reason>") and writes the RESULT_JSON the runner expects. Opens no session.
line=$( ( flock 9; l=$(head -1 "$MOCK_SEQ"); sed -i 1d "$MOCK_SEQ"; echo "$l" ) 9>"$MOCK_SEQ.lock" )
echo "$PSMP_HOST $TARGET_USER@$TARGET_HOST -> ${line:-PASS} pwlen=${#PSMP_MANUAL_PW} ccp=${CCP_URL:-none}" >> "$MOCK_SEQ.calls"
echo "[10:00:00] connecting: mock via $PSMP_HOST"; echo "    ssh: [1] 1700000000 $TARGET_USER"; echo "    ssh: [2] 1700000060 $TARGET_USER"
sleep "${MOCK_SLEEP:-0}"
if [ "${line:-PASS}" = PASS ]; then
  jq -nc '{result:"PASS",failed_step:"",reason:"",detail_log:"",gap_sec:60,steps:{ccp:"OK",login:"OK",identity:"OK",hold:"OK"},fixes:""}' >> "$RESULT_JSON"
else
  IFS=: read -r _ st why <<<"$line"
  jq -nc --arg s "$st" --arg w "$why" '{result:"FAIL",failed_step:$s,reason:($s+": "+$w),detail_log:"",gap_sec:0,steps:{ccp:"OK",login:"FAIL",identity:"not run",hold:"not run"},fixes:" - mock fix"}' >> "$RESULT_JSON"
fi
exit 0
