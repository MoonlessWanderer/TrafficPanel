from datetime import datetime, timedelta

from app.services.traffic import CounterPoint, compute_traffic_delta
from app.utils.units import format_bps, format_bytes


def test_delta_uses_counter_difference():
    start = datetime(2026, 1, 1, 0, 0, 0)
    delta = compute_traffic_delta(
        CounterPoint(start, 1_000, 2_000),
        CounterPoint(start + timedelta(seconds=30), 4_000, 2_500),
        max_gap_seconds=600,
        max_plausible_bps=10**12,
    )
    assert delta.valid is True
    assert delta.rx_bytes == 3_000
    assert delta.tx_bytes == 500
    assert delta.rx_bps == 100
    assert delta.reset_detected is False


def test_counter_reset_is_not_negative():
    start = datetime(2026, 1, 1, 0, 0, 0)
    delta = compute_traffic_delta(
        CounterPoint(start, 10_000, 8_000),
        CounterPoint(start + timedelta(seconds=30), 400, 8_100),
        max_gap_seconds=600,
        max_plausible_bps=10**12,
    )
    assert delta.valid is True
    assert delta.reset_detected is True
    assert delta.rx_bytes == 400
    assert delta.tx_bytes == 100
    assert delta.rx_bytes >= 0 and delta.tx_bytes >= 0


def test_gap_is_not_zero_traffic():
    start = datetime(2026, 1, 1, 0, 0, 0)
    delta = compute_traffic_delta(
        CounterPoint(start, 100, 100),
        CounterPoint(start + timedelta(hours=2), 9_000, 9_000),
        max_gap_seconds=600,
        max_plausible_bps=10**12,
    )
    assert delta.valid is False
    assert delta.reason == "gap"
    assert delta.rx_bytes is None
    assert delta.tx_bytes is None


def test_units_convert_volume_and_speed():
    volume = format_bytes(1_500_000)
    speed = format_bps(125_000)
    assert volume["unit"] == "MB"
    assert speed["unit"] == "Mbps"
    assert abs(speed["value"] - 1) < 0.01
