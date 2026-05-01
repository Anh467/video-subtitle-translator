"""Transcript data structures."""

from dataclasses import dataclass

@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class TranscriptResult:
    text: str
    segments: list
    language: str
    source_file: str
