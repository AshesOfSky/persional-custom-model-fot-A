"""Provider-neutral, evidence-carrying trading-calendar domain models.

The models deliberately separate an exchange schedule from the wall clock of
the process using it.  Every decision can therefore retain the exact immutable
snapshot that was available at its explicit ``as_of`` cutoff.
"""

from __future__ import annotations

from datetime import date, datetime, time
from enum import Enum
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from custom_model.domain.models import Exchange, Market


def _require_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TradingSessionName(str, Enum):
    MORNING = "morning"
    AFTERNOON = "afternoon"


class TradingSessionPhase(str, Enum):
    NON_TRADING_DAY = "non_trading_day"
    PRE_OPEN = "pre_open"
    MORNING = "morning"
    MIDDAY_BREAK = "midday_break"
    AFTERNOON = "afternoon"
    AFTER_CLOSE = "after_close"


class MinuteBarClosureReason(str, Enum):
    CLOSED = "closed"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    INVALID_BAR_LABEL = "invalid_bar_label"
    NON_TRADING_DAY = "non_trading_day"


class TradingSessionTemplate(_FrozenModel):
    """One local wall-clock cash-market session inside a trading day."""

    name: TradingSessionName
    opens_at: time
    closes_at: time

    @field_validator("opens_at", "closes_at")
    @classmethod
    def local_wall_clock(cls, value: time, info: Any) -> time:
        if value.tzinfo is not None:
            raise ValueError(f"{info.field_name} must be a local wall-clock time")
        return value

    @model_validator(mode="after")
    def ordered_window(self) -> "TradingSessionTemplate":
        if self.closes_at <= self.opens_at:
            raise ValueError("session closes_at must be after opens_at")
        return self


