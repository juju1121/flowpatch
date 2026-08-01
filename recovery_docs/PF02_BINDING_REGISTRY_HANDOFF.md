# FlowPatch PF-02 Stable UIDs and BindingRegistry Handoff

Date: 2026-08-01

Branch: `recovery/v1.4.12-core`

Version: `1.4.20`

Build ID: `recovery-pf02-binding-registry-20260801`

Binding schema: `1`

## Scope

PF-02 establishes persistent identity and mappings only. It does not move mesh
geometry from guide edits, move guides from mesh edits, add boundary extrusion,
normalize general contours, solve arbitrary all-quad regions, implement
Dissolve/Reflow, or begin Contour Walk.

The implemented ownership chain is:

`Project -> GuideNode/GuideEdge/SurfaceAnchor -> Region -> mesh elements`

## Persistent Identity

The registry stores canonical UUID strings for the project, Regions,
GuideNodes, GuideEdges, and SurfaceAnchors. Mesh vertices, edges, faces,
Regions, boundaries, anchors, solver kinds, GuideNodes, and GuideEdges also
receive positive monotonic local integer IDs from `ElementUIDAllocator`.

Deleted identities are retired. Their local IDs and UUIDs are not recycled in
the same project, including when a runtime guide graph ID is later reused.
Allocator counters are reconciled above every value found in the registry and
mesh custom data after load or migration.

## Registry Data Structures

`BindingRegistry` owns:

- the schema version and project UUID;
- monotonic counters for every local-ID domain;
- Region records and cycle-key lookup;
- graph-ID to GuideNode/GuideEdge UUID lookup;
- GuideNode, GuideEdge, SurfaceAnchor, boundary, and solver-kind local IDs;
- boundary ownership shared by one or more Regions;
- migration state, notes, revision, and retired identities.

Each `RegionBinding` owns:

- Region UUID, local ID, cycle key, solver kind, and generation;
- GuideNode to boundary vertex mapping;
- GuideEdge to ordered boundary vertex path mapping;
- vertex parametric coordinates and SurfaceAnchor mapping;
- exact vertex, edge, and face UID membership;
- exterior and shared boundary keys;
- guide/mesh revisions, last source label, and state.

The registry is serialized as canonical, sorted, compact JSON in the retopo
mesh object's `flowpatch_binding_registry_v1` custom property. Schema parsing
rejects malformed UUIDs, non-positive IDs, and unsupported versions before a
mutation is accepted.

## Blender Mesh Custom Data

Blender mesh attribute names are global across vertex, edge, and face domains
when a `.blend` is saved and reloaded. PF-02 therefore uses domain-qualified
names where the conceptual field repeats across domains.

Vertex layers:

- `fp_vertex_uid` (int)
- `fp_vertex_region_local_id` (int)
- `fp_vertex_role` (int)
- `fp_vertex_generation` (int)
- `fp_guide_node_local_id` (int)
- `fp_anchor_local_id` (int)
- `fp_u` (float)
- `fp_v` (float)

Edge layers:

- `fp_edge_uid` (int)
- `fp_edge_region_local_id` (int)
- `fp_boundary_key` (int local boundary ID)
- `fp_edge_generation` (int)
- `fp_guide_edge_local_id` (int)
- `fp_edge_role` (int)

Face layers:

- `fp_face_uid` (int)
- `fp_face_region_local_id` (int)
- `fp_cell_local_id` (int)
- `fp_solver_kind` (int local solver-kind ID)
- `fp_face_generation` (int)

## Writer and Transaction Rules

- Guide identity reconciliation is the only writer for persistent
  GuideNode/GuideEdge/SurfaceAnchor identities.
- Region commit is the only writer for new Region and generated mesh-element
  mappings.
- Region retirement removes ownership and reassigns surviving scalar owners
  without recycling IDs.
- Session startup snapshots the registry property and BMesh custom data before
  PF-02 initialization. Any later startup failure restores both.
- Local FlowPatch undo snapshots registry JSON, custom-data values, Regions,
  generated mesh, and counters as one transaction.
- A copied FlowPatch project is re-keyed to a new project UUID domain instead
  of sharing identities with the source.

## Migration Contract

Deterministic legacy four-side `GRID` cells can be reconstructed into current
PF-02 mappings without changing geometry. Ambiguous legacy cells are preserved
and marked `NEEDS_REBUILD_FROM_GUIDES`; PF-02 does not guess or partially stamp
UIDs. Mesh-only data without deterministic FlowPatch ownership is marked
`UNBOUND_MESH_ONLY` and adoption remains a later batch.

## Runtime Audits

The binding audit reports identity collisions, missing/orphan mesh elements,
invalid or overlapping retired identities, broken guide mappings, stale
generation/revision values, incorrect scalar ownership, malformed boundary
keys, inconsistent shared-boundary ownership, and non-quad committed faces.

The audit is read-only. Unsafe state blocks build or requests explicit repair;
it does not silently rewrite topology.

## Automated Evidence

The PF-02 Blender fixture covers two adjacent Regions with one shared boundary,
save/reload persistence, copied-project re-keying, Region retirement, no orphan
mesh mappings, deterministic legacy migration, ambiguous-legacy preservation,
exact local undo/redo restoration, allocator advancement without reuse, and a
second cold reload with zero audit issues.

Source checks completed on 2026-08-01:

- `git diff --check`: passed;
- forced Python compilation: passed;
- pure-Python discovery: 73 tests passed;
- exact extracted package: all six legacy Blender fixtures passed;
- exact extracted PF-02 create/save and cold-verify fixture: passed.

