"""LiveKit voice worker: bridges WhatsApp call audio to jvagent Orchestrator.

Run separately from the jvagent FastAPI server::

    pip install "jvagent[livekit-voice]"
    export LIVEKIT_URL=wss://your-project.livekit.cloud
    export LIVEKIT_API_KEY=...
    export LIVEKIT_API_SECRET=...
    export DEEPGRAM_API_KEY=...
    export ELEVENLABS_API_KEY=...
    export JVAGENT_PUBLIC_BASE_URL=https://your-jvagent-host
    python -m workers.livekit_voice.main dev

The worker registers under ``JVAGENT_VOICE_AGENT_NAME`` (default ``jvagent-voice``),
matching ``LiveKitWhatsAppAction.agent_name`` on the jvagent agent.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path


def _load_dotenv() -> None:
    """Load env files; examples/jvagent_app/.env overrides repo-root .env."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    repo_root = Path(__file__).resolve().parents[2]
    for path in (
        repo_root / ".env",
        repo_root / "examples" / "jvagent_app" / ".env",
    ):
        if path.is_file():
            load_dotenv(path, override=True)


_load_dotenv()

from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.plugins import deepgram, elevenlabs, silero

from .dispatch import resolve_call_context
from .jvagent_llm import JvagentOrchestratorLLM

logger = logging.getLogger(__name__)

_AGENT_NAME = os.environ.get("JVAGENT_VOICE_AGENT_NAME", "jvagent-voice")
_ELEVEN_VOICE = os.environ.get("ELEVENLABS_VOICE_ID", "")
_ELEVEN_MODEL = os.environ.get("ELEVENLABS_TTS_MODEL", "eleven_turbo_v2_5")
_DEEPGRAM_MODEL = os.environ.get("DEEPGRAM_STT_MODEL", "nova-3")


async def entrypoint(ctx: JobContext) -> None:
    """Join a LiveKit room dispatched from WhatsApp Connector."""
    await ctx.connect()

    call_context = await resolve_call_context(ctx)
    orchestrator = JvagentOrchestratorLLM.from_call_context(call_context)

    tts_kwargs: dict = {"model": _ELEVEN_MODEL}
    if _ELEVEN_VOICE:
        tts_kwargs["voice_id"] = _ELEVEN_VOICE

    session = AgentSession(
        vad=silero.VAD.load(),
        stt=deepgram.STT(model=_DEEPGRAM_MODEL, interim_results=True),
        llm=orchestrator,
        tts=elevenlabs.TTS(**tts_kwargs),
    )

    agent = Agent(
        instructions=(
            "You are a helpful voice assistant on a WhatsApp phone call. "
            "Keep responses concise and conversational."
        ),
    )

    await session.start(agent=agent, room=ctx.room)
    await session.generate_reply(
        instructions="Greet the caller briefly and ask how you can help."
    )


def main() -> None:
    if not os.environ.get("LIVEKIT_URL"):
        raise SystemExit(
            "LIVEKIT_URL is required. Set it in your environment or in "
            "examples/jvagent_app/.env (from your LiveKit Cloud project settings). "
            "Also set LIVEKIT_API_KEY and LIVEKIT_API_SECRET."
        )
    from .jvagent_bridge import jvagent_base_url

    logger.info("jvagent voice worker interact base URL: %s", jvagent_base_url())
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name=_AGENT_NAME,
        )
    )


if __name__ == "__main__":
    main()
