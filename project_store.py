import json
import uuid
from dataclasses import dataclass


PROJECT_SCHEMA_VERSION = 1
COMPOSITE_SCHEMA_VERSION = 1
OBJECT_UUID_KEY = "flowpatch_object_uuid_v1"
PROJECT_UUID_KEY = "flowpatch_project_uuid_v1"
PROJECT_RECORD_KEY = "flowpatch_project_record_v1"
COMPOSITE_SESSION_KEY = "flowpatch_composite_session_v1"


class ProjectStoreError(RuntimeError):
    def __init__(self, reason_code, message):
        super().__init__(message)
        self.reason_code = str(reason_code)


@dataclass(frozen=True)
class ProjectRecord:
    project_uuid: str
    schema_version: int
    target_object_uuid: str
    retopo_object_uuid: str

    def as_dict(self):
        return {
            "project_uuid": self.project_uuid,
            "schema_version": int(self.schema_version),
            "target_object_uuid": self.target_object_uuid,
            "retopo_object_uuid": self.retopo_object_uuid,
        }


@dataclass(frozen=True)
class CompositeMember:
    project_uuid: str
    target_object_uuid: str
    retopo_object_uuid: str

    def as_dict(self):
        return {
            "project_uuid": self.project_uuid,
            "target_object_uuid": self.target_object_uuid,
            "retopo_object_uuid": self.retopo_object_uuid,
        }


@dataclass(frozen=True)
class CompositeSessionRecord:
    session_uuid: str
    schema_version: int
    members: tuple

    def as_dict(self):
        return {
            "session_uuid": self.session_uuid,
            "schema_version": int(self.schema_version),
            "members": [
                member.as_dict() for member in self.members
            ],
        }


@dataclass(frozen=True)
class ProjectIssue:
    reason_code: str
    object_name: str
    project_uuid: str
    message: str

    def as_dict(self):
        return {
            "reason_code": self.reason_code,
            "object_name": self.object_name,
            "project_uuid": self.project_uuid,
            "message": self.message,
        }


def new_uuid():
    return str(uuid.uuid4())


def _canonical_uuid(value, reason_code, label):
    text = str(value or "").strip()
    try:
        return str(uuid.UUID(text))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ProjectStoreError(
            reason_code,
            f"{label} is missing or malformed.",
        ) from exc


def _object_name(owner):
    return str(getattr(owner, "name", "") or owner.get("name", "<unnamed>"))


def ensure_object_uuid(owner, uuid_factory=new_uuid):
    existing = owner.get(OBJECT_UUID_KEY, "")
    if existing:
        return _canonical_uuid(
            existing,
            "MALFORMED_OBJECT_UUID",
            f"Object UUID on {_object_name(owner)}",
        )
    value = _canonical_uuid(
        uuid_factory(),
        "MALFORMED_GENERATED_UUID",
        "Generated object UUID",
    )
    owner[OBJECT_UUID_KEY] = value
    return value


def parse_project_record(raw):
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ProjectStoreError(
                "MALFORMED_PROJECT_RECORD",
                "FlowPatch project metadata is not valid JSON.",
            ) from exc
    elif isinstance(raw, dict):
        payload = dict(raw)
    else:
        raise ProjectStoreError(
            "MALFORMED_PROJECT_RECORD",
            "FlowPatch project metadata must be a JSON object.",
        )

    try:
        schema_version = int(payload.get("schema_version", 0))
    except (TypeError, ValueError) as exc:
        raise ProjectStoreError(
            "MALFORMED_PROJECT_SCHEMA",
            "FlowPatch project schema is malformed.",
        ) from exc
    if schema_version != PROJECT_SCHEMA_VERSION:
        raise ProjectStoreError(
            "UNSUPPORTED_PROJECT_SCHEMA",
            (
                f"FlowPatch project schema {schema_version} is unsupported; "
                f"expected {PROJECT_SCHEMA_VERSION}."
            ),
        )

    return ProjectRecord(
        project_uuid=_canonical_uuid(
            payload.get("project_uuid"),
            "MALFORMED_PROJECT_UUID",
            "Project UUID",
        ),
        schema_version=schema_version,
        target_object_uuid=_canonical_uuid(
            payload.get("target_object_uuid"),
            "MALFORMED_TARGET_UUID",
            "Target object UUID",
        ),
        retopo_object_uuid=_canonical_uuid(
            payload.get("retopo_object_uuid"),
            "MALFORMED_RETOPO_UUID",
            "Retopo object UUID",
        ),
    )


