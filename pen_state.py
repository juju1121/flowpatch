from dataclasses import dataclass
from enum import Enum


class PenPhase(str, Enum):
    IDLE = "IDLE"
    POINTER_DOWN = "POINTER_DOWN"
    DRAWING = "DRAWING"
    FINALIZING = "FINALIZING"
    CANCELLING = "CANCELLING"


_ACTIVE_PHASES = frozenset(
    {
        PenPhase.POINTER_DOWN,
        PenPhase.DRAWING,
    }
)


@dataclass(frozen=True)
class PenTransition:
    accepted: bool
    reason_code: str
    previous_phase: PenPhase
    current_phase: PenPhase
    stroke_serial: int

    def as_dict(self):
        return {
            "accepted": bool(self.accepted),
            "reason_code": str(self.reason_code),
            "previous_phase": self.previous_phase.value,
            "current_phase": self.current_phase.value,
            "stroke_serial": int(self.stroke_serial),
        }


@dataclass
class PenState:
    phase: PenPhase = PenPhase.IDLE
    stroke_serial: int = 0
    finalized_stroke_serial: int = 0
    pointer_captured: bool = False
    projection_gap: bool = False
    projected_sample_count: int = 0
    cancel_reason: str = ""

    @property
    def active(self):
        return self.phase in _ACTIVE_PHASES and self.pointer_captured

    @property
    def drawing(self):
        return self.phase in _ACTIVE_PHASES

    @property
    def can_append(self):
        return self.active

    def _result(self, accepted, reason_code, previous_phase):
        return PenTransition(
            accepted=bool(accepted),
            reason_code=str(reason_code),
            previous_phase=previous_phase,
            current_phase=self.phase,
            stroke_serial=self.stroke_serial,
        )

    def begin(self):
        previous = self.phase
        if self.phase is not PenPhase.IDLE or self.pointer_captured:
            return self._result(False, "STROKE_ALREADY_ACTIVE", previous)
        self.stroke_serial += 1
        self.phase = PenPhase.POINTER_DOWN
        self.pointer_captured = True
        self.projection_gap = False
        self.projected_sample_count = 0
        self.cancel_reason = ""
        return self._result(True, "STROKE_STARTED", previous)

    def projected_sample(self):
        previous = self.phase
        if not self.can_append:
            return self._result(False, "NO_ACTIVE_STROKE", previous)
        self.projected_sample_count += 1
        self.projection_gap = False
        self.phase = PenPhase.DRAWING
        return self._result(True, "PROJECTED_SAMPLE", previous)

    def projection_miss(self):
        previous = self.phase
        if not self.can_append:
            return self._result(False, "NO_ACTIVE_STROKE", previous)
        self.projection_gap = True
        return self._result(True, "LOCAL_RAY_MISS", previous)

    def claim_finalize(self):
        previous = self.phase
        if not self.can_append:
            reason = (
                "STROKE_ALREADY_FINALIZED"
                if self.stroke_serial > 0
                and self.finalized_stroke_serial == self.stroke_serial
                else "NO_ACTIVE_STROKE"
            )
            return self._result(False, reason, previous)
        if self.finalized_stroke_serial == self.stroke_serial:
            return self._result(
                False,
                "STROKE_ALREADY_FINALIZED",
                previous,
            )
        self.finalized_stroke_serial = self.stroke_serial
        self.pointer_captured = False
        self.phase = PenPhase.FINALIZING
        return self._result(True, "FINALIZE_CLAIMED", previous)

    def cancel(self, reason_code):
        previous = self.phase
        if self.phase is PenPhase.IDLE and not self.pointer_captured:
            return self._result(False, "NO_ACTIVE_STROKE", previous)
        self.pointer_captured = False
        self.phase = PenPhase.CANCELLING
        self.cancel_reason = str(reason_code)
        return self._result(True, "STROKE_CANCELLED", previous)

    def reset(self):
        previous = self.phase
        self.phase = PenPhase.IDLE
        self.pointer_captured = False
        self.projection_gap = False
        self.projected_sample_count = 0
        self.cancel_reason = ""
        return self._result(True, "RESET_IDLE", previous)

    def snapshot(self):
        return {
            "phase": self.phase.value,
            "stroke_serial": int(self.stroke_serial),
            "finalized_stroke_serial": int(
                self.finalized_stroke_serial
            ),
            "pointer_captured": bool(self.pointer_captured),
            "projection_gap": bool(self.projection_gap),
            "projected_sample_count": int(
                self.projected_sample_count
            ),
            "cancel_reason": str(self.cancel_reason),
        }
