import argparse
import hashlib
import json
from pathlib import Path
import sys
import uuid

import bmesh
import bpy


OBJECT_NAME = "FlowPatchCR00Binding"
PROJECT_UUID_KEY = "flowpatch_cr00_project_uuid"
REGISTRY_DIGEST_KEY = "flowpatch_cr00_registry_sha256"


def parse_args():
    argv = sys.argv
    argv = argv[argv.index("--") + 1 :] if "--" in argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--addon-root", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--mode", choices=("create", "verify"), required=True)
    return parser.parse_args(argv)


def clear_scene():
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


def create_grid_object():
    vertices = [
        (float(x), float(y), 0.0)
        for y in range(3)
        for x in range(3)
    ]
    faces = (
        (0, 1, 4, 3),
        (1, 2, 5, 4),
        (3, 4, 7, 6),
        (4, 5, 8, 7),
    )
    mesh = bpy.data.meshes.new(f"{OBJECT_NAME}Mesh")
    mesh.from_pydata(vertices, (), faces)
    mesh.update()
    obj = bpy.data.objects.new(OBJECT_NAME, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def registry_digest(binding, registry):
    payload = binding.encode_binding_registry(registry).encode("utf-8")
    return hashlib.sha256(payload).hexdigest().upper()


def write_evidence(run_dir, name, payload):
    path = run_dir / name
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return path


def create_fixture(binding, run_dir):
    clear_scene()
    obj = create_grid_object()
    project_uuid = str(uuid.uuid4())
    registry = binding.initialize_binding_registry(obj, project_uuid)

    guides = (
        {"guide_id": 1, "start_node": 1, "end_node": 2},
        {"guide_id": 2, "start_node": 2, "end_node": 3},
        {"guide_id": 3, "start_node": 3, "end_node": 4},
        {"guide_id": 4, "start_node": 4, "end_node": 1},
    )
    binding.reconcile_guide_identities(registry, guides)

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    layers = binding.ensure_binding_layers(bm)
    vertex_uid = {}
    edge_uid = {}
    face_uid = {}
    used_vertex = {}
    used_edge = {}
    used_face = {}

    for vert in sorted(bm.verts, key=lambda item: item.index):
        vertex_uid[vert.index] = binding.allocate_element_uid(
            vert,
            layers["verts"][binding.FP_VERTEX_UID],
            registry.allocator,
            "VERTEX",
            used_vertex,
        )
    for edge in sorted(bm.edges, key=lambda item: item.index):
        uid = binding.allocate_element_uid(
            edge,
            layers["edges"][binding.FP_EDGE_UID],
            registry.allocator,
            "EDGE",
            used_edge,
        )
        edge_uid[frozenset(vert.index for vert in edge.verts)] = uid
    for face in sorted(bm.faces, key=lambda item: item.index):
        face_uid[face.index] = binding.allocate_element_uid(
            face,
            layers["faces"][binding.FP_FACE_UID],
            registry.allocator,
            "FACE",
            used_face,
        )

    node_paths = {
        1: (0, 1, 2),
        2: (2, 5, 8),
        3: (8, 7, 6),
        4: (6, 3, 0),
    }
    guide_node_to_vertex_uid = {
        registry.guide_node_uuid_by_graph_id[str(graph_id)]: vertex_uid[path[0]]
        for graph_id, path in node_paths.items()
    }
    guide_edge_to_ordered_vertex_uids = {
        registry.guide_edge_uuid_by_graph_id[str(graph_id)]: tuple(
            vertex_uid[index] for index in path
        )
        for graph_id, path in node_paths.items()
    }
    region = binding.upsert_grid_region(
        registry,
        cycle_key="cr00-four-quad-grid",
        vertex_uids=vertex_uid.values(),
        edge_uids=edge_uid.values(),
        face_uids=face_uid.values(),
        guide_node_to_vertex_uid=guide_node_to_vertex_uid,
        guide_edge_to_ordered_vertex_uids=(
            guide_edge_to_ordered_vertex_uids
        ),
    )

    for vert in bm.verts:
        vert[layers["verts"][binding.FP_VERTEX_REGION]] = (
            region.region_local_id
        )
        vert[layers["verts"][binding.FP_VERTEX_GENERATION]] = (
            region.generation
        )
    for edge in bm.edges:
        edge[layers["edges"][binding.FP_EDGE_REGION]] = (
            region.region_local_id
        )
        edge[layers["edges"][binding.FP_EDGE_GENERATION]] = (
            region.generation
        )
    for face in bm.faces:
        face[layers["faces"][binding.FP_FACE_REGION]] = (
            region.region_local_id
        )
        face[layers["faces"][binding.FP_FACE_GENERATION]] = (
            region.generation
        )
        face[layers["faces"][binding.FP_SOLVER_KIND]] = 1

    for graph_id, path in node_paths.items():
        next_graph_id = 1 if graph_id == 4 else graph_id + 1
        edge_pairs = tuple(zip(path, path[1:]))
        mesh_edge_uids = tuple(
            edge_uid[frozenset(pair)] for pair in edge_pairs
        )
        key = binding.register_boundary(
            registry,
            guide_edge_uuids=(
                registry.guide_edge_uuid_by_graph_id[str(graph_id)],
            ),
            endpoint_node_uuids=(
                registry.guide_node_uuid_by_graph_id[str(graph_id)],
                registry.guide_node_uuid_by_graph_id[str(next_graph_id)],
            ),
            vertex_uids=(vertex_uid[index] for index in path),
            edge_uids=(value for value in mesh_edge_uids),
            region_uuids=(region.region_uuid,),
        )
        boundary_local_id = registry.boundary_local_ids[key]
        for pair in edge_pairs:
            mesh_edge = next(
                edge
                for edge in bm.edges
                if frozenset(vert.index for vert in edge.verts)
                == frozenset(pair)
            )
            mesh_edge[
                layers["edges"][binding.FP_EDGE_BOUNDARY_KEY]
            ] = boundary_local_id

    registry.solver_kind_local_ids["GRID"] = (
        registry.allocator.allocate("SOLVER_KIND")
    )
    audit = binding.audit_binding_registry(registry, bm=bm)
    assert audit["status"] == "PASS", audit
    binding.save_binding_registry(obj, registry)
    digest = registry_digest(binding, registry)
    obj[PROJECT_UUID_KEY] = project_uuid
    obj[REGISTRY_DIGEST_KEY] = digest
    bm.to_mesh(obj.data)
    obj.data.update()
    bm.free()

    blend_path = run_dir / "cr00_binding_roundtrip.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
    evidence = {
        "mode": "create",
        "status": "PASS",
        "blend_path": str(blend_path),
        "object_name": obj.name,
        "project_uuid": project_uuid,
        "registry_digest": digest,
        "audit": audit,
        "foundation": binding.foundation_status(),
    }
    write_evidence(run_dir, "cr00_binding_create.json", evidence)
    print("FLOWPATCH_CR00_BINDING_CREATE_PASS")


def verify_fixture(binding, run_dir):
    obj = bpy.data.objects.get(OBJECT_NAME)
    assert obj is not None, "Saved CR-00 binding object is missing."
    registry = binding.load_binding_registry(obj)
    assert registry is not None, "Saved BindingRegistry is missing."
    project_uuid = str(obj.get(PROJECT_UUID_KEY, ""))
    assert registry.project_uuid == project_uuid
    digest = registry_digest(binding, registry)
    assert digest == str(obj.get(REGISTRY_DIGEST_KEY, ""))

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    assert binding.missing_binding_layers(bm) == {}
    audit = binding.audit_binding_registry(registry, bm=bm)
    all_quads = all(len(face.verts) == 4 for face in bm.faces)
    uid_state = {
        "vertices": binding.domain_uid_state(
            bm.verts,
            binding.binding_layers(bm)["verts"][binding.FP_VERTEX_UID],
        ),
        "edges": binding.domain_uid_state(
            bm.edges,
            binding.binding_layers(bm)["edges"][binding.FP_EDGE_UID],
        ),
        "faces": binding.domain_uid_state(
            bm.faces,
            binding.binding_layers(bm)["faces"][binding.FP_FACE_UID],
        ),
    }
    uid_counts = {
        key: len(value["by_uid"]) for key, value in uid_state.items()
    }
    assert audit["status"] == "PASS", audit
    assert all_quads
    assert uid_counts == {"vertices": 9, "edges": 12, "faces": 4}
    assert all(not value["duplicates"] for value in uid_state.values())
    bm.free()

    evidence = {
        "mode": "verify",
        "status": "PASS",
        "source_blend": str(bpy.data.filepath),
        "project_uuid": project_uuid,
        "registry_digest": digest,
        "uid_counts": uid_counts,
        "all_quads": all_quads,
        "audit": audit,
        "foundation": binding.foundation_status(),
    }
    write_evidence(run_dir, "cr00_binding_verify.json", evidence)
    print("FLOWPATCH_CR00_BINDING_VERIFY_PASS")


args = parse_args()
run_dir = Path(args.run_dir).resolve()
run_dir.mkdir(parents=True, exist_ok=True)
addon_root = str(Path(args.addon_root).resolve())
if addon_root not in sys.path:
    sys.path.insert(0, addon_root)

from flowpatch_retopo import binding_registry as binding


if args.mode == "create":
    create_fixture(binding, run_dir)
else:
    verify_fixture(binding, run_dir)
