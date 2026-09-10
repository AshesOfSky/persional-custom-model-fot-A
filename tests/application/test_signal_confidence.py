from __future__ import annotations

import pytest
import pandas as pd

from modules import signal_generator
from modules.buy_action_plan import _score_signal
from modules.technical import add_all_indicators
from modules.signal_confidence import (
    SIGNAL_EVIDENCE_SCORE_SEMANTICS,
    signal_evidence_level,
    score_signal_evidence,
)


def test_same_bucket_count_keeps_continuous_trend_information() -> None:
    weak_alignment = score_signal_evidence(
        independent_bucket_count=2,
        trend_strength=42,
        direction="buy",
    )
    strong_alignment = score_signal_evidence(
        independent_bucket_count=2,
        trend_strength=78,
        direction="buy",
    )

    assert weak_alignment.bucket_score == pytest.approx(58.0)
    assert strong_alignment.bucket_score == pytest.approx(58.0)
    assert weak_alignment.score != pytest.approx(58.0)
    assert strong_alignment.score != pytest.approx(58.0)
    assert strong_alignment.score > weak_alignment.score
    assert weak_alignment.semantics == SIGNAL_EVIDENCE_SCORE_SEMANTICS




def test_sell_signal_uses_bearish_directional_alignment() -> None:
    bearish = score_signal_evidence(
        independent_bucket_count=2,
        trend_strength=20,
        direction="sell",
    )
    bullish = score_signal_evidence(
        independent_bucket_count=2,
        trend_strength=80,
        direction="sell",
    )

    assert bearish.directional_trend_score == pytest.approx(80.0)
    assert bullish.directional_trend_score == pytest.approx(20.0)
    assert bearish.score > bullish.score


def test_independent_evidence_breadth_remains_the_primary_component() -> None:
    two_buckets = score_signal_evidence(
        independent_bucket_count=2,
        trend_strength=60,
        direction="buy",
    )
    four_buckets = score_signal_evidence(
        independent_bucket_count=4,
        trend_strength=60,
        direction="buy",
    )

    assert four_buckets.score > two_buckets.score
    assert two_buckets.independent_bucket_count == 2
    assert four_buckets.independent_bucket_count == 4


def test_same_two_bucket_signal_has_a_monotonic_continuous_distribution() -> None:
    scores = [
        score_signal_evidence(2, trend, "buy").score
        for trend in range(35, 86, 5)
    ]

    assert scores == sorted(scores)
    assert len(set(scores)) == len(scores)
    assert 58.0 not in scores


@pytest.mark.parametrize(
    ("score", "expected"),
    [(49.9, "弱"), (50.0, "中"), (65.0, "中强"), (75.0, "强")],
)
def test_signal_evidence_level_is_derived_from_the_continuous_score(
    score: float,
    expected: str,
) -> None:
    assert signal_evidence_level(score) == expected


@pytest.mark.parametrize("direction", ["buy", "sell"])
def test_signal_evidence_score_is_bounded_and_not_a_probability(direction: str) -> None:
    result = score_signal_evidence(
        independent_bucket_count=99,
        trend_strength=100,
        direction=direction,
    )

    assert 0 <= result.score <= 95
    assert result.semantics == "signal_evidence_score_not_probability"


def test_signal_evidence_rejects_invalid_inputs() -> None:
    with pytest.raises(ValueError, match="independent_bucket_count"):
        score_signal_evidence(-1, 50, "buy")
    with pytest.raises(ValueError, match="trend_strength"):
        score_signal_evidence(2, 101, "buy")
    with pytest.raises(ValueError, match="direction"):
        score_signal_evidence(2, 50, "hold")


def test_missing_trend_history_downgrades_instead_of_inventing_neutral_evidence() -> None:
    missing = score_signal_evidence(
        2,
        50,
        "buy",
        trend_evidence_available=False,
    )
    available = score_signal_evidence(2, 50, "buy")

    assert missing.trend_evidence_status == "insufficient_history"
    assert missing.score == pytest.approx(34.8)
    assert missing.score < available.score