def encode_project_record(record):
    if not isinstance(record, ProjectRecord):
        record = parse_project_record(record)
    return json.dumps(
        record.as_dict(),
        sort_keys=True,
        separators=(",", ":"),
    )


def _composite_member(payload):
    if isinstance(payload, CompositeMember):
        return payload
    if not isinstance(payload, dict):
        raise ProjectStoreError(
            "MALFORMED_COMPOSITE_MEMBER",
            "Composite session members must be JSON objects.",
        )
    return CompositeMember(
        project_uuid=_canonical_uuid(
            payload.get("project_uuid"),
            "MALFORMED_PROJECT_UUID",
            "Composite project UUID",
        ),
        target_object_uuid=_canonical_uuid(
            payload.get("target_object_uuid"),
            "MALFORMED_TARGET_UUID",
            "Composite target UUID",
        ),
        retopo_object_uuid=_canonical_uuid(
            payload.get("retopo_object_uuid"),
            "MALFORMED_RETOPO_UUID",
            "Composite retopo UUID",
        ),
    )


def parse_composite_session(raw):
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ProjectStoreError(
                "MALFORMED_COMPOSITE_SESSION",
                "Composite session metadata is not valid JSON.",
            ) from exc
    elif isinstance(raw, dict):
        payload = dict(raw)
    elif isinstance(raw, CompositeSessionRecord):
        return raw
    else:
        raise ProjectStoreError(
            "MALFORMED_COMPOSITE_SESSION",
            "Composite session metadata must be a JSON object.",
        )
    try:
        schema_version = int(payload.get("schema_version", 0))
    except (TypeError, ValueError) as exc:
        raise ProjectStoreError(
            "MALFORMED_COMPOSITE_SCHEMA",
            "Composite session schema is malformed.",
        ) from exc
    if schema_version != COMPOSITE_SCHEMA_VERSION:
        raise ProjectStoreError(
            "UNSUPPORTED_COMPOSITE_SCHEMA",
            (
                f"Composite session schema {schema_version} is unsupported; "
                f"expected {COMPOSITE_SCHEMA_VERSION}."
            ),
        )
    raw_members = payload.get("members", ())
    if not isinstance(raw_members, (list, tuple)):
        raise ProjectStoreError(
            "MALFORMED_COMPOSITE_MEMBERS",
            "Composite session members must be a JSON list.",
        )
    members = tuple(
        sorted(
            (_composite_member(item) for item in raw_members),
            key=lambda item: (
                item.project_uuid,
                item.retopo_object_uuid,
            ),
        )
    )
    if len(members) < 2:
        raise ProjectStoreError(
            "COMPOSITE_MEMBER_COUNT",
            "A composite session needs at least two projects.",
        )
    if len({item.project_uuid for item in members}) != len(members):
        raise ProjectStoreError(
            "DUPLICATE_COMPOSITE_PROJECT",
            "A project appears more than once in the composite session.",
        )
    if len({item.retopo_object_uuid for item in members}) != len(members):
        raise ProjectStoreError(
            "DUPLICATE_COMPOSITE_OBJECT",
            "A retopo object appears more than once in the composite session.",
        )
    return CompositeSessionRecord(
        session_uuid=_canonical_uuid(
            payload.get("session_uuid"),
            "MALFORMED_COMPOSITE_UUID",
            "Composite session UUID",
        ),
        schema_version=schema_version,
        members=members,
    )


def encode_composite_session(record):
    record = parse_composite_session(record)
    return json.dumps(
        record.as_dict(),
        sort_keys=True,
        separators=(",", ":"),
    )


def build_composite_session(
    project_records,
    session_uuid="",
    uuid_factory=new_uuid,
):
    records = tuple(project_records)
    members = tuple(
        sorted(
            (
                CompositeMember(
                    project_uuid=record.project_uuid,
                    target_object_uuid=record.target_object_uuid,
                    retopo_object_uuid=record.retopo_object_uuid,
                )
                for record in records
            ),
            key=lambda item: (
                item.project_uuid,
                item.retopo_object_uuid,
            ),
        )
    )
    return parse_composite_session(
        {
            "session_uuid": session_uuid or uuid_factory(),
            "schema_version": COMPOSITE_SCHEMA_VERSION,
            "members": [member.as_dict() for member in members],
        }
    )


