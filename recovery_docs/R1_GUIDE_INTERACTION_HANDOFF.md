# FlowPatch R1 Guide Interaction Handoff

Date: 2026-08-01

Branch: `recovery/v1.4.12-core`

Version: `1.4.19`

Build ID: `recovery-r1-guide-interaction-20260801`

## Scope

This checkpoint repairs the foreground guide-editing regressions reported after
the v1.4.18 M0 package. It does not begin the guide/mesh binding registry,
Contour Walk, boundary extrusion, or Dissolve/Reflow stages.

## Implemented Contract

- Hovering the middle of a guide segment and pressing `G` inserts one control
  at that location, then moves only the inserted control.
- Cancelling that hover-insert move restores the exact pre-insertion guide.
- Clicking one control selects only that control; it does not implicitly select
  the whole edge.
- Shift-clicking two physically adjacent controls selects only their connecting
  guide segment.
- Delete works from an explicit control, node, selected segment, whole linked
  shape, or control/segment hover fallback.
- An explicitly selected degree-2 logical corner is no longer categorically
  protected. Compatible sides merge deterministically; junctions and unsafe
  shared ownership still reject before mutation.
- Deleting a segment from a closed guide opens the loop. Deleting a first,
  last, or interior segment from an open guide preserves valid endpoint
  topology and produces the expected remaining fragment or fragments.
- Auto Build now attaches its mesh state to the preceding local guide-history
  snapshot. One `Ctrl+Z` restores the guide and generated mesh together.
- A clear closed quadrilateral is normalized into four logical guide sides
  before region extraction, allowing the structured all-quad cell path to run.
  Circular contours remain outside this four-side classifier.

## Verified Package

Archive:
`FlowPatch_Retopo_recovery_R1_guide_interaction_v1.4.19.zip`

ZIP SHA-256:
`3A937F20F901DBB3ACA211BF3C48A614E434B271A5D81F549B1D6D89D99DD7D5`

Runtime payload SHA-256:
`6C378373E2921A561E493A2EDFF0FC6614567D8EF5A72376E88FCED7B1720C09`

Package audit:

- Root is exactly `flowpatch_retopo/`.
- 51 clean runtime entries; 2,374,884-byte ZIP.
- The detached `.sha256` matches the archive.
- Changed runtime files extracted from the ZIP match branch source byte for
  byte.
- No tests, recovery documents, Git data, logs, bytecode, or `__pycache__` are
  packaged.

## Automated Validation

- Static Python suite: 63 tests passed.
- Python compilation and `git diff --check`: passed.
- Exact extracted-ZIP Blender 5.3 fixtures: 6 passed.
- Launch guard and ViewLayer transaction fixtures passed at v1.4.19.
- ViewLayer transaction evidence reports zero project-audit issues.
- R1 interaction evidence covers explicit corner merge, hover control insert,
  segment deletion into two open fragments, closed-quad four-side splitting,
  adjacent-cell preservation, and mesh-backed local undo.

All Blender fixture runs used `--factory-startup --background`. They did not
install or reload the real profile, save preferences, or save a `.blend`.

## Foreground Acceptance

Use a new disposable Blender file, not a production `.blend`.

1. Select a simple plane or cube as Surface, start FlowPatch, and confirm the
   HUD and primary toolbar remain visible.
2. Draw one clear closed four-corner guide. Confirm one structured all-quad
   patch appears and remains attached to the selected surface.
3. Hover the middle of one guide segment and press `G`. Confirm one new control
   appears and only that control moves. Press `Esc` and confirm it disappears.
4. Click one control and confirm no edge is selected. Shift-click its adjacent
   control and confirm only the connecting segment is selected.
5. Delete the selected segment, then undo. Repeat with a hovered control and a
   hovered segment. Confirm there is no protected-corner warning for a
   compatible explicit degree-2 deletion.
6. Draw and auto-build another quad, then press `Ctrl+Z` once. Confirm both the
   newly formed patch and its guide disappear together.
7. Press `F7` to stop and resume. Confirm retained guides, HUD, toolbar, and
   project identity return.
8. Run Copy Debug State and confirm no traceback or project-audit error.

Foreground acceptance remains required before beginning the binding-registry
or later topology stages.

## Boundaries

- Recovery branch source was patched and packaged.
- Canonical frozen source was not edited.
- Real Blender extension/profile was not installed, reloaded, or modified.
- User preferences were not saved.
- No `.blend` was saved.
- The existing dirty production Blender process was not closed or restarted.
- Real foreground mouse-and-keyboard behavior is not yet proven.
- Restart persistence is not tested.
