#!/bin/sh
set -e
cd "$(dirname "$0")"

RUNS=${RUNS:-1}
PORT=${PORT:-8766}
PYTHON=${PYTHON:-venv/bin/python}

run_test() {
  name=$1
  shift
  echo "##### $name"
  "$PYTHON" run_demo.py --runs "$RUNS" --port "$PORT" --quiet-server \
    --output-stem "results/$name" "$@"
}

run_test t1 --clients 20 --duration 20
run_test t2 --clients 100 --duration 40
run_test t3 --clients 100 --duration 60 --slow-percent 10 --slow-ms 500
run_test t4 --clients 100 --duration 60 \
  --disconnect-percent 20 --disconnect-at 15 --disconnect-seconds 5
