#!/bin/sh
# Run the tests in tests.json inside the liverecall Multipass VM, RUNS
# independent runs each. Prints one row of medians as each test finishes.
# Usage: ./run_tests.sh [--tests=id1,id2]    (default: every test)
set -eu
cd "$(dirname "$0")"
ROOT=$(pwd)
VM=${VM:-liverecall}
VM_DIR=/home/ubuntu/cs3103_a4
VENV=/home/ubuntu/venv
PORT=8766
RUNS=${RUNS:-1}
RESULTS=results/$(date +%s)
ROW='%-16s %12s %14s %8s %10s %10s %10s %10s\n'

case "${1:-}" in
  "") SELECTED= ;;
  --tests=*) SELECTED=${1#--tests=} ;;
  *) echo "Usage: ./run_tests.sh [--tests=id1,id2]" >&2; exit 1 ;;
esac

# One "id<TAB>profile<TAB>args" line per selected test, in tests.json order.
PLAN=$(python3 - "$SELECTED" <<'PY'
import json, sys

tests = json.load(open("tests.json"))
for test_id in sys.argv[1].split(",") if sys.argv[1] else tests:
    if test_id not in tests:
        sys.exit(f"Unknown test: {test_id}")
    test = tests[test_id]
    print(test_id, test.get("network_profile", "-"), test["args"], sep="\t")
PY
)

STATE=$(multipass info "$VM" | awk '/^State:/{print $2}')
# Two runs on one port connect to each other's servers and corrupt both results.
if [ "$STATE" = Running ] && multipass exec "$VM" -- bash -lc "ss -Hltn | grep -q ':$PORT '"; then
  echo "Port $PORT is in use in $VM: is another run_tests.sh running?" >&2
  exit 1
fi

mkdir -p "$RESULTS"

if [ "$STATE" != Running ]; then
  multipass start "$VM" >&2
fi
# See vm/99-nohlt.cfg. This comes before mounting, because a restart drops mounts.
if ! multipass exec "$VM" -- grep -qw nohlt /proc/cmdline; then
  echo "Enabling nohlt in $VM and restarting it" >&2
  multipass transfer vm/99-nohlt.cfg "$VM:/tmp/99-nohlt.cfg"
  # bash -lc: multipass exec mangles the arguments of "sudo sh -c".
  multipass exec "$VM" -- bash -lc \
    "sudo mv /tmp/99-nohlt.cfg /etc/default/grub.d/ && sudo update-grub" >&2
  multipass restart "$VM" >&2
fi
if ! multipass info "$VM" | grep -qF "$VM_DIR"; then
  multipass mount "$ROOT" "$VM:$VM_DIR" >&2
fi
multipass exec "$VM" -- bash -lc "set -eu
  if [ ! -x '$VENV/bin/python' ]; then
    python3 -m venv '$VENV'
    '$VENV/bin/pip' install -q -r '$VM_DIR/requirements.txt'
  fi" >&2

# Prints the median of each metric across the test's runs, one field per column.
print_row() {
  # shellcheck disable=SC2046  # word splitting into printf fields is intended
  printf "$ROW" $(python3 - "$RESULTS" "$1" "$RUNS" <<'PY'
import json, statistics, sys

results, test_id, runs = sys.argv[1], sys.argv[2], int(sys.argv[3])
# run_demo.py only adds the _runNN suffix when there is more than one run.
stems = [f"{test_id}_run{i:02d}" for i in range(1, runs + 1)] if runs > 1 else [test_id]
paths = [f"{results}/{stem}.json" for stem in stems]
summaries = [json.load(open(path)) for path in paths]

def median(key, fmt="{:.2f}"):
    values = [s[key] for s in summaries if key in s and s[key] == s[key]]  # skip NaN
    return fmt.format(statistics.median(values)) if values else "-"

print(
    test_id,
    median("unique_finals", "{:.0f}") + "/" + median("expected_finals_total", "{:.0f}"),
    median("final_completeness_pct", "{:.2f}%"),
    median("connection_errors", "{:.0f}"),
    median("p50_ms"),
    median("p95_ms"),
    median("p99_ms"),
    median("fresh_pct", "{:.2f}%"),
)
PY
  )
}

run_test() {
  test_id=$1 profile=$2 args=$3
  apply_profile=true
  if [ "$profile" != - ]; then
    apply_profile="sudo bash ./network_profiles/$profile.sh lo && tc qdisc show dev lo"
  fi
  # stdin is the test plan, so multipass must not read it.
  multipass exec "$VM" -- bash -lc "set -eu
    cd '$VM_DIR'
    export PYTHONDONTWRITEBYTECODE=1
    ulimit -n \"\$(ulimit -Hn)\"  # one socket per client; the default 1024 caps scale tests
    trap 'sudo bash ./network_profiles/reset.sh lo || true' EXIT
    sudo bash ./network_profiles/reset.sh lo
    $apply_profile
    '$VENV/bin/python' run_demo.py --runs $RUNS --port $PORT --quiet-server \
      --output-stem '$RESULTS/$test_id' $args
  " </dev/null >"$RESULTS/$test_id.log" 2>&1
}

# shellcheck disable=SC2059  # ROW is the shared format string
printf "$ROW" test finals completeness conn_err p50_ms p95_ms p99_ms fresh
TAB=$(printf '\t')
echo "$PLAN" | while IFS=$TAB read -r test_id profile args; do
  if ! run_test "$test_id" "$profile" "$args"; then
    echo "$test_id failed. Last lines of $RESULTS/$test_id.log:" >&2
    tail -n 40 "$RESULTS/$test_id.log" >&2
    exit 1
  fi
  print_row "$test_id"
done
