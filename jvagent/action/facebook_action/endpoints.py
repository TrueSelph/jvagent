"""HTTP endpoints for the Facebook action."""

import asyncio
import json
import logging
from typing import Any, Callable, Dict, List, Optional

from fastapi import HTTPException, Request
from fastapi.responses import PlainTextResponse
from jvspatial import create_task
from jvspatial.api import endpoint
from jvspatial.api.exceptions import ResourceNotFoundError, ValidationError
from jvspatial.exceptions import ValidationError as SpatialValidationError

from jvagent.action.access_control.access_control_action import log_access_denied
from jvagent.action.utils.endpoint_helpers import require_typed_action
from jvagent.core.agent import Agent

from .facebook_action import FacebookAction
from .facebook_api import FacebookAPI
from .messenger_message_coalescer import MessengerMessageCoalescer
from .messenger_webhook_helpers import (
    prime_messenger_sender_actions,
    process_messenger_interaction_async,
    resolve_messenger_inbound_event,
    verify_meta_messenger_signature,
)

logger = logging.getLogger(__name__)

MESSENGER_UTTERANCE_MAX = 32000


async def _require_facebook_action(action_id: str) -> FacebookAction:
    return await require_typed_action(
        action_id,
        FacebookAction,
        not_found_message=f"Facebook action with ID '{action_id}' not found",
        wrong_type_message=f"Action '{action_id}' is not a FacebookAction",
    )


async def _run_facebook_graph(
    action_id: str,
    action: FacebookAction,
    fn: Callable[[], Any],
) -> Any:
    try:
        return await asyncio.to_thread(fn)
    except ValidationError as e:
        raise ValidationError(
            message=str(e),
            details={"action_id": action_id},
        ) from e


def _raise_if_graph_error(action_id: str, result: Any) -> None:
    if isinstance(result, dict) and result.get("error"):
        raise ValidationError(
            message=str(result.get("error")),
            details={"action_id": action_id, "graph": result},
        )


@endpoint(
    "/actions/{action_id}/facebook/health",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Facebook Action"],
    summary="Facebook action health check",
)
async def facebook_health_check(action_id: str) -> Dict[str, Any]:
    """Check Facebook Graph connectivity using page details."""
    action = await _require_facebook_action(action_id)

    health = await action.healthcheck()
    if health is True:
        return {"healthy": True, "details": None}
    if isinstance(health, dict):
        return {
            "healthy": health.get("healthy", False),
            "details": health,
        }
    return {"healthy": bool(health), "details": None}


@endpoint(
    "/actions/{action_id}/facebook/page",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Facebook Action"],
    summary="Get connected Facebook Page details (requires Graph permissions)",
)
async def facebook_get_page(action_id: str) -> Dict[str, Any]:
    action = await _require_facebook_action(action_id)

    result = await _run_facebook_graph(
        action_id,
        action,
        lambda: action.api().get_page_details(),
    )
    _raise_if_graph_error(action_id, result)
    return {"success": True, "page": result}


@endpoint(
    "/actions/{action_id}/facebook/messenger/text",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Facebook Action"],
    summary="Send a Messenger text message (RESPONSE)",
)
async def facebook_send_messenger_text(
    action_id: str, recipient_id: str, message: str
) -> Dict[str, Any]:
    action = await _require_facebook_action(action_id)

    if not recipient_id or not message:
        raise ValidationError(
            message="recipient_id and message are required",
            details={"action_id": action_id},
        )

    result = await _run_facebook_graph(
        action_id,
        action,
        lambda: action.api().send_text_message(recipient_id, message),
    )
    _raise_if_graph_error(action_id, result)
    return {"success": True, "result": result}


@endpoint(
    "/actions/{action_id}/facebook/messenger/media",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Facebook Action"],
    summary="Send a Messenger media message (image, video, audio, file; requires permissions)",
)
async def facebook_send_messenger_media(
    action_id: str, recipient_id: str, media_url: str, media_type: str
) -> Dict[str, Any]:
    action = await _require_facebook_action(action_id)

    if not recipient_id or not media_url or not media_type:
        raise ValidationError(
            message="recipient_id, media_url, and media_type are required",
            details={"action_id": action_id},
        )

    result = await _run_facebook_graph(
        action_id,
        action,
        lambda: action.api().send_media(recipient_id, media_url, media_type),
    )
    _raise_if_graph_error(action_id, result)
    return {"success": True, "result": result}


