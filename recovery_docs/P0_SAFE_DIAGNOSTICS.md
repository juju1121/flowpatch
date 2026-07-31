# Recovery P0 Safe Diagnostics And Build Identity

Date: 2026-07-31
Status: COMPLETE - PACKAGED - BACKGROUND VERIFIED - USER FOREGROUND PENDING

## Scope

P0 owns only stale-RNA-safe debug capture and exact build identity. Copy Bug
State now reacquires project and target objects by durable UUID, returns a
partial JSON document when objects or project metadata are missing, and avoids
dereferencing stale Blender Object wrappers in its poll and snapshot paths.

One compact `v1.4.14 P0` badge is shown beside Copy Debug State. Projection,
drawing, surface anchors, contour normalization, n-gon solving, Auto Build,
selection domains, extrusion, Dissolve/Reflow, and broader UI work are
unchanged.

## Implementation

- Added guarded RNA attribute, custom-property, name, and object helpers.
- Added a resolver that searches the current ViewLayer first and then
  `bpy.data`, with explicit missing, renamed, unlinked, and ambiguous states.
- Replaced persistent diagnostic Object references with project UUID, object
  UUID, target UUID, and name hints. A live Object may still be cached only
  inside the active modal operation.
- Added partial project and target status, warnings, and the last recorded
  exception to debug JSON schema version 2.
- Added build version, build ID, source branch, deterministic payload digest,
  active source path, familiar and junction-resolved installed paths, and
  project schema version.
- Hardened the adjacent project-object lookup used by project controls against
  the same deleted-wrapper failure class.

## Verification

- Python/static suite: 41/41 passed.
- Python compile: passed with bytecode redirected outside the repository.
- Blender P0 fixture: `FLOWPATCH_P0_DIAGNOSTICS_PASS`.
- Blender R1 regression: `FLOWPATCH_R1_VIEWLAYER_PASS`.
- Blender launch regression: `FLOWPATCH_LAUNCH_GUARD_PASS`.
- All three Blender fixtures passed again from the extracted final ZIP under
  `--factory-startup --background`.
- Covered valid, deleted, renamed, unlinked, missing-target, and
  missing-registry states; Copy Bug State returned `FINISHED` and serialized
  valid JSON.
- Final ZIP audit: 50 runtime files, zero missing, extra, mismatched, unsafe,
  cache, test, or recovery-document entries.

Evidence:
`D:\BlenderLibrary\project file\FlowPatch Retopo\test_runs\recovery_p0_20260731`

## Identity

- Add-on version: `1.4.14`.
- Build ID: `recovery-p0-safe-diagnostics-20260731`.
- Runtime payload SHA-256:
  `9D81F27C9D7D24472BC33809F174712ED7F60ED7B9D57E9D3DACDD3E027C397F`.
- Package SHA-256:
  `5333E4E8F85987D0C7B1444851924D981B55759A475E9F736D09840AD8C07726`.

Package:
`D:\BlenderLibrary\project file\FlowPatch Retopo\packages\FlowPatch_Retopo_recovery_P0_diagnostics_v1.4.14.zip`

The payload digest excludes `build_identity.py` so it is reproducible. The
exact archive digest is supplied in the detached `.sha256` sidecar because a
ZIP cannot contain its own final digest.

## Boundaries

- Frozen canonical source: unchanged.
- Real installed extension: unchanged and not reloaded.
- Production Blender PID `13176`: alive, dirty, unsaved, and untouched.
- Real preferences: not saved.
- Production `.blend`: not opened or saved.
- Foreground Blender and Computer Use: not launched for P0; the handoff assigns
  foreground acceptance to the user.
- Restart persistence: not tested because P0 was not installed.

## Next Batch

Stop after P0 packaging and user foreground acceptance. P1 surface anchors is
next. P2 contour normalization and P3 general all-quad fallback own the n-gon
work; P4-P8 remain later isolated batches.
