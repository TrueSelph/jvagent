"""Upload ingestion and vision reflex for OrchestratorInteractAction."""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from typing import Any, List, Optional, Set, Tuple

from jvagent.action.interact.utils.uploads import (
    DEFAULT_UPLOAD_KEYS,
    collect_uploads,
    decode_text,
    human_size,
)

logger = logging.getLogger(__name__)

_BACKREF_CUE = re.compile(
    r"\b(image|images|photo|photos|picture|pictures|pic|pics|screenshot|"
    r"file|files|document|documents|doc|docs|attachment|attachments|"
    r"upload|uploaded|sent|showed|shown|shared|earlier|before|previous|"
    r"them|those|these|it|that|compare|comparison|which|more|most|describe|"
    r"luxur)\w*",
    re.IGNORECASE,
)
_RECALL_MAX_ARTIFACTS = 2
_RECALL_MAX_CHARS = 1200
_MAX_UPLOADS_PER_TURN = 20


class OrchestratorUploadsMixin:
    async def _resolve_action(self, name: str) -> Optional[Any]:
        try:
            return await self.get_action(name)
        except Exception as exc:
            logger.debug("orchestrator: get_action(%r) raised: %s", name, exc)
            return None

    async def _interpret_upload(self, visitor: Any, item: Any) -> str:
        """Derive an interpretation for one upload, by kind (ADR-0021 S4).

        The single extension point that enriches an upload artifact with derived
        understanding. Today: images → a per-image VisionAction description (so
        an uploaded image is ONE artifact = file + its own interpretation, not
        two). Other kinds return "" here; documents/binaries get their own
        interpreters later (extraction/summary) by extending this dispatch —
        their artifact already carries the file reference + metadata.
        Returns "" when there is no interpreter or interpretation is suppressed.
        """
        if item.kind != "image":
            return ""
        if not self.vision:
            return ""
        data = getattr(visitor, "data", None) or {}
        if data.get("image_interpretation") is False:
            return ""
        vision = await self._resolve_action("VisionAction")
        if vision is None or not hasattr(vision, "describe"):
            return ""
        if item.raw is not None:
            entry: Any = {
                "base64": base64.b64encode(item.raw).decode("ascii"),
                "mime_type": item.mime,
                "filename": item.filename,
            }
        elif item.url:
            entry = {"url": item.url}
        else:
            return ""
        try:
            return (await vision.describe(visitor=visitor, images=[entry])) or ""
        except Exception as exc:
            logger.warning("ingest_uploads: image interpret failed: %s", exc)
            return ""

    async def _ingest_uploads(self, visitor: Any) -> str:
        """Persist every uploaded file in ``visitor.data`` as an artifact (S4).

        ADR-0021 S4. For each file across ``upload_data_keys`` (images, docs,
        generic attachments): write the bytes to the caller's per-user file
        storage and record ONE ``source="upload"`` conversation artifact that is
        the single home for that file — its reference (``path``/``mime``/
        ``size``) plus its content/understanding: text files decoded into the
        payload, images enriched in place with a per-image interpretation
        (consolidated, not a second artifact), other binaries a descriptor.
        Bytes are reaped with the artifact. Best-effort and bounded; returns the
        concatenated image interpretation(s) to seed the loop ("" if none).
        """
        if not self.ingest_uploads:
            return ""
        data = getattr(visitor, "data", None) or {}
        keys = list(self.upload_data_keys or DEFAULT_UPLOAD_KEYS)
        items = collect_uploads(data, keys)
        if not items:
            return ""
        conversation = getattr(visitor, "conversation", None)
        if conversation is None or not hasattr(conversation, "add_artifact"):
            return ""
        interaction = getattr(visitor, "interaction", None)

        from jvagent.core.sandbox import (
            resolve_agent_user,
            resolve_user_sandbox_relpath,
            sanitize_segment,
        )

        try:
            agent_id, user_id = await resolve_agent_user(visitor)
        except Exception:
            agent_id, user_id = (self.agent_id or ""), ""
        base_rel = resolve_user_sandbox_relpath(agent_id, user_id)
        iid = getattr(interaction, "id", "") or "turn"

        app = None
        try:
            from jvagent.core.app import App

            app = await App.get()
        except Exception:
            app = None

        seen: Set[Tuple[str, int]] = set()
        selected: List[Tuple[int, Any]] = []
        for idx, item in enumerate(items):
            if len(selected) >= _MAX_UPLOADS_PER_TURN:
                logger.debug(
                    "ingest_uploads: capped at %d files", _MAX_UPLOADS_PER_TURN
                )
                break
            dedup = (item.filename, item.size)
            if dedup in seen:
                continue
            seen.add(dedup)
            selected.append((idx, item))

        # Interpret concurrently — each image interpretation is a model
        # round-trip, and running them serially blocked the loop start for
        # the whole batch. Failures degrade to "" per item.
        async def _interp(one: Any) -> str:
            try:
                return await self._interpret_upload(visitor, one)
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("ingest_uploads: interpret failed: %s", exc)
                return ""

        interpretations: List[str] = (
            list(await asyncio.gather(*(_interp(it) for _, it in selected)))
            if selected
            else []
        )

        written = 0
        seeds: List[str] = []
        for (idx, item), interpretation in zip(selected, interpretations):
            # Persist bytes to the per-user slice (lean graph: path, not blob).
            path = ""
            if item.raw is not None and app is not None:
                safe = sanitize_segment(item.filename, default=f"file_{idx}")
                candidate = f"{base_rel}/uploads/{iid}/{idx}_{safe}"
                try:
                    if await app.save_file(
                        candidate, item.raw, metadata={"mime": item.mime}
                    ):
                        path = candidate
                except Exception as exc:  # pragma: no cover - defensive
                    logger.debug("ingest_uploads: save failed for %s: %s", safe, exc)
            tags = ["upload", item.kind, item.filename]
            if interpretation:
                payload = interpretation
                summary = (interpretation.strip().split("\n", 1)[0] or "")[:160]
                tags.append("interpreted")
                if item.kind == "image":
                    tags.append("vision")
                seeds.append(interpretation)
            elif item.kind == "text" and item.raw is not None:
                payload = decode_text(item.raw)
                summary = f"{item.filename} ({item.mime}, {human_size(item.size)})"
            else:
                loc = path or item.url or "(bytes not stored)"
                payload = (
                    f"Uploaded {item.kind}: {item.filename} "
                    f"({item.mime}, {human_size(item.size)}). Stored at: {loc}"
                )
                summary = f"{item.filename} ({item.mime}, {human_size(item.size)})"
            try:
                await conversation.add_artifact(
                    interaction,
                    name=item.filename or f"upload:{iid}:{idx}",
                    data=payload,
                    summary=summary,
                    source="upload",
                    kind=item.kind,
                    tags=tags,
                    filename=item.filename,
                    mime=item.mime,
                    size=item.size,
                    path=path,
                )
                written += 1
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("ingest_uploads: artifact write failed: %s", exc)
        return "\n\n---\n\n".join(seeds)

    async def _vision_reflex(self, visitor: Any) -> str:
        """Pre-loop image interpretation (ADR-0021).

        When ``vision`` is on, the current turn carries images, and vision isn't
        suppressed: run ``VisionAction`` (its own multimodal model), persist the
        interpretation as a ``source:"vision"`` conversation artifact, and return
        the text to seed the loop so this turn's reply uses it. Best-effort —
        any failure returns "" and the turn proceeds without vision.
        """
        if not self.vision:
            return ""
        data = getattr(visitor, "data", None) or {}
        if data.get("image_interpretation") is False:
            return ""
        if not (data.get("image_urls") or []):
            return ""
        vision = await self._resolve_action("VisionAction")
        if vision is None or not hasattr(vision, "describe"):
            return ""
        try:
            text = await vision.describe(visitor=visitor)
        except Exception as exc:
            logger.warning("orchestrator: vision reflex failed: %s", exc)
            return ""
        if not text:
            return ""
        conversation = getattr(visitor, "conversation", None)
        interaction = getattr(visitor, "interaction", None)
        if conversation is not None and hasattr(conversation, "add_artifact"):
            try:
                iid = getattr(interaction, "id", "") or ""
                summary = (text.strip().split("\n", 1)[0] or "")[:160]
                await conversation.add_artifact(
                    interaction,
                    name=(
                        f"image_interpretation:{iid}" if iid else "image_interpretation"
                    ),
                    data=text,
                    summary=summary,
                    source="vision",
                    tags=["image", "vision"],
                )
            except Exception as exc:
                logger.warning("orchestrator: vision artifact write failed: %s", exc)
        return text

    async def _artifact_recall_seed(self, visitor: Any) -> str:
        """Deterministically recall earlier image artifacts on a back-reference.

        ADR-0021 S3. The vision reflex covers turns that carry a *new* image; a
        weak model still fails to recall a *prior* image when the user refers
        back to it ("which house is nicer", "compare them"). When vision is on,
        this turn has no new image, the conversation holds image artifacts, and
        the utterance reads like a back-reference, seed the most recent image
        interpretation(s) into the loop so recall doesn't depend on the model
        choosing list_artifacts/get_artifact. Best-effort: returns "" on any miss.
        """
        if not self.vision:
            return ""
        data = getattr(visitor, "data", None) or {}
        if data.get("image_urls"):  # a new image → the vision reflex handles it
            return ""
        utterance = (getattr(visitor, "utterance", "") or "").lower()
        if not utterance or not _BACKREF_CUE.search(utterance):
            return ""
        conversation = getattr(visitor, "conversation", None)
        if conversation is None or not hasattr(conversation, "get_artifacts"):
            return ""
        try:
            # Consolidated image artifacts are source="upload" tagged "image"
            # (S4); legacy standalone interpretations are source="vision".
            items = await conversation.get_artifacts(tags=["image"])
            if not items:
                items = await conversation.get_artifacts(source="vision")
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("artifact recall seed: query failed: %s", exc)
            return ""
        if not items:
            return ""
        # Most recent first, capped, each payload bounded to keep the prompt lean.
        chunks: List[str] = []
        for art in list(items)[-_RECALL_MAX_ARTIFACTS:]:
            text = (getattr(art, "data", "") or "").strip()
            if text:
                chunks.append(text[:_RECALL_MAX_CHARS])
        return "\n\n---\n\n".join(chunks)
