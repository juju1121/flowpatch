import importlib
import importlib.util
import sys
import types
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "flowpatch_cr00_test_package"


def load_package_module(name):
    package = sys.modules.get(PACKAGE_NAME)
    if package is None:
        package = types.ModuleType(PACKAGE_NAME)
        package.__path__ = [str(ROOT)]
        sys.modules[PACKAGE_NAME] = package
    return importlib.import_module(f"{PACKAGE_NAME}.{name}")


def load_standalone(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


binding = load_package_module("binding_registry")
diagnostics = load_standalone(
    "flowpatch_cr00_diagnostics",
    ROOT / "diagnostics.py",
)


class UUIDFactory:
    def __init__(self, start=100):
        self.value = int(start)

    def __call__(self):
        self.value += 1
        return str(uuid.UUID(int=self.value))


class FakeID(dict):
    pass


class BindingFoundationTests(unittest.TestCase):
    def test_facade_is_small_and_modules_are_split(self):
        expected = {
            "binding_model.py",
            "binding_storage.py",
            "binding_layers.py",
            "binding_writer.py",
            "binding_migration.py",
            "binding_audit.py",
            "binding_registry.py",
        }
        self.assertTrue(all((ROOT / name).is_file() for name in expected))
        facade_lines = (ROOT / "binding_registry.py").read_text(
            encoding="utf-8"
        ).splitlines()
        self.assertLess(len(facade_lines), 500)
        self.assertEqual(
            binding.BINDING_FOUNDATION_STATE,
            "FOUNDATION_READY_NOT_WIRED",
        )
        self.assertFalse(binding.foundation_status()["guide_to_mesh_sync"])
        self.assertFalse(binding.foundation_status()["mesh_to_guide_sync"])

    def test_round_trip_is_canonical_and_malformed_schema_is_rejected(self):
        project_uuid = str(uuid.UUID(int=1))
        registry = binding.new_binding_registry(project_uuid)
        encoded = binding.encode_binding_registry(registry)
        restored = binding.parse_binding_registry(encoded)
        self.assertEqual(binding.encode_binding_registry(restored), encoded)
        with self.assertRaises(binding.BindingRegistryError):
            binding.parse_binding_registry("[]")
        with self.assertRaises(binding.BindingRegistryError):
            binding.parse_binding_registry(
                {"schema_version": 999, "project_uuid": project_uuid}
            )

    def test_allocator_is_monotonic_and_never_reuses_retired_ids(self):
        allocator = binding.ElementUIDAllocator()
        self.assertEqual(allocator.allocate("VERTEX"), 1)
        self.assertEqual(allocator.allocate("VERTEX"), 2)
        allocator.raise_above("VERTEX", 99)
        self.assertEqual(allocator.allocate("VERTEX"), 100)

    def _built_registry(self):
        registry = binding.new_binding_registry(str(uuid.UUID(int=2)))
        guides = (
            SimpleNamespace(guide_id=1, start_node=1, end_node=2),
        )
        binding.reconcile_guide_identities(
            registry,
            guides,
            uuid_factory=UUIDFactory(),
        )
        node_a = registry.guide_node_uuid_by_graph_id["1"]
        node_b = registry.guide_node_uuid_by_graph_id["2"]
        edge = registry.guide_edge_uuid_by_graph_id["1"]
        region = binding.upsert_grid_region(
            registry,
            "cycle:grid:1",
            vertex_uids=(1, 2, 3, 4),
            edge_uids=(1, 2, 3, 4),
            face_uids=(1,),
            guide_node_to_vertex_uid={node_a: 1, node_b: 2},
            guide_edge_to_ordered_vertex_uids={edge: (1, 2)},
            uuid_factory=UUIDFactory(200),
        )
        binding.register_boundary(
            registry,
            guide_edge_uuids=(edge,),
            endpoint_node_uuids=(node_a, node_b),
            vertex_uids=(1, 2),
            edge_uids=(1,),
            region_uuids=(region.region_uuid,),
        )
        return registry, region

    def test_explicit_writer_builds_audit_zero_registry(self):
        registry, region = self._built_registry()
        before = binding.encode_binding_registry(registry)
        audit = binding.audit_binding_registry(registry)
        after = binding.encode_binding_registry(registry)
        self.assertEqual(audit["status"], "PASS")
        self.assertEqual(audit["issue_count"], 0)
        self.assertEqual(before, after)
        self.assertEqual(region.solver_kind, "GRID")

    def test_audit_rejects_non_bijective_guide_identity(self):
        registry, _region = self._built_registry()
        registry.guide_node_uuid_by_graph_id["3"] = (
            registry.guide_node_uuid_by_graph_id["1"]
        )
        audit = binding.audit_binding_registry(registry)
        codes = {item["reason_code"] for item in audit["issues"]}
        self.assertIn("BINDING_IDENTITY_NOT_BIJECTIVE", codes)

    def test_copy_rekey_isolated_and_rekeys_built_cell_metadata(self):
        registry, region = self._built_registry()
        original = binding.encode_binding_registry(registry)
        new_project_uuid = str(uuid.UUID(int=3))
        bundle = binding.rekey_project_bundle(
            registry,
            new_project_uuid,
            built_cells=(
                {
                    "project_uuid": registry.project_uuid,
                    "region_uuid": region.region_uuid,
                },
            ),
            uuid_factory=UUIDFactory(300),
        )
        self.assertEqual(binding.encode_binding_registry(registry), original)
        self.assertEqual(bundle.registry.project_uuid, new_project_uuid)
        self.assertEqual(
            bundle.built_cells[0]["project_uuid"],
            new_project_uuid,
        )
        self.assertNotEqual(
            bundle.built_cells[0]["region_uuid"],
            region.region_uuid,
        )
        self.assertEqual(
            binding.audit_binding_registry(bundle.registry)["issue_count"],
            0,
        )
        with self.assertRaises(binding.BindingRegistryError):
            binding.rekey_project_bundle(
                registry,
                str(uuid.UUID(int=4)),
                mesh_users=2,
            )

    def test_storage_snapshot_restores_exact_property(self):
        owner = FakeID()
        registry = binding.initialize_binding_registry(
            owner,
            str(uuid.UUID(int=5)),
        )
        snapshot = binding.snapshot_binding_property(owner)
        registry.revision += 1
        binding.save_binding_registry(owner, registry)
        binding.restore_binding_property(owner, snapshot)
        self.assertEqual(binding.snapshot_binding_property(owner), snapshot)


class HonestToolStateTests(unittest.TestCase):
    def test_fake_primary_tools_are_not_registered(self):
        ids = set(diagnostics.TOOL_REGISTRY.ids)
        self.assertNotIn("DISSOLVE", ids)
        self.assertNotIn("TRIM", ids)
        self.assertNotIn("LOOP_CUT", ids)
        self.assertEqual(
            diagnostics.TOOL_REGISTRY.get("BUILD").predicate,
            "has_active_valid_grid_cell",
        )

    def test_disabled_toolbar_hit_is_consumed_by_modal_owner(self):
        source = (ROOT / "drawing.py").read_text(encoding="utf-8")
        method = source[
            source.index("    def toolbar_hit_test") : source.index(
                "    def toolbar_hover_test"
            )
        ]
        self.assertIn("return identifier", method)
        self.assertNotIn("_toolbar_enabled", method)

    def test_primary_toolbar_has_no_fake_entries_or_handlers(self):
        source = (ROOT / "operators.py").read_text(encoding="utf-8")
        update = source[
            source.index("    def _update_renderer") : source.index(
                "    def _rebuild_previews"
            )
        ]
        handler = source[
            source.index("    def _handle_toolbar") : source.index(
                "    def _cleanup", source.index("    def _handle_toolbar")
            )
        ]
        for identifier in ("DISSOLVE", "TRIM", "LOOP_CUT"):
            self.assertNotIn(f'"{identifier}"', update)
            self.assertNotIn(f'action == "{identifier}"', handler)
        self.assertIn("ACTIVE_VALID_GRID_REQUIRED", source)
        self.assertIn("CR-00 supports only deterministic", source)


if __name__ == "__main__":
    unittest.main()
