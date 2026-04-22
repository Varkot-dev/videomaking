from dataclasses import dataclass


@dataclass
class CueSegment:
    cue_index: int      # 0-based index of this segment within the section
    total_cues: int     # total number of segments in this section
    start_time: float   # seconds from start of section audio
    duration: float     # seconds this segment should play
