from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
import math

import pandas as pd

from custom_model.application.data_gateway import DataGateway
from custom_model.domain.models import DataEnvelope, DataQuery


MARKET_BARS_SCHEMA_VERSION = "market-bars-view/v1"


class MarketViewInputError(ValueError):
    """Raised when an envelope cannot be exposed as a finite typed market view."""


@dataclass(frozen=True)
class CoreIndicatorProfile:
    name: str
    version: str
    formula_source: str
    ema_periods: tuple[int, ...]
    macd_fast: int
    macd_slow: int
    macd_signal: int
    bollinger_period: int
    bollinger_std_dev: float
    kdj_n: int
    kdj_m1: int
    kdj_m2: int
    rsi_period: int
    atr_period: int


CORE_INDICATOR_PROFILE = CoreIndicatorProfile(
    name="legacy-core",
    version="1.0.0",
    formula_source="modules.technical.add_all_indicators",
    ema_periods=(8, 13, 21, 55, 144, 169, 288, 338),
    macd_fast=12,
    macd_slow=26,
    macd_signal=9,
    bollinger_period=20,
    bollinger_std_dev=2.0,
    kdj_n=9,
    kdj_m1=3,
    kdj_m2=3,
    rsi_period=14,
    atr_period=14,
)


@dataclass(frozen=True)
class IndicatorPoint:
    timestamp: datetime
    value: float


@dataclass(frozen=True)
class IndicatorSeries:
    points: tuple[IndicatorPoint, ...]


@dataclass(frozen=True)
class EmaIndicatorSet:
    ema8: IndicatorSeries
    ema13: IndicatorSeries
    ema21: IndicatorSeries
    ema55: IndicatorSeries
    ema144: IndicatorSeries
    ema169: IndicatorSeries
    ema288: IndicatorSeries
    ema338: IndicatorSeries


@dataclass(frozen=True)
class MacdIndicatorSet:
    dif: IndicatorSeries
    dea: IndicatorSeries
    hist: IndicatorSeries


@dataclass(frozen=True)
class BollingerIndicatorSet:
    mid: IndicatorSeries
    upper: IndicatorSeries
    lower: IndicatorSeries


@dataclass(frozen=True)
class KdjIndicatorSet:
    k: IndicatorSeries
    d: IndicatorSeries
    j: IndicatorSeries


@dataclass(frozen=True)
class CoreIndicators:
    ema: EmaIndicatorSet
    macd: MacdIndicatorSet
    bollinger: BollingerIndicatorSet
    kdj: KdjIndicatorSet
    rsi: IndicatorSeries
    atr: IndicatorSeries


@dataclass(frozen=True)
class MarketBarsViewResult:
    schema_version: str
    data_fingerprint: str
    data: DataEnvelope
    indicator_profile: CoreIndicatorProfile
    indicators: CoreIndicators


class CoreIndicatorMapper:
    """Map the legacy technical engine output to explicit finite native series."""

    def __init__(self, profile: CoreIndicatorProfile = CORE_INDICATOR_PROFILE) -> None:
        self.profile = profile

    def prepare(self, frame: pd.DataFrame) -> pd.DataFrame:
        from modules.technical import add_all_indicators

        prepared = add_all_indicators(frame)
        # The current legacy default keeps a reduced outer tunnel. Preserve its
        # exact EMA formula for the historical full-tunnel periods required by
        # the shared contract.
        close = prepared["Close"]
        for period in self.profile.ema_periods:
            column = f"EMA_{period}"
            if column not in prepared.columns:
                prepared[column] = close.ewm(span=period, adjust=False).mean()
        return prepared

    def map(self, prepared: pd.DataFrame) -> CoreIndicators:
        return CoreIndicators(
            ema=EmaIndicatorSet(
                ema8=self._series(prepared, "EMA_8"),
                ema13=self._series(prepared, "EMA_13"),
                ema21=self._series(prepared, "EMA_21"),
                ema55=self._series(prepared, "EMA_55"),
                ema144=self._series(prepared, "EMA_144"),
                ema169=self._series(prepared, "EMA_169"),
                ema288=self._series(prepared, "EMA_288"),
                ema338=self._series(prepared, "EMA_338"),
            ),
            macd=MacdIndicatorSet(
                dif=self._series(prepared, "MACD"),
                dea=self._series(prepared, "MACD_signal"),
                hist=self._series(prepared, "MACD_hist"),
            ),
            bollinger=BollingerIndicatorSet(
                mid=self._series(prepared, "BB_middle"),
                upper=self._series(prepared, "BB_upper"),
                lower=self._series(prepared, "BB_lower"),
            ),
            kdj=KdjIndicatorSet(
                k=self._series(prepared, "KDJ_K"),
                d=self._series(prepared, "KDJ_D"),
                j=self._series(prepared, "KDJ_J"),
            ),
            rsi=self._series(prepared, "RSI"),
            atr=self._series(prepared, "ATR"),
        )

    @staticmethod
    def _series(frame: pd.DataFrame, column: str) -> IndicatorSeries:
        if column not in frame.columns:
            raise MarketViewInputError(f"required indicator column is missing: {column}")

        points: list[IndicatorPoint] = []
        for raw_timestamp, raw_value in frame[column].items():
            try:
                value = float(raw_value)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(value):
                # Warm-up NaNs are expected for rolling indicators. They are
                # absent from the wire representation instead of becoming null,
                # NaN, Infinity, or a fabricated numeric value.
                continue

            timestamp = (
                raw_timestamp.to_pydatetime()
                if isinstance(raw_timestamp, pd.Timestamp)
                else raw_timestamp
            )
            if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
                raise MarketViewInputError("indicator timestamps must be timezone-aware")
            points.append(IndicatorPoint(timestamp=timestamp, value=value))
        return IndicatorSeries(points=tuple(points))


