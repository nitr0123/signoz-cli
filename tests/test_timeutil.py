"""Tests for time range parsing."""

import pytest

from signoz_cli.timeutil import parse_time_range, resolve_timestamps


class TestParseTimeRange:
    def test_minutes(self):
        assert parse_time_range("30m") == 30 * 60_000

    def test_hours(self):
        assert parse_time_range("2h") == 2 * 3_600_000

    def test_days(self):
        assert parse_time_range("7d") == 7 * 86_400_000

    def test_seconds(self):
        assert parse_time_range("45s") == 45_000

    @pytest.mark.parametrize("bad", ["", "h", "10x", "-5h", "1.5h", "1 h"])
    def test_invalid(self, bad):
        with pytest.raises(ValueError):
            parse_time_range(bad)


class TestResolveTimestamps:
    def test_explicit_start_end_win(self):
        s, e = resolve_timestamps(1000, 2000, "6h")
        assert (s, e) == (1000, 2000)

    def test_default_range_ms(self):
        before = resolve_timestamps(None, None, None, default_range="6h")
        after = resolve_timestamps(None, None, None, default_range="6h")
        duration = 6 * 3_600_000
        for s, e in (before, after):
            assert e - s == duration
            # end should be near current time in ms
            assert e > 1_600_000_000_000

    def test_time_range_overrides_default(self):
        s, e = resolve_timestamps(None, None, "30m", default_range="6h")
        assert e - s == 30 * 60_000

    def test_ns_unit(self):
        s, e = resolve_timestamps(None, None, "1h", unit="ns")
        assert e - s == 3_600_000 * 1_000_000
        assert e > 1_600_000_000_000_000_000

    def test_lone_start_runs_to_now(self):
        # Previously a lone --start was silently ignored; it must be honoured.
        s, e = resolve_timestamps(1000, None, "1h")
        assert s == 1000
        assert e > 1_600_000_000_000  # ~now in ms
        assert e > s

    def test_lone_end_runs_back_one_range(self):
        s, e = resolve_timestamps(None, 5_000_000, "30m")
        assert e == 5_000_000
        assert e - s == 30 * 60_000

    def test_lone_end_respects_ns_unit(self):
        s, e = resolve_timestamps(None, 9_000_000_000, "1h", unit="ns")
        assert e == 9_000_000_000
        assert e - s == 3_600_000 * 1_000_000

    def test_start_after_end_rejected(self):
        with pytest.raises(ValueError, match="must be before end"):
            resolve_timestamps(2000, 1000, "6h")

    def test_start_equal_end_rejected(self):
        with pytest.raises(ValueError, match="must be before end"):
            resolve_timestamps(1000, 1000, "6h")
