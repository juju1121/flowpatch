import json
import math
from collections import deque
from dataclasses import dataclass


DEBUG_STATE_SCHEMA_VERSION = 1
DEFAULT_BREADCRUMB_LIMIT = 96
_MAX_COLLECTION_ITEMS = 64
_MAX_SNAPSHOT_DEPTH = 6


@dataclass(frozen=True)
class CapabilityResult:
    enabled: bool
    reason_code: str
    message: str = ""
    missing: tuple = ()
    state: str = "AVAILABLE"

    @classmethod
    def available(cls, state="AVAILABLE"):
        state = str(state).upper()
        if state not in {"AVAILABLE", "EXPERIMENTAL"}:
            raise ValueError(f"Invalid enabled capability state: {state}")
        return cls(
            enabled=True,
            reason_code="AVAILABLE",
            state=state,
        )

    @classmethod
    def disabled(cls, reason_code, message, missing=()):
        return cls(
            enabled=False,
            reason_code=str(reason_code),
            message=str(message),
            missing=tuple(str(value) for value in missing),
            state="DISABLED",
        )

    def as_dict(self):
        return {
            "enabled": bool(self.enabled),
            "state": str(self.state),
            "reason_code": str(self.reason_code),
            "message": str(self.message),
            "missing": list(self.missing),
        }


@dataclass(frozen=True)
class ToolSpec:
    identifier: str
    predicate: str
    maturity: str = "STABLE"

    def as_dict(self):
        return {
            "id": self.identifier,
            "predicate": self.predicate,
            "maturity": self.maturity,
        }


class ToolRegistry:
    def __init__(self, specs):
        ordered = tuple(specs)
        identifiers = [spec.identifier for spec in ordered]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("ToolRegistry contains duplicate identifiers.")
        self._ordered = ordered
        self._by_id = {spec.identifier: spec for spec in ordered}

    @property
    def ids(self):
        return tuple(spec.identifier for spec in self._ordered)

    def get(self, identifier):
        return self._by_id.get(str(identifier))

    def unknown_capability(self, identifier):
        return CapabilityResult.disabled(
            "UNKNOWN_TOOL_ID",
            f"Unknown FlowPatch tool: {identifier}",
            ("registered_tool_id",),
        )

    def normalize(self, capabilities):
        capabilities = dict(capabilities or {})
        normalized = {}
        for identifier in self.ids:
            result = capabilities.get(identifier)
            if isinstance(result, CapabilityResult):
                normalized[identifier] = result
            elif result is None:
                normalized[identifier] = CapabilityResult.disabled(
                    "MISSING_CAPABILITY_PREDICATE",
                    f"{identifier} has no evaluated capability predicate.",
                    (self._by_id[identifier].predicate,),
                )
            else:
                normalized[identifier] = CapabilityResult.disabled(
                    "INVALID_CAPABILITY_RESULT",
                    f"{identifier} returned an invalid capability result.",
                    ("CapabilityResult",),
                )
        return normalized

    def capability(self, identifier, capabilities):
        identifier = str(identifier)
        if identifier not in self._by_id:
            return self.unknown_capability(identifier)
        return self.normalize(capabilities)[identifier]

    def validate_ids(self, identifiers):
        identifiers = tuple(str(value) for value in identifiers)
        known = set(self.ids)
        provided = set(identifiers)
        issues = []
        duplicates = sorted(
            identifier
            for identifier in provided
            if identifiers.count(identifier) > 1
        )
        if duplicates:
            issues.append(f"duplicate:{','.join(duplicates)}")
        missing = sorted(known - provided)
        if missing:
            issues.append(f"missing:{','.join(missing)}")
        unknown = sorted(provided - known)
        if unknown:
            issues.append(f"unknown:{','.join(unknown)}")
        return tuple(issues)

    def inventory(self, capabilities=None):
        normalized = (
            self.normalize(capabilities)
            if capabilities is not None
            else None
        )
        inventory = []
        for spec in self._ordered:
            record = spec.as_dict()
            record["capability"] = (
                normalized[spec.identifier].as_dict()
                if normalized is not None
                else None
            )
            inventory.append(record)
        return inventory


