"""Leadgen spec — frontmatter schema parsing and registry."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Union

from jvagent.action.skill_spec.base import (
    SkillToolDef,
    load_frontmatter_block_from_skill,
    parse_handlers_mapping,
    parse_skill_tools,
    reject_unknown_keys,
)
from jvagent.action.skill_spec.registry import BaseSkillRegistry

from .validators import known_validators

logger = logging.getLogger(__name__)

LEADGEN_FRONTMATTER_KEY = "leadgen"

SyncMode = Literal["on_capture", "on_complete", "manual"]

#: No channel autofill unless the skill declares ``channel_defaults``.
DEFAULT_CHANNEL_DEFAULTS: Dict[str, Dict[str, str]] = {}

_LEADGEN_KEYS = frozenset(
    {
        "title",
        "summary",
        "fields",
        "gap_fill",
        "sync",
        "handlers",
        "skill_tools",
        "messages",
        "channel_defaults",
    }
)
_FIELD_KEYS = frozenset(
    {
        "key",
        "guidance",
        "label",
        "required",
        "aliases",
        "validator",
        "validator_args",
        "decline_value",
        "merge",
        "phone_locale",
        "relevant_when",
        "capture_only",
    }
)
# ``priority``, ``qualification`` and ``project_qualification`` used to order
# the ask. Declaration order replaced them. They are still accepted so an
# existing skill parses; the values are ignored.
_GAP_FILL_DEPRECATED = frozenset({"priority", "qualification", "project_qualification"})
_GAP_FILL_KEYS = frozenset({"batch"}) | _GAP_FILL_DEPRECATED
_RELEVANT_WHEN_KEYS = frozenset({"any_present", "not_declined", "values_not"})
_SYNC_KEYS = frozenset(
    {"mode", "min_fields", "require_any", "destinations", "sales_contact", "timezone"}
)
_HANDLER_KEYS = frozenset({"post_capture", "qualify", "on_sync"})
_MESSAGES_KEYS = frozenset({"entity", "handoff_team", "contact_label", "visitor"})
_CHANNEL_DEFAULT_SOURCES = frozenset({"user_id", "user_name"})


@dataclass
class FieldDef:
    key: str
    guidance: str = ""
    # Short visitor-facing name for directives when guidance is long.
    label: str = ""
    required: bool = False
    aliases: List[str] = field(default_factory=list)
    validator: str = ""
    validator_args: Dict[str, Any] = field(default_factory=dict)
    decline_value: Optional[str] = None
    merge: bool = False
    phone_locale: str = ""
    # Captured only when volunteered; never asked, never offered in the pool.
    capture_only: bool = False
    # Eligibility gate the catalogue carries for the model to honour: the field
    # is askable only once at least one ``any_present`` key is filled, and never
    # while a ``not_declined`` key holds its own ``decline_value``. ``values_not``
    # maps a filled key to banned values (case-insensitive) that keep the field
    # ineligible. Absent = always eligible.
    relevant_when: Optional[Dict[str, Any]] = None


@dataclass
class GapFillDef:
    batch: bool = True


@dataclass
class SyncDef:
    mode: SyncMode = "on_capture"
    min_fields: List[str] = field(default_factory=list)
    require_any: List[str] = field(default_factory=list)
    destinations: List[Dict[str, Any]] = field(default_factory=list)
    # Offered in the failed-hand-off directive. Empty means the directive
    # does not name a contact.
    sales_contact: str = ""
    # IANA timezone for ``{last_updated}`` in sync templates.
    timezone: str = "UTC"


@dataclass
class MessagesDef:
    """Neutral hand-off vocabulary. Skills override for domain wording."""

    entity: str = "request"
    handoff_team: str = "team"
    contact_label: str = "contact"
    visitor: str = "visitor"


@dataclass
class HandlersDef:
    post_capture: Optional[str] = None
    qualify: Optional[str] = None
    on_sync: Optional[str] = None


@dataclass
class LeadGenSpec:
    name: str
    title: str = ""
    summary: str = ""
    fields: List[FieldDef] = field(default_factory=list)
    gap_fill: GapFillDef = field(default_factory=GapFillDef)
    sync: SyncDef = field(default_factory=SyncDef)
    handlers: HandlersDef = field(default_factory=HandlersDef)
    skill_tools: List[SkillToolDef] = field(default_factory=list)
    messages: MessagesDef = field(default_factory=MessagesDef)
    # None → DEFAULT_CHANNEL_DEFAULTS; {} → disable autofill.
    channel_defaults: Optional[Dict[str, Dict[str, str]]] = None
    source_dir: str = ""

    def get_required_fields(self) -> List[str]:
        return [f.key for f in self.fields if f.required]

    def get_field(self, key: str) -> Optional[FieldDef]:
        for f in self.fields:
            if f.key == key:
                return f
        return None

    def field_keys(self) -> List[str]:
        return [f.key for f in self.fields]

    def alias_map(self) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for f in self.fields:
            out[f.key.lower()] = f.key
            for alias in f.aliases:
                out[alias.lower().replace(" ", "_").replace("-", "_")] = f.key
        return out

    def merge_fields(self) -> List[str]:
        return [f.key for f in self.fields if f.merge]

    def resolved_channel_defaults(self) -> Dict[str, Dict[str, str]]:
        if self.channel_defaults is None:
            return dict(DEFAULT_CHANNEL_DEFAULTS)
        return dict(self.channel_defaults)


def fields_reference(spec: LeadGenSpec) -> List[Dict[str, Any]]:
    """The catalogue the model reads: every declared field, declaration order.

    Filled fields stay in the list so the model can correct or refresh a value;
    ``relevant_when`` travels with the entry and is honoured by the SOP, not
    filtered here.
    """
    out: List[Dict[str, Any]] = []
    for f in spec.fields:
        entry: Dict[str, Any] = {
            "key": f.key,
            "guidance": f.guidance,
            "required": f.required,
        }
        if f.label:
            entry["label"] = f.label
        if f.decline_value is not None:
            entry["decline_value"] = f.decline_value
        if f.capture_only:
            entry["capture_only"] = True
        if f.relevant_when:
            entry["relevant_when"] = dict(f.relevant_when)
        out.append(entry)
    return out


def _parse_relevant_when(data: Any, *, path: str) -> Optional[Dict[str, Any]]:
    if data is None or data == "":
        return None
    if not isinstance(data, dict):
        raise ValueError(f"{path} must be a mapping")
    reject_unknown_keys(data, _RELEVANT_WHEN_KEYS, path=path)
    any_present = [str(x) for x in (data.get("any_present") or []) if x]
    not_declined = [str(x) for x in (data.get("not_declined") or []) if x]
    raw_values_not = data.get("values_not") or {}
    if raw_values_not and not isinstance(raw_values_not, dict):
        raise ValueError(f"{path}.values_not must be a mapping of field → list")
    values_not: Dict[str, List[str]] = {}
    for key, vals in raw_values_not.items():
        banned = [str(v) for v in (vals or []) if v]
        if banned:
            values_not[str(key)] = banned
    if not any_present and not not_declined and not values_not:
        return None
    gate: Dict[str, Any] = {}
    if any_present:
        gate["any_present"] = any_present
    if not_declined:
        gate["not_declined"] = not_declined
    if values_not:
        gate["values_not"] = values_not
    return gate


def _parse_field(data: Dict[str, Any], *, index: int) -> FieldDef:
    path = f"fields[{index}]"
    if not isinstance(data, dict):
        raise ValueError(f"Field at {path} must be a mapping")
    reject_unknown_keys(data, _FIELD_KEYS, path=path)
    validator = str(data.get("validator", "") or "").strip()
    if validator and validator.lower() not in known_validators():
        raise ValueError(
            f"{path}.validator unknown '{validator}'; "
            f"known: {', '.join(sorted(known_validators()))}"
        )
    return FieldDef(
        key=str(data.get("key", "") or "").strip(),
        guidance=str(data.get("guidance", "") or ""),
        label=str(data.get("label", "") or "").strip(),
        required=bool(data.get("required", False)),
        aliases=[str(a) for a in (data.get("aliases") or []) if a],
        validator=validator,
        validator_args=dict(data.get("validator_args") or {}),
        decline_value=data.get("decline_value"),
        merge=bool(data.get("merge", False)),
        phone_locale=str(data.get("phone_locale", "") or ""),
        capture_only=bool(data.get("capture_only", False)),
        relevant_when=_parse_relevant_when(
            data.get("relevant_when"), path=f"{path}.relevant_when"
        ),
    )


def _parse_gap_fill(data: Any) -> GapFillDef:
    if not data:
        return GapFillDef()
    if not isinstance(data, dict):
        raise ValueError("gap_fill must be a mapping")
    reject_unknown_keys(data, _GAP_FILL_KEYS, path="gap_fill")
    stale = sorted(k for k in _GAP_FILL_DEPRECATED if k in data)
    if stale:
        logger.warning(
            "leadgen gap_fill.%s is ignored; ask order is field declaration order",
            ", ".join(stale),
        )
    return GapFillDef(batch=bool(data.get("batch", True)))


def _parse_sync(data: Any) -> SyncDef:
    if not data:
        return SyncDef()
    if not isinstance(data, dict):
        raise ValueError("sync must be a mapping")
    reject_unknown_keys(data, _SYNC_KEYS, path="sync")
    mode = str(data.get("mode") or "on_capture").strip().lower()
    if mode not in ("on_capture", "on_complete", "manual"):
        raise ValueError("sync.mode must be on_capture, on_complete, or manual")
    # Omitted keys → empty lists (agent-agnostic). Explicit empty list stays empty.
    if "min_fields" in data:
        min_fields = [str(x) for x in (data.get("min_fields") or []) if x]
    else:
        min_fields = []
    if "require_any" in data:
        require_any = [str(x) for x in (data.get("require_any") or []) if x]
    else:
        require_any = []
    return SyncDef(
        mode=mode,  # type: ignore[arg-type]
        min_fields=min_fields,
        require_any=require_any,
        destinations=list(data.get("destinations") or []),
        sales_contact=str(data.get("sales_contact") or "").strip(),
        timezone=str(data.get("timezone") or "UTC").strip() or "UTC",
    )


def _parse_messages(data: Any) -> MessagesDef:
    if not data:
        return MessagesDef()
    if not isinstance(data, dict):
        raise ValueError("messages must be a mapping")
    reject_unknown_keys(data, _MESSAGES_KEYS, path="messages")
    return MessagesDef(
        entity=str(data.get("entity") or "request").strip() or "request",
        handoff_team=str(data.get("handoff_team") or "team").strip() or "team",
        contact_label=str(data.get("contact_label") or "contact").strip() or "contact",
        visitor=str(data.get("visitor") or "visitor").strip() or "visitor",
    )


def _parse_channel_defaults(data: Any) -> Optional[Dict[str, Dict[str, str]]]:
    """None when omitted (use framework default); ``{}`` disables autofill."""
    if data is None:
        return None
    if not isinstance(data, dict):
        raise ValueError("channel_defaults must be a mapping")
    out: Dict[str, Dict[str, str]] = {}
    for channel, mapping in data.items():
        ch = str(channel or "").strip().lower()
        if not ch:
            continue
        if not isinstance(mapping, dict):
            raise ValueError(f"channel_defaults.{ch} must be a mapping")
        resolved: Dict[str, str] = {}
        for field_key, source in mapping.items():
            key = str(field_key or "").strip()
            src = str(source or "").strip().lower()
            if not key:
                continue
            if src not in _CHANNEL_DEFAULT_SOURCES:
                raise ValueError(
                    f"channel_defaults.{ch}.{key} source must be "
                    f"user_id or user_name, got '{source}'"
                )
            resolved[key] = src
        out[ch] = resolved
    return out


def _parse_handlers(data: Any) -> HandlersDef:
    def _build(raw: Dict[str, Any]) -> HandlersDef:
        return HandlersDef(
            post_capture=raw.get("post_capture"),
            qualify=raw.get("qualify"),
            on_sync=raw.get("on_sync"),
        )

    return parse_handlers_mapping(
        data,
        allowed_keys=_HANDLER_KEYS,
        path="handlers",
        builder=_build,
    )


def parse_leadgen_spec(
    data: Dict[str, Any],
    *,
    source_dir: str,
    default_name: str = "",
) -> LeadGenSpec:
    if not isinstance(data, dict):
        raise ValueError("leadgen spec must be a YAML mapping")
    reject_unknown_keys(data, _LEADGEN_KEYS, path="leadgen")

    fields = [
        _parse_field(q, index=i) for i, q in enumerate(data.get("fields", []) or [])
    ]
    skill_tools = parse_skill_tools(
        data.get("skill_tools"),
        path_prefix="leadgen",
        require_mapping=False,
    )

    channel_defaults: Optional[Dict[str, Dict[str, str]]]
    if "channel_defaults" in data:
        channel_defaults = _parse_channel_defaults(data.get("channel_defaults"))
    else:
        channel_defaults = None

    return LeadGenSpec(
        name=default_name,
        title=str(data.get("title", "") or ""),
        summary=str(data.get("summary", "") or ""),
        fields=fields,
        gap_fill=_parse_gap_fill(data.get("gap_fill")),
        sync=_parse_sync(data.get("sync")),
        handlers=_parse_handlers(data.get("handlers")),
        skill_tools=skill_tools,
        messages=_parse_messages(data.get("messages")),
        channel_defaults=channel_defaults,
        source_dir=source_dir,
    )


def load_leadgen_spec_from_skill(skill_dir: Union[str, Path]) -> Optional[LeadGenSpec]:
    leadgen_data, default_name, _skill_file = load_frontmatter_block_from_skill(
        skill_dir,
        block_key=LEADGEN_FRONTMATTER_KEY,
    )
    if leadgen_data is None:
        return None
    spec = parse_leadgen_spec(
        leadgen_data, source_dir=str(Path(skill_dir)), default_name=default_name
    )
    spec.name = default_name
    return spec


class LeadGenRegistry(BaseSkillRegistry[LeadGenSpec]):
    def __init__(self) -> None:
        super().__init__(label="leadgen", loader=load_leadgen_spec_from_skill)
