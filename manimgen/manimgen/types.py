import enum
from dataclasses import dataclass, field


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
    and the section routed straight into the retry path. ``precheck_blocked`` means
    the same for a draft codeguard's precheck rejected: retry_scene re-runs the
    precheck on its first attempt and feeds the errors to the fixes.
    """

    code: str
    class_name: str
    scene_path: str
    timing_blocked: bool
    precheck_blocked: bool = False


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


class SectionStatus(str, enum.Enum):
    """What happened to one section of a run (#71).

    Every section of a run ends in exactly one of these, recorded in the run
    summary and in run_manifest.json. A section served from the cache keeps the
    status it had when it was built (stored in its .hash sidecar).
    """

    OK = "ok"  # rendered (first pass or after a repair) or cached, narrated
    ACCEPTED_WITH_DEFECTS = "accepted_with_defects"  # shipped with known defects
    FALLBACK = "fallback"  # the styled title card stands in for the animation
    DROPPED = "dropped"  # nothing from this section is in the video
    SILENT = "silent"  # in the video, but with no narration
    ERRORED = "errored"  # an unexpected error stopped this section
    NOT_RUN = "not_run"  # the run stopped before this section started

    @property
    def degraded(self) -> bool:
        """True when the video is missing something the plan promised."""
        return self in DEGRADED_STATUSES


DEGRADED_STATUSES = frozenset(
    {
        SectionStatus.FALLBACK,
        SectionStatus.DROPPED,
        SectionStatus.SILENT,
        SectionStatus.ERRORED,
        SectionStatus.NOT_RUN,
    }
)


@dataclass(frozen=True)
class RenderResult:
    """Output of the render seam (cli._render_with_retry).

    ``status`` is OK, ACCEPTED_WITH_DEFECTS or FALLBACK when ``path`` is a
    video, and DROPPED when not even the fallback rendered.
    """

    path: str | None
    status: SectionStatus
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.path is not None


@dataclass
class SectionOutcome:
    """Result of one section: its status, why, and the clips it contributes."""

    status: SectionStatus
    clips: list[str] = field(default_factory=list)
    reason: str = ""
