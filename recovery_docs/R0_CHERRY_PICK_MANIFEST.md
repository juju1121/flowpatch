# Recovery R0 Keep/Omit Manifest

## Selected From V1.4.11

- Shared `_resolve_session_target` for F7 and guide-session launch.
- Edit Mode F7 Surface preflight.
- Containment of nested Blender operator cancellation as a clean error.

## Selected From V1.4.12

- Named duplicate-object UUID diagnostics.
- `repair_passive_target_uuid_copies` with project-owner protection and exact
  rollback.
- Specific `ProjectStoreError` propagation through F7 and guide-session start.
- Explicit Recover support that preserves valid project metadata.

## Deliberately Omitted

- `selection_state.py`.
- Batch 09 Vertex/Edge/Face domains and modifier paths.
- Batch 09 selection drawing overlays and caches.
- Batch 09 selection properties and UI controls.
- Batch 09 guide-modal event routing and transform handoff changes.
- V1.4.10, v1.4.11, and v1.4.12 metadata bumps.
- Any R1-R7 implementation.

## Runtime Data Touched By Selected Fixes

- Only `flowpatch_object_uuid` may be removed from passive copied Surface
  objects after full validation.
- A copied object carrying project metadata is never repaired automatically.
- Target, retopo, and project UUIDs remain unchanged.
- No guide, mesh, selection, handler, keymap, or preference data is added by
  R0.
