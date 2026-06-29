"""Messenger interact webhook helpers (parity with WhatsApp interact flow)."""

import asyncio
import base64
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from fastapi import Request
from jvspatial.exceptions import DatabaseError

from jvagent.action.facebook_action.facebook_api import FacebookAPI
from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.action.utils.meta_webhook import verify_meta_webhook_signature
from jvagent.action.whatsapp.utils.endpoint_helpers import (
    _build_utterance_with_quoted_context,
    get_conversation_with_lock,
)
from jvagent.action.whatsapp.utils.media_manager import MediaManager
from jvagent.core.app import App
from jvagent.core.public_url import get_public_base_url

logger = logging.getLogger(__name__)

# Same default as WhatsApp batched media (media_batch_manager._batch_utterance_and_media_urls).
MESSENGER_DEFAULT_MEDIA_UTTERANCE = "Please receive and interpret the attached media."


def verify_meta_messenger_signature(
    raw_body: bytes, request: Request, app_secret: str
) -> bool:
    """Verify ``X-Hub-Signature-256`` using Meta app secret (SHA256 HMAC, hex digest)."""
    return verify_meta_webhook_signature(raw_body, request, app_secret)


def _display_name_from_psid_profile(profile: Dict[str, Any]) -> Optional[str]:
    if not isinstance(profile, dict) or profile.get("error"):
        return None
    first = (profile.get("first_name") or "").strip()
    last = (profile.get("last_name") or "").strip()
    if first and last:
        return f"{first} {last}"
    if first:
        return first
    if last:
        return last
    return None


async def prime_messenger_sender_actions(
    agent_id: str,
    fb_action: Any,
    run_graph: Callable[[str, Any, Callable[[], Any]], Awaitable[Any]],
    sender_psid: str,
) -> None:
    """Messenger Sender Actions: read receipt + typing. No API ``recording`` action — ``typing_on`` = busy."""
    if not fb_action or not fb_action.is_configured():
        return
    for sa in ("mark_seen", "typing_on"):
        try:

            def _sender_action_call(action_name: str = sa) -> Any:
                return fb_action.api().send_sender_action(sender_psid, action_name)

            result = await run_graph(agent_id, fb_action, _sender_action_call)
            if isinstance(result, dict) and result.get("error"):
                logger.debug(
                    "Messenger sender_action %s failed for %s: %s",
                    sa,
                    sender_psid,
                    result.get("error"),
                )
        except Exception as e:
            logger.debug(
                "Messenger sender_action %s failed for %s: %s",
                sa,
                sender_psid,
                e,
            )


async def resolve_messenger_sender_name_if_needed(
    fb_action: Any,
    psid: str,
    sender_name: Optional[str],
) -> Optional[str]:
    if sender_name and str(sender_name).strip():
        return sender_name
    if not fb_action or not fb_action.is_configured():
        return sender_name
    try:
        profile = await asyncio.to_thread(
            lambda: fb_action.api().get_messenger_psid_profile(psid)
        )
    except Exception as e:
        logger.debug("Messenger PSID profile fetch failed for %s: %s", psid, e)
        return None
    return _display_name_from_psid_profile(profile)


def messenger_event_to_data_payload(event: Dict[str, Any]) -> Dict[str, Any]:
    """Shape stored on InteractWalker ``data`` for adapters and logging."""
    payload: Dict[str, Any] = {
        "sender_id": event.get("sender_id", ""),
        "page_id": event.get("page_id", ""),
        "mid": event.get("mid", ""),
        "attachments": event.get("attachments") or [],
        "isGroup": False,
    }
    merged = event.get("merged_mids")
    if isinstance(merged, list) and merged:
        payload["merged_mids"] = merged
    rt = event.get("reply_to")
    if isinstance(rt, dict) and rt.get("mid"):
        payload["reply_to_mid"] = str(rt.get("mid"))
    return payload


def _graph_message_attachments_list(
    graph_payload: Dict[str, Any]
) -> List[Dict[str, Any]]:
    atts = graph_payload.get("attachments")
    if isinstance(atts, list):
        return [a for a in atts if isinstance(a, dict)]
    if isinstance(atts, dict):
        data = atts.get("data")
        if isinstance(data, list):
            return [a for a in data if isinstance(a, dict)]
    return []


def _first_messenger_graph_cdn_url_from_attachments(
    atts: List[Dict[str, Any]],
) -> Optional[str]:
    for att in atts:
        for key in ("image_data", "video_data"):
            block = att.get(key)
            if isinstance(block, dict):
                u = block.get("url")
                if u and str(u).strip():
                    return str(u).strip()
        pay = att.get("payload")
        if isinstance(pay, dict) and pay.get("url"):
            u = pay.get("url")
            if u and str(u).strip():
                return str(u).strip()
        if str(att.get("type") or "").lower() == "image" and isinstance(pay, dict):
            u = pay.get("url")
            if u and str(u).strip():
                return str(u).strip()
    return None


