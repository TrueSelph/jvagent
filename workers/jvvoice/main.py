"""jvvoice: bridges WhatsApp call audio to jvagent Orchestrator.

Run as a standalone service (this directory is the project / repo root)::

    pip install -r requirements.txt
    cp .env.example .env   # fill in keys
    python main.py dev

Production / Docker::

    python main.py start

Registers with LiveKit under ``LIVEKIT_AGENT_NAME`` (default ``jvvoice``),
matching ``LiveKitWhatsAppAction.agent_name`` on the jvagent agent.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path


def _load_dotenv() -> None:
    """Load ``.env`` from this directory when present (no-op in Docker)."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.is_file():
        load_dotenv(env_path, override=True)


_load_dotenv()

from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.plugins import deepgram, elevenlabs, silero

from dispatch import MissingDispatchMetadata, resolve_call_context
from jvagent_llm import JvagentOrchestratorLLM

logger = logging.getLogger(__name__)

_AGENT_NAME = os.environ.get("LIVEKIT_AGENT_NAME", "jvvoice")
_ELEVEN_VOICE = os.environ.get("ELEVENLABS_VOICE_ID", "")
_ELEVEN_MODEL = os.environ.get("ELEVENLABS_TTS_MODEL", "eleven_turbo_v2_5")
_DEEPGRAM_MODEL = os.environ.get("DEEPGRAM_STT_MODEL", "nova-3")


async def entrypoint(ctx: JobContext) -> None:
    """Join a LiveKit room dispatched from WhatsApp Connector."""
    await ctx.connect()

    try:
        call_context = await resolve_call_context(ctx)
    except MissingDispatchMetadata as exc:
        logger.error("Rejecting call: %s", exc)
        ctx.shutdown(reason="missing jvagent dispatch metadata")
        return

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
    import sys

    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command != "download-files":
        if not os.environ.get("LIVEKIT_URL"):
            raise SystemExit(
                "LIVEKIT_URL is required. Set it in your environment or in "
                ".env (from your LiveKit Cloud project settings). "
                "Also set LIVEKIT_API_KEY and LIVEKIT_API_SECRET."
            )
        logger.info(
            "jvvoice registering as LIVEKIT_AGENT_NAME=%s "
            "(jvagent host resolved per call from dispatch metadata)",
            _AGENT_NAME,
        )
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name=_AGENT_NAME,
        )
    )


if __name__ == "__main__":
    main()
