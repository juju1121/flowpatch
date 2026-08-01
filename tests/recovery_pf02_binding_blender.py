import argparse
import importlib.util
import json
from pathlib import Path
from types import MethodType
from types import SimpleNamespace
import sys

import bmesh
import bpy
from mathutils import Vector


def parse_args():
    argv = sys.argv
    argv = argv[argv.index("--") + 1 :] if "--" in argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--addon-root", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--phase", choices=("create", "verify"), required=True)
    return parser.parse_args(argv)


def import_addon(package_dir):
    package_dir = Path(package_dir)
    spec = importlib.util.spec_from_file_location(
        "flowpatch_retopo",
        package_dir / "__init__.py",
        submodule_search_locations=[str(package_dir)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def clear_scene():
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in tuple(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def make_mesh_object(name, vertices, faces):
    mesh = bpy.data.meshes.new(f"{name}Mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def activate(obj, edit=False):
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for selected in tuple(bpy.context.selected_objects):
        selected.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    if edit:
        bpy.ops.object.mode_set(mode="EDIT")


def guide(guide_id, start_node, end_node):
    return GuidePath(
        guide_id=guide_id,
        points_local=[
            Vector((float(start_node), 0.0, 0.0)),
            Vector((float(end_node), 0.0, 0.0)),
        ],
        start_node=start_node,
        end_node=end_node,
        logical_side_id=guide_id,
    )


def cell_record(grid, corner_nodes, side_ids):
    return {
        "topology_kind": "GRID",
        "grid_vertex_uids": [list(row) for row in grid],
        "vertex_uids": sorted({value for row in grid for value in row}),
        "corner_node_ids": list(corner_nodes),
        "side_edge_ids": [[value] for value in side_ids],
        "side_keys": [str(value) for value in side_ids],
        "revision": 1,
        "state": "PARAMETRIC",
    }


def bmesh_with_legacy_uids(obj):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    legacy = bm.verts.layers.int.new(binding.LEGACY_VERTEX_UID)
    bm.verts.ensure_lookup_table()
    bm.verts.index_update()
    for vert in bm.verts:
        vert[legacy] = vert.index + 1
    return bm


args = parse_args()
run_dir = Path(args.run_dir)
run_dir.mkdir(parents=True, exist_ok=True)
blend_path = run_dir / "pf02_binding_persistence.blend"
import_addon(args.addon_root)

from flowpatch_retopo import binding_registry as binding
from flowpatch_retopo.guides import load_built_cells
from flowpatch_retopo.guides import remove_built_cell_geometry
from flowpatch_retopo.guides import save_built_cells
from flowpatch_retopo.guide_graph import GuidePath
from flowpatch_retopo.operators import FLOWPATCH_OT_guide_session
from flowpatch_retopo.project_store import bind_project
from flowpatch_retopo.project_store import read_project_record


if args.phase == "create":
    clear_scene()
    target = make_mesh_object(
        "PF02Target",
        ((0, 0, -1), (2, 0, -1), (2, 1, -1), (0, 1, -1)),
        ((0, 1, 2, 3),),
    )
    retopo = make_mesh_object(
        "PF02Retopo",
        (
            (0, 1, 0),
            (1, 1, 0),
            (2, 1, 0),
            (0, 0, 0),
            (1, 0, 0),
            (2, 0, 0),
        ),
        ((0, 1, 4, 3), (1, 2, 5, 4)),
    )
    project = bind_project(target, retopo)
    guides = (
        guide(1, 10, 11),
        guide(2, 11, 14),
        guide(3, 14, 13),
        guide(4, 13, 10),
        guide(5, 11, 12),
        guide(6, 12, 15),
        guide(7, 15, 14),
    )
    records = {
        "left": cell_record(((1, 2), (4, 5)), (10, 11, 14, 13), (1, 2, 3, 4)),
        "right": cell_record(((2, 3), (5, 6)), (11, 12, 15, 14), (5, 6, 7, 2)),
    }
    results = tuple(
        SimpleNamespace(cycle_key=key, cell_record=record)
        for key, record in records.items()
    )

    bm = bmesh_with_legacy_uids(retopo)

    registry, initial_audit = binding.ensure_binding_registry(
        retopo,
        bm,
        project,
        guides,
        {},
    )
    assert initial_audit["issue_count"] == 0, initial_audit
    registry, build_audit = binding.record_committed_regions(
        retopo,
        bm,
        project,
        guides,
        results,
    )
    assert build_audit["issue_count"] == 0, build_audit
    assert build_audit["region_count"] == 2
    assert build_audit["shared_boundary_count"] == 1
    assert len({region.region_uuid for region in registry.regions.values()}) == 2
    assert all(len(face.verts) == 4 for face in bm.faces)
    bm.to_mesh(retopo.data)
    retopo.data.update()
    bm.free()
    save_built_cells(retopo, records)

    activate(retopo, edit=True)
    owner = SimpleNamespace(
        _retopo=retopo,
        _guides=list(guides),
        _u_segments=1,
        _v_segments=1,
        _built_cells=json.loads(json.dumps(records)),
        _density_overrides={},
        _selected_control=None,
        _selected_guides=set(),
        _selected_points=set(),
        _selected_segment=None,
        _active_anchor=None,
        _scene=None,
        _history=[],
        _redo_history=[],
        _sync_scene_settings=lambda: None,
        _rebuild_previews=lambda: None,
    )
    for method_name in (
        "_state_snapshot",
        "_restore_state",
        "_discard_state_snapshot",
        "_discard_history_stack",
    ):
        setattr(
            owner,
            method_name,
            MethodType(getattr(FLOWPATCH_OT_guide_session, method_name), owner),
        )
    full_registry_raw = str(retopo[binding.BINDING_REGISTRY_KEY])
    full_allocator = binding.load_binding_registry(retopo).allocator.as_dict()
    owner._history.append(owner._state_snapshot(include_mesh=True))

    edit_bm = bmesh.from_edit_mesh(retopo.data)
    removal = remove_built_cell_geometry(
        retopo,
        edit_bm,
        owner._built_cells,
        ("right",),
    )
    owner._built_cells = removal.built_cells
    deleted_registry = binding.load_binding_registry(retopo)
    deleted_registry.allocator.allocate("VERTEX")
    binding.save_binding_registry(retopo, deleted_registry)
    deleted_registry_raw = str(retopo[binding.BINDING_REGISTRY_KEY])
    deleted_allocator = deleted_registry.allocator.as_dict()
    deleted_audit = binding.audit_binding_registry(
        retopo,
        bmesh.from_edit_mesh(retopo.data),
        guides=owner._guides,
    )
    assert deleted_audit["issue_count"] == 0, deleted_audit
    assert deleted_audit["region_count"] == 1
    assert deleted_allocator["next_vertex_uid"] > full_allocator["next_vertex_uid"]

    assert FLOWPATCH_OT_guide_session._undo_guide_edit(owner) is True
    undo_registry_raw = str(retopo[binding.BINDING_REGISTRY_KEY])
    undo_registry = binding.load_binding_registry(retopo)
    undo_audit = binding.audit_binding_registry(
        retopo,
        bmesh.from_edit_mesh(retopo.data),
        guides=owner._guides,
    )
    assert undo_registry_raw == full_registry_raw
    assert undo_registry.allocator.as_dict() == full_allocator
    assert undo_audit["issue_count"] == 0, undo_audit
    assert undo_audit["region_count"] == 2
    assert undo_audit["shared_boundary_count"] == 1

    assert FLOWPATCH_OT_guide_session._redo_guide_edit(owner) is True
    redo_registry_raw = str(retopo[binding.BINDING_REGISTRY_KEY])
    redo_registry = binding.load_binding_registry(retopo)
    redo_audit = binding.audit_binding_registry(
        retopo,
        bmesh.from_edit_mesh(retopo.data),
        guides=owner._guides,
    )
    assert redo_registry_raw == deleted_registry_raw
    assert redo_registry.allocator.as_dict() == deleted_allocator
    assert redo_audit["issue_count"] == 0, redo_audit
    assert redo_audit["region_count"] == 1

    assert FLOWPATCH_OT_guide_session._undo_guide_edit(owner) is True
    final_undo_audit = binding.audit_binding_registry(
        retopo,
        bmesh.from_edit_mesh(retopo.data),
        guides=owner._guides,
    )
    assert str(retopo[binding.BINDING_REGISTRY_KEY]) == full_registry_raw
    assert final_undo_audit["issue_count"] == 0, final_undo_audit
    assert final_undo_audit["region_count"] == 2
    owner._discard_history_stack(owner._history)
    owner._discard_history_stack(owner._redo_history)
    bpy.ops.object.mode_set(mode="OBJECT")

    legacy_retopo = make_mesh_object(
        "PF02LegacyRetopo",
        (
            (0, 1, 0),
            (1, 1, 0),
            (2, 1, 0),
            (0, 0, 0),
            (1, 0, 0),
            (2, 0, 0),
        ),
        ((0, 1, 4, 3), (1, 2, 5, 4)),
    )
    legacy_project = bind_project(target, legacy_retopo)
    legacy_records = json.loads(json.dumps(records))
    legacy_bm = bmesh_with_legacy_uids(legacy_retopo)
    legacy_registry, migration_audit = binding.ensure_binding_registry(
        legacy_retopo,
        legacy_bm,
        legacy_project,
        guides,
        legacy_records,
    )
    assert migration_audit["issue_count"] == 0, migration_audit
    assert migration_audit["region_count"] == 2
    assert migration_audit["shared_boundary_count"] == 1
    assert any(
        note.startswith("Reconstructed 2 deterministic legacy Region")
        for note in legacy_registry.migration_notes
    )
    legacy_bm.to_mesh(legacy_retopo.data)
    legacy_retopo.data.update()
    legacy_bm.free()
    save_built_cells(legacy_retopo, legacy_records)

    ambiguous_retopo = make_mesh_object(
        "PF02AmbiguousRetopo",
        ((0, 1, 0), (1, 1, 0), (0, 0, 0), (1, 0, 0)),
        ((0, 1, 3, 2),),
    )
    ambiguous_project = bind_project(target, ambiguous_retopo)
    ambiguous_record = cell_record(
        ((1, 2), (3, 4)),
        (10, 11, 14, 13),
        (1, 999, 3, 4),
    )
    ambiguous_bm = bmesh_with_legacy_uids(ambiguous_retopo)
    ambiguous_registry, ambiguous_audit = binding.ensure_binding_registry(
        ambiguous_retopo,
        ambiguous_bm,
        ambiguous_project,
        guides,
        {"ambiguous": ambiguous_record},
    )
    assert ambiguous_audit["issue_count"] == 0, ambiguous_audit
    assert ambiguous_audit["region_count"] == 0
    assert ambiguous_registry.migration_state == "NEEDS_REBUILD_FROM_GUIDES"
    ambiguous_layers = binding.binding_layers(ambiguous_bm)
    assert all(
        int(vert[ambiguous_layers["verts"][binding.FP_VERTEX_UID]]) == 0
        for vert in ambiguous_bm.verts
    )
    assert all(
        int(edge[ambiguous_layers["edges"][binding.FP_EDGE_UID]]) == 0
        for edge in ambiguous_bm.edges
    )
    assert all(
        int(face[ambiguous_layers["faces"][binding.FP_FACE_UID]]) == 0
        for face in ambiguous_bm.faces
    )
    ambiguous_bm.to_mesh(ambiguous_retopo.data)
    ambiguous_retopo.data.update()
    ambiguous_bm.free()
    save_built_cells(ambiguous_retopo, {"ambiguous": ambiguous_record})

    evidence = {
        "phase": "create",
        "project_uuid": project.project_uuid,
        "binding_schema_version": binding.BINDING_SCHEMA_VERSION,
        "audit": build_audit,
        "migration_audit": migration_audit,
        "ambiguous_audit": ambiguous_audit,
        "undo_audit": undo_audit,
        "redo_audit": redo_audit,
        "final_undo_audit": final_undo_audit,
        "undo_allocator": full_allocator,
        "redo_allocator": deleted_allocator,
        "region_uuids": sorted(registry.regions),
        "custom_attributes": sorted(attribute.name for attribute in retopo.data.attributes),
    }
    (run_dir / "pf02_create.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    activate(retopo)
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
    print("PF02_CREATE", json.dumps(evidence, sort_keys=True))

else:
    retopo = bpy.data.objects.get("PF02Retopo")
    assert retopo is not None and retopo.type == "MESH"
    project = read_project_record(retopo)
    assert project is not None
    registry = binding.load_binding_registry(retopo)
    assert registry is not None

    legacy_retopo = bpy.data.objects.get("PF02LegacyRetopo")
    ambiguous_retopo = bpy.data.objects.get("PF02AmbiguousRetopo")
    assert legacy_retopo is not None
    assert ambiguous_retopo is not None
    legacy_bm = bmesh.new()
    legacy_bm.from_mesh(legacy_retopo.data)
    legacy_audit = binding.audit_binding_registry(legacy_retopo, legacy_bm)
    legacy_bm.free()
    assert legacy_audit["issue_count"] == 0, legacy_audit
    assert legacy_audit["region_count"] == 2
    assert legacy_audit["shared_boundary_count"] == 1
    ambiguous_bm = bmesh.new()
    ambiguous_bm.from_mesh(ambiguous_retopo.data)
    ambiguous_audit = binding.audit_binding_registry(
        ambiguous_retopo,
        ambiguous_bm,
    )
    ambiguous_bm.free()
    assert ambiguous_audit["issue_count"] == 0, ambiguous_audit
    assert ambiguous_audit["region_count"] == 0
    assert ambiguous_audit["migration_state"] == "NEEDS_REBUILD_FROM_GUIDES"

    bm = bmesh.new()
    bm.from_mesh(retopo.data)
    audit = binding.audit_binding_registry(retopo, bm, registry=registry)
    assert audit["issue_count"] == 0, audit
    assert audit["region_count"] == 2
    assert audit["shared_boundary_count"] == 1
    assert all(len(face.verts) == 4 for face in bm.faces)
    maxima = {
        "next_vertex_uid": max(
            int(vert[binding.binding_layers(bm)["verts"][binding.FP_VERTEX_UID]])
            for vert in bm.verts
        ),
        "next_edge_uid": max(
            int(edge[binding.binding_layers(bm)["edges"][binding.FP_EDGE_UID]])
            for edge in bm.edges
        ),
        "next_face_uid": max(
            int(face[binding.binding_layers(bm)["faces"][binding.FP_FACE_UID]])
            for face in bm.faces
        ),
    }
    allocator = registry.allocator.as_dict()
    for key, maximum in maxima.items():
        assert allocator[key] > maximum, (key, allocator[key], maximum)
    bm.free()

    copied = binding.rekey_binding_registry(registry, binding.new_uuid())
    assert copied.project_uuid != registry.project_uuid
    assert set(copied.regions).isdisjoint(registry.regions)

    delete_probe = retopo.copy()
    delete_probe.data = retopo.data.copy()
    delete_probe.name = "PF02DeleteProbe"
    bpy.context.scene.collection.objects.link(delete_probe)
    activate(delete_probe, edit=True)
    edit_bm = bmesh.from_edit_mesh(delete_probe.data)
    cells = load_built_cells(delete_probe)
    removal = remove_built_cell_geometry(
        delete_probe,
        edit_bm,
        cells,
        ("left",),
    )
    delete_audit = binding.audit_binding_registry(delete_probe, edit_bm)
    assert removal.removed_faces == 1
    assert delete_audit["issue_count"] == 0, delete_audit
    assert delete_audit["region_count"] == 1
    assert delete_audit["shared_boundary_count"] == 0
    assert delete_audit["orphan_element_count"] == 0
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.data.objects.remove(delete_probe, do_unlink=True)

    evidence = {
        "phase": "verify",
        "project_uuid": project.project_uuid,
        "audit": audit,
        "delete_audit": delete_audit,
        "legacy_audit": legacy_audit,
        "ambiguous_audit": ambiguous_audit,
        "allocator": allocator,
        "maxima": maxima,
        "copy_project_uuid": copied.project_uuid,
    }
    (run_dir / "pf02_verify.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print("PF02_VERIFY", json.dumps(evidence, sort_keys=True))