def read_composite_session(owner):
    raw = owner.get(COMPOSITE_SESSION_KEY, "")
    return parse_composite_session(raw) if raw else None


def write_composite_session(owner, record, allow_replace=False):
    record = parse_composite_session(record)
    raw = owner.get(COMPOSITE_SESSION_KEY, "")
    if raw and not allow_replace:
        existing = parse_composite_session(raw)
        if existing != record:
            raise ProjectStoreError(
                "COMPOSITE_OVERWRITE_BLOCKED",
                (
                    "A different composite session already exists; "
                    "its membership was not overwritten."
                ),
            )
    owner[COMPOSITE_SESSION_KEY] = encode_composite_session(record)
    return record


def clear_composite_session(owner):
    if COMPOSITE_SESSION_KEY not in owner:
        return False
    del owner[COMPOSITE_SESSION_KEY]
    return True


def resolve_composite_objects(objects, record):
    record = parse_composite_session(record)
    resolved = []
    owners = tuple(objects)
    for member in record.members:
        owner = find_object_by_uuid(owners, member.retopo_object_uuid)
        if owner is None:
            raise ProjectStoreError(
                "MISSING_COMPOSITE_OBJECT",
                (
                    "A composite project object is missing: "
                    f"{member.retopo_object_uuid}."
                ),
            )
        project = read_project_record(owner)
        if project is None or (
            project.project_uuid != member.project_uuid
            or project.target_object_uuid != member.target_object_uuid
            or project.retopo_object_uuid != member.retopo_object_uuid
        ):
            raise ProjectStoreError(
                "COMPOSITE_PROJECT_MISMATCH",
                (
                    f"Composite member {_object_name(owner)} no longer "
                    "matches its persistent project record."
                ),
            )
        resolved.append(owner)
    return tuple(resolved)


def without_composite_project(record, project_uuid):
    record = parse_composite_session(record)
    project_uuid = _canonical_uuid(
        project_uuid,
        "MALFORMED_PROJECT_UUID",
        "Removed composite project UUID",
    )
    members = tuple(
        member
        for member in record.members
        if member.project_uuid != project_uuid
    )
    if len(members) == len(record.members):
        return record
    if len(members) < 2:
        return None
    return CompositeSessionRecord(
        session_uuid=record.session_uuid,
        schema_version=record.schema_version,
        members=members,
    )


def read_project_record(owner):
    raw = owner.get(PROJECT_RECORD_KEY, "")
    if not raw:
        return None
    record = parse_project_record(raw)
    direct_uuid = owner.get(PROJECT_UUID_KEY, "")
    if direct_uuid:
        direct_uuid = _canonical_uuid(
            direct_uuid,
            "MALFORMED_PROJECT_UUID",
            "Direct project UUID",
        )
        if direct_uuid != record.project_uuid:
            raise ProjectStoreError(
                "PROJECT_UUID_MISMATCH",
                "Direct and serialized FlowPatch project UUIDs disagree.",
            )
    return record


def write_project_record(owner, record, allow_replace=False):
    if not isinstance(record, ProjectRecord):
        record = parse_project_record(record)
    raw = owner.get(PROJECT_RECORD_KEY, "")
    if raw and not allow_replace:
        existing = parse_project_record(raw)
        if existing != record:
            raise ProjectStoreError(
                "PROJECT_OVERWRITE_BLOCKED",
                (
                    "This mesh already belongs to another FlowPatch project; "
                    "its metadata was not overwritten."
                ),
            )
    owner[PROJECT_UUID_KEY] = record.project_uuid
    owner[PROJECT_RECORD_KEY] = encode_project_record(record)
    return record


def _snapshot_owner_keys(owner, keys):
    return {
        key: (key in owner, owner.get(key))
        for key in keys
    }


def _restore_owner_keys(owner, snapshot):
    for key, (existed, value) in snapshot.items():
        if existed:
            owner[key] = value
        elif key in owner:
            del owner[key]