async def _quoted_message_from_messenger_reply(
    event: Dict[str, Any],
    fb_action: Any,
    page_token: str,
    pub_base: str,
    sender_id: str,
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Load parent message via Graph for ``reply_to`` ( Page token; may fail if permissions omit read).

    Returns ``(quoted_message, rehosted_image_public_urls)`` for vision parity with WhatsApp.
    """
    reply_to = event.get("reply_to")
    if not isinstance(reply_to, dict):
        return None, []
    if reply_to.get("is_self_reply") is True:
        return None, []
    parent_mid = reply_to.get("mid")
    if not parent_mid or not str(parent_mid).strip():
        return None, []
    if not fb_action or not page_token:
        return None, []

    mid = str(parent_mid).strip()

    def _fetch() -> Dict[str, Any]:
        return fb_action.api().get_messenger_message(mid)

    try:
        raw = await asyncio.to_thread(_fetch)
    except Exception as e:
        logger.debug("Messenger quoted parent Graph error: %s", e)
        return None, []

    if not isinstance(raw, dict) or raw.get("error"):
        logger.debug(
            "Messenger quoted parent fetch failed: %s",
            raw.get("error") if isinstance(raw, dict) else raw,
        )
        return None, []

    msg_body = raw.get("message")
    text = str(msg_body).strip() if msg_body is not None else ""
    atts_list = _graph_message_attachments_list(raw)
    image_cdn_url = _first_messenger_graph_cdn_url_from_attachments(atts_list)

    quoted: Dict[str, Any] = {
        "type": "image" if image_cdn_url else "chat",
        "body": text,
        "text": text,
        "message": {"text": text, "body": text},
        "mid": mid,
    }
    frm = raw.get("from")
    if frm is not None:
        quoted["from"] = frm
    ts = raw.get("timestamp")
    if ts is not None:
        quoted["timestamp"] = ts

    extra_rehosted: List[str] = []
    if image_cdn_url and pub_base and sender_id:
        extra_rehosted = await _rehost_messenger_urls_to_media(
            sender_id, [image_cdn_url], page_token, pub_base
        )

    return quoted, extra_rehosted


def _messenger_attachment_buckets(
    attachments: Any,
) -> Tuple[List[str], List[str], List[str], List[str]]:
    """Split attachment URLs by Meta ``type`` (image / audio / video / file / sticker)."""
    images: List[str] = []
    audios: List[str] = []
    videos: List[str] = []
    files: List[str] = []
    if not isinstance(attachments, list):
        return images, audios, videos, files
    for att in attachments:
        url = FacebookAPI._messenger_attachment_url(att)
        if not url:
            continue
        raw_type = att.get("type") if isinstance(att, dict) else None
        t = str(raw_type or "").lower()
        if t == "image":
            images.append(url)
        elif t == "sticker":
            images.append(url)
        elif t == "audio":
            audios.append(url)
        elif t == "video":
            videos.append(url)
        elif t == "file":
            files.append(url)
        elif t != "location":
            files.append(url)
    return images, audios, videos, files


def _messenger_location_lines(attachments: Any) -> List[str]:
    lines: List[str] = []
    if not isinstance(attachments, list):
        return lines
    for att in attachments:
        pair = FacebookAPI._messenger_attachment_location_coords(att)
        if pair is None:
            continue
        lat, lon = pair
        lines.append(f"Location: {lat}, {lon}")
    return lines


async def _rehost_messenger_urls_to_media(
    sender_id: str,
    urls: List[str],
    page_token: str,
    pub_base: str,
) -> List[str]:
    """Download Graph CDN URLs with Page token and store via MediaManager (public URLs)."""
    if not urls or not page_token or not pub_base:
        return []
    media_mgr = MediaManager(storage_dir="messenger_media")
    out: List[str] = []
    base = pub_base.rstrip("/")
    for url in urls:
        try:

            def _dl(u: str = url) -> Tuple[Optional[bytes], Optional[str]]:
                return FacebookAPI.download_messenger_attachment(u, page_token)

            content, mime = await asyncio.to_thread(_dl)
            if not content:
                continue
            path = await media_mgr.save_media(
                sender_id, content, mime_type=mime, filename=None
            )
            if path:
                out.append(f"{base}{path}")
        except Exception as e:
            logger.warning("Messenger: rehost failed for attachment: %s", e)
    return out


async def resolve_messenger_inbound_event(
    event: Dict[str, Any],
    agent: Any,
    fb_action: Any,
) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Build visitor ``data_dict`` and effective utterance (STT, rehosted media, location).

    Returns ``None`` when the message should not spawn an interaction (e.g. audio-only
    without ``stt_action``, or audio-only with empty transcripts).
    """
    sender_id = str(event.get("sender_id") or "").strip()
    text = str(event.get("message") or "").strip()
    attachments = event.get("attachments") or []
    location_lines = _messenger_location_lines(attachments)
    image_urls, audio_urls, video_urls, file_urls = _messenger_attachment_buckets(
        attachments
    )

    data_dict: Dict[str, Any] = {
        "messenger_payload": messenger_event_to_data_payload(event),
    }

    page_token = ""
    pub_base = ""
    if fb_action:
        fb_action._apply_env_defaults()
        try:
            pub_base = str(fb_action.base_url or "").strip()
        except Exception:
            pub_base = ""
        if not pub_base:
            pub_base = get_public_base_url()
        try:
            page_token = (fb_action.api().page_access_token or "").strip()
        except Exception:
            page_token = ""

    rehosted_images: List[str] = []
    if image_urls and page_token and pub_base and sender_id:
        rehosted_images = await _rehost_messenger_urls_to_media(
            sender_id, image_urls, page_token, pub_base
        )
    if rehosted_images:
        data_dict["image_urls"] = rehosted_images
    elif image_urls:
        data_dict["image_urls"] = list(image_urls)

    quoted_msg, quoted_rehosted_images = await _quoted_message_from_messenger_reply(
        event, fb_action, page_token, pub_base, sender_id
    )
    if quoted_msg:
        data_dict["quoted_message"] = quoted_msg
    if quoted_rehosted_images:
        existing = list(data_dict.get("image_urls") or [])
        data_dict["image_urls"] = existing + quoted_rehosted_images

    extra_media: List[str] = []
    if video_urls and page_token and pub_base and sender_id:
        extra_media.extend(
            await _rehost_messenger_urls_to_media(
                sender_id, list(video_urls), page_token, pub_base
            )
        )
    if file_urls and page_token and pub_base and sender_id:
        extra_media.extend(
            await _rehost_messenger_urls_to_media(
                sender_id, list(file_urls), page_token, pub_base
            )
        )
    if extra_media:
        data_dict["messenger_media"] = extra_media

    stt_label = ""
    if fb_action and getattr(fb_action, "stt_action", None):
        stt_label = str(fb_action.stt_action or "").strip()

    has_location = bool(location_lines)
    has_images = bool(data_dict.get("image_urls"))
    audio_only = (
        not text
        and not has_images
        and not video_urls
        and not file_urls
        and not has_location
        and bool(audio_urls)
    )

    if audio_only and not stt_label:
        logger.debug(
            "Messenger: ignoring audio-only message (no stt_action configured)"
        )
        return None

    transcripts: List[str] = []
    if audio_urls and stt_label and fb_action and page_token:
        try:
            stt_impl = await fb_action.get_action(stt_label)
        except Exception as e:
            logger.warning("Messenger: could not load STT action %s: %s", stt_label, e)
            stt_impl = None
        if stt_impl is None:
            logger.debug("Messenger: STT action %s not found", stt_label)
        else:
            invoke_b64 = getattr(stt_impl, "invoke_base64", None)
            if not invoke_b64:
                logger.debug("Messenger: STT action has no invoke_base64")
            else:
                for url in audio_urls:
                    try:

                        def _dl(u: str = url) -> Tuple[Optional[bytes], Optional[str]]:
                            return FacebookAPI.download_messenger_attachment(
                                u, page_token
                            )

                        content, mime = await asyncio.to_thread(_dl)
                        if not content:
                            logger.warning(
                                "Messenger: no bytes for audio attachment (download failed)"
                            )
                            continue
                        b64 = base64.b64encode(content).decode("ascii")
                        audio_type = (mime or "audio/mpeg").split(";", 1)[0].strip()
                        raw = await invoke_b64(b64, audio_type)
                        if raw and str(raw).strip():
                            transcripts.append(str(raw).strip())
                    except Exception as e:
                        logger.warning(
                            "Messenger: STT invoke_base64 failed for attachment: %s",
                            e,
                        )

    merged_transcript = "\n".join(transcripts)
    effective = text
    if location_lines:
        loc_block = "\n".join(location_lines)
        effective = f"{effective}\n{loc_block}".strip() if effective else loc_block
    if merged_transcript:
        effective = (
            f"{effective}\n{merged_transcript}".strip()
            if effective
            else merged_transcript
        )

    if audio_only and not merged_transcript:
        return None

    if not effective.strip() and not has_images:
        if video_urls:
            effective = "[User sent a video attachment]"
        elif file_urls:
            effective = "[User sent a file]"
        elif has_location:
            effective = "\n".join(location_lines)
        else:
            return None

    if has_images and not (effective or "").strip():
        effective = MESSENGER_DEFAULT_MEDIA_UTTERANCE

    mp = data_dict["messenger_payload"]
    mp["had_audio_attachment"] = bool(audio_urls)
    tts = ""
    if fb_action and getattr(fb_action, "tts_action", None):
        tts = str(fb_action.tts_action or "").strip()
    if audio_urls and tts:
        data_dict["respond_with_voice"] = True

    qm = data_dict.get("quoted_message")
    if qm and isinstance(qm, dict):
        effective = _build_utterance_with_quoted_context(qm, effective) or effective

    return (effective, data_dict)


async def create_messenger_walker(
    agent_id: str,
    utterance: str,
    sender: str,
    data_dict: Dict[str, Any],
    sender_name: Optional[str] = None,
) -> Optional[InteractWalker]:
    """Create an InteractWalker for Messenger (PSID as user id)."""
    try:
        convo_obj = await get_conversation_with_lock(sender)

        if convo_obj and getattr(convo_obj, "session_id", None):
            return InteractWalker(
                agent_id=agent_id,
                utterance=utterance,
                channel="messenger",
                data=data_dict,
                session_id=convo_obj.session_id,
                user_name=sender_name,
                stream=False,
            )
        return InteractWalker(
            agent_id=agent_id,
            utterance=utterance,
            channel="messenger",
            data=data_dict,
            user_id=sender,
            user_name=sender_name,
            stream=False,
        )
    except Exception as e:
        logger.error("Error creating messenger walker for %s: %s", sender, e)
        return None


async def finalize_messenger_interaction(
    walker: InteractWalker,
    agent_id: str,
    sender: str,
) -> None:
    """Close interaction, flush, usage, log (mirror WhatsApp finalization)."""
    interaction = walker.interaction
    if not interaction:
        return

    try:
        await interaction.close_interaction()
        from jvspatial import flush_deferred_entities

        await flush_deferred_entities(interaction, walker.conversation, strict=True)

        from jvagent.action.interact.endpoints import (
            _build_interaction_log_data,
            _finalize_usage,
        )
        from jvagent.logging.service import INTERACTION_LEVEL_NUMBER

        await _finalize_usage(interaction)

        try:
            from jvagent.action.interact.response_builder import (
                _consolidated_tasks_for_interaction,
            )

            app = await App.get()
            app_id = app.id if app else ""
            tasks = []
            if walker.conversation:
                active = walker.conversation.get_tasks(status="active")
                tasks = _consolidated_tasks_for_interaction(
                    interaction, walker.conversation, active
                )
            log_data, message = _build_interaction_log_data(
                interaction,
                app_id,
                agent_id,
                tasks=tasks,
                visitor_data=walker.data,
            )
            logger.log(INTERACTION_LEVEL_NUMBER, message, extra=log_data)
        except Exception as log_err:
            logger.debug("Messenger interaction log failed: %s", log_err)

    except DatabaseError as e:
        logger.error(
            "Database error finalizing messenger interaction for %s: %s",
            sender,
            e,
        )
        raise
    except Exception as e:
        logger.error("Error finalizing messenger interaction for %s: %s", sender, e)


async def process_messenger_interaction_async(
    utterance: str,
    sender: str,
    agent_id: str,
    agent: Any,
    data_dict: Dict[str, Any],
    sender_name: Optional[str] = None,
) -> None:
    """Background task: adapter registration, walker spawn, finalize."""
    fb_action: Any = None
    try:
        fb_action = await agent.get_action_by_type("FacebookAction")
        if fb_action:
            await fb_action.ensure_adapter_registered()
    except Exception as e:
        logger.warning("Messenger adapter ensure failed for agent %s: %s", agent_id, e)

    try:
        resolved_name = await resolve_messenger_sender_name_if_needed(
            fb_action, sender, sender_name
        )

        walker = await create_messenger_walker(
            agent_id, utterance, sender, data_dict, sender_name=resolved_name
        )
        if not walker:
            return
        await walker.spawn(agent)
        await finalize_messenger_interaction(walker, agent_id, sender)
    except DatabaseError:
        raise
    except Exception as e:
        logger.error(
            "Error in messenger interaction for %s: %s", sender, e, exc_info=True
        )
