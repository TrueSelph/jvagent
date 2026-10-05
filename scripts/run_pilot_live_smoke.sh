#!/bin/sh
# Bounded live pilot smoke. The key is read without terminal echo and is
# passed only to the isolated pytest process, never written to a file.
set -eu

if [ ! -t 0 ]; then
    printf '%s\n' 'Run this script from an interactive terminal.' >&2
    exit 2
fi

saved_terminal_state=$(stty -g)
restore_terminal() {
    stty "$saved_terminal_state"
    unset OPENAI_API_KEY JVAGENT_RUN_PILOT_LIVE_SMOKE
}
trap restore_terminal EXIT HUP INT TERM

printf '%s' 'OpenAI API key (hidden input; do not paste it into chat): ' >&2
stty -echo
IFS= read -r OPENAI_API_KEY
stty "$saved_terminal_state"
printf '\n' >&2

if [ -z "$OPENAI_API_KEY" ]; then
    printf '%s\n' 'No key entered; no API request was made.' >&2
    exit 2
fi

export OPENAI_API_KEY
export JVAGENT_RUN_PILOT_LIVE_SMOKE=1
uv run --python 3.10 --extra test --extra pydantic-pilot \
    pytest tests/action/orchestrator/pilot/test_pilot_live_model_smoke.py -q -s
