# Recovery R1 ViewLayer Transaction

Date: 2026-07-31
Status: COMPLETE - PACKAGED - ISOLATED FOREGROUND PASS

## Scope

R1 owns only Start/Create session ordering and rollback:

1. Resolve and validate the target and optional existing retopo object.
2. Create the retopo object when needed.
3. Link it to the active ViewLayer collection.
4. Update the ViewLayer and verify object identity membership.
5. Activate and select only after membership is proven.
6. Enter Edit Mode and start the guide modal.
7. Roll back every change owned by the attempt if any step fails.

Projection, drawing, contour recognition, Build feedback, Adopt, Bridge,
Dissolve/Reflow, Trim, Cut selection, HUD design, and Mirror are unchanged.

## Transaction Contract

`_SessionStartTransaction` snapshots the prior mode, active object, selection,
session settings, relevant FlowPatch owner metadata, and retopo visibility. It
tracks only links, object data, and mesh data created by the current attempt.

Failure cleanup ends any partially started modal, leaves Edit Mode if needed,
restores metadata and visibility, unlinks only newly added collection links,
removes only newly created object/mesh data, restores session settings, and
restores the original selection, active object, and Edit Mode state.

The object-level `flowpatch_session_active` flag is set only after Blender
accepts `modal_handler_add`. Structured breadcrumbs identify the failed step,
reason code, message, and any rollback cleanup error.

## Verification

- Python/static tests: 32 passed.
- New R1 static transaction tests: 5 passed.
- Isolated Blender R1 fixture: `FLOWPATCH_R1_VIEWLAYER_PASS`.
- Existing launch-guard fixture: `FLOWPATCH_LAUNCH_GUARD_PASS`.
- Extracted package reran the R1 Blender fixture successfully.
- Package audit: 49 runtime files, zero extra, mismatched, cache, bytecode,
  test, recovery-document, or unsafe-root entries.
- Package runtime files byte-match the recovery branch source 49/49.

The isolated foreground Computer Use pass used Blender 5.3 and a new disposable
file. A real panel click created the retopo object in the active collection,
entered Edit Mode, activated the modal, and displayed both Live Session and the
bottom toolbar. F7 stopped the modal, panel Start resumed it, and panel Stop
returned to Object Mode. Every sampled state had zero project-audit issues.

## Identity

- Package version: `1.4.13`.
- Runtime fingerprint: `245B23CAB4392F9EC235F195B8D1B08E9CA027476E64B41855D13C59658CE486`.
- Package SHA-256: `D3C3DA863D24114FDADB7A0424D20116BAA265DE4C2B0BCFDBC54756C5C58590`.
- `operators.py` SHA-256: `3E6739F206D2181A317F553B6005BCE45027BACFC8AD9C9F4722BD30D2CDA282`.

Package:
`D:\BlenderLibrary\project file\FlowPatch Retopo\packages\FlowPatch_Retopo_recovery_R1_viewlayer_v1.4.13.zip`

Evidence:
`D:\BlenderLibrary\project file\FlowPatch Retopo\test_runs\recovery_r1_20260731`

## Boundaries

- Frozen canonical v1.4.12 source: unchanged.
- Real on-disk extension: unchanged and not reloaded; the final read-only
  audit reports metadata v1.4.9 and `operators.py` SHA-256
  `DD2ABD3BAE0FDD96597F410E2FE4ED27E7DC5A094C8750F824BE92B949BBE33E`.
- Modules loaded inside production PID 13176: unknown and deliberately not
  inspected or reloaded.
- Real preferences: not saved.
- Production Blender PID 13176: not controlled, closed, restarted, or saved.
- Production `.blend`: not opened or saved.
- Disposable isolated `.blend`: saved under the R1 evidence directory.
- Real-profile install and restart persistence: not tested.

## Next Batch

R1 is complete. Per the supplied queue, Q-A Build feedback and Auto Build
states is next, but it must remain a separate batch.
