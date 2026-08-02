import json

from .binding_model import BindingRegistry
from .binding_model import BindingRegistryError


BINDING_REGISTRY_KEY = "flowpatch_binding_registry_v1"


def parse_binding_registry(raw):
    if isinstance(raw, BindingRegistry):
        return raw
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise BindingRegistryError(
                "BINDING_SERIALIZATION_FAILED",
                "BindingRegistry metadata is not valid JSON.",
            ) from exc
    elif isinstance(raw, dict):
        payload = dict(raw)
    else:
        raise BindingRegistryError(
            "BINDING_SERIALIZATION_FAILED",
            "BindingRegistry metadata must be a JSON object.",
        )
    if not isinstance(payload, dict):
        raise BindingRegistryError(
            "BINDING_SERIALIZATION_FAILED",
            "BindingRegistry JSON must contain an object.",
        )
    return BindingRegistry.from_dict(payload)


def encode_binding_registry(registry):
    if not isinstance(registry, BindingRegistry):
        registry = parse_binding_registry(registry)
    return json.dumps(
        registry.as_dict(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def clone_binding_registry(registry):
    return parse_binding_registry(encode_binding_registry(registry))


def load_binding_registry(obj):
    try:
        raw = obj.get(BINDING_REGISTRY_KEY, "")
    except (AttributeError, ReferenceError, RuntimeError, TypeError) as exc:
        raise BindingRegistryError(
            "BINDING_OBJECT_UNAVAILABLE",
            "The binding owner object is unavailable.",
        ) from exc
    return parse_binding_registry(raw) if raw else None


def save_binding_registry(obj, registry):
    encoded = encode_binding_registry(registry)
    try:
        obj[BINDING_REGISTRY_KEY] = encoded
    except (AttributeError, ReferenceError, RuntimeError, TypeError) as exc:
        raise BindingRegistryError(
            "BINDING_WRITE_FAILED",
            "The BindingRegistry could not be stored on its owner object.",
        ) from exc
    return registry


def snapshot_binding_property(obj):
    try:
        return str(obj.get(BINDING_REGISTRY_KEY, "") or "")
    except (AttributeError, ReferenceError, RuntimeError, TypeError) as exc:
        raise BindingRegistryError(
            "BINDING_OBJECT_UNAVAILABLE",
            "The binding owner object is unavailable.",
        ) from exc


def restore_binding_property(obj, snapshot):
    try:
        if snapshot:
            obj[BINDING_REGISTRY_KEY] = str(snapshot)
        elif BINDING_REGISTRY_KEY in obj:
            del obj[BINDING_REGISTRY_KEY]
    except (AttributeError, ReferenceError, RuntimeError, TypeError) as exc:
        raise BindingRegistryError(
            "BINDING_WRITE_FAILED",
            "The BindingRegistry snapshot could not be restored.",
        ) from exc