class CalendarSnapshotEvidence(_FrozenModel):
    """Small lineage record callers persist beside a calendar decision."""

    provider: str = Field(min_length=1, max_length=128)
    snapshot_id: str = Field(min_length=1, max_length=128)
    source: str = Field(min_length=1, max_length=256)
    source_revision: str = Field(min_length=1, max_length=256)
    published_at: datetime
    captured_at: datetime
    coverage_start: date
    coverage_end: date
    timezone: str = Field(min_length=1, max_length=128)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("provider", "snapshot_id", "source", "source_revision")
    @classmethod
    def non_blank_labels(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("calendar evidence labels cannot be blank")
        return normalized

    @field_validator("published_at", "captured_at")
    @classmethod
    def aware_evidence_times(cls, value: datetime, info: Any) -> datetime:
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

    @model_validator(mode="after")
    def ordered_evidence(self) -> "CalendarSnapshotEvidence":
        if self.published_at > self.captured_at:
            raise ValueError("published_at cannot be after captured_at")
        if self.coverage_end < self.coverage_start:
            raise ValueError("coverage_end cannot be before coverage_start")
        return self


class TradingCalendarSnapshot(_FrozenModel):
    """An immutable, contiguous and versioned exchange-calendar snapshot.

    ``coverage_complete`` means every omitted date inside the inclusive range
    is explicitly a non-trading date, rather than merely absent from an API
    response. ``point_in_time_reproducible`` means the source revision and local
    capture are stable enough to rerun by ``snapshot_id``.
    """

    schema_version: Literal[1] = 1
    snapshot_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    market: Market
    exchanges: tuple[Exchange, ...] = Field(min_length=1)
    timezone: str = Field(min_length=1, max_length=128)
    source: str = Field(min_length=1, max_length=256)
    source_revision: str = Field(min_length=1, max_length=256)
    published_at: datetime
    captured_at: datetime
    coverage_start: date
    coverage_end: date
    coverage_complete: bool
    point_in_time_reproducible: bool
    sessions: tuple[TradingSessionTemplate, ...] = Field(min_length=1)
    trading_days: tuple[date, ...]
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("source", "source_revision")
    @classmethod
    def non_blank_source_labels(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("calendar source labels cannot be blank")
        return normalized

    @field_validator("published_at", "captured_at")
    @classmethod
    def aware_snapshot_times(cls, value: datetime, info: Any) -> datetime:
        return _require_aware(value, info.field_name)

    @field_validator("timezone")
    @classmethod
    def valid_snapshot_timezone(cls, value: str) -> str:
        normalized = value.strip()
        try:
            ZoneInfo(normalized)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return normalized

    @model_validator(mode="after")
    def coherent_snapshot(self) -> "TradingCalendarSnapshot":
        if self.published_at > self.captured_at:
            raise ValueError("published_at cannot be after captured_at")
        if self.coverage_end < self.coverage_start:
            raise ValueError("coverage_end cannot be before coverage_start")
        if len(self.exchanges) != len(set(self.exchanges)):
            raise ValueError("exchanges must be unique")
        if tuple(sorted(self.trading_days)) != self.trading_days or len(
            self.trading_days
        ) != len(set(self.trading_days)):
            raise ValueError("trading_days must be strictly sorted and unique")
        if any(
            day < self.coverage_start or day > self.coverage_end
            for day in self.trading_days
        ):
            raise ValueError("trading_days must be inside snapshot coverage")
        if len({session.name for session in self.sessions}) != len(self.sessions):
            raise ValueError("session names must be unique")
        ordered_sessions = tuple(
            sorted(self.sessions, key=lambda item: item.opens_at)
        )
        if ordered_sessions != self.sessions:
            raise ValueError("sessions must be ordered by opens_at")
        for previous, current in zip(self.sessions, self.sessions[1:]):
            if previous.closes_at > current.opens_at:
                raise ValueError("sessions must not overlap")
        return self

    def evidence(self, provider: str) -> CalendarSnapshotEvidence:
        return CalendarSnapshotEvidence(
            provider=provider,
            snapshot_id=self.snapshot_id,
            source=self.source,
            source_revision=self.source_revision,
            published_at=self.published_at,
            captured_at=self.captured_at,
            coverage_start=self.coverage_start,
            coverage_end=self.coverage_end,
            timezone=self.timezone,
            content_sha256=self.content_sha256,
        )


class CalendarDayStatus(_FrozenModel):
    exchange: Exchange
    trading_date: date
    as_of: datetime
    is_trading_day: bool
    evidence: CalendarSnapshotEvidence

    @field_validator("as_of")
    @classmethod
    def aware_as_of(cls, value: datetime) -> datetime:
        return _require_aware(value, "as_of")


class TradingDateResult(_FrozenModel):
    exchange: Exchange
    trading_date: date
    as_of: datetime
    evidence: CalendarSnapshotEvidence

    @field_validator("as_of")
    @classmethod
    def aware_as_of(cls, value: datetime) -> datetime:
        return _require_aware(value, "as_of")


class TradingSessionWindow(_FrozenModel):
    name: TradingSessionName
    opens_at: datetime
    closes_at: datetime

    @field_validator("opens_at", "closes_at")
    @classmethod
    def aware_window_times(cls, value: datetime, info: Any) -> datetime:
        return _require_aware(value, info.field_name)

    @model_validator(mode="after")
    def valid_window(self) -> "TradingSessionWindow":
        if self.opens_at.date() != self.closes_at.date():
            raise ValueError("session window must not cross a local date")
        if self.closes_at <= self.opens_at:
            raise ValueError("session closes_at must be after opens_at")
        return self


class TradingSessionState(_FrozenModel):
    exchange: Exchange
    at: datetime
    trading_date: date
    is_trading_day: bool
    phase: TradingSessionPhase
    windows: tuple[TradingSessionWindow, ...]
    active_session: TradingSessionWindow | None = None
    evidence: CalendarSnapshotEvidence

    @field_validator("at")
    @classmethod
    def aware_at(cls, value: datetime) -> datetime:
        return _require_aware(value, "at")

    @model_validator(mode="after")
    def coherent_state(self) -> "TradingSessionState":
        if not self.is_trading_day:
            if self.phase is not TradingSessionPhase.NON_TRADING_DAY:
                raise ValueError("non-trading day must use non_trading_day phase")
            if self.windows or self.active_session is not None:
                raise ValueError("non-trading day cannot expose session windows")
        if self.active_session is not None and self.active_session not in self.windows:
            raise ValueError("active_session must be one of windows")
        return self


class MinuteBarClosure(_FrozenModel):
    exchange: Exchange
    bar_close: datetime
    as_of: datetime
    timeframe_minutes: int = Field(gt=0)
    confirmation_delay_seconds: float = Field(ge=0, allow_inf_nan=False)
    is_closed: bool
    reason: MinuteBarClosureReason
    evidence: CalendarSnapshotEvidence

    @field_validator("bar_close", "as_of")
    @classmethod
    def aware_times(cls, value: datetime, info: Any) -> datetime:
        return _require_aware(value, info.field_name)

    @model_validator(mode="after")
    def coherent_closure(self) -> "MinuteBarClosure":
        if self.is_closed != (self.reason is MinuteBarClosureReason.CLOSED):
            raise ValueError("is_closed must agree with closure reason")
        return self


__all__ = [
    "CalendarDayStatus",
    "CalendarSnapshotEvidence",
    "MinuteBarClosure",
    "MinuteBarClosureReason",
    "TradingCalendarSnapshot",
    "TradingDateResult",
    "TradingSessionName",
    "TradingSessionPhase",
    "TradingSessionState",
    "TradingSessionTemplate",
    "TradingSessionWindow",
]
