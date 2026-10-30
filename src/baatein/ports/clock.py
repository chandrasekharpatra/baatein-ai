import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class DemoClock:
    """Real time moved to a chosen moment, for recording a demo across "days" (development
    only; `BAATEIN_DEV__CLOCK_FILE`). `baatein demo-date` writes the file: the moment, and
    the real time it was set, after which time runs on from that moment. With no file it is
    real time. The file is read on every call, so the agent worker, the API and the CLI all
    follow a change at once, without a restart."""

    def __init__(self, path: Path, real: Clock | None = None) -> None:
        self._path = path
        self._real = real or SystemClock()
        self._warned = False
        logger.warning("demo clock on: memory time comes from %s when it exists", path)

    def now(self) -> datetime:
        real = self._real.now()
        try:
            data = json.loads(self._path.read_text())
            moment = datetime.fromisoformat(data["now"])
            if moment.tzinfo is None:
                raise ValueError("the demo moment needs a time zone")
            shifted = moment + (real - datetime.fromisoformat(data["set_at"]))
        except FileNotFoundError:
            return real
        except (ValueError, KeyError, TypeError):
            if not self._warned:
                logger.warning("ignoring unreadable demo clock file %s", self._path)
                self._warned = True
            return real
        return shifted.astimezone(UTC)

    def set(self, moment: datetime) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {"now": moment.isoformat(), "set_at": self._real.now().isoformat()}
        self._path.write_text(json.dumps(data))

    def clear(self) -> bool:
        """Back to real time. Returns whether a moment was set."""
        existed = self._path.exists()
        self._path.unlink(missing_ok=True)
        return existed