@endpoint(
    "/actions/{action_id}/facebook/me",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Facebook Action"],
    summary="Get Graph API user for the configured token (fields depend on token type)",
)
async def facebook_get_me(
    action_id: str, fields: Optional[str] = "id,name"
) -> Dict[str, Any]:
    action = await _require_facebook_action(action_id)

    def _me():
        action._apply_env_defaults()
        if action.access_token and str(action.access_token).strip():
            return action.discovery_api().get_user_info(fields or "id,name")
        return action.api().get_user_info(fields or "id,name")

    result = await _run_facebook_graph(
        action_id,
        action,
        _me,
    )
    _raise_if_graph_error(action_id, result)
    return {"success": True, "me": result}


@endpoint(
    "/actions/{action_id}/facebook/pages",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Facebook Action"],
    summary="List Pages for the configured token (typically user token; requires permissions)",
)
async def facebook_list_pages(action_id: str, limit: int = 100) -> Dict[str, Any]:
    action = await _require_facebook_action(action_id)

    result = await _run_facebook_graph(
        action_id,
        action,
        lambda: action.discovery_api().list_all_pages(limit=limit),
    )
    _raise_if_graph_error(action_id, result)
    return {"success": True, "pages": result}


@endpoint(
    "/actions/{action_id}/facebook/page/posts",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Facebook Action"],
    summary="List recent posts on the connected Page",
)
async def facebook_get_page_posts(
    action_id: str, limit: int = 10, post_filter: Optional[str] = None
) -> Dict[str, Any]:
    action = await _require_facebook_action(action_id)

    result = await _run_facebook_graph(
        action_id,
        action,
        lambda: action.api().get_page_posts(limit=limit, post_filter=post_filter),
    )
    _raise_if_graph_error(action_id, result)
    return {"success": True, "posts": result}


@endpoint(
    "/actions/{action_id}/facebook/posts/{post_id}",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Facebook Action"],
    summary="Get a single post by ID",
)
async def facebook_get_post(action_id: str, post_id: str) -> Dict[str, Any]:
    action = await _require_facebook_action(action_id)

    if not post_id:
        raise ValidationError(
            message="post_id is required",
            details={"action_id": action_id},
        )

    result = await _run_facebook_graph(
        action_id,
        action,
        lambda: action.api().get_single_post(post_id),
    )
    _raise_if_graph_error(action_id, result)
    return {"success": True, "post": result}


@endpoint(
    "/actions/{action_id}/facebook/posts/{post_id}/permalink",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Facebook Action"],
    summary="Get permalink URL for a post",
)
async def facebook_get_post_permalink(action_id: str, post_id: str) -> Dict[str, Any]:
    action = await _require_facebook_action(action_id)

    if not post_id:
        raise ValidationError(
            message="post_id is required",
            details={"action_id": action_id},
        )

    result = await _run_facebook_graph(
        action_id,
        action,
        lambda: action.api().share_facebook_post(post_id),
    )
    if isinstance(result, dict) and result.get("status") == "error":
        raise ValidationError(
            message=str(result.get("message", "Unknown error")),
            details={"action_id": action_id, "graph": result},
        )
    return {"success": True, "result": result}


@endpoint(
    "/actions/{action_id}/facebook/posts/{post_id}/comments",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Facebook Action"],
    summary="List comments on a post",
)
async def facebook_get_post_comments(
    action_id: str, post_id: str, limit: int = 10
) -> Dict[str, Any]:
    action = await _require_facebook_action(action_id)

    if not post_id:
        raise ValidationError(
            message="post_id is required",
            details={"action_id": action_id},
        )

    result = await _run_facebook_graph(
        action_id,
        action,
        lambda: action.api().get_post_comments(post_id, limit=limit),
    )
    _raise_if_graph_error(action_id, result)
    return {"success": True, "comments": result}


@endpoint(
    "/actions/{action_id}/facebook/posts/{post_id}/reactions",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Facebook Action"],
    summary="List reactions on a post",
)
async def facebook_get_post_reactions(action_id: str, post_id: str) -> Dict[str, Any]:
    action = await _require_facebook_action(action_id)

    if not post_id:
        raise ValidationError(
            message="post_id is required",
            details={"action_id": action_id},
        )

    result = await _run_facebook_graph(
        action_id,
        action,
        lambda: action.api().get_reactions(post_id),
    )
    _raise_if_graph_error(action_id, result)
    return {"success": True, "reactions": result}


