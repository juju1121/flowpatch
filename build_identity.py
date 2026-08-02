ADDON_VERSION = (1, 4, 21)
ADDON_VERSION_STRING = ".".join(str(value) for value in ADDON_VERSION)
BUILD_ID = "core-reset-cr00-20260802"
SOURCE_BRANCH = "recovery/core-reset-from-v1.4.19"

# This digest covers the canonical runtime payload, excluding this metadata
# file and the human README. The exact ZIP archive digest is delivered beside
# the package as a detached checksum.
PACKAGE_PAYLOAD_SHA256 = (
    "2C76A4A498E85F0B60AA2C412011F207"
    "523141FC95CDCD257A8B6DF4788F715B"
)
PACKAGE_PAYLOAD_HASH_SCOPE = (
    "canonical packaged runtime files except build_identity.py and README.md; "
    "sorted relative UTF-8 path, NUL, file bytes, NUL"
)


def semantic_version():
    return ADDON_VERSION_STRING


def compact_build_label():
    return f"v{ADDON_VERSION_STRING}  CR-00"