The exact-package PF-02 audit recorded 2 Regions, 6 vertex UIDs, 7 edge UIDs,
2 face UIDs, 1 shared boundary, deterministic legacy migration, preserved
ambiguous legacy state, and zero duplicate, missing, orphan, project-audit, or
binding-audit issues.

## Package

Release archive:

`E:\games\FlowPatch_Retopo_PF02_v1.4.20.zip`

- archive size: 2,392,174 bytes;
- archive SHA-256:
  `D8BE7513ED0548AC69F0310B2AB88FC6E145FDF930E03156830A8CB423425B7E`;
- detached checksum:
  `E:\games\FlowPatch_Retopo_PF02_v1.4.20.zip.sha256`;
- 52 runtime entries under the single `flowpatch_retopo/` root;
- staged, archived, and extracted runtime bytes matched exactly;
- runtime payload SHA-256:
  `3EF68598002A4C2E773B01DDD4C4506C714B57371655BC1BAA9D9F88C690A811`.

Exact package evidence is under:

`D:\BlenderLibrary\project file\FlowPatch Retopo\_codex_runs\pf02_release_20260801_044519`

## Real-Profile Installation

The accepted v1.4.19 installation and preferences were backed up before the
real-profile install:

`D:\BlenderLibrary\project file\FlowPatch Retopo\_codex_backups\pf02_preinstall_v1.4.19_20260801_045550`

The 52 PF-02 runtime files were installed to the Blender 5.3 user extension.
Their current disk bytes match the exact release package. Blender resolves the
loaded extension path to:

`E:\Archive_C_AppData\C_Moved\Blender\Blender\5.3\extensions\user_default\flowpatch_retopo`

The user's unrelated dirty production Blender remained open as PID 23772 at
`* (Unsaved) - Blender 5.3.0 Alpha`. It was not closed, restarted, reloaded,
driven, or saved. Consequently its already-loaded v1.4.19 Python modules remain
stale in memory until the user deliberately restarts or reloads that process;
only the real-profile files on disk are v1.4.20.

## Controlled Save and Cold Restart

The separate controlled real-profile run used:

`D:\BlenderLibrary\project file\FlowPatch Retopo\_codex_runs\pf02_live_profile_20260801_051100`

It saved only the disposable file
`PF02_Foreground_Disposable.blend`, closed that controlled process, reopened the
same disposable file in a new controlled Blender 5.3 process, and verified:

- v1.4.20, the PF-02 build ID, and runtime payload hash;
- one stable project UUID and the same two Region UUIDs after restart;
- identical registry SHA-256
  `DA0A8212B875F3B5ACF9610F53F773C631D114325AB90A4D745DE9F5770F01C0`;
- 2 Regions, 6 bound vertices, 7 bound edges, 2 bound faces, and 1 shared
  boundary;
- exact allocator persistence;
- zero duplicate, missing, orphan, project-audit, or binding-audit issues;
- successful cold-restart screenshot capture.

The production `.blend` was never saved. Evidence is in `live_save.json`,
`live_verify.json`, and `live_verify.png` in that run directory.

## Preferences Boundary

The preinstall and final preference-file SHA-256 is:

`A56F7B7F7351499CF50C85DACF39919398BE346E1F02D615160B0109B19293B7`

Preferences were not intentionally saved. During the first failed controlled
launcher attempt Blender's enabled automatic preference save wrote the file on
quit. The original backup was immediately restored byte-for-byte. The
successful save and cold-restart processes disabled automatic preference save
inside those test processes, and the final hash still matches the preinstall
backup exactly.

## Rollback Proof

The verified v1.4.19 backup was copied into an isolated rollback trial. All 73
backup files matched the trial copy byte-for-byte. Blender 5.3 then imported,
registered, and unregistered that copy with exit code 0:

- version: 1.4.19;
- build ID: `recovery-r1-guide-interaction-20260801`;
- runtime payload SHA-256:
  `6C378373E2921A561E493A2EDFF0FC6614567D8EF5A72376E88FCED7B1720C09`.

Rollback evidence is under:

`D:\BlenderLibrary\project file\FlowPatch Retopo\_codex_runs\pf02_live_profile_20260801_051100\rollback_trial_20260801_051523`

The actual profile was not rolled back because PF-02 data and lifecycle tests
passed; v1.4.20 remains installed on disk.

## Foreground Boundary

The controlled visible/scripted save and restart gate passed, but the real
mouse-and-keyboard FlowPatch gate did not run. Computer Use repeatedly listed
the correct Blender windows but rejected every state capture with the
contradictory ownership error:

`window id <id> no longer belongs to blender.5.3; current owner is blender.5.3`

Two bounded recovery attempts produced the same result. One helper relaunch
resolved to Steam Blender instead of the explicit 5.3 executable; only that
newly launched wrong process was immediately closed. The production PID 23772
was untouched.

Therefore HUD, toolbar, Draw/Edit input, Delete, undo, F7 Stop/Resume, and real
mouse/keyboard interaction are not claimed as PF-02 foreground-proven. This is
an automation-infrastructure blocker, not evidence of a new PF-02 data defect.
PF-03 remains gated until the foreground interaction pass succeeds.

The controlled profile emitted unrelated Tripo port, DecalMachine revision,
and HardOps unregister errors. They were not attributed to FlowPatch and were
not modified in PF-02.

## Hard Stop

PF-03 guide-to-mesh synchronization and PF-04 mesh-to-guide synchronization
remain unimplemented. PF-02 records revisions and ownership needed by those
batches but never performs either synchronization direction.
