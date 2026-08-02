import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "recovery_cr00_binding_blender.py"


class CR00BlenderFixtureStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = FIXTURE.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_fixture_has_separate_create_and_verify_process_modes(self):
        self.assertIn('choices=("create", "verify")', self.source)
        self.assertIn("FLOWPATCH_CR00_BINDING_CREATE_PASS", self.source)
        self.assertIn("FLOWPATCH_CR00_BINDING_VERIFY_PASS", self.source)

    def test_fixture_uses_real_bmesh_layers_and_read_only_audit(self):
        self.assertIn("binding.ensure_binding_layers(bm)", self.source)
        self.assertIn("binding.allocate_element_uid(", self.source)
        self.assertIn("binding.register_boundary(", self.source)
        self.assertIn("binding.audit_binding_registry(registry, bm=bm)", self.source)

    def test_fixture_saves_only_named_disposable_roundtrip_file(self):
        self.assertIn('"cr00_binding_roundtrip.blend"', self.source)
        self.assertIn("bpy.ops.wm.save_as_mainfile", self.source)
        self.assertNotIn("save_userpref", self.source)


if __name__ == "__main__":
    unittest.main()
