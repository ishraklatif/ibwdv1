#!/bin/sh
# usage (inside the container): run_tests.sh MODE   where MODE is `plain` or `traced`; PYTEST_ARGS carries the selected tests.
# Test results and traces go to SEPARATE files under /out.
set -u
MODE="$1"; mkdir -p /out/$MODE /out/$MODE/traces
cd /work
if [ "$MODE" = traced ]; then
  export IBWD_TRACE_OUT=/out/$MODE/traces IBWD_TRACE_ROOT=/work IBWD_TRACE_SCOPE=/trace/manifest.json PYTHONPATH=/trace
fi
START=$(date +%s)
timeout "${TIMEOUT:-1500}" python -m pytest $PYTEST_ARGS -p no:cacheprovider -q --junitxml=/out/$MODE/junit.xml > /out/$MODE/pytest.log 2>&1
echo "exit=$? seconds=$(( $(date +%s) - START ))" > /out/$MODE/status.txt
