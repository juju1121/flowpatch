import importlib.util
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "binding_registry.py"
OPERATORS_PATH = ROOT / "operators.py"
SPEC = importlib.util.spec_from_file_location(
    "flowpatch_binding_registry_under_test",
    MODULE_PATH,
)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


UUIDS = tuple(
    f"00000000-0000-4000-8000-{index:012d}"
    for index in range(1, 40)
)


class _MissingLayerAccess:
    def get(self, _name):
        return None


class _MissingDomainLayers:
    def __init__(self):
        self.int = _MissingLayerAccess()
        self.float = _MissingLayerAccess()


class _MissingElements:
    def __init__(self):
        self.layers = _MissingDomainLayers()


class _MissingLayerBMesh:
    def __init__(self):
        self.verts = _MissingElements()
        self.edges = _MissingElements()
        self.faces = _MissingElements()


def guide(guide_id=7, start_node=11, end_node=12):
    return SimpleNamespace(
        guide_id=guide_id,
        start_node=start_node,
        end_node=end_node,
    )


class BindingRegistryPureTests(unittest.TestCase):
    def test_persistent_mesh_layer_names_are_globally_unique(self):
        names = (
            MODULE.FP_VERTEX_UID,
            MODULE.FP_VERTEX_REGION,
            MODULE.FP_VERTEX_ROLE,
            MODULE.FP_VERTEX_GENERATION,
            MODULE.FP_GUIDE_NODE_LOCAL_ID,
            MODULE.FP_ANCHOR_LOCAL_ID,
            MODULE.FP_PARAM_U,
            MODULE.FP_PARAM_V,
            MODULE.FP_EDGE_UID,
            MODULE.FP_EDGE_REGION,
            MODULE.FP_EDGE_BOUNDARY_KEY,
            MODULE.FP_EDGE_GENERATION,
            MODULE.FP_GUIDE_EDGE_LOCAL_ID,
            MODULE.FP_EDGE_ROLE,
            MODULE.FP_FACE_UID,
            MODULE.FP_FACE_REGION,
            MODULE.FP_CELL_LOCAL_ID,
            MODULE.FP_SOLVER_KIND,
            MODULE.FP_FACE_GENERATION,
        )

        self.assertEqual(len(names), len(set(names)))

    def test_allocator_and_serialization_preserve_monotonic_next_values(self):
        registry = MODULE.BindingRegistry.empty(UUIDS[0])

        self.assertEqual(registry.allocator.allocate("VERTEX"), 1)
        self.assertEqual(registry.allocator.allocate("VERTEX"), 2)
        registry.allocator.raise_above("VERTEX", 17)
        self.assertEqual(registry.allocator.allocate("VERTEX"), 18)
        self.assertEqual(registry.allocator.allocate("EDGE"), 1)

        encoded = MODULE.encode_binding_registry(registry)
        restored = MODULE.parse_binding_registry(encoded)

        self.assertEqual(json.loads(encoded), restored.as_dict())
        self.assertEqual(restored.allocator.allocate("VERTEX"), 19)
        self.assertEqual(restored.allocator.allocate("EDGE"), 2)

    def test_boundary_identity_is_orientation_independent(self):
        forward_key, forward = MODULE.normalized_boundary_key(
            UUIDS[0],
            (UUIDS[1], UUIDS[2]),
            (UUIDS[3], UUIDS[4]),
            6,
        )
        reverse_key, reverse = MODULE.normalized_boundary_key(
            UUIDS[0],
            (UUIDS[2], UUIDS[1]),
            (UUIDS[4], UUIDS[3]),
            6,
        )

        self.assertEqual(forward_key, reverse_key)
        self.assertNotEqual(forward, reverse)

    def test_deleted_guide_identity_is_retired_and_never_reused(self):
        registry = MODULE.BindingRegistry.empty(UUIDS[0])
        generated = iter(UUIDS[1:])
        paths = (guide(),)

        MODULE.reconcile_guide_identities(
            registry,
            paths,
            generated.__next__,
        )
        old_node_uuid = registry.guide_node_uuid_by_graph_id["11"]
        old_edge_uuid = registry.guide_edge_uuid_by_graph_id["7"]
        old_node_local_id = registry.guide_node_local_ids[old_node_uuid]
        old_edge_local_id = registry.guide_edge_local_ids[old_edge_uuid]
        old_anchor_uuid = registry.anchor_uuid_by_key[f"node:{old_node_uuid}"]

        MODULE.reconcile_guide_identities(registry, (), generated.__next__)
        MODULE.reconcile_guide_identities(
            registry,
            paths,
            generated.__next__,
        )

        self.assertIn(old_node_uuid, registry.retired_guide_node_uuids)
        self.assertIn(old_edge_uuid, registry.retired_guide_edge_uuids)
        self.assertIn(old_anchor_uuid, registry.retired_anchor_uuids)
        self.assertNotEqual(
            registry.guide_node_uuid_by_graph_id["11"],
            old_node_uuid,
        )
        self.assertNotEqual(
            registry.guide_edge_uuid_by_graph_id["7"],
            old_edge_uuid,
        )
        self.assertGreater(
            registry.guide_node_local_ids[
                registry.guide_node_uuid_by_graph_id["11"]
            ],
            old_node_local_id,
        )
        self.assertGreater(
            registry.guide_edge_local_ids[
                registry.guide_edge_uuid_by_graph_id["7"]
            ],
            old_edge_local_id,
        )

    def test_copy_rekey_preserves_element_uids_but_changes_identity_domain(self):
        registry = MODULE.BindingRegistry.empty(UUIDS[0])
        node_a, node_b = UUIDS[1:3]
        edge_uuid = UUIDS[3]
        anchor_a, anchor_b = UUIDS[4:6]
        region_uuid = UUIDS[6]
        boundary_key, _forward = MODULE.normalized_boundary_key(
            UUIDS[0],
            (edge_uuid,),
            (node_a, node_b),
            1,
        )
        registry.guide_node_local_ids = {node_a: 11, node_b: 12}
        registry.guide_edge_local_ids = {edge_uuid: 7}
        registry.anchor_local_ids = {anchor_a: 1, anchor_b: 2}
        registry.guide_node_uuid_by_graph_id = {
            "11": node_a,
            "12": node_b,
        }
        registry.guide_edge_uuid_by_graph_id = {"7": edge_uuid}
        registry.anchor_uuid_by_key = {
            f"node:{node_a}": anchor_a,
            f"node:{node_b}": anchor_b,
        }
        registry.region_uuid_by_cycle_key = {"cycle-a": region_uuid}
        registry.boundary_local_ids = {boundary_key: 1}
        registry.boundary_bindings = {
            boundary_key: {
                "local_id": 1,
                "guide_edge_uuids": [edge_uuid],
                "endpoint_node_uuids": [node_a, node_b],
                "segment_count": 1,
                "vertex_uids": [1, 2],
                "edge_uids": [1],
                "region_uuids": [region_uuid],
            }
        }
        registry.regions[region_uuid] = MODULE.RegionBinding(
            project_uuid=UUIDS[0],
            region_uuid=region_uuid,
            region_local_id=1,
            solver_kind="GRID",
            generation=1,
            cycle_key="cycle-a",
            guide_node_to_vertex_uid={node_a: 1, node_b: 2},
            guide_edge_to_ordered_vertex_uids={edge_uuid: (1, 2)},
            vertex_uid_to_anchor_id={1: anchor_a, 2: anchor_b},
            vertex_uids={1, 2},
            edge_uids={1},
            face_uids={1},
            outer_boundary_keys=(boundary_key,),
        )

        generated = iter(UUIDS[8:])
        copied = MODULE.rekey_binding_registry(
            registry,
            UUIDS[7],
            generated.__next__,
        )

        self.assertEqual(registry.project_uuid, UUIDS[0])
        self.assertEqual(copied.project_uuid, UUIDS[7])
        self.assertEqual(len(copied.regions), 1)
        copied_region = next(iter(copied.regions.values()))
        self.assertEqual(copied_region.project_uuid, UUIDS[7])
        self.assertEqual(copied_region.vertex_uids, {1, 2})
        self.assertEqual(copied_region.edge_uids, {1})
        self.assertEqual(copied_region.face_uids, {1})
        self.assertNotEqual(set(copied.regions), set(registry.regions))
        self.assertNotEqual(
            set(copied.guide_node_local_ids),
            set(registry.guide_node_local_ids),
        )

    def test_read_only_audit_reports_missing_custom_data_layers(self):
        registry = MODULE.BindingRegistry.empty(UUIDS[0])

        result = MODULE.audit_binding_registry(
            {},
            _MissingLayerBMesh(),
            registry=registry,
        )

        self.assertEqual(result["issue_count"], 1)
        self.assertEqual(
            result["issues"][0]["reason_code"],
            "BINDING_ELEMENT_MISSING",
        )
        self.assertIn("fp_vertex_uid", result["issues"][0]["message"])


class BindingRegistryStaticIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.operators = OPERATORS_PATH.read_text(encoding="utf-8")

    def test_session_start_and_build_paths_are_binding_transactional(self):
        owner_keys_start = self.operators.index("_SESSION_START_OWNER_KEYS = (")
        owner_keys_end = self.operators.index(")", owner_keys_start)
        owner_keys = self.operators[owner_keys_start:owner_keys_end]
        self.assertIn("BINDING_REGISTRY_KEY", owner_keys)

        session_start = self.operators.index("class FLOWPATCH_OT_guide_session")
        invoke_start = self.operators.index(
            "    def invoke(self, context, event):",
            session_start,
        )
        invoke_end = self.operators.index("    def _modal_impl(", invoke_start)
        invoke = self.operators[invoke_start:invoke_end]
        self.assertIn("binding_backup = _begin_auto_build_mesh_snapshot", invoke)
        self.assertIn("_restore_auto_build_mesh_snapshot(", invoke)
        self.assertIn("binding_start_rollback_failed", invoke)

        build_start = self.operators.index("    def _commit_ready_cells(")
        build_end = self.operators.index("    def _finalize_session(", build_start)
        build = self.operators[build_start:build_end]
        self.assertIn("record_committed_regions(", build)
        self.assertIn('"binding_audit": deepcopy(_LAST_BINDING_AUDIT)', build)
        self.assertIn("build_rollback_failed", build)

    def test_undo_snapshot_contains_registry_and_mesh_custom_data(self):
        snapshot_start = self.operators.index("    def _state_snapshot(")
        snapshot_end = self.operators.index(
            "    def _undo_guide_edit(",
            snapshot_start,
        )
        snapshot = self.operators[snapshot_start:snapshot_end]
        self.assertIn('"_binding_registry_property"', snapshot)
        self.assertIn('snapshot["_mesh_backup"]', snapshot)
        self.assertIn("_restore_auto_build_mesh_snapshot(", snapshot)


if __name__ == "__main__":
    unittest.main()
