import ast
import hashlib
import importlib.util
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIAGNOSTICS_PATH = ROOT / "diagnostics.py"
BUILD_IDENTITY_PATH = ROOT / "build_identity.py"
OPERATORS_PATH = ROOT / "operators.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


diagnostics = load_module("flowpatch_p0_diagnostics", DIAGNOSTICS_PATH)
build_identity = load_module(
    "flowpatch_p0_build_identity",
    BUILD_IDENTITY_PATH,
)


class FakeObject:
    def __init__(self, name, object_uuid):
        self.name = name
        self.type = "MESH"
        self._properties = {
            "flowpatch_object_uuid_v1": object_uuid,
        }

    def get(self, key, default=None):
        return self._properties.get(key, default)


class StaleObject:
    @property
    def name(self):
        raise ReferenceError("StructRNA of type Object has been removed")

    @property
    def type(self):
        raise ReferenceError("StructRNA of type Object has been removed")

    def get(self, _key, _default=None):
        raise ReferenceError("StructRNA of type Object has been removed")


class DiagnosticsPureTests(unittest.TestCase):
    def test_stale_rna_reads_return_safe_defaults(self):
        stale = StaleObject()
        self.assertEqual(diagnostics.safe_rna_name(stale), "")
        self.assertIsNone(diagnostics.safe_rna_object(stale))
        self.assertEqual(
            diagnostics.safe_rna_get(stale, "missing", "fallback"),
            "fallback",
        )
        self.assertEqual(diagnostics._json_safe(stale), "<StaleObject>")

    def test_uuid_resolver_prefers_view_layer_and_reports_rename(self):
        owner = FakeObject("RetopoRenamed", "retopo-uuid")
        resolved, status = diagnostics.resolve_rna_object(
            "retopo-uuid",
            "RetopoOriginal",
            view_layer_objects=(owner,),
            data_objects=(owner,),
        )
        self.assertIs(resolved, owner)
        self.assertTrue(status["found"])
        self.assertTrue(status["in_view_layer"])
        self.assertTrue(status["renamed"])
        self.assertEqual(status["source"], "VIEW_LAYER")

    def test_uuid_resolver_reports_unlinked_data_object(self):
        owner = FakeObject("Retopo", "retopo-uuid")
        resolved, status = diagnostics.resolve_rna_object(
            "retopo-uuid",
            "Retopo",
            view_layer_objects=(),
            data_objects=(owner,),
        )
        self.assertIs(resolved, owner)
        self.assertTrue(status["found"])
        self.assertFalse(status["in_view_layer"])
        self.assertEqual(status["source"], "BLEND_DATA")

    def test_uuid_resolver_skips_stale_and_rejects_ambiguity(self):
        first = FakeObject("First", "shared-uuid")
        second = FakeObject("Second", "shared-uuid")
        resolved, status = diagnostics.resolve_rna_object(
            "shared-uuid",
            view_layer_objects=(StaleObject(), first),
            data_objects=(first, second),
        )
        self.assertIsNone(resolved)
        self.assertFalse(status["found"])
        self.assertTrue(status["ambiguous"])
        self.assertEqual(status["match_count"], 2)

    def test_partial_debug_document_is_json_serializable(self):
        document = diagnostics.build_debug_document(
            build_identity={"build_id": "p0"},
            project_state={
                "project_object_found": False,
                "target_object_found": True,
            },
            warnings=("project object missing",),
            last_exception=None,
        )
        encoded = diagnostics.debug_document_json(document)
        decoded = json.loads(encoded)
        self.assertEqual(decoded["schema_version"], 2)
        self.assertFalse(decoded["project_object_found"])
        self.assertTrue(decoded["target_object_found"])
        self.assertEqual(decoded["warnings"], ["project object missing"])

    def test_build_identity_is_complete_and_versioned(self):
        self.assertEqual(build_identity.ADDON_VERSION, (1, 4, 20))
        self.assertEqual(
            build_identity.BUILD_ID,
            "recovery-pf02-binding-registry-20260801",
        )
        self.assertRegex(
            build_identity.PACKAGE_PAYLOAD_SHA256,
            re.compile(r"^[0-9A-F]{64}$"),
        )
        init_source = (ROOT / "__init__.py").read_text(encoding="utf-8")
        manifest = (ROOT / "blender_manifest.toml").read_text(
            encoding="utf-8"
        )
        self.assertIn('"version": (1, 4, 20)', init_source)
        self.assertIn('version = "1.4.20"', manifest)

    def test_payload_hash_matches_canonical_runtime_files(self):
        runtime_files = sorted(
            (
                path
                for path in ROOT.iterdir()
                if path.is_file()
                and path.name not in {"build_identity.py", "README.md"}
            ),
            key=lambda path: path.name,
        )
        runtime_files.extend(
            sorted(
                (path for path in (ROOT / "icons").iterdir() if path.is_file()),
                key=lambda path: path.name,
            )
        )
        runtime_files.sort(key=lambda path: path.relative_to(ROOT).as_posix())
        digest = hashlib.sha256()
        for path in runtime_files:
            relative = path.relative_to(ROOT).as_posix().encode("utf-8")
            digest.update(relative)
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        self.assertEqual(
            digest.hexdigest().upper(),
            build_identity.PACKAGE_PAYLOAD_SHA256,
        )


class DiagnosticsStaticIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = OPERATORS_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def _class_method_source(self, class_name, method_name):
        class_node = next(
            node
            for node in self.tree.body
            if isinstance(node, ast.ClassDef) and node.name == class_name
        )
        method = next(
            node
            for node in class_node.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == method_name
        )
        return ast.get_source_segment(self.source, method)

    def test_copy_debug_poll_is_lightweight_and_guarded(self):
        source = self._class_method_source(
            "FLOWPATCH_OT_copy_debug_state",
            "poll",
        )
        self.assertIn("safe_rna_attr", source)
        self.assertNotIn("_context_project_object", source)
        self.assertNotIn("_retopo", source)

    def test_snapshot_uses_id_resolution_and_build_identity(self):
        self.assertIn("_project_diagnostic_state(context, session)", self.source)
        self.assertIn("resolve_rna_object(", self.source)
        self.assertIn("build_identity=_build_identity_state()", self.source)
        self.assertIn("project_state=project_state", self.source)
        self.assertIn("last_exception=_last_exception_snapshot()", self.source)


if __name__ == "__main__":
    unittest.main()
