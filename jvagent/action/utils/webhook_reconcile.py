"""Shared webhook endpoint reconcile loop for provider integrations."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional

logger = logging.getLogger(__name__)

GetUrlFn = Callable[[Mapping[str, Any]], str]
GetIdFn = Callable[[Mapping[str, Any]], str]
IsStaleFn = Callable[[str], bool]
DeleteFn = Callable[[str], Awaitable[None]]
CreateFn = Callable[[], Awaitable[Mapping[str, Any]]]
OnKeptFn = Callable[[Mapping[str, Any]], Awaitable[None]]


async def reconcile_webhook_endpoint(
    *,
    desired_url: str,
    list_endpoints: Callable[[], Awaitable[List[Dict[str, Any]]]],
    get_endpoint_url: GetUrlFn,
    get_endpoint_id: GetIdFn,
    is_stale: IsStaleFn,
    delete_endpoint: DeleteFn,
    create_endpoint: CreateFn,
    urls_equivalent: Optional[Callable[[str, str], bool]] = None,
    on_kept: Optional[OnKeptFn] = None,
    provider_label: str = "webhook",
) -> Dict[str, Any]:
    """Ensure a provider has exactly one webhook row for ``desired_url``.

    - Keeps the first endpoint whose URL matches ``desired_url`` (via
      ``urls_equivalent`` when supplied, else strict string equality).
    - Deletes endpoints classified as stale by ``is_stale``.
    - Deletes duplicate exact matches beyond the first kept row.
    - Creates a new endpoint when none was kept.
    """
    desired = (desired_url or "").strip()
    if not desired:
        raise ValueError("desired_url is empty")

    equiv = urls_equivalent or (lambda a, b: (a or "").strip() == (b or "").strip())

    try:
        existing = await list_endpoints()
    except Exception as exc:
        logger.warning("%s webhook list failed: %s", provider_label, exc)
        return {"status": "error", "message": f"webhook list failed: {exc}"}

    exact_matches: List[Mapping[str, Any]] = []
    stale_matches: List[Mapping[str, Any]] = []
    for ep in existing:
        ep_url = get_endpoint_url(ep)
        if not ep_url:
            continue
        if equiv(ep_url, desired):
            exact_matches.append(ep)
        elif is_stale(ep_url):
            stale_matches.append(ep)

    deleted: List[str] = []
    for ep in stale_matches:
        wid = get_endpoint_id(ep)
        if not wid:
            logger.warning(
                "%s reconcile: skipping stale webhook with no id (url=%s)",
                provider_label,
                get_endpoint_url(ep) or "?",
            )
            continue
        try:
            await delete_endpoint(wid)
            deleted.append(wid)
        except Exception as exc:
            logger.warning(
                "%s: failed deleting stale webhook %s: %s", provider_label, wid, exc
            )

    kept: Optional[Mapping[str, Any]] = None
    if exact_matches:
        kept = exact_matches[0]
        for ep in exact_matches[1:]:
            wid = get_endpoint_id(ep)
            if not wid:
                continue
            try:
                await delete_endpoint(wid)
                deleted.append(wid)
            except Exception as exc:
                logger.warning(
                    "%s: failed deleting duplicate webhook %s: %s",
                    provider_label,
                    wid,
                    exc,
                )

    created: Optional[Mapping[str, Any]] = None
    if kept is not None and on_kept is not None:
        await on_kept(kept)

    if kept is None:
        try:
            created = await create_endpoint()
            kept = created
        except Exception as exc:
            logger.error("%s: webhook create failed: %s", provider_label, exc)
            return {
                "status": "error",
                "message": f"webhook create failed: {exc}",
                "desired_url": desired,
                "deleted_webhook_ids": deleted,
            }

    return {
        "status": "ok",
        "desired_url": desired,
        "webhook": dict(kept or {}),
        "created": created is not None,
        "deleted_webhook_ids": deleted,
    }
