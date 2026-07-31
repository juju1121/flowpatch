from dataclasses import dataclass


class AutoBuildPlanError(ValueError):
    def __init__(self, reason_code, message):
        super().__init__(str(message))
        self.reason_code = str(reason_code)


@dataclass(frozen=True)
class AutoBuildSkip:
    cycle_key: str
    reason_code: str

    def as_dict(self):
        return {
            "cycle_key": self.cycle_key,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True)
class AutoBuildPlan:
    cycle_keys: tuple
    skipped: tuple

    def as_dict(self):
        return {
            "cycle_keys": list(self.cycle_keys),
            "skipped": [item.as_dict() for item in self.skipped],
        }


def _preview_value(preview, name, default=None):
    if isinstance(preview, dict):
        return preview.get(name, default)
    return getattr(preview, name, default)


def _owned_tuple(preview, name):
    value = _preview_value(preview, name, ())
    if value is None or isinstance(value, (str, bytes)):
        return ()
    try:
        return tuple(value)
    except TypeError:
        return ()


def _key_set(values):
    return {
        str(value)
        for value in tuple(values or ())
        if str(value)
    }


def plan_auto_build(
    previews,
    previous_preview_keys=(),
    built_cell_keys=(),
):
    """Select only newly available, valid four-sided grid previews."""

    previous = _key_set(previous_preview_keys)
    built = _key_set(built_cell_keys)
    seen = set()
    eligible = []
    skipped = []

    for index, preview in enumerate(tuple(previews or ())):
        cycle_key = str(_preview_value(preview, "cycle_key", "") or "")
        if not cycle_key:
            skipped.append(
                AutoBuildSkip(
                    cycle_key=f"<missing:{index}>",
                    reason_code="MISSING_CYCLE_KEY",
                )
            )
            continue
        if cycle_key in seen:
            raise AutoBuildPlanError(
                "DUPLICATE_PREVIEW_KEY",
                f"Preview key {cycle_key!r} appears more than once.",
            )
        seen.add(cycle_key)

        if cycle_key in built:
            skipped.append(
                AutoBuildSkip(cycle_key, "ALREADY_BUILT")
            )
            continue
        if cycle_key in previous:
            skipped.append(
                AutoBuildSkip(cycle_key, "MANUAL_RETRY_REQUIRED")
            )
            continue
        if str(
            _preview_value(preview, "validation_status", "")
        ).upper() != "VALID":
            skipped.append(
                AutoBuildSkip(cycle_key, "INVALID_PREVIEW")
            )
            continue
        if str(
            _preview_value(preview, "topology_kind", "")
        ).upper() != "GRID":
            skipped.append(
                AutoBuildSkip(cycle_key, "NOT_FOUR_SIDED_GRID")
            )
            continue

        side_keys = tuple(str(value) for value in _owned_tuple(
            preview,
            "side_keys",
        ))
        side_forward = _owned_tuple(preview, "side_forward")
        corner_node_ids = _owned_tuple(preview, "corner_node_ids")
        if (
            len(side_keys) != 4
            or len(set(side_keys)) != 4
            or len(side_forward) != 4
            or len(corner_node_ids) != 4
            or len(set(corner_node_ids)) != 4
        ):
            skipped.append(
                AutoBuildSkip(
                    cycle_key,
                    "INVALID_FOUR_SIDED_OWNERSHIP",
                )
            )
            continue

        eligible.append(cycle_key)

    return AutoBuildPlan(
        cycle_keys=tuple(eligible),
        skipped=tuple(skipped),
    )
