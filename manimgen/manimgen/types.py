import enum
from dataclasses import dataclass


@dataclass
class CueSegment:
    cue_index: int
    total_cues: int
    start_time: float
    duration: float


class MuxStatus(str, enum.Enum):
    """Outcome of muxing one cue's narration audio onto its video clip.

    Inherits str so instances compare equal to their string values, mirroring
    SceneErrorType. Only SUCCESS / RETRIED_OK may ship to the assembler; FAILED
    must never produce a clip in the assembled video (a FAILED cue means the
    viewer would see animation with no voice — see issue #28).
    """

    SUCCESS = "success"  # muxed cleanly on the first attempt
    RETRIED_OK = "retried_ok"  # first attempt failed, second succeeded
    FAILED = "failed"  # missing audio slice or mux failed even after one retry


@dataclass(frozen=True)
class GateResult:
    """Output of the codegen + timing-gate seam (cli._generate_and_gate).

    Carries the (possibly auto-fixed) scene source plus where it was written and
    whether the zero-cost timing gate found unresolvable freeze-frame issues. A
    ``timing_blocked`` of True means the expensive first render should be skipped
    and the section routed straight into the retry path.
    """

    code: str
    class_name: str
    scene_path: str
    timing_blocked: bool


@dataclass(frozen=True)
class CueMuxResult:
    """Result of attempting to mux one cue.

    A narration-less (silent) video clip is NEVER carried in ``path`` for a
    FAILED result — ``path`` is the muxed clip on SUCCESS/RETRIED_OK and None on
    FAILED. This makes a failed cue impossible to mistake for a successful one
    downstream.
    """

    cue_index: int
    status: MuxStatus
    path: str | None  # muxed clip path on success; None on FAILED
    error: str | None = None  # underlying error message on FAILED

    @property
    def ok(self) -> bool:
        return self.status in (MuxStatus.SUCCESS, MuxStatus.RETRIED_OK)
