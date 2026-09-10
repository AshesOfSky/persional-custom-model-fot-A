from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
import hashlib
import json
import math
import re
from typing import Any, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _require_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


def _canonical_json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class Market(str, Enum):
    CN = "CN"
    HK = "HK"
    US = "US"
    GLOBAL = "GLOBAL"


class Exchange(str, Enum):
    SSE = "SSE"
    SZSE = "SZSE"
    BSE = "BSE"
    HKEX = "HKEX"
    NASDAQ = "NASDAQ"
    NYSE = "NYSE"
    AMEX = "AMEX"
    OTHER = "OTHER"


class AssetType(str, Enum):
    STOCK = "stock"
    ETF = "etf"
    INDEX = "index"
    SECTOR_INDEX = "sector_index"
    FUND = "fund"
    FUTURES = "futures"
    FX = "fx"
    UNKNOWN = "unknown"


class Timeframe(str, Enum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    D1 = "1d"
    W1 = "1w"
    MO1 = "1mo"


class Adjustment(str, Enum):
    FORWARD = "forward"
    BACKWARD = "backward"
    NONE = "none"


class DataPurpose(str, Enum):
    RESEARCH = "research"
    SCREENER = "screener"
    ALERT = "alert"
    REPORT = "report"
    BACKTEST = "backtest"
    DEMO = "demo"


class DataMode(str, Enum):
    LIVE = "live"
    DELAYED = "delayed"
    CACHE = "cache"
    FALLBACK = "fallback"
    DEMO = "demo"


class QualityStatus(str, Enum):
    VALID = "valid"
    STALE = "stale"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class QuoteSessionStatus(str, Enum):
    """Conservative local-session classification for a market snapshot."""

    OPEN = "open"
    MIDDAY_BREAK = "midday_break"
    AFTER_CLOSE = "after_close"
    PRE_OPEN = "pre_open"
    NON_TRADING_DAY_UNVERIFIED = "non_trading_day_unverified"


class QuoteTemporalMode(str, Enum):
    """Whether a quote is an exact point-in-time lookup or a latest snapshot."""

    EXACT = "exact"
    LATEST = "latest"


class FundamentalEvidenceBasis(str, Enum):
    MARKET_SNAPSHOT = "market_snapshot"
    FINANCIAL_STATEMENT = "financial_statement"
    DERIVED_FINANCIAL_STATEMENT = "derived_financial_statement"
    PROVIDER_METADATA = "provider_metadata"


class FundamentalFieldStatus(str, Enum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"


class SignalStatus(str, Enum):
    PROVISIONAL = "provisional"
    CONFIRMED = "confirmed"


class AlertDeliveryStatus(str, Enum):
    """Authoritative SQLite outbox state for an alert delivery."""

    PENDING = "pending"
    CLAIMED = "claimed"
    RETRY_SCHEDULED = "retry_scheduled"
    FAILED = "failed"
    DELIVERED = "delivered"
    UNCERTAIN = "uncertain"
    SUPPRESSED = "suppressed"


class AlertEvidenceStatus(str, Enum):
    """Whether an event can be used as a formally traceable alert record."""

    VERIFIED = "verified"
    LEGACY_UNVERIFIED = "legacy_unverified"


class MonitoringTier(str, Enum):
    """Quota-aware rule scheduling tiers shared by API and Worker."""

    CORE = "core_60s"
    WATCHLIST = "watchlist_5m"
    SECTOR = "sector_5m"
    SECTOR_CORE = "sector_core_60s"


class MonitorProfile(str, Enum):
    """Explicit scheduling profile for an independently managed monitor target."""

    STOCK_CORE_60S = "stock_core_60s"
    SECTOR_CORE_60S = "sector_core_60s"
    SECTOR_STANDARD_300S = "sector_standard_300s"

    @property
    def interval_seconds(self) -> int:
        if self in {self.STOCK_CORE_60S, self.SECTOR_CORE_60S}:
            return 60
        return 300


class FetchOrigin(str, Enum):
    """Why the shared quote coordinator requested a provider snapshot."""

    SCHEDULED = "scheduled"
    MANUAL_REFRESH = "manual_refresh"
    RESONANCE_SCAN = "resonance_scan"


class QuoteBatchRoute(str, Enum):
    """Provider-neutral route family; one upstream request may use only one family."""

    EQUITY_SNAPSHOT = "equity_snapshot"
    INDEX_SNAPSHOT = "index_snapshot"


class DecisionAction(str, Enum):
    LONG = "long"
    HOLD = "hold"
    REDUCE = "reduce"
    EXIT = "exit"
    CASH = "cash"


class MarketRegimeState(str, Enum):
    RISK_ON = "risk_on"
    NEUTRAL = "neutral"
    RISK_OFF = "risk_off"
    UNKNOWN = "unknown"


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class InstrumentId(FrozenModel):
    symbol: str = Field(min_length=1, max_length=32)
    exchange: Exchange
    market: Market
    asset_type: AssetType
    currency: str = Field(default="CNY", min_length=3, max_length=3)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("symbol cannot be blank")
        return normalized

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.strip().upper()

    @property
    def key(self) -> str:
        return f"{self.market.value}:{self.exchange.value}:{self.asset_type.value}:{self.symbol}"


class MonitorTarget(FrozenModel):
    """One explicit monitoring assignment, separate from watchlist organization."""

    target_id: str = Field(min_length=1, max_length=64)
    instrument: InstrumentId
    profile: MonitorProfile
    display_name: str = Field(min_length=1, max_length=128)
    note: str = Field(default="", max_length=256)
    enabled: bool = True

    @field_validator("target_id", "display_name", "note")
    @classmethod
    def normalized_monitor_target_text(cls, value: str, info: Any) -> str:
        normalized = value.strip()
        if info.field_name != "note" and not normalized:
            raise ValueError(f"{info.field_name} cannot be blank")
        return normalized

    @model_validator(mode="after")
    def admitted_monitor_profile(self) -> "MonitorTarget":
        instrument = self.instrument
        if instrument.market is not Market.CN or instrument.currency != "CNY":
            raise ValueError("monitor profile requires a CNY instrument in market CN")
        if self.profile is MonitorProfile.STOCK_CORE_60S:
            if (
                instrument.asset_type is not AssetType.STOCK
                or instrument.exchange not in {Exchange.SSE, Exchange.SZSE, Exchange.BSE}
            ):
                raise ValueError("stock monitor profile requires an A-share stock")
            return self
        if (
            instrument.asset_type is not AssetType.SECTOR_INDEX
            or instrument.exchange is not Exchange.OTHER
            or re.fullmatch(r"\d{6}\.TI", instrument.symbol) is None
        ):
            raise ValueError(
                "sector monitor profile requires an exact six-digit .TI sector_index"
            )
        return self

    @property
    def interval_seconds(self) -> int:
        return self.profile.interval_seconds


class DataQuery(FrozenModel):
    instrument: InstrumentId
    timeframe: Timeframe
    purpose: DataPurpose = DataPurpose.RESEARCH
    adjustment: Adjustment = Adjustment.FORWARD
    start: datetime | None = None
    end: datetime | None = None
    as_of: datetime = Field(default_factory=utc_now)

    @field_validator("start", "end", "as_of")
    @classmethod
    def aware_datetimes(cls, value: datetime | None, info: Any) -> datetime | None:
        if value is None:
            return None
        return _require_aware(value, info.field_name)

    @model_validator(mode="after")
    def valid_range(self) -> "DataQuery":
        if self.start and self.end and self.end < self.start:
            raise ValueError("end must be on or after start")
        return self


class Bar(FrozenModel):
    timestamp: datetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)
    amount: float | None = Field(default=None, ge=0)

    @field_validator("timestamp")
    @classmethod
    def aware_timestamp(cls, value: datetime) -> datetime:
        return _require_aware(value, "timestamp")

    @model_validator(mode="after")
    def valid_ohlc(self) -> "Bar":
        if self.high < max(self.open, self.low, self.close):
            raise ValueError("high is below another OHLC value")
        if self.low > min(self.open, self.high, self.close):
            raise ValueError("low is above another OHLC value")
        return self


class DataLineage(FrozenModel):
    provider: str = Field(min_length=1)
    mode: DataMode
    fetched_at: datetime
    last_bar_at: datetime | None = None
    adjustment: Adjustment
    source_chain: list[str] = Field(default_factory=list)
    request_id: str = Field(default_factory=lambda: uuid4().hex)

    @field_validator("fetched_at", "last_bar_at")
    @classmethod
    def aware_lineage_times(cls, value: datetime | None, info: Any) -> datetime | None:
        if value is None:
            return None
        return _require_aware(value, info.field_name)


class DataEnvelope(FrozenModel):
    instrument: InstrumentId
    timeframe: Timeframe
    purpose: DataPurpose
    bars: list[Bar]
    provider: str = Field(min_length=1)
    mode: DataMode
    quality: QualityStatus
    as_of: datetime
    fetched_at: datetime
    last_bar_at: datetime | None = None
    timezone: str = Field(min_length=1)
    adjustment: Adjustment
    freshness_seconds: float = Field(ge=0)
    is_synthetic: bool = False
    warnings: list[str] = Field(default_factory=list)
    request_id: str = Field(default_factory=lambda: uuid4().hex)
    source_chain: list[str] = Field(default_factory=list)
    calendar_snapshot_id: str | None = Field(default=None, min_length=1, max_length=128)
    calendar_content_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )

    @field_validator("provider")
    @classmethod
    def normalize_provider(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("provider cannot be blank")
        return normalized

    @field_validator("as_of", "fetched_at", "last_bar_at")
    @classmethod
    def aware_envelope_times(cls, value: datetime | None, info: Any) -> datetime | None:
        if value is None:
            return None
        return _require_aware(value, info.field_name)

    @model_validator(mode="after")
    def validate_provenance(self) -> "DataEnvelope":
        timestamps = [bar.timestamp for bar in self.bars]
        if timestamps != sorted(timestamps) or len(timestamps) != len(set(timestamps)):
            raise ValueError("bars must be unique and sorted by timestamp")
        if self.bars and self.last_bar_at != self.bars[-1].timestamp:
            raise ValueError("last_bar_at must match the final bar")
        if self.is_synthetic and self.mode is not DataMode.DEMO:
            raise ValueError("synthetic data must use demo mode")
        if self.is_synthetic and self.purpose is not DataPurpose.DEMO:
            raise ValueError("synthetic data is only valid for demo purpose")
        if (self.calendar_snapshot_id is None) != (
            self.calendar_content_sha256 is None
        ):
            raise ValueError("calendar snapshot id and digest must be supplied together")
        return self

    def lineage(self) -> DataLineage:
        return DataLineage(
            provider=self.provider,
            mode=self.mode,
            fetched_at=self.fetched_at,
            last_bar_at=self.last_bar_at,
            adjustment=self.adjustment,
            source_chain=self.source_chain,
            request_id=self.request_id,
        )


class QuoteQuery(FrozenModel):
    """Explicit temporal request for a native provider quote snapshot."""

    instrument: InstrumentId
    purpose: DataPurpose = DataPurpose.RESEARCH
    temporal_mode: QuoteTemporalMode = QuoteTemporalMode.EXACT
    as_of: datetime | None = None
    requested_at: datetime | None = None
    max_live_age_seconds: float = Field(default=90.0, gt=0, le=900)

    @field_validator("as_of", "requested_at")
    @classmethod
    def aware_quote_query_times(cls, value: datetime | None, info: Any) -> datetime | None:
        if value is None:
            return None
        return _require_aware(value, info.field_name)

    @model_validator(mode="after")
    def valid_temporal_request(self) -> "QuoteQuery":
        if self.temporal_mode is QuoteTemporalMode.EXACT:
            if self.as_of is None:
                raise ValueError("exact quote query requires as_of")
            if self.requested_at is not None:
                raise ValueError("exact quote query must not set requested_at")
        else:
            if self.as_of is not None:
                raise ValueError("latest quote query must not set as_of")
            if self.requested_at is None:
                raise ValueError("latest quote query requires requested_at")
        return self


class QuoteBatchQuery(FrozenModel):
    """Latest native quote request whose instruments share one provider route."""

    instruments: tuple[InstrumentId, ...]
    purpose: DataPurpose = DataPurpose.RESEARCH
    requested_at: datetime
    origin: FetchOrigin = FetchOrigin.SCHEDULED
    max_live_age_seconds: float = Field(default=90.0, gt=0, le=900)

    @field_validator("requested_at")
    @classmethod
    def aware_batch_requested_at(cls, value: datetime) -> datetime:
        return _require_aware(value, "requested_at")

    @model_validator(mode="after")
    def one_unique_route(self) -> "QuoteBatchQuery":
        if not self.instruments:
            raise ValueError("quote batch requires at least one instrument")
        if len(self.instruments) > 100:
            raise ValueError("quote batch cannot exceed 100 instruments")
        keys = [instrument.key for instrument in self.instruments]
        if len(keys) != len(set(keys)):
            raise ValueError("quote batch cannot contain duplicate instruments")
        routes = {self._route_for(instrument) for instrument in self.instruments}
        if len(routes) != 1:
            raise ValueError("quote batch requires a single provider route")
        return self

    @staticmethod
    def _route_for(instrument: InstrumentId) -> QuoteBatchRoute:
        if instrument.asset_type in {AssetType.STOCK, AssetType.ETF}:
            return QuoteBatchRoute.EQUITY_SNAPSHOT
        if instrument.asset_type in {AssetType.INDEX, AssetType.SECTOR_INDEX}:
            return QuoteBatchRoute.INDEX_SNAPSHOT
        raise ValueError(
            f"unsupported quote batch asset type: {instrument.asset_type.value}"
        )

    @property
    def route(self) -> QuoteBatchRoute:
        return self._route_for(self.instruments[0])


class QuoteEnvelope(FrozenModel):
    """One native quote plus explicit source, time and admission evidence."""

    instrument: InstrumentId
    purpose: DataPurpose
    display_name: str = Field(min_length=1, max_length=128)
    provider: str = Field(min_length=1, max_length=128)
    mode: DataMode
    quality: QualityStatus
    temporal_mode: QuoteTemporalMode
    requested_at: datetime | None
    as_of: datetime
    fetched_at: datetime
    data_time: datetime
    provider_updated_at: datetime
    data_time_basis: Literal[
        "provider_timestamp",
        "cn_midday_close",
        "cn_session_close",
        "cn_prior_session_close",
    ]
    timezone: str = Field(min_length=1, max_length=128)
    currency: str = Field(min_length=3, max_length=3)
    freshness_seconds: float = Field(ge=0, allow_inf_nan=False)
    session_status: QuoteSessionStatus
    trade_status_code: int = Field(ge=0)
    trade_status_verified: bool
    last_price: float = Field(gt=0, allow_inf_nan=False)
    previous_close: float = Field(gt=0, allow_inf_nan=False)
    open: float = Field(gt=0, allow_inf_nan=False)
    high: float = Field(gt=0, allow_inf_nan=False)
    low: float = Field(gt=0, allow_inf_nan=False)
    change: float = Field(allow_inf_nan=False)
    change_percent: float = Field(allow_inf_nan=False)
    volume_lots: float = Field(ge=0, allow_inf_nan=False)
    amount: float = Field(ge=0, allow_inf_nan=False)
    turnover_rate: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    amplitude: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    pe_dynamic: float | None = Field(default=None, allow_inf_nan=False)
    pb: float | None = Field(default=None, allow_inf_nan=False)
    total_market_cap: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    float_market_cap: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    warnings: list[str] = Field(default_factory=list)
    is_synthetic: bool
    source_chain: list[str] = Field(min_length=1)
    request_id: str = Field(default_factory=lambda: uuid4().hex)
    calendar_snapshot_id: str | None = Field(default=None, min_length=1, max_length=128)
    calendar_content_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )

    @field_validator("display_name", "provider")
    @classmethod
    def known_labels(cls, value: str) -> str:
        normalized = value.strip()
        if normalized.lower() in {"", "unknown", "none", "n/a"}:
            raise ValueError("quote labels cannot be unknown")
        return normalized

    @field_validator(
        "requested_at", "as_of", "fetched_at", "data_time", "provider_updated_at"
    )
    @classmethod
    def aware_quote_times(cls, value: datetime | None, info: Any) -> datetime | None:
        if value is None:
            return None
        return _require_aware(value, info.field_name)

    @field_validator("timezone")
    @classmethod
    def valid_quote_timezone(cls, value: str) -> str:
        normalized = value.strip()
        try:
            ZoneInfo(normalized)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return normalized

    @field_validator("currency")
    @classmethod
    def normalized_quote_currency(cls, value: str) -> str:
        normalized = value.strip().upper()
        if len(normalized) != 3 or not normalized.isalpha():
            raise ValueError("currency must be a three-letter code")
        return normalized

    @field_validator("source_chain")
    @classmethod
    def known_quote_source_chain(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(item.lower() in {"", "unknown", "none", "n/a"} for item in normalized):
            raise ValueError("source_chain cannot contain an unknown source")
        return normalized

    @field_validator("warnings")
    @classmethod
    def normalized_quote_warnings(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))

    @model_validator(mode="after")
    def valid_quote_evidence(self) -> "QuoteEnvelope":
        if self.as_of > self.fetched_at:
            raise ValueError("quote as_of cannot be after fetched_at")
        if self.temporal_mode is QuoteTemporalMode.EXACT:
            if self.requested_at is not None:
                raise ValueError("exact quote must not set requested_at")
        else:
            if self.requested_at is None:
                raise ValueError("latest quote requires requested_at")
            if self.requested_at > self.as_of:
                raise ValueError("latest quote requested_at cannot be after as_of")
            if self.as_of != self.fetched_at:
                raise ValueError("latest quote as_of must equal fetch completion time")
        if self.data_time > self.as_of:
            raise ValueError("quote data_time cannot be after as_of")
        if self.provider_updated_at > self.as_of:
            raise ValueError("quote provider_updated_at cannot be after as_of")
        computed_freshness = (self.as_of - self.data_time).total_seconds()
        if abs(computed_freshness - self.freshness_seconds) > 1.0:
            raise ValueError("freshness_seconds must match as_of minus data_time")
        if self.is_synthetic:
            raise ValueError("native quote snapshots cannot be synthetic")
        if self.mode not in {DataMode.LIVE, DataMode.DELAYED}:
            raise ValueError("native quote mode must be live or delayed")
        if self.quality not in {QualityStatus.VALID, QualityStatus.STALE}:
            raise ValueError("native quote quality must be valid or stale")
        if self.currency != self.instrument.currency:
            raise ValueError("quote currency must match instrument currency")
        if self.high < max(self.open, self.low, self.last_price):
            raise ValueError("quote high is below another price field")
        if self.low > min(self.open, self.high, self.last_price):
            raise ValueError("quote low is above another price field")
        expected_change = self.last_price - self.previous_close
        if abs(self.change - expected_change) > 0.011:
            raise ValueError("quote change is inconsistent with price and previous_close")
        expected_percent = expected_change / self.previous_close * 100.0
        if abs(self.change_percent - expected_percent) > 0.051:
            raise ValueError("quote change_percent is inconsistent with prices")
        if self.mode is DataMode.LIVE and (
            self.session_status is not QuoteSessionStatus.OPEN
            or self.quality is not QualityStatus.VALID
        ):
            raise ValueError("live quote must be valid and inside an open session")
        if (self.calendar_snapshot_id is None) != (
            self.calendar_content_sha256 is None
        ):
            raise ValueError("calendar snapshot id and digest must be supplied together")
        return self


