# Recovery P1 Surface Anchors And Patch Fairness

Date: 2026-07-31
Status: COMPLETE - PACKAGED - BACKGROUND VERIFIED - USER FOREGROUND PENDING

## Scope

P1 owns surface attachment and fairness for retained guides and structured
four-sided GRID patches. It does not implement contour/n-gon solving, Auto
Build multi-region behavior, selection domains, extrusion, Dissolve/Reflow,
Trim/Cut, or a broader primary UI redesign.

## Root Cause

The first flat-target error occurred in stroke regularization. The prior
view-depth filter reconstructed smoothed points in free space, and those
points were stored without a final surface reprojection. Preview interiors,
transforms, direct control drags, and Relax also used independent global
nearest-surface queries, which allowed detached points or wrong-side jumps.

## Implementation

- Expanded every `SurfaceAnchor` with target UUID, owning face and triangle,
  barycentric/local position, local normal, normal offset, topology revision,
  and connected shell component.
- Built evaluated triangle ownership, face adjacency, connected shell
  components, target bounds, and anchor reconstruction into
  `SurfaceProjector`.
- Added target-only front-face raycasts and local same-side projection. Hits
  are rejected when they cross a disconnected shell, exceed projection or
  surface-step limits, reverse the normal beyond the configured cosine, or
  face away from the current view.
- Preserved the last valid point or preview when a projection is rejected.
  Rejected previews are marked `REJECTED_PROJECTION` and cannot be committed.
- Reprojected regularized strokes after view-depth filtering and retained the
  new per-sample anchors.
- Resampled each logical boundary by normalized cumulative arc length while
  interpolating anchor frames in the same orientation as its points.
- Kept normalized four-side traversal before Coons interpolation, including
  reversed stored boundaries and unequal raw control counts.
- Projected GRID interiors row by row from anchored boundaries. Smoothed and
  Flattened results are reprojected before validation rather than left in
  free space.
- Replaced free-space Relax with transactional tangent-only fairing. Endpoints,
  corners, and shared patch boundaries remain pinned; each interior update is
  reprojected on every iteration.
- Added and persisted the required planar metrics:
  `patch_max_plane_deviation`, `min_quad_signed_area`, `max_edge_ratio`, and
  `normal_flip_count`.
- Added the seven requested defaults only to the existing Advanced panel:
  `frontface_epsilon`, `max_projection_distance`, `max_surface_step`,
  `normal_continuity_cos`, `guide_fair_strength`,
  `guide_fair_iterations`, and `flat_target_tolerance`.

## Verification

- Python/static suite: 47/47 passed.
- Source Blender fixtures passed under Blender 5.3 Alpha with
  `--factory-startup --background`:
  `FLOWPATCH_LAUNCH_GUARD_PASS`, `FLOWPATCH_R1_VIEWLAYER_PASS`,
  `FLOWPATCH_P0_DIAGNOSTICS_PASS`, and
  `FLOWPATCH_P1_SURFACE_ANCHOR_PASS`.
- The same four fixtures passed from the extracted final ZIP.
- P1 fixture coverage: complete anchor payload, front/back ray rejection,
  disconnected double-shell rejection, accepted 90-degree cube continuity,
  opposite cube-face rejection, curved-cylinder continuity, reversed and
  unequal boundary controls, tangent fairing, atomic fairing rollback, and
  topology-revision reacquisition.
- Flat irregular 7x5 GRID result:
  `patch_max_plane_deviation = 0.0`,
  `min_quad_signed_area = 0.2189357429742813`,
  `max_edge_ratio = 1.6587146338258705`, and
  `normal_flip_count = 0`.
- Boundary arc-length resampling produced a bottom chord ratio of
  `1.0155430025719492` in the irregular test.
- Final ZIP audit: 50 runtime files, zero forbidden/cache/test/recovery-doc
  entries, and zero byte differences from the clean allowlisted stage.

Source evidence:
`D:\BlenderLibrary\project file\FlowPatch Retopo\runs\recovery_p1_surface_anchor_dev_20260731\source_evidence`

Package evidence:
`D:\BlenderLibrary\project file\FlowPatch Retopo\runs\recovery_p1_surface_anchor_dev_20260731\package_evidence`

## Identity

- Add-on version: `1.4.15`.
- Build ID: `recovery-p1-surface-anchors-20260731`.
- Runtime payload SHA-256:
  `EDB1976836D2995079EEA5071B1890405F95E0C6604DFA407A048F08BD6884A5`.
- Package SHA-256:
  `27AEDA3EE59A018569E5020206985A9114BE3A01E726F15190752BC1171C2D6C`.

Package:
`D:\BlenderLibrary\project file\FlowPatch Retopo\packages\FlowPatch_Retopo_recovery_P1_surface_anchors_v1.4.15.zip`

Detached checksum:
`D:\BlenderLibrary\project file\FlowPatch Retopo\packages\FlowPatch_Retopo_recovery_P1_surface_anchors_v1.4.15.zip.sha256`

## Boundaries

- Frozen canonical source: unchanged.
- Real installed extension: unchanged; P1 was not installed or reloaded.
- Production Blender PID `13176`: not controlled, closed, restarted, or saved.
- Real preferences: not saved.
- Production `.blend`: not opened or saved.
- Foreground Blender: not tested by Codex; P1 requires the user foreground
  mouse-and-keyboard pass.
- Restart persistence: not tested.
- P2-P8: not started.

## Foreground Gate

The user should test `v1.4.15` in a disposable Blender file: five four-sided
patches on a plane and five on a curved surface, plus a small hard-corner
control move, a deliberately excessive G/R/S move, Relax, F7 Stop/Resume, and
Copy Debug State. No ripple, target penetration, opposite-side jump, shared
boundary movement, rejected-preview commit, traceback, or project-audit error
is allowed.

P1 stops here until that foreground result is reported. P2 contour
normalization remains the next implementation batch only after acceptance.
