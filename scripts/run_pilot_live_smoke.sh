#!/bin/sh
# Bounded live pilot smoke through the signed-in Ollama daemon.
set -eu

if [ ! -t 0 ]; then
    printf '%s\n' 'Run this script from an interactive terminal.' >&2
    exit 2
fi

saved_terminal_state=$(stty -g)
restore_terminal() {
    stty "$saved_terminal_state"
    unset JVAGENT_RUN_PILOT_LIVE_SMOKE
}
trap restore_terminal EXIT HUP INT TERM

printf '%s' 'Run the cost-capped GLM-5.3 Cloud smoke via the signed-in Ollama daemon? [y/N] ' >&2
IFS= read -r confirmation
case "$confirmation" in
    y|Y|yes|YES) ;;
    *)
        printf '%s\n' 'No API request was made.' >&2
        exit 2
        ;;
esac

if ! curl --fail --silent --show-error --max-time 2 \
    http://127.0.0.1:11434/api/version >/dev/null; then
    printf '%s\n' 'Ollama is not responding on 127.0.0.1:11434; no model call was made.' >&2
    exit 2
fi

export JVAGENT_RUN_PILOT_LIVE_SMOKE=1
uv run --python 3.10 --extra test --extra pydantic-pilot \
    pytest tests/action/orchestrator/pilot/test_pilot_live_model_smoke.py -q -s
