"""As-of-safe scoring for individual composite signal events.

The numeric result is an evidence score, not a calibrated probability.  Signal
generation still requires independent confirmation buckets; the per-bar trend
score only preserves information that the former bucket-only mapping discarded.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

import pandas as pd


SIGNAL_EVIDENCE_SCORE_SEMANTICS = "signal_evidence_score_not_probability"
SIGNAL_EVIDENCE_SCORE_VERSION = "signal-evidence-v2"
INDEPENDENT_BUCKET_WEIGHT = 0.60
DIRECTIONAL_TREND_WEIGHT = 0.40


@dataclass(frozen=True)
class SignalEvidenceScore:
    score: float
    independent_bucket_count: int
    bucket_score: float
    directional_trend_score: float
    trend_evidence_status: str
    version: str = SIGNAL_EVIDENCE_SCORE_VERSION
    semantics: str = SIGNAL_EVIDENCE_SCORE_SEMANTICS


def signal_evidence_level(score: float) -> str:
    """Map the continuous evidence score to a display label only."""

    if score >= 75:
        return "强"
    if score >= 65:
        return "中强"
    if score >= 50:
        return "中"
    return "弱"


def score_signal_evidence(
    independent_bucket_count: int,
    trend_strength: float,
    direction: Literal["buy", "sell"],
    *,
    trend_evidence_available: bool = True,
) -> SignalEvidenceScore:
    """Combine independent evidence breadth with as-of directional trend.

    The bucket component intentionally remains dominant because it prevents
    correlated indicators from receiving multiple full votes.  Trend strength
    is calculated at the signal bar and supplies continuous, direction-aware
    variation without using today's total score for a historical event.
    """

    if (
        isinstance(independent_bucket_count, bool)
        or not isinstance(independent_bucket_count, int)
        or independent_bucket_count < 0
    ):
        raise ValueError("independent_bucket_count must be a non-negative integer")
    if not isinstance(trend_strength, (int, float)) or isinstance(trend_strength, bool):
        raise ValueError("trend_strength must be a finite number in 0..100")
    normalized_trend = float(trend_strength)
    if not 0 <= normalized_trend <= 100:
        raise ValueError("trend_strength must be a finite number in 0..100")
    if direction not in ("buy", "sell"):
        raise ValueError("direction must be buy or sell")

    bucket_score = min(95.0, 30.0 + independent_bucket_count * 14.0)
    directional_trend = (
        normalized_trend if direction == "buy" else 100.0 - normalized_trend
    )
    if trend_evidence_available:
        score = min(
            95.0,
            max(
                0.0,
                bucket_score * INDEPENDENT_BUCKET_WEIGHT
                + directional_trend * DIRECTIONAL_TREND_WEIGHT,
            ),
        )
        trend_evidence_status = "available"
    else:
        score = bucket_score * INDEPENDENT_BUCKET_WEIGHT
        trend_evidence_status = "insufficient_history"
    return SignalEvidenceScore(
        score=round(score, 1),
        independent_bucket_count=independent_bucket_count,
        bucket_score=round(bucket_score, 1),
        directional_trend_score=round(directional_trend, 1),
        trend_evidence_status=trend_evidence_status,
    )


def online_trend_strength(
    df: pd.DataFrame,
    i: int,
    benchmark_df: Optional[pd.DataFrame] = None,
) -> int:
    """Return the existing 0..100 trend score using data available at bar ``i``."""

    if i < 60:
        return 50

    score = 45

    if all(column in df.columns for column in ("EMA_8", "EMA_13", "EMA_21", "EMA_55")):
        e8 = df["EMA_8"].iloc[i]
        e13 = df["EMA_13"].iloc[i]
        e21 = df["EMA_21"].iloc[i]
        e55 = df["EMA_55"].iloc[i]
        if all(not pd.isna(value) for value in (e8, e13, e21, e55)):
            if e8 > e13 > e21:
                score += 12
                if e21 > e55:
                    score += 3
            elif e8 < e13 < e21:
                score -= 12
                if e21 < e55:
                    score -= 3

    if "MACD" in df.columns and "MACD_signal" in df.columns and i >= 1:
        macd = df["MACD"].iloc[i]
        signal = df["MACD_signal"].iloc[i]
        previous_macd = df["MACD"].iloc[i - 1]
        previous_signal = df["MACD_signal"].iloc[i - 1]
        histogram = df["MACD_hist"].iloc[i] if "MACD_hist" in df.columns else 0
        previous_histogram = (
            df["MACD_hist"].iloc[i - 1] if "MACD_hist" in df.columns else 0
        )
        if not pd.isna(macd) and not pd.isna(signal):
            if macd > signal and previous_macd <= previous_signal:
                score += 12 + (3 if abs(histogram) > abs(previous_histogram) else 0)
            elif macd < signal and previous_macd >= previous_signal:
                score -= 12 + (3 if abs(histogram) > abs(previous_histogram) else 0)

    if all(column in df.columns for column in ("KDJ_K", "KDJ_D", "KDJ_J")) and i >= 1:
        k = df["KDJ_K"].iloc[i]
        d = df["KDJ_D"].iloc[i]
        j = df["KDJ_J"].iloc[i]
        previous_k = df["KDJ_K"].iloc[i - 1]
        previous_d = df["KDJ_D"].iloc[i - 1]
        if not pd.isna(j):
            kdj_strength = max(-3, min(3, (j - 50) / 25))
            score += int(kdj_strength * 2)
            if k > d and previous_k <= previous_d and k < 50:
                score += 4
            elif k < d and previous_k >= previous_d and k > 50:
                score -= 4

    if all(column in df.columns for column in ("BB_upper", "BB_middle", "BB_lower")):
        close = df["Close"].iloc[i]
        upper = df["BB_upper"].iloc[i]
        middle = df["BB_middle"].iloc[i]
        lower = df["BB_lower"].iloc[i]
        if not pd.isna(upper):
            if close > upper:
                score += 5
            elif close < lower:
                score -= 5
            elif close > middle:
                score += 2
            else:
                score -= 2

    if "Volume" in df.columns and i >= 19:
        volume_5 = df["Volume"].iloc[i - 4 : i + 1].mean()
        volume_20 = df["Volume"].iloc[i - 19 : i + 1].mean()
        price_up_5d = df["Close"].iloc[i] > df["Close"].iloc[i - 5]
        if volume_20 > 0:
            volume_ratio = volume_5 / volume_20
            if price_up_5d and volume_ratio > 1.2:
                score += 6
            elif not price_up_5d and volume_ratio < 0.8:
                score += 2
            elif price_up_5d and volume_ratio < 0.8:
                score -= 4
            elif not price_up_5d and volume_ratio > 1.2:
                score -= 6

    momentum_5 = df["Close"].iloc[i] / df["Close"].iloc[i - 5] - 1
    momentum_60 = df["Close"].iloc[i] / df["Close"].iloc[i - 60] - 1
    if benchmark_df is not None and len(benchmark_df) >= i + 1:
        benchmark_5 = benchmark_df["Close"].iloc[i] / benchmark_df["Close"].iloc[i - 5] - 1
        benchmark_60 = (
            benchmark_df["Close"].iloc[i] / benchmark_df["Close"].iloc[i - 60] - 1
        )
        if momentum_5 - benchmark_5 > 0.02:
            score += 5
        elif momentum_5 - benchmark_5 < -0.02:
            score -= 5
        if momentum_60 - benchmark_60 > 0.05:
            score += 3
        elif momentum_60 - benchmark_60 < -0.05:
            score -= 3
    else:
        if momentum_5 > 0.03:
            score += 5
        elif momentum_5 < -0.03:
            score -= 5
        if momentum_60 > 0.10:
            score += 3
        elif momentum_60 < -0.10:
            score -= 3

    return max(0, min(100, score))
