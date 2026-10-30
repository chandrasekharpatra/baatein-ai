from baatein.domain.models import CallSummary
from baatein.ports.analyzer import CallRecord


class FakeAnalyzer:
    def __init__(self, result: CallSummary | None = None, error: Exception | None = None):
        self.result = result or CallSummary("A short, light chat about his day.")
        self.error = error
        self.calls: list[CallRecord] = []

    async def analyze(self, call: CallRecord) -> CallSummary:
        self.calls.append(call)
        if self.error is not None:
            raise self.error
        return self.result