def bind_project(target, retopo, uuid_factory=new_uuid):
    existing = read_project_record(retopo)
    if existing is not None:
        target_uuid = _canonical_uuid(
            target.get(OBJECT_UUID_KEY, ""),
            "MISSING_TARGET_UUID",
            "Existing project target UUID",
        )
        retopo_uuid = _canonical_uuid(
            retopo.get(OBJECT_UUID_KEY, ""),
            "MISSING_RETOPO_UUID",
            "Existing project retopo UUID",
        )
        if (
            existing.target_object_uuid != target_uuid
            or existing.retopo_object_uuid != retopo_uuid
        ):
            raise ProjectStoreError(
                "PROJECT_BINDING_CONFLICT",
                (
                    "The selected retopo mesh belongs to a different FlowPatch "
                    "target or object identity."
                ),
            )
        return existing

    target_before = _snapshot_owner_keys(target, (OBJECT_UUID_KEY,))
    retopo_before = _snapshot_owner_keys(
        retopo,
        (OBJECT_UUID_KEY, PROJECT_UUID_KEY, PROJECT_RECORD_KEY),
    )
    try:
        target_uuid = ensure_object_uuid(target, uuid_factory)
        retopo_uuid = ensure_object_uuid(retopo, uuid_factory)
        record = ProjectRecord(
            project_uuid=_canonical_uuid(
                uuid_factory(),
                "MALFORMED_GENERATED_UUID",
                "Generated project UUID",
            ),
            schema_version=PROJECT_SCHEMA_VERSION,
            target_object_uuid=target_uuid,
            retopo_object_uuid=retopo_uuid,
        )
        return write_project_record(retopo, record)
    except Exception:
        _restore_owner_keys(target, target_before)
        _restore_owner_keys(retopo, retopo_before)
        raise


def clear_project_identity(owner, clear_object_uuid=True):
    removed = []
    for key in (PROJECT_UUID_KEY, PROJECT_RECORD_KEY):
        if key in owner:
            del owner[key]
            removed.append(key)
    if clear_object_uuid and OBJECT_UUID_KEY in owner:
        del owner[OBJECT_UUID_KEY]
        removed.append(OBJECT_UUID_KEY)
    return tuple(removed)


def find_object_by_uuid(objects, object_uuid):
    wanted = _canonical_uuid(
        object_uuid,
        "MALFORMED_OBJECT_UUID",
        "Requested object UUID",
    )
    matches = []
    for owner in objects:
        raw = owner.get(OBJECT_UUID_KEY, "")
        if not raw:
            continue
        try:
            current = _canonical_uuid(
                raw,
                "MALFORMED_OBJECT_UUID",
                f"Object UUID on {_object_name(owner)}",
            )
        except ProjectStoreError:
            continue
        if current == wanted:
            matches.append(owner)
    if len(matches) > 1:
        names = ", ".join(
            _object_name(owner)
            for owner in sorted(matches, key=_object_name)
        )
        raise ProjectStoreError(
            "DUPLICATE_OBJECT_UUID",
            f"More than one object owns UUID {wanted}: {names}.",
        )
    return matches[0] if matches else None


