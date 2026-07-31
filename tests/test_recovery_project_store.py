import ast
from copy import deepcopy
import importlib.util
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT
    / "project_store.py"
)
OPERATORS_PATH = (
    ROOT
    / "operators.py"
)
UI_PATH = ROOT / "ui.py"
SPEC = importlib.util.spec_from_file_location(
    "flowpatch_project_store_under_test",
    MODULE_PATH,
)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

OBJECT_UUID_KEY = MODULE.OBJECT_UUID_KEY
PROJECT_RECORD_KEY = MODULE.PROJECT_RECORD_KEY
PROJECT_UUID_KEY = MODULE.PROJECT_UUID_KEY
ProjectRecord = MODULE.ProjectRecord
ProjectStoreError = MODULE.ProjectStoreError
audit_projects = MODULE.audit_projects
audit_snapshot = MODULE.audit_snapshot
bind_project = MODULE.bind_project
clear_project_identity = MODULE.clear_project_identity
encode_project_record = MODULE.encode_project_record
find_object_by_uuid = MODULE.find_object_by_uuid
parse_project_record = MODULE.parse_project_record
project_objects_for_target = MODULE.project_objects_for_target
repair_passive_target_uuid_copies = (
    MODULE.repair_passive_target_uuid_copies
)
read_project_record = MODULE.read_project_record
write_project_record = MODULE.write_project_record


UUIDS = (
    "00000000-0000-4000-8000-000000000001",
    "00000000-0000-4000-8000-000000000002",
    "00000000-0000-4000-8000-000000000003",
    "00000000-0000-4000-8000-000000000004",
    "00000000-0000-4000-8000-000000000005",
    "00000000-0000-4000-8000-000000000006",
)


def owner(name):
    return {"name": name}


class DeleteFailOwner(dict):
    def __delitem__(self, key):
        if key == OBJECT_UUID_KEY:
            raise RuntimeError("forced delete failure")
        super().__delitem__(key)


