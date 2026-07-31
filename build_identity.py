ADDON_VERSION = (1, 4, 14)
ADDON_VERSION_STRING = ".".join(str(value) for value in ADDON_VERSION)
BUILD_ID = "recovery-p0-safe-diagnostics-20260731"
SOURCE_BRANCH = "recovery/v1.4.12-core"

# This digest covers the canonical runtime payload, excluding this metadata
# file so the value is reproducible rather than self-referential. The exact
# ZIP archive digest is delivered beside the package as a detached checksum.
PACKAGE_PAYLOAD_SHA256 = (
    "9D81F27C9D7D24472BC33809F174712E"
    "D7F60ED7B9D57E9D3DACDD3E027C397F"
)
PACKAGE_PAYLOAD_HASH_SCOPE = (
    "canonical packaged runtime files except build_identity.py; "
    "sorted relative UTF-8 path, NUL, file bytes, NUL"
)


def semantic_version():
    return ADDON_VERSION_STRING


def compact_build_label():
    return f"v{ADDON_VERSION_STRING}  P0"
