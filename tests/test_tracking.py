from pathlib import Path

import pytest

from prawn_monitor.history import bucket_readings
from prawn_monitor.schedule import slots_due
from prawn_monitor.tracking import analyze_tracks, parse_tracking_csv

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = (ROOT / "examples" / "amadeus_sample.csv").read_text(encoding="utf-8")


def test_sample_csv_speed_distance_crowding_and_length():
    frames = parse_tracking_csv(SAMPLE)
    result = analyze_tracks(frames, fps=10, mm_per_pixel=1, crowding_mm=40)
    summary = result["summary"]
    assert summary["animals"] == 2
    assert summary["total_distance_mm"] == pytest.approx(90)
    assert summary["mean_speed_mm_s"] == pytest.approx(50)
    assert summary["median_length_mm"] == pytest.approx(35)
    assert result["samples"][0]["mean_speed_mm_s"] is None
    assert result["samples"][0]["mean_nn_mm"] == pytest.approx(30)
    assert result["samples"][0]["crowded_fraction"] == pytest.approx(1)
    assert result["samples"][-1]["mean_nn_mm"] == pytest.approx((90**2 + 30**2) ** 0.5)
    assert result["samples"][-1]["cum_distance_mm"] == pytest.approx(90)
    assert result["samples"][-1]["crowded_fraction"] == pytest.approx(0)


def test_heading_optional_and_blank_detection_is_skipped():
    text = "frame,cx0,cy0,w0,h0\n0,1,1,8,3\n1,5,1,8,3\n2,, ,,\n"
    frames = parse_tracking_csv(text)
    assert [frame["frame"] for frame in frames] == [0, 1, 2]
    assert 0 not in frames[2]["animals"]
    result = analyze_tracks(frames, fps=2, mm_per_pixel=2, max_gap_frames=3)
    # One step of 4 px * 2 mm, then a gap with no detection, so distance stays 8 mm.
    assert result["summary"]["total_distance_mm"] == pytest.approx(8)
    assert result["summary"]["animals"] == 1


def test_bad_csv_explains_the_problem():
    with pytest.raises(ValueError, match="frame"):
        parse_tracking_csv("cx0,cy0,w0,h0\n1,2,3,4\n")


def test_bucket_readings_averages_long_series():
    rows = [
        {"ts": f"2026-10-10T00:00:{index:02d}Z", "do_mg_l": float(index), "temp_f": 84.0, "ph": None, "level_cm": None, "level_state": "ok"}
        for index in range(10)
    ]
    points = bucket_readings(rows, max_points=5)
    assert len(points) == 5
    assert points[0]["do_mg_l"] == pytest.approx(0.5)
    assert points[-1]["level_state"] == "ok"


def test_schedule_fires_once_inside_the_lookback():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("America/Los_Angeles")
    moment = datetime(2026, 10, 10, 8, 0, 20, tzinfo=tz)
    assert slots_due(moment, ["08:00", "20:00"], set()) == ["08:00"]
    fired = {"2026-10-10 08:00"}
    assert slots_due(moment, ["08:00"], fired) == []
    early = datetime(2026, 10, 10, 7, 59, 0, tzinfo=tz)
    assert slots_due(early, ["08:00"], set()) == []