TOOL_REGISTRY = ToolRegistry(
    (
        ToolSpec("DRAW", "always"),
        ToolSpec("EDIT", "has_retained_guides"),
        ToolSpec("BUILD", "has_valid_pending_cells", "LIMITED"),
        ToolSpec("DISSOLVE", "has_valid_pending_cells", "LIMITED"),
        ToolSpec("CUT", "has_uncommitted_guide_network", "EXPERIMENTAL"),
        ToolSpec("TRIM", "deferred_batch_17", "DISABLED"),
        ToolSpec("LOOP_CUT", "deferred_batch_13", "DISABLED"),
        ToolSpec("SURFACE_FOLLOW", "has_one_pending_cell", "LIMITED"),
        ToolSpec("SURFACE_TIGHTEN", "has_one_pending_cell", "LIMITED"),
        ToolSpec("BOUNDARY", "has_selected_open_mesh_boundary", "EXPERIMENTAL"),
        ToolSpec("DENSITY", "has_valid_pending_cells", "LIMITED"),
        ToolSpec("RELAX", "has_selected_editable_guides", "LIMITED"),
        ToolSpec("DELETE", "has_retained_guides"),
        ToolSpec("MIRROR", "always", "EXPERIMENTAL"),
        ToolSpec("CONTROL_POINTS", "always"),
    )
)


def _json_safe(value, depth=0):
    if depth > _MAX_SNAPSHOT_DEPTH:
        return "<depth-limit>"
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, dict):
        items = sorted(value.items(), key=lambda item: str(item[0]))
        return {
            str(key): _json_safe(item, depth + 1)
            for key, item in items[:_MAX_COLLECTION_ITEMS]
        }
    if isinstance(value, (list, tuple, deque)):
        return [
            _json_safe(item, depth + 1)
            for item in list(value)[:_MAX_COLLECTION_ITEMS]
        ]
    if isinstance(value, (set, frozenset)):
        ordered = sorted(value, key=lambda item: repr(item))
        return [
            _json_safe(item, depth + 1)
            for item in ordered[:_MAX_COLLECTION_ITEMS]
        ]
    to_tuple = getattr(value, "to_tuple", None)
    if callable(to_tuple):
        try:
            return _json_safe(tuple(to_tuple()), depth + 1)
        except Exception:
            pass
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return {
            "type": type(value).__name__,
            "name": name,
        }
    return f"<{type(value).__name__}>"


class BreadcrumbLog:
    def __init__(self, limit=DEFAULT_BREADCRUMB_LIMIT):
        limit = int(limit)
        if limit < 1:
            raise ValueError("Breadcrumb limit must be positive.")
        self._entries = deque(maxlen=limit)
        self._next_sequence = 1

    @property
    def limit(self):
        return int(self._entries.maxlen)

    def record(self, event, **details):
        event = str(event).strip()
        if not event:
            raise ValueError("Breadcrumb event must not be empty.")
        record = {
            "sequence": self._next_sequence,
            "event": event,
            "details": _json_safe(details),
        }
        self._next_sequence += 1
        self._entries.append(record)
        return _json_safe(record)

    def snapshot(self):
        return _json_safe(list(self._entries))

    def clear(self, reset_sequence=False):
        self._entries.clear()
        if reset_sequence:
            self._next_sequence = 1


SESSION_BREADCRUMBS = BreadcrumbLog()


def build_debug_document(
    session_state=None,
    capabilities=None,
    context_state=None,
    breadcrumbs=None,
):
    breadcrumbs = breadcrumbs or SESSION_BREADCRUMBS
    return {
        "schema_version": DEBUG_STATE_SCHEMA_VERSION,
        "context": _json_safe(context_state or {}),
        "session": _json_safe(session_state or {"active": False}),
        "tool_registry": TOOL_REGISTRY.inventory(capabilities),
        "breadcrumbs": breadcrumbs.snapshot(),
    }


def debug_document_json(document):
    return json.dumps(
        _json_safe(document),
        indent=2,
        sort_keys=True,
        ensure_ascii=True,
    )
