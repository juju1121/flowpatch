from .binding_model import BindingRegistryError


FP_VERTEX_UID = "fp_vertex_uid"
FP_VERTEX_REGION = "fp_vertex_region_local_id"
FP_VERTEX_ROLE = "fp_vertex_role"
FP_VERTEX_GENERATION = "fp_vertex_generation"
FP_GUIDE_NODE_LOCAL_ID = "fp_guide_node_local_id"
FP_ANCHOR_LOCAL_ID = "fp_anchor_local_id"
FP_PARAM_U = "fp_u"
FP_PARAM_V = "fp_v"

FP_EDGE_UID = "fp_edge_uid"
FP_EDGE_REGION = "fp_edge_region_local_id"
FP_EDGE_BOUNDARY_KEY = "fp_boundary_key"
FP_EDGE_GENERATION = "fp_edge_generation"
FP_GUIDE_EDGE_LOCAL_ID = "fp_guide_edge_local_id"
FP_EDGE_ROLE = "fp_edge_role"

FP_FACE_UID = "fp_face_uid"
FP_FACE_REGION = "fp_face_region_local_id"
FP_CELL_LOCAL_ID = "fp_cell_local_id"
FP_SOLVER_KIND = "fp_solver_kind"
FP_FACE_GENERATION = "fp_face_generation"


VERTEX_INT_LAYERS = (
    FP_VERTEX_UID,
    FP_VERTEX_REGION,
    FP_VERTEX_ROLE,
    FP_VERTEX_GENERATION,
    FP_GUIDE_NODE_LOCAL_ID,
    FP_ANCHOR_LOCAL_ID,
)
VERTEX_FLOAT_LAYERS = (FP_PARAM_U, FP_PARAM_V)
EDGE_INT_LAYERS = (
    FP_EDGE_UID,
    FP_EDGE_REGION,
    FP_EDGE_BOUNDARY_KEY,
    FP_EDGE_GENERATION,
    FP_GUIDE_EDGE_LOCAL_ID,
    FP_EDGE_ROLE,
)
FACE_INT_LAYERS = (
    FP_FACE_UID,
    FP_FACE_REGION,
    FP_CELL_LOCAL_ID,
    FP_SOLVER_KIND,
    FP_FACE_GENERATION,
)


def _ensure_layer(collection, kind, name):
    layers = getattr(collection.layers, kind)
    layer = layers.get(name)
    return layer if layer is not None else layers.new(name)


def ensure_binding_layers(bm):
    # Blender can invalidate cached BMesh element wrappers when a CustomData
    # layer is added. Create every layer before reacquiring element tables.
    for name in VERTEX_INT_LAYERS:
        _ensure_layer(bm.verts, "int", name)
    for name in VERTEX_FLOAT_LAYERS:
        _ensure_layer(bm.verts, "float", name)
    for name in EDGE_INT_LAYERS:
        _ensure_layer(bm.edges, "int", name)
    for name in FACE_INT_LAYERS:
        _ensure_layer(bm.faces, "int", name)
    for collection in (bm.verts, bm.edges, bm.faces):
        collection.ensure_lookup_table()
        collection.index_update()
    return binding_layers(bm)


def binding_layers(bm):
    return {
        "verts": {
            name: bm.verts.layers.int.get(name)
            for name in VERTEX_INT_LAYERS
        }
        | {
            name: bm.verts.layers.float.get(name)
            for name in VERTEX_FLOAT_LAYERS
        },
        "edges": {
            name: bm.edges.layers.int.get(name)
            for name in EDGE_INT_LAYERS
        },
        "faces": {
            name: bm.faces.layers.int.get(name)
            for name in FACE_INT_LAYERS
        },
    }


def missing_binding_layers(bm):
    layers = binding_layers(bm)
    return {
        domain: tuple(
            name for name, handle in values.items() if handle is None
        )
        for domain, values in layers.items()
        if any(handle is None for handle in values.values())
    }


def domain_uid_state(elements, layer):
    by_uid = {}
    duplicates = set()
    unassigned = 0
    for element in elements:
        value = int(element[layer])
        if value <= 0:
            unassigned += 1
            continue
        if value in by_uid:
            duplicates.add(value)
        else:
            by_uid[value] = element
    return {
        "by_uid": by_uid,
        "duplicates": duplicates,
        "unassigned": unassigned,
    }


def allocate_element_uid(
    element,
    layer,
    allocator,
    domain,
    used,
    candidate=0,
):
    existing = int(element[layer])
    if existing > 0:
        owner = used.get(existing)
        if owner is not None and owner is not element:
            raise BindingRegistryError(
                "BINDING_UID_COLLISION",
                f"Duplicate {str(domain).lower()} UID {existing}.",
            )
        used[existing] = element
        allocator.raise_above(domain, existing)
        return existing
    candidate = int(candidate)
    if candidate > 0 and candidate not in used:
        value = candidate
        allocator.raise_above(domain, value)
    else:
        value = allocator.allocate(domain)
        while value in used:
            value = allocator.allocate(domain)
    element[layer] = value
    used[value] = element
    return value
