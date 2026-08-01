ADDON_VERSION = (1, 4, 17)
ADDON_VERSION_STRING = ".".join(str(value) for value in ADDON_VERSION)
BUILD_ID = "recovery-p1-draw-sampling-20260731"
SOURCE_BRANCH = "recovery/v1.4.12-core"

# This digest covers the canonical runtime payload, excluding this metadata
# file so the value is reproducible rather than self-referential. The exact
# ZIP archive digest is delivered beside the package as a detached checksum.
PACKAGE_PAYLOAD_SHA256 = (
    "46C85DAAA7102D754529C038639012CA"
    "0118C0849ADD3B73902375744932C7B4"
)
PACKAGE_PAYLOAD_HASH_SCOPE = (
    "canonical packaged runtime files except build_identity.py; "
    "sorted relative UTF-8 path, NUL, file bytes, NUL"
)


def semantic_version():
    return ADDON_VERSION_STRING


def compact_build_label():
    return f"v{ADDON_VERSION_STRING}  P1"
