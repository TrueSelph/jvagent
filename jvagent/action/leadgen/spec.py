"""Leadgen spec — frontmatter schema parsing and registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Union

from jvagent.action.skill_spec.base import (
    SKILL_MD,
    SkillToolDef,
    load_frontmatter_block_from_skill,
    parse_handlers_mapping,
    parse_skill_tools,
    reject_unknown_keys,
)
from jvagent.action.skill_spec.registry import BaseSkillRegistry

LEADGEN_FRONTMATTER_KEY = "leadgen"

SyncMode = Literal["on_capture", "on_complete", "manual"]

_LEADGEN_KEYS = frozenset(
    {"title", "summary", "fields", "gap_fill", "sync", "handlers", "skill_tools"}
)
_FIELD_KEYS = frozenset(
    {
        "key",
        "guidance",
        "required",
        "aliases",
        "validator",
        "validator_args",
        "decline_value",
        "merge",
        "phone_locale",
    }
)
_GAP_FILL_KEYS = frozenset({"batch", "priority"})
_SYNC_KEYS = frozenset({"mode", "min_fields", "require_any", "destinations"})
_HANDLER_KEYS = frozenset({"post_capture", "qualify", "on_sync"})


@dataclass
class FieldDef:
    key: str
    guidance: str = ""
    required: bool = False
    aliases: List[str] = field(default_factory=list)
    validator: str = ""
    validator_args: Dict[str, Any] = field(default_factory=dict)
    decline_value: Optional[str] = None
    merge: bool = False
    phone_locale: str = ""


@dataclass
class GapFillDef:
    batch: bool = True
    priority: List[str] = field(default_factory=list)


@dataclass
class SyncDef:
    mode: SyncMode = "on_capture"
    min_fields: List[str] = field(default_factory=lambda: ["name"])
    require_any: List[str] = field(default_factory=lambda: ["phone", "email"])
    destinations: List[Dict[str, Any]] = field(default_factory=list)


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


def fields_reference(spec: LeadGenSpec) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for f in spec.fields:
        entry: Dict[str, Any] = {
            "key": f.key,
            "guidance": f.guidance,
            "required": f.required,
        }
        if f.decline_value is not None:
            entry["decline_value"] = f.decline_value
        out.append(entry)
    return out


def _parse_field(data: Dict[str, Any], *, index: int) -> FieldDef:
    path = f"fields[{index}]"
    if not isinstance(data, dict):
        raise ValueError(f"Field at {path} must be a mapping")
    reject_unknown_keys(data, _FIELD_KEYS, path=path)
    return FieldDef(
        key=str(data.get("key", "") or "").strip(),
        guidance=str(data.get("guidance", "") or ""),
        required=bool(data.get("required", False)),
        aliases=[str(a) for a in (data.get("aliases") or []) if a],
        validator=str(data.get("validator", "") or "").strip(),
        validator_args=dict(data.get("validator_args") or {}),
        decline_value=data.get("decline_value"),
        merge=bool(data.get("merge", False)),
        phone_locale=str(data.get("phone_locale", "") or ""),
    )


def _parse_gap_fill(data: Any) -> GapFillDef:
    if not data:
        return GapFillDef()
    if not isinstance(data, dict):
        raise ValueError("gap_fill must be a mapping")
    reject_unknown_keys(data, _GAP_FILL_KEYS, path="gap_fill")
    return GapFillDef(
        batch=bool(data.get("batch", True)),
        priority=[str(x) for x in (data.get("priority") or []) if x],
    )


def _parse_sync(data: Any) -> SyncDef:
    if not data:
        return SyncDef()
    if not isinstance(data, dict):
        raise ValueError("sync must be a mapping")
    reject_unknown_keys(data, _SYNC_KEYS, path="sync")
    mode = str(data.get("mode") or "on_capture").strip().lower()
    if mode not in ("on_capture", "on_complete", "manual"):
        raise ValueError("sync.mode must be on_capture, on_complete, or manual")
    return SyncDef(
        mode=mode,  # type: ignore[arg-type]
        min_fields=[str(x) for x in (data.get("min_fields") or ["name"]) if x],
        require_any=[
            str(x) for x in (data.get("require_any") or ["phone", "email"]) if x
        ],
        destinations=list(data.get("destinations") or []),
    )


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

    return LeadGenSpec(
        name=default_name,
        title=str(data.get("title", "") or ""),
        summary=str(data.get("summary", "") or ""),
        fields=fields,
        gap_fill=_parse_gap_fill(data.get("gap_fill")),
        sync=_parse_sync(data.get("sync")),
        handlers=_parse_handlers(data.get("handlers")),
        skill_tools=skill_tools,
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
