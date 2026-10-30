from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from baatein.ports.clock import DemoClock, SystemClock


def test_system_clock_returns_current_utc_time() -> None:
    before = datetime.now(UTC)

    now = SystemClock().now()

    assert now.tzinfo is UTC
    assert before <= now <= datetime.now(UTC)


class Ticking:
    def __init__(self, now: datetime) -> None:
        self.at = now

    def now(self) -> datetime:
        return self.at


def test_demo_clock_is_real_time_until_a_moment_is_set(tmp_path: Path) -> None:
    real = Ticking(datetime(2026, 10, 30, 9, 0, tzinfo=UTC))
    clock = DemoClock(tmp_path / "demo" / "clock.json", real)
    assert clock.now() == real.at
    assert clock.clear() is False

    clock.set(datetime(2026, 11, 2, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata")))
    real.at += timedelta(minutes=5)  # time runs on from the moment

    assert clock.now() == datetime(2026, 11, 2, 4, 35, tzinfo=UTC)
    assert clock.now().tzinfo is UTC
    assert clock.clear() is True
    assert clock.now() == real.at


def test_demo_clock_ignores_an_unreadable_file(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "clock.json"
    path.write_text('{"now": "not a date"}')
    real = Ticking(datetime(2026, 10, 30, 9, 0, tzinfo=UTC))

    assert DemoClock(path, real).now() == real.at
    assert "unreadable demo clock" in caplog.text