def repair_passive_target_uuid_copies(objects, preferred_target, project_owner):
    owners = tuple(objects)
    record = read_project_record(project_owner)
    if record is None:
        raise ProjectStoreError(
            "MISSING_PROJECT_RECORD",
            "The FlowPatch project has no stable ownership record to recover.",
        )

    preferred_uuid = _canonical_uuid(
        preferred_target.get(OBJECT_UUID_KEY, ""),
        "MISSING_TARGET_UUID",
        f"Configured Surface UUID on {_object_name(preferred_target)}",
    )
    if preferred_uuid != record.target_object_uuid:
        raise ProjectStoreError(
            "CONFIGURED_TARGET_UUID_MISMATCH",
            (
                f"Configured Surface {_object_name(preferred_target)} does not "
                "own the target UUID recorded by this FlowPatch project."
            ),
        )

    matches = []
    for owner in owners:
        raw = owner.get(OBJECT_UUID_KEY, "")
        if not raw:
            continue
        try:
            current = _canonical_uuid(
                raw,
                "MALFORMED_OBJECT_UUID",
                f"Object UUID on {_object_name(owner)}",
            )
        except ProjectStoreError:
            continue
        if current == preferred_uuid:
            matches.append(owner)

    if not any(owner is preferred_target for owner in matches):
        raise ProjectStoreError(
            "CONFIGURED_TARGET_NOT_FOUND",
            "The configured Surface is not part of the current Blender data.",
        )

    passive = tuple(
        sorted(
            (
                owner
                for owner in matches
                if owner is not preferred_target
            ),
            key=_object_name,
        )
    )
    if not passive:
        return ()

    protected = tuple(
        owner
        for owner in passive
        if PROJECT_UUID_KEY in owner or PROJECT_RECORD_KEY in owner
    )
    if protected:
        names = ", ".join(_object_name(owner) for owner in protected)
        raise ProjectStoreError(
            "DUPLICATE_OBJECT_UUID_HAS_PROJECT",
            (
                "FlowPatch cannot automatically recover the copied Surface "
                f"identity because {names} also owns project metadata."
            ),
        )

    snapshots = {
        id(owner): _snapshot_owner_keys(owner, (OBJECT_UUID_KEY,))
        for owner in passive
    }
    try:
        for owner in passive:
            del owner[OBJECT_UUID_KEY]
        resolved = find_object_by_uuid(owners, preferred_uuid)
        if resolved is not preferred_target:
            raise ProjectStoreError(
                "DUPLICATE_OBJECT_UUID_REPAIR_FAILED",
                "Surface ownership was still ambiguous after recovery.",
            )
    except Exception as exc:
        for owner in passive:
            _restore_owner_keys(owner, snapshots[id(owner)])
        if isinstance(exc, ProjectStoreError):
            raise
        raise ProjectStoreError(
            "DUPLICATE_OBJECT_UUID_REPAIR_FAILED",
            "FlowPatch could not safely recover copied Surface ownership.",
        ) from exc

    return tuple(_object_name(owner) for owner in passive)


def repair_active_project_copy(
    objects,
    target,
    retopo,
    uuid_factory=new_uuid,
):
    """Give an explicitly active copied retopo object its own project domain."""
    owners = tuple(objects)
    record = read_project_record(retopo)
    if record is None:
        return None, ()

    copied_from = tuple(
        sorted(
            {
                _object_name(owner)
                for owner in owners
                if owner is not retopo
                and (
                    owner.get(OBJECT_UUID_KEY, "")
                    == record.retopo_object_uuid
                    or owner.get(PROJECT_UUID_KEY, "")
                    == record.project_uuid
                )
            }
        )
    )
    if not copied_from:
        return record, ()

    target_uuid = _canonical_uuid(
        target.get(OBJECT_UUID_KEY, ""),
        "MISSING_TARGET_UUID",
        f"Configured Surface UUID on {_object_name(target)}",
    )
    if target_uuid != record.target_object_uuid:
        raise ProjectStoreError(
            "COPIED_PROJECT_TARGET_MISMATCH",
            "The copied FlowPatch project no longer resolves its recorded Surface.",
        )

    snapshot = _snapshot_owner_keys(
        retopo,
        (OBJECT_UUID_KEY, PROJECT_UUID_KEY, PROJECT_RECORD_KEY),
    )
    try:
        retopo_uuid = _canonical_uuid(
            uuid_factory(),
            "MALFORMED_GENERATED_UUID",
            "Copied retopo object UUID",
        )
        project_uuid = _canonical_uuid(
            uuid_factory(),
            "MALFORMED_GENERATED_UUID",
            "Copied project UUID",
        )
        retopo[OBJECT_UUID_KEY] = retopo_uuid
        repaired = ProjectRecord(
            project_uuid=project_uuid,
            schema_version=PROJECT_SCHEMA_VERSION,
            target_object_uuid=record.target_object_uuid,
            retopo_object_uuid=retopo_uuid,
        )
        write_project_record(retopo, repaired, allow_replace=True)
    except Exception:
        _restore_owner_keys(retopo, snapshot)
        raise
    return repaired, copied_from


def project_objects_for_target(objects, target_uuid):
    wanted = _canonical_uuid(
        target_uuid,
        "MALFORMED_TARGET_UUID",
        "Requested target UUID",
    )
    matches = []
    for owner in objects:
        try:
            record = read_project_record(owner)
        except ProjectStoreError:
            continue
        if record is not None and record.target_object_uuid == wanted:
            matches.append(owner)
    return tuple(sorted(matches, key=_object_name))