class ProjectStoreTests(unittest.TestCase):
    def test_happy_path_binds_and_resumes_same_uuid(self):
        target = owner("Target")
        retopo = owner("Retopo")
        values = iter(UUIDS)

        record = bind_project(target, retopo, uuid_factory=values.__next__)
        resumed = bind_project(
            target,
            retopo,
            uuid_factory=lambda: self.fail("resume generated a new UUID"),
        )

        self.assertEqual(record, resumed)
        self.assertEqual(record.project_uuid, UUIDS[2])
        self.assertEqual(record.target_object_uuid, UUIDS[0])
        self.assertEqual(record.retopo_object_uuid, UUIDS[1])
        self.assertEqual(retopo[PROJECT_UUID_KEY], UUIDS[2])
        self.assertEqual(read_project_record(retopo), record)

    def test_record_round_trip_is_canonical_and_json_safe(self):
        record = ProjectRecord(
            project_uuid=UUIDS[2],
            schema_version=1,
            target_object_uuid=UUIDS[0],
            retopo_object_uuid=UUIDS[1],
        )

        encoded = encode_project_record(record)
        decoded = parse_project_record(encoded)

        self.assertEqual(decoded, record)
        self.assertEqual(json.loads(encoded), record.as_dict())
        self.assertEqual(encoded, encode_project_record(decoded))

    def test_conflicting_rebind_is_blocked_without_overwrite(self):
        target = owner("Target")
        retopo = owner("Retopo")
        values = iter(UUIDS)
        bind_project(target, retopo, uuid_factory=values.__next__)
        before_retopo = deepcopy(retopo)
        wrong_target = {
            "name": "WrongTarget",
            OBJECT_UUID_KEY: UUIDS[4],
        }
        before_target = deepcopy(wrong_target)

        with self.assertRaises(ProjectStoreError) as caught:
            bind_project(wrong_target, retopo)

        self.assertEqual(caught.exception.reason_code, "PROJECT_BINDING_CONFLICT")
        self.assertEqual(retopo, before_retopo)
        self.assertEqual(wrong_target, before_target)

    def test_new_bind_generation_failure_rolls_back_all_metadata(self):
        target = owner("Target")
        retopo = owner("Retopo")
        before = deepcopy((target, retopo))
        values = iter((UUIDS[0], UUIDS[1], "not-a-uuid"))

        with self.assertRaises(ProjectStoreError) as caught:
            bind_project(target, retopo, uuid_factory=values.__next__)

        self.assertEqual(
            caught.exception.reason_code,
            "MALFORMED_GENERATED_UUID",
        )
        self.assertEqual((target, retopo), before)

    def test_malformed_and_unknown_schema_fail_closed(self):
        malformed = owner("Malformed")
        malformed[PROJECT_RECORD_KEY] = "{bad json"
        with self.assertRaises(ProjectStoreError) as malformed_error:
            read_project_record(malformed)
        self.assertEqual(
            malformed_error.exception.reason_code,
            "MALFORMED_PROJECT_RECORD",
        )

        unsupported = {
            "project_uuid": UUIDS[2],
            "schema_version": 99,
            "target_object_uuid": UUIDS[0],
            "retopo_object_uuid": UUIDS[1],
        }
        with self.assertRaises(ProjectStoreError) as schema_error:
            parse_project_record(unsupported)
        self.assertEqual(
            schema_error.exception.reason_code,
            "UNSUPPORTED_PROJECT_SCHEMA",
        )

    def test_audit_reports_missing_target_and_duplicate_ownership(self):
        target = {
            "name": "Target",
            OBJECT_UUID_KEY: UUIDS[0],
        }
        left = {
            "name": "Left",
            OBJECT_UUID_KEY: UUIDS[1],
        }
        right = {
            "name": "Right",
            OBJECT_UUID_KEY: UUIDS[1],
        }
        record = ProjectRecord(UUIDS[2], 1, UUIDS[4], UUIDS[1])
        write_project_record(left, record)
        write_project_record(right, record)

        issues = audit_projects((target, left, right))
        reason_codes = {issue.reason_code for issue in issues}

        self.assertIn("DUPLICATE_OBJECT_UUID", reason_codes)
        self.assertIn("DUPLICATE_PROJECT_UUID", reason_codes)
        self.assertIn("MISSING_TARGET_OBJECT", reason_codes)
        snapshot = audit_snapshot(issues, limit=2)
        self.assertEqual(snapshot["issue_count"], len(issues))
        self.assertEqual(len(snapshot["issues"]), 2)
        self.assertTrue(snapshot["truncated"])

    def test_candidate_resolution_is_uuid_based_not_name_based(self):
        target = owner("RenamedTarget")
        retopo = owner("RenamedRetopo")
        values = iter(UUIDS)
        record = bind_project(target, retopo, uuid_factory=values.__next__)

        target["name"] = "TargetAfterRename"
        retopo["name"] = "RetopoAfterRename"

        self.assertIs(
            find_object_by_uuid((target, retopo), record.target_object_uuid),
            target,
        )
        self.assertEqual(
            project_objects_for_target(
                (target, retopo),
                record.target_object_uuid,
            ),
            (retopo,),
        )

    def test_passive_target_uuid_copies_are_repaired_transactionally(self):
        target = owner("Target")
        retopo = owner("Retopo")
        values = iter(UUIDS)
        record = bind_project(target, retopo, uuid_factory=values.__next__)
        clone_b = {
            "name": "CloneB",
            OBJECT_UUID_KEY: record.target_object_uuid,
        }
        clone_a = {
            "name": "CloneA",
            OBJECT_UUID_KEY: record.target_object_uuid,
        }
        project_before = deepcopy(retopo)

        repaired = repair_passive_target_uuid_copies(
            (retopo, clone_b, target, clone_a),
            target,
            retopo,
        )

        self.assertEqual(repaired, ("CloneA", "CloneB"))
        self.assertNotIn(OBJECT_UUID_KEY, clone_a)
        self.assertNotIn(OBJECT_UUID_KEY, clone_b)
        self.assertEqual(target[OBJECT_UUID_KEY], record.target_object_uuid)
        self.assertEqual(retopo, project_before)
        self.assertEqual(
            audit_projects((target, retopo, clone_a, clone_b)),
            (),
        )

    def test_project_bearing_uuid_copy_fails_without_mutation(self):
        target = owner("Target")
        retopo = owner("Retopo")
        values = iter(UUIDS)
        record = bind_project(target, retopo, uuid_factory=values.__next__)
        protected = {
            "name": "ProtectedClone",
            OBJECT_UUID_KEY: record.target_object_uuid,
            PROJECT_UUID_KEY: UUIDS[5],
        }
        before = deepcopy((target, retopo, protected))

        with self.assertRaises(ProjectStoreError) as caught:
            repair_passive_target_uuid_copies(
                (target, retopo, protected),
                target,
                retopo,
            )

        self.assertEqual(
            caught.exception.reason_code,
            "DUPLICATE_OBJECT_UUID_HAS_PROJECT",
        )
        self.assertEqual((target, retopo, protected), before)

    def test_uuid_copy_delete_failure_rolls_back_earlier_copy(self):
        target = owner("Target")
        retopo = owner("Retopo")
        values = iter(UUIDS)
        record = bind_project(target, retopo, uuid_factory=values.__next__)
        first = {
            "name": "CloneA",
            OBJECT_UUID_KEY: record.target_object_uuid,
        }
        failing = DeleteFailOwner(
            {
                "name": "CloneB",
                OBJECT_UUID_KEY: record.target_object_uuid,
            }
        )
        before = deepcopy((target, retopo, first, dict(failing)))

        with self.assertRaises(ProjectStoreError) as caught:
            repair_passive_target_uuid_copies(
                (target, retopo, first, failing),
                target,
                retopo,
            )

        self.assertEqual(
            caught.exception.reason_code,
            "DUPLICATE_OBJECT_UUID_REPAIR_FAILED",
        )
        self.assertEqual(
            (target, retopo, first, dict(failing)),
            before,
        )

    def test_clear_project_identity_preserves_unrelated_guide_payload(self):
        target = owner("Target")
        retopo = owner("Retopo")
        retopo["flowpatch_guide_network_v1"] = "[guide-data]"
        values = iter(UUIDS)
        bind_project(target, retopo, uuid_factory=values.__next__)

        removed = clear_project_identity(retopo)

        self.assertEqual(
            set(removed),
            {PROJECT_UUID_KEY, PROJECT_RECORD_KEY, OBJECT_UUID_KEY},
        )
        self.assertEqual(
            retopo["flowpatch_guide_network_v1"],
            "[guide-data]",
        )

    def test_repeat_audit_is_deterministic_and_non_mutating(self):
        target = owner("Target")
        retopo = owner("Retopo")
        values = iter(UUIDS)
        bind_project(target, retopo, uuid_factory=values.__next__)
        before = deepcopy((target, retopo))

        snapshots = [
            audit_snapshot(audit_projects((target, retopo)))
            for _index in range(128)
        ]

        self.assertTrue(all(item == snapshots[0] for item in snapshots))
        self.assertEqual(snapshots[0]["issue_count"], 0)
        self.assertEqual((target, retopo), before)


class ProjectIntegrationStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = OPERATORS_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_open_guides_are_serialized_and_project_resume_is_uuid_based(self):
        self.assertIn("save_guides(self._retopo, self._guides or [])", self.source)
        self.assertIn("project_objects_for_target", self.source)
        self.assertIn("find_object_by_uuid", self.source)
        self.assertIn("_legacy_project_candidates", self.source)
        self.assertIn("PROJECT_OVERWRITE_BLOCKED", MODULE_PATH.read_text("utf-8"))
        self.assertIn("repair_passive_target_uuid_copies", self.source)
        self.assertIn("self.report({\"ERROR\"}, str(exc))", self.source)

    def test_register_load_start_audit_and_exact_handler_cleanup_exist(self):
        self.assertIn("@persistent\ndef _project_load_post", self.source)
        self.assertIn('_run_project_audit("REGISTER")', self.source)
        self.assertIn('_run_project_audit("SESSION_START")', self.source)
        self.assertIn(
            "bpy.app.handlers.load_post.append(_project_load_post)",
            self.source,
        )
        self.assertIn(
            "bpy.app.handlers.load_post.remove(_project_load_post)",
            self.source,
        )

    def test_delete_keeps_mesh_and_removes_only_flowpatch_ownership(self):
        delete_class = next(
            node
            for node in self.tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "FLOWPATCH_OT_delete_project"
        )
        source = ast.unparse(delete_class)

        self.assertIn("_purge_project_object", source)
        self.assertIn("invoke_confirm", source)
        self.assertNotIn("bpy.data.objects.remove", source)
        self.assertNotIn("bpy.ops.object.delete", source)
        self.assertIn("ordinary mesh kept", source)

    def test_ui_exposes_resume_recover_and_explicit_delete(self):
        source = UI_PATH.read_text(encoding="utf-8")

        self.assertIn("Resume FlowPatch Project", source)
        self.assertIn("flowpatch.recover_project", source)
        self.assertIn("flowpatch.delete_project", source)
        self.assertIn("delete.alert = True", source)


if __name__ == "__main__":
    unittest.main()
