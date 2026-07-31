# FlowPatch Recovery R0 Inventory

Date: 2026-07-31
Branch: `recovery/v1.4.12-core`
Status: SOURCE-ONLY RECOVERY BASELINE - DO NOT INSTALL

## Baselines

- Foreground baseline: FlowPatch v1.4.9 Quadrant 2 package.
- Baseline package SHA-256:
  `9D850E76EDE819C83929EB9CFA892B41EAFA61F5A168DAB92CEABF78010ED216`
- Failed comparison package: FlowPatch v1.4.12 UUID recovery.
- Comparison package SHA-256:
  `98E73B55E0FABB275C3AB7CD215F466937D51F23F28138C085A585992273DCAC`
- The baseline extraction contains 49 files and byte-matches the package.

## V1.4.9 To V1.4.12 Diff

- 42 runtime files are byte-identical.
- Changed: `__init__.py`, `blender_manifest.toml`, `drawing.py`,
  `operators.py`, `project_store.py`, `properties.py`, and `ui.py`.
- Added: `selection_state.py`.
- Batch 09 accounts for 1,564 additions and 28 removals across selection,
  drawing overlays, modal routing, properties, and UI.
- The launch guard and passive UUID recovery are the only selected later
  infrastructure changes.

## Root-Cause Boundaries

- The reported `retopo.select_set(True)` ViewLayer failure is present in the
  v1.4.9 baseline. R1 must repair it transactionally.
- `projection.py` is byte-identical between v1.4.9 and v1.4.12. Wrong-side or
  detached projection predates Batch 09 and remains R2.
- `region_solver.py` is byte-identical between v1.4.9 and v1.4.12. Brittle
  irregular-contour recognition remains R4.
- Batch 09 made large changes inside the guide modal and is excluded from the
  recovery source until core gates G1-G7 pass.

## R0 Verification

- Python compile passed with bytecode redirected outside the repository.
- 27 focused project-store, pen-lifecycle, F7, and static tests passed.
- Isolated background Blender returned `FLOWPATCH_LAUNCH_GUARD_PASS`.
- The fixture proved clean missing-Surface cancellation, passive-copy UUID
  recovery, protected-copy rejection, metadata/topology retention, and no
  started session after rejection.
- Real installed extension changed: no.
- Foreground Blender launched: no.
- Production `.blend` opened or saved: no.
- Preferences saved: no.

## Next Batch

R1 owns only the Start/Create ViewLayer transaction. Drawing, projection,
recognition, persistence, Auto Build, Batch 09, and advanced UI remain out of
scope.