def audit_projects(objects):
    owners = tuple(objects)
    issues = []
    uuid_owners = {}
    project_owners = {}
    records = []

    for owner in owners:
        raw_object_uuid = owner.get(OBJECT_UUID_KEY, "")
        if raw_object_uuid:
            try:
                object_uuid = _canonical_uuid(
                    raw_object_uuid,
                    "MALFORMED_OBJECT_UUID",
                    f"Object UUID on {_object_name(owner)}",
                )
            except ProjectStoreError as exc:
                issues.append(
                    ProjectIssue(
                        exc.reason_code,
                        _object_name(owner),
                        "",
                        str(exc),
                    )
                )
            else:
                uuid_owners.setdefault(object_uuid, []).append(owner)

        if not owner.get(PROJECT_RECORD_KEY, ""):
            continue
        try:
            record = read_project_record(owner)
        except ProjectStoreError as exc:
            issues.append(
                ProjectIssue(
                    exc.reason_code,
                    _object_name(owner),
                    str(owner.get(PROJECT_UUID_KEY, "")),
                    str(exc),
                )
            )
            continue
        records.append((owner, record))
        project_owners.setdefault(record.project_uuid, []).append(owner)

    for object_uuid, matches in uuid_owners.items():
        if len(matches) <= 1:
            continue
        for owner in matches:
            issues.append(
                ProjectIssue(
                    "DUPLICATE_OBJECT_UUID",
                    _object_name(owner),
                    "",
                    f"Object UUID {object_uuid} is owned by multiple objects.",
                )
            )

    for project_uuid, matches in project_owners.items():
        if len(matches) <= 1:
            continue
        for owner in matches:
            issues.append(
                ProjectIssue(
                    "DUPLICATE_PROJECT_UUID",
                    _object_name(owner),
                    project_uuid,
                    (
                        f"Project UUID {project_uuid} is owned by multiple "
                        "retopo objects."
                    ),
                )
            )

    for owner, record in records:
        owner_uuid = owner.get(OBJECT_UUID_KEY, "")
        try:
            owner_uuid = _canonical_uuid(
                owner_uuid,
                "MALFORMED_OBJECT_UUID",
                f"Object UUID on {_object_name(owner)}",
            )
        except ProjectStoreError:
            continue
        if owner_uuid != record.retopo_object_uuid:
            issues.append(
                ProjectIssue(
                    "RETOPO_UUID_MISMATCH",
                    _object_name(owner),
                    record.project_uuid,
                    "The project record points at a different retopo object UUID.",
                )
            )
        try:
            target = find_object_by_uuid(
                owners,
                record.target_object_uuid,
            )
        except ProjectStoreError as exc:
            issues.append(
                ProjectIssue(
                    exc.reason_code,
                    _object_name(owner),
                    record.project_uuid,
                    str(exc),
                )
            )
            continue
        if target is None:
            issues.append(
                ProjectIssue(
                    "MISSING_TARGET_OBJECT",
                    _object_name(owner),
                    record.project_uuid,
                    "The FlowPatch target object is missing.",
                )
            )

    unique = {}
    for issue in issues:
        key = (
            issue.reason_code,
            issue.object_name,
            issue.project_uuid,
            issue.message,
        )
        unique[key] = issue
    return tuple(
        sorted(
            unique.values(),
            key=lambda item: (
                item.reason_code,
                item.object_name,
                item.project_uuid,
            ),
        )
    )


def audit_composite_session(owner, objects):
    raw = owner.get(COMPOSITE_SESSION_KEY, "")
    if not raw:
        return ()
    try:
        record = parse_composite_session(raw)
        resolve_composite_objects(tuple(objects), record)
    except ProjectStoreError as exc:
        return (
            ProjectIssue(
                exc.reason_code,
                _object_name(owner),
                "",
                str(exc),
            ),
        )
    return ()


def audit_snapshot(issues, limit=32):
    items = list(issues)
    bounded = items[: max(0, int(limit))]
    return {
        "issue_count": len(items),
        "issues": [issue.as_dict() for issue in bounded],
        "truncated": len(items) > len(bounded),
    }