@endpoint(
    "/actions/{action_id}/facebook/posts/{post_id}/comments",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Facebook Action"],
    summary="Comment on a post",
)
async def facebook_comment_on_post(
    action_id: str, post_id: str, message: str
) -> Dict[str, Any]:
    action = await _require_facebook_action(action_id)

    if not post_id or not message:
        raise ValidationError(
            message="post_id and message are required",
            details={"action_id": action_id},
        )

    result = await _run_facebook_graph(
        action_id,
        action,
        lambda: action.api().comment_on_post(post_id, message),
    )
    _raise_if_graph_error(action_id, result)
    return {"success": True, "result": result}


@endpoint(
    "/actions/{action_id}/facebook/page/feed",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Facebook Action"],
    summary="Post a message to the Page feed",
)
async def facebook_post_page_feed(action_id: str, message: str) -> Dict[str, Any]:
    action = await _require_facebook_action(action_id)

    if not message:
        raise ValidationError(
            message="message is required",
            details={"action_id": action_id},
        )

    result = await _run_facebook_graph(
        action_id,
        action,
        lambda: action.api().post_message_to_page(message),
    )
    _raise_if_graph_error(action_id, result)
    return {"success": True, "result": result}


@endpoint(
    "/actions/{action_id}/facebook/page/feed/images",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Facebook Action"],
    summary="Post multiple images to the Page feed with a caption",
)
async def facebook_post_page_feed_images(
    action_id: str, image_urls: List[str], caption: str
) -> Dict[str, Any]:
    action = await _require_facebook_action(action_id)

    if not image_urls or not caption:
        raise ValidationError(
            message="image_urls and caption are required",
            details={"action_id": action_id},
        )

    result = await _run_facebook_graph(
        action_id,
        action,
        lambda: action.api().post_images_to_page(image_urls, caption),
    )
    _raise_if_graph_error(action_id, result)
    return {"success": True, "result": result}


@endpoint(
    "/actions/{action_id}/facebook/page/feed/videos",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Facebook Action"],
    summary="Post multiple videos to the Page feed with title and caption",
)
async def facebook_post_page_feed_videos(
    action_id: str, title: str, caption: str, video_urls: List[str]
) -> Dict[str, Any]:
    action = await _require_facebook_action(action_id)

    if not video_urls or not caption:
        raise ValidationError(
            message="video_urls and caption are required",
            details={"action_id": action_id},
        )

    result = await _run_facebook_graph(
        action_id,
        action,
        lambda: action.api().post_videos_to_page(title, caption, video_urls),
    )
    _raise_if_graph_error(action_id, result)
    return {"success": True, "result": result}


@endpoint(
    "/actions/{action_id}/facebook/page/feed/media",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Facebook Action"],
    summary="Post mixed image/video URLs to the Page feed with a caption",
)
async def facebook_post_page_feed_media(
    action_id: str, caption: str, media_urls: List[Dict[str, Any]]
) -> Dict[str, Any]:
    action = await _require_facebook_action(action_id)

    if not media_urls or not caption:
        raise ValidationError(
            message="media_urls and caption are required",
            details={"action_id": action_id},
        )

    result = await _run_facebook_graph(
        action_id,
        action,
        lambda: action.api().post_media_to_page(caption, media_urls),
    )
    _raise_if_graph_error(action_id, result)
    return {"success": True, "result": result}


@endpoint(
    "/actions/{action_id}/facebook/comments/{comment_id}/replies",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Facebook Action"],
    summary="Reply to a comment with text",
)
async def facebook_reply_to_comment(
    action_id: str, comment_id: str, message: str
) -> Dict[str, Any]:
    action = await _require_facebook_action(action_id)

    if not comment_id or not message:
        raise ValidationError(
            message="comment_id and message are required",
            details={"action_id": action_id},
        )

    result = await _run_facebook_graph(
        action_id,
        action,
        lambda: action.api().reply_to_comment(comment_id, message),
    )
    _raise_if_graph_error(action_id, result)
    return {"success": True, "result": result}


