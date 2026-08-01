from dataclasses import dataclass


_TRANSFORM_MODES = {"MOVE", "ROTATE", "SCALE"}
_HOVER_KINDS = {"CANONICAL_NODE", "GUIDE_CONTROL", "GUIDE_EDGE"}


def _normalize_refs(refs):
    normalized = set()
    for ref in refs or ():
        try:
            guide_index, point_index = ref
            guide_index = int(guide_index)
            point_index = int(point_index)
        except (TypeError, ValueError):
            continue
        if guide_index < 0 or point_index < 0:
            continue
        normalized.add((guide_index, point_index))
    return tuple(sorted(normalized))


@dataclass(frozen=True)
class HoverTransformDecision:
    accepted: bool
    mode: str
    refs: tuple = ()
    temporary: bool = False
    reason_code: str = ""
    message: str = ""


def resolve_hover_transform(
    mode,
    *,
    selected_refs=(),
    hover_kind="",
    hover_refs=(),
):
    mode = str(mode).upper()
    if mode not in _TRANSFORM_MODES:
        return HoverTransformDecision(
            False,
            mode,
            reason_code="UNKNOWN_TRANSFORM",
            message="FlowPatch does not recognize that guide transform.",
        )

    selected = _normalize_refs(selected_refs)
    if selected:
        return HoverTransformDecision(
            True,
            mode,
            refs=selected,
            temporary=False,
        )

    hover_kind = str(hover_kind).upper()
    hovered = _normalize_refs(hover_refs)
    if hover_kind not in _HOVER_KINDS or not hovered:
        return HoverTransformDecision(
            False,
            mode,
            reason_code="GUIDE_HOVER_REQUIRED",
            message="Hover a guide node or edge, then press G, R, or S.",
        )

    if hover_kind in {"CANONICAL_NODE", "GUIDE_CONTROL"} and mode != "MOVE":
        return HoverTransformDecision(
            False,
            mode,
            reason_code="HOVER_EDGE_REQUIRED",
            message="Hover a guide edge for R or S; a single node supports G.",
        )

    if hover_kind == "GUIDE_EDGE" and len(hovered) < 2:
        return HoverTransformDecision(
            False,
            mode,
            reason_code="INVALID_GUIDE_EDGE",
            message="The hovered guide edge has too few control points.",
        )

    return HoverTransformDecision(
        True,
        mode,
        refs=hovered,
        temporary=True,
    )
