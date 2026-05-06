"""Cockpit Phase A smoke test — exercises the new instruments end-to-end.

Hits POST /agents/{agent_id}/interact on the running jvagent server and
captures the full response, tool dispatch trace, and active artifacts so
they can be inspected afterwards.

Run from the jvagent repo root with the server up on :8000:

    python cockpit_phaseA_smoke.py

Writes results to ./cockpit_phaseA_smoke_results.json.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

BASE_URL = os.environ.get("JVAGENT_URL", "http://localhost:8000")
AGENT_ID = os.environ.get("JVAGENT_AGENT_ID", "n.Agent.fde66b9f5607427bab9c9c08")
RESULTS_PATH = os.environ.get("JVAGENT_RESULTS", "cockpit_phaseA_smoke_results.json")
TIMEOUT = float(os.environ.get("JVAGENT_TIMEOUT", "180"))


def _post(url: str, body: Dict[str, Any]) -> Dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            payload = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {
            "_http_error": e.code,
            "_body": body[:4000],
        }
    except Exception as e:
        return {"_transport_error": str(e)}
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return {"_raw": payload[:4000]}


def interact(
    utterance: str,
    *,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    body: Dict[str, Any] = {"utterance": utterance, "channel": "default"}
    if user_id:
        body["user_id"] = user_id
    if session_id:
        body["session_id"] = session_id
    url = f"{BASE_URL}/agents/{AGENT_ID}/interact"
    started = time.time()
    out = _post(url, body)
    out["_duration_s"] = round(time.time() - started, 2)
    out["_utterance"] = utterance
    return out


def summarize(resp: Dict[str, Any]) -> Dict[str, Any]:
    """Pull the data the test cares about from a verbose interact response."""
    data = resp.get("data") or resp
    interaction = data.get("interaction") or {}
    return {
        "duration_s": resp.get("_duration_s"),
        "user_id": data.get("user_id"),
        "session_id": data.get("session_id"),
        "response": data.get("response"),
        "actions": interaction.get("actions"),
        "events": interaction.get("events"),
        "directives": interaction.get("directives"),
        "active_tasks": interaction.get("active_tasks"),
        "completed_tasks": interaction.get("completed_tasks"),
        "observability_metrics": interaction.get("observability_metrics"),
        "agent_trace": interaction.get("agent_trace"),
        "artifacts": interaction.get("artifacts"),
        "interaction_id": interaction.get("id"),
        "_raw_keys": list(data.keys()) if isinstance(data, dict) else None,
        "_error": resp.get("_http_error") or resp.get("_transport_error"),
        "_body": resp.get("_body") or resp.get("_raw"),
    }


def assert_(cond: bool, msg: str, results: List[Dict[str, Any]]) -> None:
    results.append({"check": msg, "passed": bool(cond)})


def main() -> int:
    cases: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Test 1: artifact write/read round trip
    # ------------------------------------------------------------------
    user_id = None
    session_id = None
    raw1 = interact(
        "Use the artifact_add tool to store an artifact named "
        "'test_artifact_alpha' with the data 'phaseA verification payload' "
        "and tag 'smoke'. Then briefly confirm in your reply that you saved it."
    )
    s1 = summarize(raw1)
    user_id = s1.get("user_id")
    session_id = s1.get("session_id")
    cases.append({"case": "1. artifact_add", "summary": s1, "raw": raw1})

    raw2 = interact(
        "Retrieve the artifact named 'test_artifact_alpha' using artifact_get and tell me its data verbatim.",
        user_id=user_id,
        session_id=session_id,
    )
    s2 = summarize(raw2)
    cases.append({"case": "2. artifact_get", "summary": s2, "raw": raw2})

    # ------------------------------------------------------------------
    # Test 3: artifact_search by tag
    # ------------------------------------------------------------------
    raw3 = interact(
        "Use artifact_search with tag='smoke' to list artifacts tagged 'smoke' "
        "in this conversation. Report what you find.",
        user_id=user_id,
        session_id=session_id,
    )
    s3 = summarize(raw3)
    cases.append({"case": "3. artifact_search by tag", "summary": s3, "raw": raw3})

    # ------------------------------------------------------------------
    # Test 4: cockpit_search engine surface (skills + tools, no interact_actions)
    # ------------------------------------------------------------------
    raw4 = interact(
        "Use the cockpit_search tool with query='web search' to find capabilities "
        "available to you. Report the categories you got back (just the section headers).",
        user_id=user_id,
        session_id=session_id,
    )
    s4 = summarize(raw4)
    cases.append(
        {"case": "4. cockpit_search engine surface", "summary": s4, "raw": raw4}
    )

    # ------------------------------------------------------------------
    # Test 5: deny-list / kind filter — explicit interact_actions request
    # ------------------------------------------------------------------
    raw5 = interact(
        "Try to call cockpit_search with query='handoff' and kind='interact_actions'. "
        "Report literally what the tool returned.",
        user_id=user_id,
        session_id=session_id,
    )
    s5 = summarize(raw5)
    cases.append(
        {
            "case": "5. cockpit_search rejects interact_actions in engine",
            "summary": s5,
            "raw": raw5,
        }
    )

    # ------------------------------------------------------------------
    # Test 6: artifact lifecycle — update + delete
    # ------------------------------------------------------------------
    raw6 = interact(
        "Call artifact_update on key='test_artifact_alpha' with new data 'updated body' "
        "and tags ['smoke', 'updated']. Then call artifact_delete on the same key. "
        "Confirm both operations worked.",
        user_id=user_id,
        session_id=session_id,
    )
    s6 = summarize(raw6)
    cases.append({"case": "6. artifact update + delete", "summary": s6, "raw": raw6})

    # ------------------------------------------------------------------
    # Persist results
    # ------------------------------------------------------------------
    out: Dict[str, Any] = {
        "base_url": BASE_URL,
        "agent_id": AGENT_ID,
        "completed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "user_id": user_id,
        "session_id": session_id,
        "cases": cases,
    }

    with open(RESULTS_PATH, "w") as f:
        json.dump(out, f, indent=2, default=str)

    # Quick console summary
    print(f"Wrote {RESULTS_PATH}")
    for c in cases:
        s = c["summary"]
        err = s.get("_error")
        actions = s.get("actions") or []
        reply = (s.get("response") or "")[:120].replace("\n", " ")
        flag = " ERROR" if err else ""
        print(
            f"  - {c['case']}{flag}  ({s.get('duration_s', '?')}s)  "
            f"actions={len(actions)}  reply={reply!r}"
        )
        if err:
            print(f"     err: {err}  body: {(s.get('_body') or '')[:200]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