def test_composite_signal_uses_continuous_score_and_exposes_components(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = 80
    index = pd.date_range("2026-01-01", periods=rows, freq="D")
    frame = pd.DataFrame(
        {
            "Open": [100.0 + value for value in range(rows)],
            "High": [101.0 + value for value in range(rows)],
            "Low": [99.0 + value for value in range(rows)],
            "Close": [100.5 + value for value in range(rows)],
            "Volume": [1_000_000.0] * rows,
        },
        index=index,
    )

    def buy_conditions(_df, i, _levels, rsi_thresholds=None):
        del rsi_thresholds
        return ["EMA金叉(8×21)", "MACD金叉"] if i == rows - 1 else []

    monkeypatch.setattr(signal_generator, "_check_buy_conditions", buy_conditions)
    monkeypatch.setattr(
        signal_generator,
        "_check_sell_conditions",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(signal_generator, "online_trend_strength", lambda *_args: 78)

    result = signal_generator.generate_composite_signals(frame, {}, {})

    assert len(result["buy_signals"]) == 1
    signal = result["buy_signals"][0]
    assert signal["confidence"] == pytest.approx(66.0)
    assert signal["confidence"] != pytest.approx(58.0)
    assert signal["strength"] == "中"
    assert signal["confidence_level"] == "中强"
    assert signal["confidence_components"] == {
        "version": "signal-evidence-v2",
        "semantics": "signal_evidence_score_not_probability",
        "independent_bucket_count": 2,
        "bucket_score": 58.0,
        "directional_trend_score": 78.0,
        "trend_evidence_status": "available",
    }


def test_buy_plan_does_not_count_independent_buckets_twice() -> None:
    signal = {
        "confidence": 66.0,
        "buckets": ["trend", "momentum"],
        "confidence_components": {
            "version": "signal-evidence-v2",
            "semantics": "signal_evidence_score_not_probability",
            "independent_bucket_count": 2,
            "bucket_score": 58.0,
            "directional_trend_score": 78.0,
            "trend_evidence_status": "available",
        },
    }

    assert _score_signal(
        {"current_signal": "买入", "buy_signals": [signal], "sell_signals": []}
    ) == pytest.approx(66.0)


def test_buy_plan_does_not_treat_an_old_buy_point_as_current_evidence() -> None:
    stale_signal = {
        "confidence": 88.0,
        "buckets": ["trend", "momentum", "volume"],
        "confidence_components": {
            "version": "signal-evidence-v2",
            "semantics": "signal_evidence_score_not_probability",
            "independent_bucket_count": 3,
            "bucket_score": 72.0,
            "directional_trend_score": 96.0,
            "trend_evidence_status": "available",
        },
    }

    assert _score_signal(
        {
            "current_signal": "观望",
            "buy_signals": [stale_signal],
            "sell_signals": [],
        }
    ) == pytest.approx(20.0)


def test_buy_plan_keeps_legacy_signal_scoring_for_old_payloads() -> None:
    legacy = {"confidence": 58.0, "buckets": ["trend", "momentum"]}

    assert _score_signal({"buy_signals": [legacy]}) == pytest.approx(68.0)


def test_volume_and_td_buy_evidence_use_the_columns_produced_by_technical() -> None:
    frame = pd.DataFrame(
        {
            "Open": [100.0, 100.0],
            "High": [101.0, 101.0],
            "Low": [99.0, 99.0],
            "Close": [100.0, 100.0],
            "Volume": [100.0, 200.0],
            "Vol_MA_5": [100.0, 100.0],
            "TD_Setup": [0, 9],
            "TD_Signal": [0, 1],
        }
    )

    confirmations = signal_generator._check_buy_conditions(frame, 1, {})

    assert "放量(>1.5xMA5)" in confirmations
    assert "TD买入信号" in confirmations


def test_add_all_indicators_wires_smart_obv_evidence() -> None:
    rows = 80
    frame = pd.DataFrame(
        {
            "Open": [100.0 + index * 0.2 for index in range(rows)],
            "High": [101.0 + index * 0.2 for index in range(rows)],
            "Low": [99.0 + index * 0.2 for index in range(rows)],
            "Close": [100.5 + index * 0.2 for index in range(rows)],
            "Volume": [1_000_000.0 + index * 1000 for index in range(rows)],
        }
    )

    enriched = add_all_indicators(frame)

    assert "OBV_Divergence" in enriched.columns
    assert "OBV_Regime" in enriched.columns


def test_multi_timeframe_bucket_requires_real_as_of_ema_alignment() -> None:
    frame = pd.DataFrame(
        {
            "Open": [100.0, 100.0],
            "High": [101.0, 101.0],
            "Low": [99.0, 99.0],
            "Close": [100.0, 100.0],
            "Volume": [100.0, 100.0],
            "ADX": [30.0, 30.0],
            "SAR": [90.0, 90.0],
        }
    )

    confirmations = signal_generator._check_buy_conditions(frame, 1, {})

    assert "ADX趋势强+多周期看多" not in confirmations


def test_multi_timeframe_bucket_uses_weekly_and_monthly_ema_alignment() -> None:
    rows = 150
    frame = pd.DataFrame(
        {
            "Open": [100.0] * rows,
            "High": [101.0] * rows,
            "Low": [99.0] * rows,
            "Close": [100.0] * rows,
            "Volume": [100.0] * rows,
            "ADX": [30.0] * rows,
            "SAR": [90.0] * rows,
            "EMA_21": [120.0] * rows,
            "EMA_55": [110.0] * rows,
            "EMA_144": [100.0] * rows,
        }
    )

    confirmations = signal_generator._check_buy_conditions(frame, rows - 1, {})

    assert "ADX趋势强+多周期看多" in confirmations
