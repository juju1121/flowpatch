ADDON_VERSION = (1, 4, 16)
ADDON_VERSION_STRING = ".".join(str(value) for value in ADDON_VERSION)
BUILD_ID = "recovery-p1-session-stop-20260731"
SOURCE_BRANCH = "recovery/v1.4.12-core"

# This digest covers the canonical runtime payload, excluding this metadata
# file so the value is reproducible rather than self-referential. The exact
# ZIP archive digest is delivered beside the package as a detached checksum.
PACKAGE_PAYLOAD_SHA256 = (
    "3FE90417B46874DABDA0AF0518794A02"
    "84746F8583D2300D7F4FDBF0808744E6"
)
PACKAGE_PAYLOAD_HASH_SCOPE = (
    "canonical packaged runtime files except build_identity.py; "
    "sorted relative UTF-8 path, NUL, file bytes, NUL"
)


def semantic_version():
    return ADDON_VERSION_STRING


def compact_build_label():
    return f"v{ADDON_VERSION_STRING}  P1"