class QuoteSnapshotEvidence(FrozenModel):
    """Minimal source-time evidence persisted for one monitored quote snapshot."""

    instrument: InstrumentId
    provider: str = Field(min_length=1, max_length=128)
    data_time: datetime
    provider_updated_at: datetime
    fetched_at: datetime
    freshness_seconds: float = Field(ge=0, allow_inf_nan=False)
    request_id: str = Field(min_length=1, max_length=256)
    origin: FetchOrigin
    cache_hit: bool

    @field_validator("provider", "request_id")
    @classmethod
    def known_snapshot_labels(cls, value: str, info: Any) -> str:
        normalized = value.strip()
        if normalized.lower() in {"", "unknown", "none", "n/a"}:
            raise ValueError(f"{info.field_name} cannot be unknown")
        return normalized

    @field_validator("data_time", "provider_updated_at", "fetched_at")
    @classmethod
    def aware_snapshot_times(cls, value: datetime, info: Any) -> datetime:
        return _require_aware(value, info.field_name)

    @model_validator(mode="after")
    def coherent_snapshot_times(self) -> "QuoteSnapshotEvidence":
        if self.data_time > self.fetched_at:
            raise ValueError("data_time cannot be after fetched_at")
        if self.provider_updated_at > self.fetched_at:
            raise ValueError("provider_updated_at cannot be after fetched_at")
        computed = (self.fetched_at - self.data_time).total_seconds()
        if abs(computed - self.freshness_seconds) > 1.0:
            raise ValueError("freshness_seconds must match fetched_at minus data_time")
        return self

    @classmethod
    def from_quote(
        cls,
        quote: QuoteEnvelope,
        *,
        origin: FetchOrigin,
        cache_hit: bool,
    ) -> "QuoteSnapshotEvidence":
        return cls(
            instrument=quote.instrument,
            provider=quote.provider,
            data_time=quote.data_time,
            provider_updated_at=quote.provider_updated_at,
            fetched_at=quote.fetched_at,
            freshness_seconds=(quote.fetched_at - quote.data_time).total_seconds(),
            request_id=quote.request_id,
            origin=origin,
            cache_hit=cache_hit,
        )


