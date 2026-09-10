"""Deterministic trading-calendar port and application service."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import date, datetime, timedelta
import math
from typing import Protocol, runtime_checkable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from custom_model.domain.models import Exchange
from custom_model.domain.trading_calendar import (
    CalendarDayStatus,
    MinuteBarClosure,
    MinuteBarClosureReason,
    TradingCalendarSnapshot,
    TradingDateResult,
    TradingSessionPhase,
    TradingSessionState,
    TradingSessionWindow,
)


class TradingCalendarUnavailableError(RuntimeError):
    """Raised whenever calendar evidence is incomplete or not point-in-time safe."""


@runtime_checkable
class TradingCalendarProvider(Protocol):
    """Read-only boundary for immutable, versioned calendar snapshots."""

    name: str

    def timezone_for(self, exchange: Exchange) -> str: ...

    def snapshot_for(
        self,
        exchange: Exchange,
        target_date: date,
        *,
        as_of: datetime,
        snapshot_id: str | None = None,
    ) -> TradingCalendarSnapshot: ...


class TradingCalendarService:
    """Answer calendar questions without consulting a process clock or network.

    ``as_of`` is always explicit.  Providers must select a snapshot captured no
    later than that cutoff.  Persisting the returned evidence (especially its
    ``snapshot_id`` and digest) makes the same decision reproducible later.
    """

    SUPPORTED_MINUTE_INTERVALS = frozenset({1, 5, 15, 30, 60})

    def __init__(self, provider: TradingCalendarProvider) -> None:
        self._provider = provider

    @property
    def provider(self) -> TradingCalendarProvider:
        return self._provider

    @staticmethod
    def _aware(value: datetime, field_name: str) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{field_name} must be timezone-aware")
        return value

    def _zone(self, exchange: Exchange) -> ZoneInfo:
        try:
            name = self._provider.timezone_for(exchange)
            return ZoneInfo(name)
        except TradingCalendarUnavailableError:
            raise
        except (AttributeError, TypeError, ValueError, ZoneInfoNotFoundError) as exc:
            raise TradingCalendarUnavailableError(
                f"calendar provider has no valid timezone for exchange {exchange.value}"
            ) from exc

    def _snapshot(
        self,
        exchange: Exchange,
        target_date: date,
        *,
        as_of: datetime,
        snapshot_id: str | None,
    ) -> TradingCalendarSnapshot:
        cutoff = self._aware(as_of, "as_of")
        result = self._provider.snapshot_for(
            exchange,
            target_date,
            as_of=cutoff,
            snapshot_id=snapshot_id,
        )
        if exchange not in result.exchanges:
            raise TradingCalendarUnavailableError(
                f"snapshot {result.snapshot_id} does not cover exchange {exchange.value}"
            )
        if not (result.coverage_start <= target_date <= result.coverage_end):
            raise TradingCalendarUnavailableError(
                f"target date {target_date.isoformat()} is outside snapshot coverage"
            )
        if not result.coverage_complete:
            raise TradingCalendarUnavailableError(
                f"snapshot {result.snapshot_id} lacks complete coverage"
            )
        if not result.point_in_time_reproducible:
            raise TradingCalendarUnavailableError(
                f"snapshot {result.snapshot_id} is not point-in-time reproducible"
            )
        if result.published_at > cutoff or result.captured_at > cutoff:
            raise TradingCalendarUnavailableError(
                f"snapshot {result.snapshot_id} was not known at as_of"
            )
        provider_timezone = self._zone(exchange).key
        if result.timezone != provider_timezone:
            raise TradingCalendarUnavailableError(
                f"snapshot timezone {result.timezone} disagrees with provider timezone "
                f"{provider_timezone}"
            )
        return result

    @staticmethod
    def _is_trading_date(snapshot: TradingCalendarSnapshot, value: date) -> bool:
        index = bisect_left(snapshot.trading_days, value)
        return (
            index < len(snapshot.trading_days)
            and snapshot.trading_days[index] == value
        )

    def is_trading_day(
        self,
        exchange: Exchange,
        trading_date: date,
        *,
        as_of: datetime,
        snapshot_id: str | None = None,
    ) -> CalendarDayStatus:
        snapshot = self._snapshot(
            exchange,
            trading_date,
            as_of=as_of,
            snapshot_id=snapshot_id,
        )
        return CalendarDayStatus(
            exchange=exchange,
            trading_date=trading_date,
            as_of=as_of,
            is_trading_day=self._is_trading_date(snapshot, trading_date),
            evidence=snapshot.evidence(self._provider.name),
        )

    def previous_trading_day(
        self,
        exchange: Exchange,
        trading_date: date,
        *,
        as_of: datetime,
        snapshot_id: str | None = None,
    ) -> TradingDateResult:
        snapshot = self._snapshot(
            exchange,
            trading_date,
            as_of=as_of,
            snapshot_id=snapshot_id,
        )
        return self._previous_from_snapshot(exchange, trading_date, as_of, snapshot)

    def _previous_from_snapshot(
        self,
        exchange: Exchange,
        trading_date: date,
        as_of: datetime,
        snapshot: TradingCalendarSnapshot,
    ) -> TradingDateResult:
        index = bisect_left(snapshot.trading_days, trading_date) - 1
        if index < 0:
            raise TradingCalendarUnavailableError(
                "previous trading day is outside snapshot coverage; fail closed"
            )
        return TradingDateResult(
            exchange=exchange,
            trading_date=snapshot.trading_days[index],
            as_of=as_of,
            evidence=snapshot.evidence(self._provider.name),
        )

    def next_trading_day(
        self,
        exchange: Exchange,
        trading_date: date,
        *,
        as_of: datetime,
        snapshot_id: str | None = None,
    ) -> TradingDateResult:
        snapshot = self._snapshot(
            exchange,
            trading_date,
            as_of=as_of,
            snapshot_id=snapshot_id,
        )
        index = bisect_right(snapshot.trading_days, trading_date)
        if index >= len(snapshot.trading_days):
            raise TradingCalendarUnavailableError(
                "next trading day is outside snapshot coverage; fail closed"
            )
        return TradingDateResult(
            exchange=exchange,
            trading_date=snapshot.trading_days[index],
            as_of=as_of,
            evidence=snapshot.evidence(self._provider.name),
        )

    @staticmethod
    def _windows(
        snapshot: TradingCalendarSnapshot,
        trading_date: date,
    ) -> tuple[TradingSessionWindow, ...]:
        zone = ZoneInfo(snapshot.timezone)
        return tuple(
            TradingSessionWindow(
                name=session.name,
                opens_at=datetime.combine(trading_date, session.opens_at, tzinfo=zone),
                closes_at=datetime.combine(trading_date, session.closes_at, tzinfo=zone),
            )
            for session in snapshot.sessions
        )

    def session_at(
        self,
        exchange: Exchange,
        at: datetime,
        *,
        snapshot_id: str | None = None,
    ) -> TradingSessionState:
        instant = self._aware(at, "at")
        local = instant.astimezone(self._zone(exchange))
        snapshot = self._snapshot(
            exchange,
            local.date(),
            as_of=instant,
            snapshot_id=snapshot_id,
        )
        evidence = snapshot.evidence(self._provider.name)
        if not self._is_trading_date(snapshot, local.date()):
            return TradingSessionState(
                exchange=exchange,
                at=instant,
                trading_date=local.date(),
                is_trading_day=False,
                phase=TradingSessionPhase.NON_TRADING_DAY,
                windows=(),
                active_session=None,
                evidence=evidence,
            )

        windows = self._windows(snapshot, local.date())
        first, last = windows[0], windows[-1]
        if local < first.opens_at:
            phase = TradingSessionPhase.PRE_OPEN
            active = None
        elif first.opens_at <= local < first.closes_at:
            phase = TradingSessionPhase.MORNING
            active = first
        elif len(windows) > 1 and local < windows[1].opens_at:
            phase = TradingSessionPhase.MIDDAY_BREAK
            active = None
        elif last.opens_at <= local < last.closes_at:
            phase = TradingSessionPhase.AFTERNOON
            active = last
        else:
            phase = TradingSessionPhase.AFTER_CLOSE
            active = None
        return TradingSessionState(
            exchange=exchange,
            at=instant,
            trading_date=local.date(),
            is_trading_day=True,
            phase=phase,
            windows=windows,
            active_session=active,
            evidence=evidence,
        )

    def latest_completed_trading_day(
        self,
        exchange: Exchange,
        as_of: datetime,
        *,
        snapshot_id: str | None = None,
    ) -> TradingDateResult:
        cutoff = self._aware(as_of, "as_of")
        local = cutoff.astimezone(self._zone(exchange))
        snapshot = self._snapshot(
            exchange,
            local.date(),
            as_of=cutoff,
            snapshot_id=snapshot_id,
        )
        if self._is_trading_date(snapshot, local.date()):
            last_close = self._windows(snapshot, local.date())[-1].closes_at
            if local >= last_close:
                return TradingDateResult(
                    exchange=exchange,
                    trading_date=local.date(),
                    as_of=cutoff,
                    evidence=snapshot.evidence(self._provider.name),
                )
        return self._previous_from_snapshot(exchange, local.date(), cutoff, snapshot)

    def is_minute_bar_closed(
        self,
        exchange: Exchange,
        bar_close: datetime,
        *,
        timeframe_minutes: int,
        as_of: datetime,
        confirmation_delay_seconds: float = 0.0,
        snapshot_id: str | None = None,
    ) -> MinuteBarClosure:
        close_at = self._aware(bar_close, "bar_close")
        cutoff = self._aware(as_of, "as_of")
        if timeframe_minutes not in self.SUPPORTED_MINUTE_INTERVALS:
            raise ValueError(
                "timeframe_minutes must be a supported minute timeframe "
                f"{sorted(self.SUPPORTED_MINUTE_INTERVALS)}"
            )
        delay = float(confirmation_delay_seconds)
        if not math.isfinite(delay) or delay < 0:
            raise ValueError("confirmation_delay_seconds must be finite and nonnegative")

        zone = self._zone(exchange)
        local_close = close_at.astimezone(zone)
        snapshot = self._snapshot(
            exchange,
            local_close.date(),
            as_of=cutoff,
            snapshot_id=snapshot_id,
        )
        evidence = snapshot.evidence(self._provider.name)
        reason = MinuteBarClosureReason.INVALID_BAR_LABEL
        if not self._is_trading_date(snapshot, local_close.date()):
            reason = MinuteBarClosureReason.NON_TRADING_DAY
        else:
            interval_seconds = timeframe_minutes * 60
            for window in self._windows(snapshot, local_close.date()):
                if window.opens_at < local_close <= window.closes_at:
                    elapsed = (local_close - window.opens_at).total_seconds()
                    if elapsed.is_integer() and int(elapsed) % interval_seconds == 0:
                        confirmed_at = close_at + timedelta(seconds=delay)
                        reason = (
                            MinuteBarClosureReason.CLOSED
                            if cutoff >= confirmed_at
                            else MinuteBarClosureReason.AWAITING_CONFIRMATION
                        )
                    break
        return MinuteBarClosure(
            exchange=exchange,
            bar_close=close_at,
            as_of=cutoff,
            timeframe_minutes=timeframe_minutes,
            confirmation_delay_seconds=delay,
            is_closed=reason is MinuteBarClosureReason.CLOSED,
            reason=reason,
            evidence=evidence,
        )


__all__ = [
    "TradingCalendarProvider",
    "TradingCalendarService",
    "TradingCalendarUnavailableError",
]
