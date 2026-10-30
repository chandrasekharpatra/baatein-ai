"""Numbers kept for each call, to tune Baatein from real calls instead of guesses: how long
replies take and where that time goes, what memory search does, and what the call used.
Saved with the call's session; `baatein metrics` reports on them (Phase 6)."""

from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field, fields
from typing import Any


@dataclass
class CallMetrics:
    reply_ms: list[float] = field(default_factory=list)
    """From the caller finishing speaking to Baatein starting to reply (LiveKit e2e_latency)."""
    transcript_ms: list[float] = field(default_factory=list)
    """From the end of their speech to the final transcript."""
    turn_end_ms: list[float] = field(default_factory=list)
    """From the end of their speech to deciding their turn was over."""
    llm_first_token_ms: list[float] = field(default_factory=list)
    tts_first_audio_ms: list[float] = field(default_factory=list)
    user_turns: int = 0
    replies: int = 0
    interrupted: int = 0
    """Replies the caller talked over."""
    llm_input_tokens: int = 0
    llm_cached_tokens: int = 0
    llm_output_tokens: int = 0
    stt_audio_seconds: float = 0.0
    tts_characters: int = 0
    searches: int = 0
    """Per-turn memory searches sent to the embedder."""
    searches_skipped: int = 0
    """Turns with nothing to search for (only fillers like "ठीक है")."""
    search_ms: list[float] = field(default_factory=list)
    search_failures: int = 0
    memories_now: int = 0
    """Memories that reached the reply they were searched for."""
    memories_late: int = 0
    """Memories that arrived after the reply started, added for the next turn."""
    recall_calls: int = 0
    thinking_said: int = 0
    """Times Baatein said "Hmm…" while a slow recall ran."""
    saved: int = 0
    already_saved: int = 0
    """Saves turned down because the memory was already there."""
    tools: dict[str, int] = field(default_factory=dict)
    """Tool calls by name."""

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "CallMetrics":
        known = {f.name for f in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in known})

    def summary(self, seconds: float) -> str:
        """The call in one log line."""
        reply, search = percentile(self.reply_ms, 50), percentile(self.search_ms, 50)
        found = self.memories_now + self.memories_late
        cached = self.llm_cached_tokens / self.llm_input_tokens if self.llm_input_tokens else 0
        return (
            f"{seconds:.0f} s, {self.user_turns} caller turns, {self.replies} replies"
            f" (p50 {_or_none(reply)}); {self.searches} searches (p50 {_or_none(search)},"
            f" {found} memories found); tools: {_tools(self.tools)};"
            f" Claude {self.llm_input_tokens} tokens in ({cached:.0%} cached),"
            f" {self.llm_output_tokens} out"
        )


def percentile(values: Sequence[float], q: float) -> float | None:
    """The q-th percentile (0-100), interpolated; None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) - 1) * q / 100
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def _ms(values: Sequence[float]) -> str:
    p50, p90 = percentile(values, 50), percentile(values, 90)
    if p50 is None or p90 is None:
        return "no data"
    return f"p50 {p50:.0f} ms, p90 {p90:.0f} ms ({len(values)})"


def _or_none(ms: float | None) -> str:
    return "-" if ms is None else f"{ms:.0f} ms"


def _tools(counts: dict[str, int]) -> str:
    return ", ".join(f"{name} {n}" for name, n in sorted(counts.items())) or "none"


def _share(part: int, whole: int) -> str:
    return f"{part}/{whole} ({part / whole:.0%})" if whole else "0/0"


def report(calls: Iterable[CallMetrics]) -> str:
    """A plain-text summary of many calls, for `baatein metrics`."""
    calls = list(calls)
    if not calls:
        return "No calls with metrics yet."

    def joined(name: str) -> list[float]:
        return [value for call in calls for value in getattr(call, name)]

    def total(name: str) -> Any:
        return sum(getattr(call, name) for call in calls)

    turns = total("user_turns")
    tools: dict[str, int] = {}
    for call in calls:
        for name, n in call.tools.items():
            tools[name] = tools.get(name, 0) + n
    memories = total("memories_now") + total("memories_late")
    lines = [
        f"Calls: {len(calls)}, caller turns: {turns}, replies: {total('replies')}",
        "",
        "Reply latency (caller stops → Baatein starts):",
        f"  reply            {_ms(joined('reply_ms'))}",
        f"  transcript       {_ms(joined('transcript_ms'))}",
        f"  end of turn      {_ms(joined('turn_end_ms'))}",
        f"  Claude 1st token {_ms(joined('llm_first_token_ms'))}",
        f"  TTS 1st audio    {_ms(joined('tts_first_audio_ms'))}",
        f"  interrupted      {_share(total('interrupted'), total('replies'))} replies",
        "",
        "Memory:",
        f"  turns searched   {_share(total('searches'), turns)}"
        f" (skipped as fillers: {total('searches_skipped')})",
        f"  search time      {_ms(joined('search_ms'))}, failed {total('search_failures')}",
        f"  memories late    {_share(total('memories_late'), memories)} (joined the next turn)",
        f'  recall tool      {total("recall_calls")} calls, "Hmm…" said {total("thinking_said")}',
        f"  saved            {total('saved')}, already saved {total('already_saved')}",
        f"  tools            {_tools(tools)}",
        "",
        "Usage:",
        f"  Claude tokens    {total('llm_input_tokens')} in ({total('llm_cached_tokens')} cached),"
        f" {total('llm_output_tokens')} out",
        f"  speech-to-text   {total('stt_audio_seconds') / 60:.1f} min",
        f"  text-to-speech   {total('tts_characters')} characters",
    ]
    return "\n".join(lines)
