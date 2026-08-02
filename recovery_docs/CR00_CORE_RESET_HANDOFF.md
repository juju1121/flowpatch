# FlowPatch CR-00 Core Reset Handoff

Date: 2026-08-02

Status: **CANDIDATE BUILT; FOREGROUND ACCEPTANCE BLOCKED**

## Goal

Restore the accepted v1.4.19 behavior as the product baseline, remove the
rejected PF-02 monolith from the product path, salvage only a modular stable-ID
and BindingRegistry foundation, and expose only truthful primary tool states.
CR-01 guide/mesh synchronization is explicitly out of scope.

## Source

- Branch: `recovery/core-reset-from-v1.4.19`
- Base: `b85fedd536...` (accepted v1.4.19 rollback baseline)
- Candidate version: `1.4.21`
- Build ID: `core-reset-cr00-20260802`
- Runtime payload SHA-256:
  `2C76A4A498E85F0B60AA2C412011F207523141FC95CDCD257A8B6DF4788F715B`

## Implemented Scope

The PF-02 identity work is split into focused modules:

- `binding_model.py`: records, UUID validation, monotonic allocators
- `binding_storage.py`: canonical JSON storage and exact property rollback
- `binding_layers.py`: BMesh CustomData schema and UID allocation
- `binding_writer.py`: explicit graph/region/boundary identity writers
- `binding_migration.py`: migration planning and isolated copy rekeying
- `binding_audit.py`: read-only identity, mapping, layer, and quad audits
- `binding_registry.py`: thin public facade

The foundation reports `FOUNDATION_READY_NOT_WIRED`. It does not hook guide
edits, mesh edits, Build, resume, or undo. Those transactions remain CR-01.

Primary toolbar corrections:

- removed fake `Trim`, fake `Dissolve`, and deferred HUD `Loop Cut`
- disabled toolbar controls consume their clicks instead of falling through
  into Draw
- `Build` is enabled only for the active `VALID` four-sided `GRID` preview
- non-GRID commit attempts are rejected with an explicit CR-00 reason

## Verification

Passed:

- 76 pure/static unit tests
- Python compile-all
- Git diff whitespace check
- six inherited Blender 5.3 background fixtures:
  launch guard, topology-aware delete, diagnostics, draw sampling,
  surface anchors, and ViewLayer transaction rollback
- CR-00 Blender-native create/reopen fixture in two separate processes
- persisted 9 vertex UIDs, 12 edge UIDs, 4 face UIDs, and one four-quad Region
- identical BindingRegistry digest after reopen
- zero CR-00 binding audit issues

The Blender-native persistence evidence is under:

`D:\BlenderLibrary\project file\FlowPatch Retopo\_codex_runs\cr00_binding_blender_20260802_003720`

## Package

Delivered candidate:

`E:\games\FlowPatch_v1.4.21_CR00_Core_Reset_Candidate.zip`

- ZIP SHA-256:
  `2B60D496F9A9880171145C14438AD7DF8FFD1BE504255BE752B09AC1F590AEB6`
- one `flowpatch_retopo` root
- 58 files
- zero missing, extra, or mismatched files after extraction

## Live Boundary

The candidate was installed byte-for-byte into the real Blender 5.3 profile
after fresh extension and preference backups. Preferences remained unchanged.
A new empty Blender process was launched, but Computer Use could not bind to
the returned Blender window and produced this repeated error:

`window id 78908016 no longer belongs to blender.5.3; current owner is blender.5.3`

No foreground mouse or keyboard acceptance actions were performed. The empty
controlled process was closed. The real profile was then restored to the clean
v1.4.19 package, matching all 51 runtime files. The current real-profile state
is therefore v1.4.19, not the CR-00 candidate.

- Source edits: complete candidate
- Isolated/background tests: passed
- Candidate live install: exact, then rolled back
- Preference save: not performed
- Production `.blend` save: not performed
- Disposable `.blend` save: background binding round-trip only
- Foreground test: blocked before first input
- Restart persistence: not proven
- Current installed product baseline: clean v1.4.19

## Next Gate

Retry Computer Use against a separately launched disposable Blender process.
CR-00 cannot be accepted, and CR-01 cannot begin, until the real foreground
v1.4.19 regression checklist and candidate checklist pass with actual pointer
and keyboard input, followed by a disposable cold restart and zero project
audit errors.