class ManualRefreshResult(FrozenModel):
    """A current-instrument refresh that cannot create monitoring side effects."""

    quote: QuoteEnvelope
    provider_unchanged: bool
    cooldown_seconds: int = Field(ge=0, le=3600)
    origin: Literal[FetchOrigin.MANUAL_REFRESH] = FetchOrigin.MANUAL_REFRESH
    cache_bypassed: Literal[True] = True
    alert_event_created: Literal[False] = False
    external_notification_sent: Literal[False] = False

    @model_validator(mode="after")
    def latest_quote_only(self) -> "ManualRefreshResult":
        if self.quote.temporal_mode is not QuoteTemporalMode.LATEST:
            raise ValueError("manual refresh requires a latest quote")
        return self


def _validate_json_evidence(value: JsonValue, field_name: str) -> JsonValue:
    """Reject values that cannot be trusted or serialized as evidence."""
    if value is None:
        raise ValueError(f"{field_name} cannot be null")
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            raise ValueError(f"{field_name} must be finite")
        return value
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{field_name} cannot be blank")
        return normalized
    if isinstance(value, list):
        if not value:
            raise ValueError(f"{field_name} cannot be empty")
        return [
            _validate_json_evidence(item, f"{field_name}[{index}]")
            for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        if not value:
            raise ValueError(f"{field_name} cannot be empty")
        normalized: dict[str, JsonValue] = {}
        for key, item in value.items():
            native_key = str(key).strip()
            if not native_key:
                raise ValueError(f"{field_name} contains a blank key")
            normalized[native_key] = _validate_json_evidence(
                item, f"{field_name}.{native_key}"
            )
        return normalized
    raise ValueError(f"{field_name} must be JSON-compatible evidence")


class FundamentalQuery(FrozenModel):
    """Point-in-time request for a provider-backed fundamental snapshot."""

    instrument: InstrumentId
    purpose: DataPurpose = DataPurpose.RESEARCH
    as_of: datetime = Field(default_factory=utc_now)
    max_market_age_seconds: float = Field(default=7 * 24 * 60 * 60, gt=0)
    max_financial_age_seconds: float = Field(default=200 * 24 * 60 * 60, gt=0)
    required_filter_fields: tuple[str, ...] = ("ROE", "debt_ratio")
    minimum_verified_financial_fields: int = Field(default=2, ge=1)

    @field_validator("as_of")
    @classmethod
    def aware_as_of(cls, value: datetime) -> datetime:
        return _require_aware(value, "as_of")

    @field_validator("required_filter_fields")
    @classmethod
    def normalized_required_fields(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if not normalized or any(not item for item in normalized):
            raise ValueError("required_filter_fields cannot be empty")
        if len(normalized) != len(set(normalized)):
            raise ValueError("required_filter_fields must be unique")
        return normalized


class FundamentalField(FrozenModel):
    """One canonical metric with its upstream value retained as evidence."""

    name: str = Field(min_length=1, max_length=128)
    source_field: str = Field(min_length=1, max_length=256)
    raw_value: JsonValue
    normalized_value: str | int | float | bool
    unit: str | None = Field(default=None, min_length=1, max_length=64)
    basis: FundamentalEvidenceBasis
    status: FundamentalFieldStatus
    period_end: datetime | None = None
    published_at: datetime | None = None
    market_time: datetime | None = None

    @field_validator("name", "source_field", "unit")
    @classmethod
    def normalize_labels(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("fundamental field labels cannot be blank")
        return normalized

    @field_validator("raw_value", "normalized_value")
    @classmethod
    def valid_evidence(cls, value: JsonValue, info: Any) -> JsonValue:
        return _validate_json_evidence(value, info.field_name)

    @field_validator("period_end", "published_at", "market_time")
    @classmethod
    def aware_field_times(cls, value: datetime | None, info: Any) -> datetime | None:
        if value is None:
            return None
        return _require_aware(value, info.field_name)

    @model_validator(mode="after")
    def valid_temporal_basis(self) -> "FundamentalField":
        if (
            self.period_end is not None
            and self.published_at is not None
            and self.published_at < self.period_end
        ):
            raise ValueError("published_at cannot be before period_end")
        if self.status is FundamentalFieldStatus.VERIFIED:
            if (
                self.basis is FundamentalEvidenceBasis.MARKET_SNAPSHOT
                and self.market_time is None
            ):
                raise ValueError("verified market field requires market_time")
            if self.basis in {
                FundamentalEvidenceBasis.FINANCIAL_STATEMENT,
                FundamentalEvidenceBasis.DERIVED_FINANCIAL_STATEMENT,
            } and (self.period_end is None or self.published_at is None):
                raise ValueError(
                    "verified financial field requires period_end and published_at"
                )
        return self


class FundamentalEnvelope(FrozenModel):
    """Provider payload with enough provenance to admit or reject formal use."""

    instrument: InstrumentId
    purpose: DataPurpose
    provider: str = Field(min_length=1, max_length=128)
    mode: DataMode
    quality: QualityStatus
    as_of: datetime
    fetched_at: datetime
    data_time: datetime
    data_time_basis: Literal["latest_field_evidence_time"] = (
        "latest_field_evidence_time"
    )
    timezone: str = Field(min_length=1, max_length=128)
    currency: str = Field(min_length=3, max_length=3)
    freshness_seconds: float = Field(ge=0)
    fields: list[FundamentalField] = Field(min_length=1)
    warnings: list[str] = Field(default_factory=list)
    is_synthetic: bool = False
    source_chain: list[str] = Field(min_length=1)
    request_id: str = Field(default_factory=lambda: uuid4().hex)

    @field_validator("provider")
    @classmethod
    def known_provider(cls, value: str) -> str:
        normalized = value.strip()
        if normalized.lower() in {"", "unknown", "none", "n/a"}:
            raise ValueError("provider cannot be unknown")
        return normalized

    @field_validator("as_of", "fetched_at", "data_time")
    @classmethod
    def aware_times(cls, value: datetime, info: Any) -> datetime:
        return _require_aware(value, info.field_name)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        normalized = value.strip()
        try:
            ZoneInfo(normalized)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return normalized

    @field_validator("currency")
    @classmethod
    def normalized_currency(cls, value: str) -> str:
        normalized = value.strip().upper()
        if len(normalized) != 3 or not normalized.isalpha():
            raise ValueError("currency must be a three-letter code")
        return normalized

    @field_validator("source_chain")
    @classmethod
    def known_source_chain(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(item.lower() in {"", "unknown", "none", "n/a"} for item in normalized):
            raise ValueError("source_chain cannot contain an unknown source")
        return normalized

    @field_validator("warnings")
    @classmethod
    def normalized_warnings(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))

    @model_validator(mode="after")
    def valid_provenance(self) -> "FundamentalEnvelope":
        if self.data_time > self.as_of:
            raise ValueError("data_time cannot be after as_of")
        computed_freshness = (self.as_of - self.data_time).total_seconds()
        if abs(computed_freshness - self.freshness_seconds) > 1.0:
            raise ValueError("freshness_seconds must match as_of minus data_time")
        names = [field.name for field in self.fields]
        if len(names) != len(set(names)):
            raise ValueError("fundamental field names must be unique")
        if self.is_synthetic and self.mode is not DataMode.DEMO:
            raise ValueError("synthetic fundamentals must use demo mode")
        if self.is_synthetic and self.purpose is not DataPurpose.DEMO:
            raise ValueError("synthetic fundamentals are only valid for demo purpose")
        for field in self.fields:
            for timestamp in (field.period_end, field.published_at, field.market_time):
                if timestamp is not None and timestamp > self.as_of:
                    raise ValueError(
                        f"fundamental field {field.name} has evidence after as_of"
                    )
        evidence_times = [
            timestamp
            for field in self.fields
            for timestamp in (field.published_at, field.market_time)
            if timestamp is not None
        ]
        if not evidence_times or self.data_time != max(evidence_times):
            raise ValueError(
                "data_time must equal the latest published_at or market_time"
            )
        return self


class FundamentalCoverage(FrozenModel):
    required_fields: list[str] = Field(min_length=1)
    verified_financial_fields: list[str]
    missing_required_fields: list[str]
    minimum_verified_financial_fields: int = Field(ge=1)
    passed: bool

    @model_validator(mode="after")
    def valid_coverage(self) -> "FundamentalCoverage":
        verified = set(self.verified_financial_fields)
        expected_missing = [
            field for field in self.required_fields if field not in verified
        ]
        expected_passed = (
            not expected_missing
            and len(verified) >= self.minimum_verified_financial_fields
        )
        if self.missing_required_fields != expected_missing:
            raise ValueError("missing_required_fields does not match verified coverage")
        if self.passed is not expected_passed:
            raise ValueError("coverage passed flag does not match field coverage")
        return self


class FundamentalSnapshot(FrozenModel):
    """Filter-only application result; it intentionally has no trade score."""

    snapshot_id: str = Field(default_factory=lambda: uuid4().hex)
    instrument: InstrumentId
    purpose: DataPurpose
    as_of: datetime
    engine_version: str = Field(min_length=1)
    data: FundamentalEnvelope
    coverage: FundamentalCoverage
    use_case: Literal["risk_and_quality_filter"] = "risk_and_quality_filter"
    formal_use_eligible: bool

    @field_validator("as_of")
    @classmethod
    def aware_snapshot_as_of(cls, value: datetime) -> datetime:
        return _require_aware(value, "as_of")

    @model_validator(mode="after")
    def matches_envelope(self) -> "FundamentalSnapshot":
        if self.instrument != self.data.instrument:
            raise ValueError("snapshot instrument must match fundamental data")
        if self.purpose is not self.data.purpose:
            raise ValueError("snapshot purpose must match fundamental data")
        if self.as_of != self.data.as_of:
            raise ValueError("snapshot as_of must match fundamental data")
        verified_financial = sorted(
            field.name
            for field in self.data.fields
            if field.status is FundamentalFieldStatus.VERIFIED
            and field.basis
            in {
                FundamentalEvidenceBasis.FINANCIAL_STATEMENT,
                FundamentalEvidenceBasis.DERIVED_FINANCIAL_STATEMENT,
            }
        )
        if self.coverage.verified_financial_fields != verified_financial:
            raise ValueError("snapshot coverage must match verified financial fields")
        expected_eligibility = (
            self.purpose is not DataPurpose.DEMO
            and self.data.mode in {DataMode.LIVE, DataMode.DELAYED, DataMode.CACHE}
            and self.data.quality is QualityStatus.VALID
            and not self.data.is_synthetic
            and self.coverage.passed
        )
        if self.formal_use_eligible is not expected_eligibility:
            raise ValueError("formal_use_eligible does not match fundamental data")
        return self


class ScoreComponent(FrozenModel):
    name: str = Field(min_length=1)
    score: float = Field(ge=0, le=100)
    weight: float = Field(ge=0, le=1)
    enabled: bool = True
    evidence: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class KeyLevel(FrozenModel):
    kind: str = Field(min_length=1)
    price: float = Field(gt=0)
    provenance: str = Field(min_length=1)
    status: SignalStatus = SignalStatus.PROVISIONAL


class Invalidation(FrozenModel):
    description: str = Field(min_length=1)
    price: float | None = Field(default=None, gt=0)
    confirmation: str = Field(min_length=1)


class MarketRegimeContext(FrozenModel):
    state: MarketRegimeState
    score: float = Field(ge=0, le=100)
    benchmark: InstrumentId
    as_of: datetime
    evidence: list[str] = Field(default_factory=list)

    @field_validator("as_of")
    @classmethod
    def aware_regime_time(cls, value: datetime) -> datetime:
        return _require_aware(value, "as_of")


class SectorOpportunity(FrozenModel):
    sector_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    score: float = Field(ge=0, le=100)
    trend: str = Field(min_length=1)
    eligible: bool
    support: float | None = Field(default=None, gt=0)
    resistance: float | None = Field(default=None, gt=0)
    evidence: list[str] = Field(default_factory=list)


class PairCandidate(FrozenModel):
    etf: InstrumentId
    leader: InstrumentId
    score: float = Field(ge=0, le=100)
    trend_aligned: bool
    liquidity_passed: bool
    capacity_passed: bool
    evidence: list[str] = Field(default_factory=list)

    @property
    def eligible(self) -> bool:
        return self.trend_aligned and self.liquidity_passed and self.capacity_passed


class PortfolioPosition(FrozenModel):
    instrument: InstrumentId
    target_weight: float = Field(gt=0, le=1)
    rationale: list[str] = Field(default_factory=list)


class PortfolioPlan(FrozenModel):
    as_of: datetime
    positions: list[PortfolioPosition] = Field(max_length=10)
    cash_weight: float = Field(ge=0, le=1)
    long_only: bool = True

    @field_validator("as_of")
    @classmethod
    def aware_portfolio_time(cls, value: datetime) -> datetime:
        return _require_aware(value, "as_of")

    @model_validator(mode="after")
    def valid_weights(self) -> "PortfolioPlan":
        total = self.cash_weight + sum(position.target_weight for position in self.positions)
        if abs(total - 1.0) > 1e-6:
            raise ValueError("portfolio weights plus cash must equal 1")
        if not self.long_only:
            raise ValueError("Custom Model production portfolio is long-only")
        return self


class AnalysisSnapshot(FrozenModel):
    snapshot_id: str = Field(default_factory=lambda: uuid4().hex)
    instrument: InstrumentId
    timeframe: Timeframe
    as_of: datetime
    bar_status: SignalStatus
    engine_version: str = Field(min_length=1)
    config_version: str = Field(min_length=1)
    data_lineage: list[DataLineage]
    market_regime: MarketRegimeContext | None = None
    sector_context: SectorOpportunity | None = None
    pair_context: PairCandidate | None = None
    component_scores: list[ScoreComponent]
    evidence_score: float = Field(ge=0, le=100)
    action: DecisionAction
    key_levels: list[KeyLevel] = Field(default_factory=list)
    invalidation: list[Invalidation] = Field(default_factory=list)
    explanations: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("as_of")
    @classmethod
    def aware_snapshot_time(cls, value: datetime) -> datetime:
        return _require_aware(value, "as_of")


class LevelRuleEvidence(FrozenModel):
    """Structured provenance for one distillation-derived price level."""

    source_kind: Literal["fanzong_distillation"] = "fanzong_distillation"
    source_file: str = Field(min_length=1, max_length=512)
    level_role: Literal["support", "resistance", "confirmation"]
    source_as_of: date | None = None
    source_line: int = Field(ge=1)
    source_text: str = Field(min_length=1, max_length=4000)

    @field_validator("source_file", "source_text")
    @classmethod
    def normalized_level_source(cls, value: str, info: Any) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{info.field_name} cannot be blank")
        return normalized


class AlertRule(FrozenModel):
    rule_id: str = Field(default_factory=lambda: uuid4().hex, min_length=1, max_length=64)
    instrument: InstrumentId
    timeframe: Timeframe
    rule_type: str = Field(min_length=1, max_length=64)
    threshold: float | None = Field(default=None, allow_inf_nan=False)
    confirmation: SignalStatus = SignalStatus.CONFIRMED
    cooldown_seconds: int = Field(default=300, ge=0, le=86400)
    enabled: bool = True
    level_evidence: LevelRuleEvidence | None = None

    @model_validator(mode="after")
    def coherent_rule_evidence(self) -> "AlertRule":
        if self.rule_type == "level_state":
            if self.threshold is None or self.threshold <= 0:
                raise ValueError("level_state rule requires a positive threshold")
            if self.level_evidence is None:
                raise ValueError("level_state rule requires level evidence")
        elif self.level_evidence is not None:
            raise ValueError("only level_state rules may carry level evidence")
        return self


class AlertRuleRecord(FrozenModel):
    """Persisted rule configuration plus immutable storage metadata."""

    rule: AlertRule
    monitoring_tier: MonitoringTier
    note: str = Field(default="", max_length=256)
    created_at: datetime
    updated_at: datetime
    revision: int = Field(ge=1)
    deleted_at: datetime | None = None
    spec_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("created_at", "updated_at", "deleted_at")
    @classmethod
    def aware_record_times(
        cls, value: datetime | None, info: Any
    ) -> datetime | None:
        return None if value is None else _require_aware(value, info.field_name)

    @model_validator(mode="after")
    def coherent_record_state(self) -> "AlertRuleRecord":
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        if self.deleted_at is not None:
            if self.deleted_at < self.created_at:
                raise ValueError("deleted_at cannot precede created_at")
            if self.rule.enabled:
                raise ValueError("deleted alert rules must be disabled")
        return self


class AlertEventEvidence(FrozenModel):
    """Immutable V1 evidence copied from the exact envelope used by a rule.

    The digest is deliberately content-addressed.  It proves that the persisted
    fields did not drift; it does not claim that a provider or an external
    transport is trustworthy by itself.
    """

    evidence_version: Literal["alert_event_evidence_v1"] = "alert_event_evidence_v1"
    rule_id: str = Field(min_length=1, max_length=64)
    rule_revision: int = Field(ge=1)
    rule_spec_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    instrument: InstrumentId
    timeframe: Timeframe
    confirmation: SignalStatus
    purpose: DataPurpose
    provider: str = Field(min_length=1)
    mode: DataMode
    quality: QualityStatus
    adjustment: Adjustment
    as_of: datetime
    fetched_at: datetime
    last_bar_at: datetime
    last_bar: Bar
    timezone: str = Field(min_length=1)
    freshness_seconds: float = Field(ge=0, allow_inf_nan=False)
    request_id: str = Field(min_length=1)
    source_chain: tuple[str, ...] = Field(min_length=1)
    calendar_snapshot_id: str | None = Field(default=None, min_length=1, max_length=128)
    calendar_content_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    is_synthetic: bool = False
    warnings: tuple[str, ...] = Field(default_factory=tuple)
    bar_count: int = Field(ge=1)
    data_content_version: Literal["data_envelope_v1"] = "data_envelope_v1"
    data_content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("provider", "timezone", "request_id")
    @classmethod
    def nonblank_evidence_text(cls, value: str, info: Any) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{info.field_name} cannot be blank")
        return normalized

    @field_validator("as_of", "fetched_at", "last_bar_at")
    @classmethod
    def aware_evidence_times(cls, value: datetime, info: Any) -> datetime:
        return _require_aware(value, info.field_name)

    @field_validator("source_chain")
    @classmethod
    def known_evidence_sources(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        forbidden_tokens = {"unknown", "fallback", "demo", "synthetic", "none"}
        normalized = [source.strip() for source in value]
        if any(
            not source
            or bool(re.search(r"(?:^|[^a-z0-9])n/a(?:$|[^a-z0-9])", source.lower()))
            or bool(
                {
                    token
                    for token in re.split(r"[^a-z0-9]+", source.lower())
                    if token
                }
                & forbidden_tokens
            )
            for source in normalized
        ):
            raise ValueError("source_chain contains an unknown or non-formal source")
        return tuple(normalized)

    def canonical_sha256(self) -> str:
        payload = self.model_dump(mode="json", exclude={"evidence_sha256"})
        return _canonical_json_sha256(payload)

    @staticmethod
    def canonical_data_content_sha256(envelope: "DataEnvelope") -> str:
        """Hash the complete input plus its explicit query-binding fields."""

        return _canonical_json_sha256(
            {
                "data_content_version": "data_envelope_v1",
                "query_binding": {
                    "instrument": envelope.instrument.model_dump(mode="json"),
                    "timeframe": envelope.timeframe.value,
                    "purpose": envelope.purpose.value,
                    "adjustment": envelope.adjustment.value,
                    "as_of": envelope.as_of.isoformat(),
                },
                "envelope": envelope.model_dump(mode="json"),
            }
        )

    @classmethod
    def from_envelope(
        cls,
        envelope: "DataEnvelope",
        *,
        rule_id: str,
        rule_revision: int,
        rule_spec_sha256: str,
        confirmation: SignalStatus,
    ) -> "AlertEventEvidence":
        """Build evidence without silently filling absent provenance."""

        if envelope.last_bar_at is None:
            raise ValueError("formal alert evidence requires last_bar_at")
        if not envelope.bars:
            raise ValueError("formal alert evidence requires at least one closed bar")
        data_content_sha256 = cls.canonical_data_content_sha256(envelope)
        values: dict[str, Any] = {
            "rule_id": rule_id,
            "rule_revision": rule_revision,
            "rule_spec_sha256": rule_spec_sha256,
            "instrument": envelope.instrument,
            "timeframe": envelope.timeframe,
            "confirmation": confirmation,
            "purpose": envelope.purpose,
            "provider": envelope.provider,
            "mode": envelope.mode,
            "quality": envelope.quality,
            "adjustment": envelope.adjustment,
            "as_of": envelope.as_of,
            "fetched_at": envelope.fetched_at,
            "last_bar_at": envelope.last_bar_at,
            "last_bar": envelope.bars[-1],
            "timezone": envelope.timezone,
            "freshness_seconds": envelope.freshness_seconds,
            "request_id": envelope.request_id,
            "source_chain": tuple(envelope.source_chain),
            "calendar_snapshot_id": envelope.calendar_snapshot_id,
            "calendar_content_sha256": envelope.calendar_content_sha256,
            "is_synthetic": envelope.is_synthetic,
            "warnings": tuple(envelope.warnings),
            "bar_count": len(envelope.bars),
            "data_content_sha256": data_content_sha256,
        }
        provisional = cls.model_construct(evidence_sha256="0" * 64, **values)
        return cls(**values, evidence_sha256=provisional.canonical_sha256())

    @model_validator(mode="after")
    def validate_formal_evidence(self) -> "AlertEventEvidence":
        if self.purpose is not DataPurpose.ALERT:
            raise ValueError("formal alert evidence requires ALERT purpose")
        if self.mode not in (DataMode.LIVE, DataMode.DELAYED):
            raise ValueError("formal alert evidence requires live or delayed mode")
        if self.quality is not QualityStatus.VALID:
            raise ValueError("formal alert evidence requires valid quality")
        if self.adjustment is not Adjustment.NONE:
            raise ValueError("formal alert evidence requires unadjusted data")
        if self.is_synthetic:
            raise ValueError("synthetic data cannot produce formal alert evidence")
        if self.provider.lower() in {"unknown", "none", "n/a"}:
            raise ValueError("formal alert evidence requires a known provider")
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        if self.last_bar_at > self.as_of:
            raise ValueError("last_bar_at cannot be after as_of")
        if self.last_bar.timestamp != self.last_bar_at:
            raise ValueError("last_bar timestamp must match last_bar_at")
        if self.evidence_sha256 != self.canonical_sha256():
            raise ValueError("evidence_sha256 does not match canonical evidence")
        return self


class QuoteSnapshotAlertEvidence(FrozenModel):
    """Local shadow evidence copied from a native quote, never a fake bar."""

    evidence_version: Literal[
        "snapshot_provisional", "snapshot_sustained"
    ] = "snapshot_provisional"
    rule_id: str = Field(min_length=1, max_length=64)
    rule_revision: int = Field(ge=1)
    rule_spec_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    instrument: InstrumentId
    timeframe: Timeframe
    confirmation: SignalStatus
    purpose: DataPurpose
    provider: str = Field(min_length=1, max_length=128)
    mode: DataMode
    quality: QualityStatus
    temporal_mode: QuoteTemporalMode
    observed_at: datetime
    fetched_at: datetime
    data_time: datetime
    provider_updated_at: datetime
    last_price: float = Field(gt=0, allow_inf_nan=False)
    freshness_seconds: float = Field(ge=0, allow_inf_nan=False)
    timezone: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=256)
    source_chain: tuple[str, ...] = Field(min_length=1)
    is_synthetic: bool = False
    warnings: tuple[str, ...] = ()
    quote_content_version: Literal["quote_envelope_v1"] = "quote_envelope_v1"
    quote_content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("provider", "timezone", "request_id")
    @classmethod
    def nonblank_snapshot_evidence_text(cls, value: str, info: Any) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{info.field_name} cannot be blank")
        return normalized

    @field_validator(
        "observed_at", "fetched_at", "data_time", "provider_updated_at"
    )
    @classmethod
    def aware_snapshot_evidence_times(cls, value: datetime, info: Any) -> datetime:
        return _require_aware(value, info.field_name)

    def canonical_sha256(self) -> str:
        return _canonical_json_sha256(
            self.model_dump(mode="json", exclude={"evidence_sha256"})
        )

    @staticmethod
    def canonical_quote_content_sha256(quote: "QuoteEnvelope") -> str:
        return _canonical_json_sha256(
            {
                "quote_content_version": "quote_envelope_v1",
                "query_binding": {
                    "instrument": quote.instrument.model_dump(mode="json"),
                    "purpose": quote.purpose.value,
                    "temporal_mode": quote.temporal_mode.value,
                    "as_of": quote.as_of.isoformat(),
                },
                "quote": quote.model_dump(mode="json"),
            }
        )

    @classmethod
    def from_quote(
        cls,
        quote: "QuoteEnvelope",
        *,
        evidence_version: Literal[
            "snapshot_provisional", "snapshot_sustained"
        ],
        rule_id: str,
        rule_revision: int,
        rule_spec_sha256: str,
        timeframe: Timeframe,
        confirmation: SignalStatus,
    ) -> "QuoteSnapshotAlertEvidence":
        quote_digest = cls.canonical_quote_content_sha256(quote)
        values: dict[str, Any] = {
            "evidence_version": evidence_version,
            "rule_id": rule_id,
            "rule_revision": rule_revision,
            "rule_spec_sha256": rule_spec_sha256,
            "instrument": quote.instrument,
            "timeframe": timeframe,
            "confirmation": confirmation,
            "purpose": quote.purpose,
            "provider": quote.provider,
            "mode": quote.mode,
            "quality": quote.quality,
            "temporal_mode": quote.temporal_mode,
            "observed_at": quote.as_of,
            "fetched_at": quote.fetched_at,
            "data_time": quote.data_time,
            "provider_updated_at": quote.provider_updated_at,
            "last_price": quote.last_price,
            "freshness_seconds": quote.freshness_seconds,
            "timezone": quote.timezone,
            "request_id": quote.request_id,
            "source_chain": tuple(quote.source_chain),
            "is_synthetic": quote.is_synthetic,
            "warnings": tuple(quote.warnings),
            "quote_content_sha256": quote_digest,
        }
        provisional = cls.model_construct(evidence_sha256="0" * 64, **values)
        return cls(**values, evidence_sha256=provisional.canonical_sha256())

    @model_validator(mode="after")
    def coherent_snapshot_evidence(self) -> "QuoteSnapshotAlertEvidence":
        if self.temporal_mode is not QuoteTemporalMode.LATEST:
            raise ValueError("snapshot alert evidence requires a latest quote")
        if self.is_synthetic or self.mode is DataMode.DEMO:
            raise ValueError("synthetic or demo quote cannot produce snapshot evidence")
        if self.provider.casefold() in {"unknown", "none", "n/a"}:
            raise ValueError("snapshot evidence requires a known provider")
        if self.data_time > self.fetched_at or self.provider_updated_at > self.fetched_at:
            raise ValueError("snapshot provider times cannot be after fetched_at")
        if self.observed_at > self.fetched_at:
            raise ValueError("snapshot observed_at cannot be after fetched_at")
        if self.evidence_sha256 != self.canonical_sha256():
            raise ValueError("snapshot evidence_sha256 does not match canonical evidence")
        return self


class SnapshotAlertEvent(FrozenModel):
    """A local state transition observed from a quote snapshot.

    This is intentionally not an ``AlertEvent`` and has no ``bar_close`` or
    delivery state.  It therefore cannot enter the formal alert outbox.
    """

    event_id: str = Field(default_factory=lambda: uuid4().hex)
    rule_id: str = Field(min_length=1, max_length=64)
    instrument: InstrumentId
    observed_at: datetime
    signal_state: str = Field(min_length=1, max_length=256)
    idempotency_key: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: datetime = Field(default_factory=utc_now)
    trigger_price: float = Field(gt=0, allow_inf_nan=False)
    evidence: QuoteSnapshotAlertEvidence

    @field_validator("observed_at", "created_at")
    @classmethod
    def aware_snapshot_event_times(cls, value: datetime, info: Any) -> datetime:
        return _require_aware(value, info.field_name)

    @model_validator(mode="after")
    def coherent_snapshot_event_binding(self) -> "SnapshotAlertEvent":
        if self.evidence.rule_id != self.rule_id:
            raise ValueError("snapshot evidence rule_id does not match event")
        if self.evidence.instrument != self.instrument:
            raise ValueError("snapshot evidence instrument does not match event")
        if self.evidence.observed_at != self.observed_at:
            raise ValueError("snapshot evidence observed_at does not match event")
        if not math.isclose(
            self.evidence.last_price,
            self.trigger_price,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError("snapshot trigger_price does not match quote evidence")
        return self


class AlertEvent(FrozenModel):
    event_id: str = Field(default_factory=lambda: uuid4().hex)
    rule_id: str = Field(min_length=1, max_length=64)
    instrument: InstrumentId
    bar_close: datetime
    signal_state: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)
    snapshot_id: str | None = None
    trigger_price: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    delivery_status: AlertDeliveryStatus = AlertDeliveryStatus.PENDING
    evidence: AlertEventEvidence | None = None

    @field_validator("bar_close", "created_at")
    @classmethod
    def aware_alert_times(cls, value: datetime, info: Any) -> datetime:
        return _require_aware(value, info.field_name)

    @model_validator(mode="after")
    def coherent_evidence_binding(self) -> "AlertEvent":
        if self.evidence is None:
            return self
        if self.evidence.rule_id != self.rule_id:
            raise ValueError("alert evidence rule_id does not match event")
        if self.evidence.instrument != self.instrument:
            raise ValueError("alert evidence instrument does not match event")
        if self.evidence.last_bar_at != self.bar_close:
            raise ValueError("alert evidence last_bar_at does not match bar_close")
        if self.trigger_price is None:
            raise ValueError("formal alert event requires trigger_price")
        if not math.isclose(
            self.trigger_price,
            self.evidence.last_bar.close,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError("alert trigger_price does not match evidence last close")
        return self


class AlertEventRecord(FrozenModel):
    """Read model merging immutable event JSON with authoritative outbox columns."""

    event: AlertEvent
    delivery_status: AlertDeliveryStatus
    attempts: int = Field(ge=0)
    created_at: datetime
    delivered_at: datetime | None = None
    last_error: str | None = None
    next_attempt_at: datetime | None = None
    claimed_by: str | None = None
    claim_expires_at: datetime | None = None
    provider_message_id: str | None = None
    updated_at: datetime
    delivery_uncertain: bool = False
    formal_use_eligible: bool = False
    evidence_status: AlertEvidenceStatus = AlertEvidenceStatus.LEGACY_UNVERIFIED

    @field_validator(
        "created_at",
        "delivered_at",
        "next_attempt_at",
        "claim_expires_at",
        "updated_at",
    )
    @classmethod
    def aware_event_record_times(
        cls, value: datetime | None, info: Any
    ) -> datetime | None:
        return None if value is None else _require_aware(value, info.field_name)

    @model_validator(mode="after")
    def coherent_delivery_record(self) -> "AlertEventRecord":
        if self.event.delivery_status is not self.delivery_status:
            raise ValueError("event delivery_status must use the authoritative table value")
        if self.delivery_status is AlertDeliveryStatus.FAILED:
            raise ValueError("legacy failed status must be migrated to uncertain")
        if self.delivery_status is AlertDeliveryStatus.CLAIMED:
            if not self.claimed_by or self.claim_expires_at is None:
                raise ValueError("claimed delivery requires owner and lease expiry")
            if self.attempts < 1:
                raise ValueError("claimed delivery requires at least one attempt")
            if self.claim_expires_at <= self.updated_at:
                raise ValueError("claim expiry must be after the claim update")
        elif self.claimed_by is not None or self.claim_expires_at is not None:
            raise ValueError("only claimed delivery may retain claim ownership")
        due_states = {
            AlertDeliveryStatus.PENDING,
            AlertDeliveryStatus.RETRY_SCHEDULED,
        }
        if self.delivery_status in due_states and self.next_attempt_at is None:
            raise ValueError("due delivery requires next_attempt_at")
        if (
            self.delivery_status in due_states
            and self.next_attempt_at is not None
            and self.next_attempt_at < self.updated_at
        ):
            raise ValueError("due delivery cannot be scheduled before its update")
        if self.delivery_status in {
            AlertDeliveryStatus.DELIVERED,
            AlertDeliveryStatus.UNCERTAIN,
            AlertDeliveryStatus.SUPPRESSED,
        } and self.next_attempt_at is not None:
            raise ValueError("terminal delivery cannot retain next_attempt_at")
        if (
            self.provider_message_id is not None
            and self.delivery_status is not AlertDeliveryStatus.DELIVERED
        ):
            raise ValueError("provider_message_id is only valid after delivery")
        if self.delivery_status is AlertDeliveryStatus.DELIVERED:
            if self.delivered_at is None or self.attempts < 1:
                raise ValueError("delivered state requires time and attempt evidence")
            if self.last_error is not None:
                raise ValueError("delivered state cannot retain a delivery error")
        elif self.delivered_at is not None:
            raise ValueError("only delivered state may retain delivered_at")
        if self.delivery_status in {
            AlertDeliveryStatus.RETRY_SCHEDULED,
            AlertDeliveryStatus.UNCERTAIN,
            AlertDeliveryStatus.SUPPRESSED,
        } and not (self.last_error or "").strip():
            raise ValueError("non-success delivery requires an audit reason")
        if self.delivered_at is not None and self.delivered_at < self.created_at:
            raise ValueError("delivered_at cannot precede created_at")
        if self.delivery_status is AlertDeliveryStatus.UNCERTAIN:
            if not self.delivery_uncertain:
                raise ValueError("uncertain delivery must be marked delivery_uncertain")
        elif self.delivery_uncertain:
            raise ValueError("delivery_uncertain requires uncertain status")
        if self.formal_use_eligible:
            if self.event.evidence is None:
                raise ValueError("formal event record requires evidence")
            if self.evidence_status is not AlertEvidenceStatus.VERIFIED:
                raise ValueError("formal event record requires verified evidence status")
        elif self.evidence_status is not AlertEvidenceStatus.LEGACY_UNVERIFIED:
            raise ValueError("non-formal event record must be legacy_unverified")
        elif self.event.evidence is not None:
            raise ValueError("non-formal event record cannot carry verified evidence")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        return self


class BacktestManifest(FrozenModel):
    manifest_id: str = Field(default_factory=lambda: uuid4().hex)
    created_at: datetime = Field(default_factory=utc_now)
    engine_version: str = Field(min_length=1)
    strategy_version: str = Field(min_length=1)
    data_version: str = Field(min_length=1)
    universe_version: str = Field(min_length=1)
    config: dict[str, Any]
    fee_schedule: dict[str, Any]
    random_seed: int
    calendar_snapshot_id: str | None = Field(default=None, min_length=1, max_length=128)
    calendar_content_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    content_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")

    @field_validator("created_at")
    @classmethod
    def aware_manifest_time(cls, value: datetime) -> datetime:
        return _require_aware(value, "created_at")


def instrument_from_legacy(symbol: str, asset_label: str | None = None) -> InstrumentId:
    """Convert existing UI symbols without hiding ambiguous index/ETF decisions."""
    raw = symbol.strip().upper()
    label = (asset_label or "").upper()
    digits = "".join(ch for ch in raw if ch.isdigit())
    numeric_symbol = raw.split(".", 1)[0].isdigit()

    if raw.endswith(".TI"):
        exchange = Exchange.OTHER
        market = Market.CN
    elif raw.endswith((".SS", ".SH")):
        exchange = Exchange.SSE
        market = Market.CN
    elif raw.endswith(".SZ"):
        exchange = Exchange.SZSE
        market = Market.CN
    elif raw.endswith(".BJ"):
        exchange = Exchange.BSE
        market = Market.CN
    elif raw.endswith(".HK") or "港股" in (asset_label or ""):
        exchange = Exchange.HKEX
        market = Market.HK
    elif "美股" in (asset_label or "") or "US" in label:
        exchange = Exchange.OTHER
        market = Market.US
    elif numeric_symbol and digits and digits[0] in "569":
        exchange = Exchange.SSE
        market = Market.CN
    elif numeric_symbol and digits and digits[0] in "0123":
        exchange = Exchange.SZSE
        market = Market.CN
    elif numeric_symbol and digits and digits[0] in "48":
        exchange = Exchange.BSE
        market = Market.CN
    else:
        exchange = Exchange.OTHER
        market = Market.US

    if raw.endswith(".TI"):
        asset_type = AssetType.SECTOR_INDEX
    elif "SECTOR" in label or "行业" in (asset_label or "") or "板块" in (asset_label or ""):
        asset_type = AssetType.SECTOR_INDEX
    elif "ETF" in label:
        asset_type = AssetType.ETF
    elif "INDEX" in label or "指数" in (asset_label or ""):
        asset_type = AssetType.INDEX
    elif market is Market.CN and numeric_symbol and digits and digits[0] in "15":
        asset_type = AssetType.ETF
    elif market is Market.CN:
        asset_type = AssetType.STOCK
    else:
        asset_type = AssetType.UNKNOWN

    currency = "CNY" if market is Market.CN else "HKD" if market is Market.HK else "USD"
    return InstrumentId(
        symbol=raw,
        exchange=exchange,
        market=market,
        asset_type=asset_type,
        currency=currency,
    )