@endpoint(
    "/actions/{action_id}/facebook/comments/{comment_id}/replies/attachment",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Facebook Action"],
    summary="Reply to a comment with an attachment URL",
)
async def facebook_reply_to_comment_attachment(
    action_id: str, comment_id: str, attachment_url: str
) -> Dict[str, Any]:
    action = await _require_facebook_action(action_id)

    if not comment_id or not attachment_url:
        raise ValidationError(
            message="comment_id and attachment_url are required",
            details={"action_id": action_id},
        )

    result = await _run_facebook_graph(
        action_id,
        action,
        lambda: action.api().reply_to_comment_with_attachment(
            comment_id, attachment_url
        ),
    )
    _raise_if_graph_error(action_id, result)
    return {"success": True, "result": result}


@endpoint(
    "/actions/{action_id}/facebook/comments/{comment_id}",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Facebook Action"],
    summary="Edit a comment message",
)
async def facebook_update_comment(
    action_id: str, comment_id: str, message: str
) -> Dict[str, Any]:
    action = await _require_facebook_action(action_id)

    if not comment_id or not message:
        raise ValidationError(
            message="comment_id and message are required",
            details={"action_id": action_id},
        )

    result = await _run_facebook_graph(
        action_id,
        action,
        lambda: action.api().update_comment(comment_id, message),
    )
    _raise_if_graph_error(action_id, result)
    return {"success": True, "result": result}


@endpoint(
    "/actions/{action_id}/facebook/comments/{comment_id}/like",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Facebook Action"],
    summary="Like a comment as the Page",
)
async def facebook_like_comment(action_id: str, comment_id: str) -> Dict[str, Any]:
    action = await _require_facebook_action(action_id)

    if not comment_id:
        raise ValidationError(
            message="comment_id is required",
            details={"action_id": action_id},
        )

    result = await _run_facebook_graph(
        action_id,
        action,
        lambda: action.api().like_comment(comment_id),
    )
    _raise_if_graph_error(action_id, result)
    return {"success": True, "result": result}


async def _agent_and_facebook_action_for_messenger_webhook(
    agent_id: str,
) -> tuple[Agent, FacebookAction]:
    agent = await Agent.get(agent_id)
    if not agent:
        raise ResourceNotFoundError(
            message=f"Agent with ID '{agent_id}' not found",
            details={"agent_id": agent_id},
        )
    fb_action = await agent.get_action_by_type("FacebookAction")
    if not fb_action:
        raise ResourceNotFoundError(
            message="Action with label 'FacebookAction' not found",
            details={"agent_id": agent_id},
        )
    return agent, fb_action


@endpoint(
    "/messenger/interact/webhook/{agent_id}",
    methods=["GET"],
    webhook=True,
    auth=False,
    webhook_auth="api_key",  # Validates API key from query param or header
    tags=["Facebook Action", "Messenger"],
    summary="Meta Messenger webhook: GET hub challenge (subscription verify)",
)
async def messenger_interact_webhook_verify(request: Request, agent_id: str) -> Any:
    """Meta webhook verification (hub.challenge) for an agent."""
    _, fb_action = await _agent_and_facebook_action_for_messenger_webhook(agent_id)
    params = getattr(request.state, "parsed_payload", None)
    if not isinstance(params, dict):
        params = dict(request.query_params)
    challenge = fb_action.parse_messenger_webhook_verify(params)
    if isinstance(challenge, dict):
        raise HTTPException(status_code=403, detail="Webhook verification failed")
    return PlainTextResponse(str(challenge), media_type="text/plain")


