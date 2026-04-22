from dataclasses import dataclass


@dataclass
class CueSegment:
    cue_index: int
    total_cues: int
    start_time: float
    duration: float
