# FlowPatch P1-F0.1: Session Stop Handoff

Status: packaged source follow-up, ready for user foreground testing.

## Assigned Batch

P1-F0.1 repairs the reported `F7`/Stop failure only. It does not begin
P1-F1 selection, P1-F2 connection, P1-F3 sharp-edge resistance, or any later
contour, Auto Build, ownership, extrusion, Dissolve, or toolbar work.

## Defect And Root Cause

The failing call path was:

```text
F7 -> flowpatch.toggle_tool.invoke -> bpy.ops.flowpatch.stop_session
-> Stop poll requires Edit Mode -> RuntimeError: context is incorrect
```

The captured debug state was internally consistent: the FlowPatch session was
active, project audit had zero issues, and Blender context was `OBJECT` mode.
That combination occurs when the retained modal is suspended for native UI or
another Object-Mode handoff. The old nested operator call could not pass its
Edit-Mode-only poll.

## Repair

`operators.py` now has one owned `_stop_active_session()` path that:

- finalizes and preserves the active FlowPatch session without another
  context-sensitive FlowPatch operator call;
- exits Edit Mode only when the current edit object is the FlowPatch retopo
  object; and
- leaves an unrelated edit context untouched instead of changing it.

Both F7 and the panel Stop operator use that path. The Stop poll now accepts an
active session from Object Mode as well as Edit Mode.

## Files Changed

- `operators.py`
- `__init__.py`
- `blender_manifest.toml`
- `build_identity.py`
- `tests/test_recovery_launch_guard.py`
- `tests/recovery_launch_guard_blender.py`
- `tests/test_recovery_p0_diagnostics.py`
- `tests/recovery_p0_diagnostics_blender.py`

## Verification

- Static suite: 48 passed.
- Source factory-startup Blender fixtures passed:
  `FLOWPATCH_LAUNCH_GUARD_PASS`, `FLOWPATCH_R1_VIEWLAYER_PASS`,
  `FLOWPATCH_P0_DIAGNOSTICS_PASS`, and
  `FLOWPATCH_P1_SURFACE_ANCHOR_PASS`.
- The launch-guard fixture now proves both F7 and panel Stop finalize an
  active session while Blender is in Object Mode, with no nested Stop poll.
- Extracted-package audit: 50 runtime files; every packaged runtime file
  byte-matches the recovery branch source.
- The same four factory-startup Blender fixtures passed from the extracted ZIP.

## Identity

- Add-on version: `1.4.16`
- Build ID: `recovery-p1-session-stop-20260731`
- Runtime payload SHA-256:
  `3FE90417B46874DABDA0AF0518794A0284746F8583D2300D7F4FDBF0808744E6`
- ZIP SHA-256:
  `5DEC665C2CD3D32BBAC8F4B912F9685D4AF1BB76AA611C35C981D3155305C4AE`
- Package:
  `D:\BlenderLibrary\project file\FlowPatch Retopo\packages\FlowPatch_Retopo_recovery_P1_session_stop_v1.4.16.zip`
- Detached checksum:
  `D:\BlenderLibrary\project file\FlowPatch Retopo\packages\FlowPatch_Retopo_recovery_P1_session_stop_v1.4.16.zip.sha256`

## Five-Minute Foreground Test

Use a new disposable Blender file, not a production scene.

1. Install the package only if you choose to test it, add a mesh Surface, and
   start FlowPatch. Confirm that the HUD and toolbar appear.
2. Press F7 while the session is active. It should stop without a traceback and
   preserve the retained guides/patch data.
3. Start FlowPatch again, reach the panel in the normal viewport, and use
   `Stop`. It should also stop without `stop_session.poll()` failing.
4. If Blender reaches the reported Object-Mode suspended state again, press F7
   once. It must end the active session cleanly rather than reporting an
   incorrect context.
5. Use Copy Debug State only if anything fails; include the exact action that
   triggered it.

## Explicit Boundaries

- Recovery branch source changed: yes.
- Frozen canonical source changed: no.
- Real installed extension changed or reloaded: no.
- Foreground Blender launched by Codex: no; only disposable background Blender
  fixtures ran.
- Blender preferences saved: no.
- Production `.blend` opened or saved: no.
- Real foreground behavior accepted: not yet; user test required.
- Restart persistence proven: no.