@endpoint(
    "/messenger/interact/webhook/{agent_id}",
    methods=["POST"],
    webhook=True,
    auth=False,
    webhook_auth="api_key",  # Validates API key from query param or header
    tags=["Facebook Action", "Messenger"],
    summary="Meta Messenger webhook: POST signed messaging events",
)
async def messenger_interact_webhook_events(request: Request, agent_id: str) -> Any:
    """Inbound Messenger events for an agent (same role as WhatsApp interact webhook)."""
    agent, fb_action = await _agent_and_facebook_action_for_messenger_webhook(agent_id)

    fb_action._apply_env_defaults()
    app_secret = str(fb_action.app_secret or "").strip()
    if not app_secret:
        raise HTTPException(
            status_code=500, detail="FACEBOOK_APP_SECRET is required for webhook POST"
        )

    raw_body: bytes = getattr(request.state, "raw_body", b"")
    if not raw_body:
        raw_body = await request.body()

    if not verify_meta_messenger_signature(raw_body, request, app_secret):
        raise HTTPException(status_code=401, detail="Invalid X-Hub-Signature-256")

    payload: Any = getattr(request.state, "parsed_payload", None)
    if payload is None:
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            logger.debug("Messenger webhook JSON parse error: %s", e)
            raise HTTPException(status_code=400, detail="Invalid JSON body")

    events = FacebookAPI.iter_messenger_user_text_events(payload)
    if not events:
        return {"status": "ignored", "response": None}

    access_control_action = await agent.get_access_control_action()
    window = float(getattr(fb_action, "messenger_message_window", 2.0) or 0.0)

    async def handle_merged_messenger_event(merged_event: Dict[str, Any]) -> None:
        sender = str(merged_event.get("sender_id") or "").strip()
        if not sender:
            return

        resolved = await resolve_messenger_inbound_event(merged_event, agent, fb_action)
        if resolved is None:
            return

        utterance, data_dict = resolved
        if len(utterance) > MESSENGER_UTTERANCE_MAX:
            return

        has_access = True
        if access_control_action:
            has_access = await access_control_action.has_action_access(
                user_id=sender,
                action_label="FacebookAction",
                channel="messenger",
            )
        if not has_access:
            log_access_denied(
                agent_id=agent_id,
                user_id=sender,
                channel="messenger",
                action_label="FacebookAction",
                stage="messenger",
            )
            return

        await prime_messenger_sender_actions(
            agent_id,
            fb_action,
            _run_facebook_graph,
            sender,
        )

        task = await create_task(
            process_messenger_interaction_async(
                utterance,
                sender,
                agent_id,
                agent,
                data_dict,
                sender_name=merged_event.get("sender_name") or None,
            ),
            name=f"messenger_interaction_{sender}",
        )
        if task is None:
            await process_messenger_interaction_async(
                utterance,
                sender,
                agent_id,
                agent,
                data_dict,
                sender_name=merged_event.get("sender_name") or None,
            )

    for event in events:
        sender = event.get("sender_id", "")
        if not sender:
            continue
        if window <= 0:
            await handle_merged_messenger_event(event)
        else:
            await MessengerMessageCoalescer.schedule_merge(
                f"{agent_id}:{sender}",
                event,
                window,
                handle_merged_messenger_event,
            )

    return {"status": "received"}


@endpoint(
    "/actions/{action_id}/facebook/messenger/webhook-url",
    methods=["GET"],
    auth=True,
    roles=["admin"],
    tags=["Facebook Action", "Messenger"],
    summary=(
        "Get or regenerate Messenger webhook URL (full URL with api_key; "
        "meta_callback_url is what to pass to Meta / manual register without query)"
    ),
)
async def facebook_get_messenger_webhook_url(
    action_id: str, regenerate: bool = False
) -> Dict[str, Any]:
    """WhatsApp-style webhook URL with API key; ``meta_callback_url`` for Graph subscribe."""
    action = await _require_facebook_action(action_id)
    try:
        url = await action.get_webhook_url(regenerate=regenerate)
    except SpatialValidationError as e:
        raise ValidationError(
            message=str(e),
            details={"action_id": action_id},
        ) from e
    meta_url = FacebookAction.meta_callback_url_for_subscription(url)
    return {
        "success": True,
        "webhook_url": url,
        "meta_callback_url": meta_url,
    }


@endpoint(
    "/actions/{action_id}/facebook/webhook/register",
    methods=["POST"],
    auth=True,
    roles=["admin"],
    tags=["Facebook Action"],
    summary="Register or update Meta app Page webhook subscription (app id|secret)",
)
async def facebook_register_webhook(
    action_id: str, webhook_url: Optional[str] = None
) -> Dict[str, Any]:
    action = await _require_facebook_action(action_id)

    if not webhook_url:
        webhook_url = await action.get_webhook_url()

    result = await _run_facebook_graph(
        action_id,
        action,
        lambda: action.app_api().register_session(webhook_url),
    )
    _raise_if_graph_error(action_id, result)
    return {"success": True, "result": result}