class MarketBarsViewService:
    """Fetch one envelope and expose the canonical legacy-core indicator view."""

    def __init__(
        self,
        data_gateway: DataGateway,
        mapper: CoreIndicatorMapper | None = None,
    ) -> None:
        self.data_gateway = data_gateway
        self.mapper = mapper or CoreIndicatorMapper()

    def query(self, query: DataQuery) -> MarketBarsViewResult:
        return self.build_view(self.data_gateway.fetch_bars(query))

    def build_view(self, envelope: DataEnvelope) -> MarketBarsViewResult:
        self._validate_finite_envelope(envelope)
        frame = self.frame_from_envelope(envelope)
        prepared = self.mapper.prepare(frame)
        indicators = self.mapper.map(prepared)
        return MarketBarsViewResult(
            schema_version=MARKET_BARS_SCHEMA_VERSION,
            data_fingerprint=self.fingerprint(envelope, self.mapper.profile),
            data=envelope,
            indicator_profile=self.mapper.profile,
            indicators=indicators,
        )

    @staticmethod
    def frame_from_envelope(envelope: DataEnvelope) -> pd.DataFrame:
        records = [
            {
                "Date": bar.timestamp,
                "Open": bar.open,
                "High": bar.high,
                "Low": bar.low,
                "Close": bar.close,
                "Volume": bar.volume,
                "Amount": bar.amount,
            }
            for bar in envelope.bars
        ]
        if not records:
            raise MarketViewInputError("at least one bar is required")
        frame = pd.DataFrame.from_records(records).set_index("Date").sort_index()
        if frame["Amount"].isna().all():
            frame = frame.drop(columns=["Amount"])
        return frame

    @staticmethod
    def _validate_finite_envelope(envelope: DataEnvelope) -> None:
        for index, bar in enumerate(envelope.bars):
            values = {
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
                "amount": bar.amount,
            }
            for name, value in values.items():
                if value is not None and not math.isfinite(float(value)):
                    raise MarketViewInputError(
                        f"bar {index} contains non-finite {name}"
                    )
        if not math.isfinite(float(envelope.freshness_seconds)):
            raise MarketViewInputError("envelope contains non-finite freshness_seconds")

    @staticmethod
    def fingerprint(
        envelope: DataEnvelope,
        profile: CoreIndicatorProfile,
    ) -> str:
        # Transport/provenance state is intentionally excluded: the same
        # adjusted series can be returned as ``delayed`` on the first request
        # and ``cache`` on the next one.  Consumers validate provider, mode,
        # quality and source_chain separately; this hash answers only whether
        # two views contain the identical market-data/indicator input.
        payload = {
            "instrument": envelope.instrument.model_dump(mode="json"),
            "timeframe": envelope.timeframe.value,
            "adjustment": envelope.adjustment.value,
            "timezone": envelope.timezone,
            "is_synthetic": envelope.is_synthetic,
            "bars": [bar.model_dump(mode="json") for bar in envelope.bars],
            "indicator_profile": asdict(profile),
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(canonical).hexdigest()}"

    # Transitional compatibility for callers created before ``fingerprint``
    # became a public content-identity helper.
    _fingerprint = fingerprint
