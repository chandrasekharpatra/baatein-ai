from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from baatein.domain.models import CallSummary, Turn


@dataclass(frozen=True)
class CallRecord:
    """What the end-of-call analyzer reads: the transcript, plus what the user agreed to keep in
    this call (the only details the summary may name) and topics it must leave out."""

    user_name: str
    turns: Sequence[Turn]
    saved: Sequence[str] = ()
    avoid: Sequence[str] = ()


class CallAnalyzer(Protocol):
    async def analyze(self, call: CallRecord) -> CallSummary: ...
