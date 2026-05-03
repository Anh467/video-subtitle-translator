"""One translated subtitle cue."""

from dataclasses import dataclass

@dataclass
class TranslatedSegment:
    start: float
    end: float
    original: str
    translated: str

