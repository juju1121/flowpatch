# FlowPatch M0 Guide Deletion Handoff

Date: 2026-07-31

Branch: `recovery/v1.4.12-core`

Version: `1.4.18`

Build ID: `recovery-m0-guide-delete-20260731`

## Scope

This checkpoint implements only M0 from the Mesh/Guide Sync and Contour Walk
master specification: topology-aware deletion of an exact selected guide
control, GuideNode, or GuideEdge. M1 binding-registry work and all later
Contour Walk work are intentionally excluded.

## Implemented Contract

- Node-first hit testing remains authoritative; an edge is selected only from
  the middle segment band when no control is hit.
- `Delete`, `Backspace`, and `X` operate on the exact selected element. There
  is no newest-guide fallback.
- Deleting an interior control preserves the two topological endpoints and
  resynchronizes any compatible parametric mesh region.
- Deleting a degree-1 node removes that node and its incident GuideEdge.
- Deleting a compatible degree-2 node merges its two GuideEdges while
  preserving the outer endpoints and retained logical-side identity.
- Logical corners, closed-loop nodes, incompatible ownership, and junctions
  are rejected without mutation.
- Deleting a GuideEdge invalidates/removes only dependent built regions.
- A shared interior edge owned by multiple built regions is rejected and
  directs the user to Dissolve/Reflow.
- Built-region mesh removal uses stable FlowPatch vertex UIDs and exact quad
  ownership. Malformed, overlapping, stale, or repeated ownership stops before
  mutation.
- Graph data, mesh data, ownership metadata, and modal history share one
  rollback snapshot. `Ctrl+Z` restores both graph and mesh.

## Verified Package

Archive:
`FlowPatch_Retopo_recovery_M0_guide_delete_v1.4.18.zip`

ZIP SHA-256:
`7CF215016579FDD5EF2361300F8E988942F4361A7273E1739F1B253E61A93484`

Runtime payload SHA-256:
`54CB5B17924F9D431155149D8128755E6BA16A444533BE2F79D64DA8E09B1959`

Package audit:

- Root is exactly `flowpatch_retopo/`.
- 51 runtime files; 2,370,275-byte ZIP.
- Extracted runtime bytes match the selected source bytes.
- No tests, recovery documents, logs, bytecode, or `__pycache__` are packaged.

## Automated Validation

- Static suite: 56 tests passed.
- Source-stage Blender 5.3 fixtures: 6 passed.
- Extracted-ZIP Blender 5.3 fixtures: 6 passed.
- M0 evidence covers control deletion, degree-1 deletion, degree-2 merge,
  exact edge deletion, protected-corner rejection, junction rejection,
  repeated ownership rejection before mutation, adjacent-cell preservation,
  and graph-plus-mesh undo restoration.
- ViewLayer transaction evidence reports zero project-audit issues.

All Blender fixture runs used `--factory-startup --background`. They did not
install or reload the real profile, save preferences, or save a `.blend`.

## Foreground Acceptance

Use a new disposable Blender file, not a production `.blend`.

1. Select a mesh as Surface, start FlowPatch, and confirm the HUD and primary
   toolbar remain visible.
2. Draw a guide with at least one interior control. In Edit mode, click the
   interior control and press `Delete` or `X`. Confirm only that control is
   removed and both endpoints remain.
3. Press `Ctrl+Z`. Confirm the guide and any synchronized patch mesh return to
   the exact prior state.
4. Select a degree-1 endpoint and delete it. Confirm only its incident branch
   and dependent region are removed.
5. Select the middle of one GuideEdge and delete it. Confirm no neighboring
   unrelated built cell is removed.
6. On a compatible degree-2 same-side node, confirm deletion produces one
   continuous guide. On a logical corner or junction, confirm FlowPatch rejects
   the deletion and leaves graph and mesh unchanged.
7. With two adjacent built cells, try deleting their shared interior edge.
   Confirm FlowPatch directs the operation to Dissolve/Reflow and changes
   nothing.
8. Run Copy Debug State and confirm no project-audit errors or traceback.

Record foreground results separately. Do not begin M1 until this acceptance
pass succeeds.

## Boundaries

- Canonical frozen source was not edited.
- Real Blender extension/profile was not installed, reloaded, or modified.
- User preferences were not saved.
- No `.blend` was saved.
- No production foreground session was touched.
- Restart persistence is not tested.
