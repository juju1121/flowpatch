# FlowPatch P1-F0.2: Draw Sampling Handoff

Date: 2026-07-31
Status: PACKAGED - SOURCE AND EXTRACTED PACKAGE L2 VERIFIED - FOREGROUND PENDING

## Assigned Batch

P1-F0.2 repairs the immediate basic-drawing regression reported after v1.4.16.
It remains inside P1 surface attachment. It does not implement P1-F1
selection domains, P1-F2 F connection, P1-F3 sharp-edge resistance, P2
contour normalization, P3 general all-quad solving, P4 multi-region Auto
Build, extrusion, Dissolve/Reflow, or toolbar redesign.

## Root Cause

`_append_surface_sample()` first raycast the final mouse location using the
previous committed surface anchor. A fast or zoomed-out mouse movement could
exceed `max_surface_step` at that endpoint and return before FlowPatch split
the screen movement into continuity-safe intermediate samples. The visible
result was a stroke that appeared unable to draw even though its intermediate
surface path was valid.

The panel also allowed the Surface field to change during an active modal
session, while the running projector remained bound to the launch target.
That UI mismatch could make a valid projection appear to use the wrong mesh.

## Repair

- The endpoint preflight is now a front-facing visibility probe only.
- Each committed interpolated candidate still receives the prior anchor and
  enforces same-side, normal-continuity, and maximum-step limits.
- A first endpoint projection miss shows a concise front-facing/locked-Surface
  status message.
- The Surface field is disabled while a session is active, with a short lock
  explanation. Stop the session before changing target.
- Added a Blender regression fixture proving a large endpoint jump no longer
  prevents valid intermediate samples while candidate continuity remains on.

## Files Changed

- `operators.py`
- `ui.py`
- `__init__.py`
- `blender_manifest.toml`
- `build_identity.py`
- `tests/test_recovery_p1_surface_anchor.py`
- `tests/recovery_p1_draw_sampling_blender.py`
- `tests/test_recovery_p0_diagnostics.py`
- `tests/recovery_p0_diagnostics_blender.py`

## Verification

- Static suite: 50 passed.
- Source factory-startup Blender fixtures: 5 passed.
- New draw-sampling evidence: endpoint preflight used no previous anchor,
  three valid samples committed, candidate continuity remained enabled, and
  the unsafe endpoint was not committed.
- Package audit: 50 runtime files, zero forbidden/cache/test/recovery-doc
  entries, zero missing or extra files, and zero byte mismatches.
- The same five Blender fixtures passed from the extracted final ZIP:
  `FLOWPATCH_P0_DIAGNOSTICS_PASS`, `FLOWPATCH_R1_VIEWLAYER_PASS`,
  `FLOWPATCH_LAUNCH_GUARD_PASS`, `FLOWPATCH_P1_SURFACE_ANCHOR_PASS`, and
  `FLOWPATCH_P1_DRAW_SAMPLING_PASS`.

## Identity

- Add-on version: `1.4.17`
- Build ID: `recovery-p1-draw-sampling-20260731`
- Runtime payload SHA-256:
  `46C85DAAA7102D754529C038639012CA0118C0849ADD3B73902375744932C7B4`
- ZIP SHA-256:
  `50154C61541E096F3A42D3CA92E27F7361413A564380A519077D9CFEF6574A9F`
- Package:
  `D:\BlenderLibrary\project file\FlowPatch Retopo\packages\FlowPatch_Retopo_recovery_P1_draw_sampling_v1.4.17.zip`
- Detached checksum:
  `D:\BlenderLibrary\project file\FlowPatch Retopo\packages\FlowPatch_Retopo_recovery_P1_draw_sampling_v1.4.17.zip.sha256`
- Evaluation folder:
  `E:\Archive_C_AppData\C_Moved\Users_Documents\blender tech bro\blender-tech-bro-runs\20260731-215351_flowpatch-p1-draw-sampling`

## Explicit Boundaries

- Recovery branch source changed: yes.
- Frozen canonical source changed: no.
- Installed profile: still v1.4.16; v1.4.17 was not installed or reloaded.
- Blender live process: none at the final boundary check.
- Preferences saved: no.
- Production `.blend` opened or saved: no.
- Foreground modal mouse/HUD behavior: not tested; user gate required.
- Restart persistence: not tested.
- P2 and later R1 stages: not started in this package.

## Foreground Gate

Use a new disposable Blender file. Install v1.4.17, choose a mesh Surface,
start FlowPatch, and confirm the HUD and toolbar remain visible. Draw slow and
fast strokes on a plane, curved surface, and across one visible hard corner.
The guide must stay on the viewed side, retain intermediate samples during a
fast drag, and never tunnel or jump to a disconnected shell. Confirm the
Surface picker is locked while drawing, then F7 Stop/Resume and check Copy
Debug State for zero project-audit errors. Do not begin P2 until this pass is
accepted.
