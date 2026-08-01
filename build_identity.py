ADDON_VERSION = (1, 4, 20)
ADDON_VERSION_STRING = ".".join(str(value) for value in ADDON_VERSION)
BUILD_ID = "recovery-pf02-binding-registry-20260801"
SOURCE_BRANCH = "recovery/v1.4.12-core"

# This digest covers the canonical runtime payload, excluding this metadata
# file so the value is reproducible rather than self-referential. The exact
# ZIP archive digest is delivered beside the package as a detached checksum.
PACKAGE_PAYLOAD_SHA256 = (
    "3EF68598002A4C2E773B01DDD4C4506C"
    "714B57371655BC1BAA9D9F88C690A811"
)
PACKAGE_PAYLOAD_HASH_SCOPE = (
    "canonical packaged runtime files except build_identity.py; "
    "sorted relative UTF-8 path, NUL, file bytes, NUL"
)


def semantic_version():
    return ADDON_VERSION_STRING


def compact_build_label():
    return f"v{ADDON_VERSION_STRING}  PF-02"
