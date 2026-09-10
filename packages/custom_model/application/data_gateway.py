from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Iterable, Iterator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from custom_model.application.market_bar_store import MarketBarStore
from custom_model.application.ports import MarketDataProvider
from custom_model.application.trading_calendar import (
    TradingCalendarService,
    TradingCalendarUnavailableError,
)
from custom_model.domain.models import (
    DataEnvelope,
    DataMode,
    DataPurpose,
    DataQuery,
    Exchange,
    Market,
    QualityStatus,
    Timeframe,
)
from custom_model.domain.trading_calendar import (
    CalendarSnapshotEvidence,
    TradingSessionPhase,
)


class DataUnavailableError(RuntimeError):
    """Raised when no provider can produce admissible data."""

    def __init__(self, query: DataQuery, attempts: list[str]):
        self.query = query
        self.attempts = attempts
        detail = "; ".join(attempts) if attempts else "no provider supports the query"
        super().__init__(f"No admissible data for {query.instrument.key} {query.timeframe.value}: {detail}")


@dataclass(frozen=True)
class FreshnessPolicy:
    max_age_seconds: dict[Timeframe, float] = field(
        default_factory=lambda: {
            Timeframe.M1: 90.0,
            Timeframe.M5: 420.0,
            Timeframe.M15: 1_200.0,
            Timeframe.M30: 2_400.0,
            Timeframe.H1: 7_200.0,
            Timeframe.D1: 4 * 86_400.0,
            Timeframe.W1: 14 * 86_400.0,
            Timeframe.MO1: 45 * 86_400.0,
        }
    )

    _CN_ZONE = ZoneInfo("Asia/Shanghai")
    _CN_INTRADAY = frozenset(
        {Timeframe.M1, Timeframe.M5, Timeframe.M15, Timeframe.M30, Timeframe.H1}
    )
    _CN_CASH_EXCHANGES = frozenset({Exchange.SSE, Exchange.SZSE, Exchange.BSE})
    _CN_DAILY_SESSION_PURPOSES = frozenset(
        {
            DataPurpose.RESEARCH,
            DataPurpose.SCREENER,
            DataPurpose.ALERT,
            DataPurpose.REPORT,
        }
    )
    _COMPLETED_SESSION_PURPOSES = frozenset(
        {DataPurpose.RESEARCH, DataPurpose.REPORT}
    )

    @classmethod
    def _previous_weekday(cls, value: date) -> date:
        candidate = value - timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate -= timedelta(days=1)
        return candidate

    @classmethod
    def _at_session_close(
        cls,
        bar_at: datetime,
        *,
        session_date: date,
        session_close: time,
    ) -> bool:
        close_at = datetime.combine(
            session_date,
            session_close,
            tzinfo=cls._CN_ZONE,
        )
        # The admitted Eastmoney minute contract is close-labelled.  Once a
        # session is complete, accepting an earlier bucket would silently hide
        # a missing final bar (14:55 for M5 or 14:00 for H1, for example).
        return bar_at == close_at

    @classmethod
    def _latest_completed_cn_daily_date(cls, as_of: datetime) -> date:
        """Return the latest *weekday-assumed* completed A-share session date.

        Daily bars are session-labelled, so their local calendar date matters
        more than their provider-specific timestamp (midnight versus close).
        Before the weekday close, the previous weekday is the latest completed
        daily session.  After the close, the current weekday is expected.  On a
        weekend, Friday remains expected.

        This intentionally does not infer weekday exchange holidays.  Without
        an exchange calendar, treating a weekday after 15:00 as completed is
        the fail-closed choice: a holiday can cause a false stale result, but a
        genuinely missing current-session bar cannot be mislabeled valid.
        """

        reference = as_of.astimezone(cls._CN_ZONE)
        local_date = reference.date()
        local_time = reference.time().replace(tzinfo=None)
        if reference.weekday() >= 5:
            return cls._previous_weekday(local_date)
        if local_time >= time(15, 0):
            return local_date
        return cls._previous_weekday(local_date)

    @classmethod
    def _is_latest_completed_cn_daily_bar(
        cls,
        query: DataQuery,
        last_bar_at: datetime,
    ) -> bool:
        bar_date = last_bar_at.astimezone(cls._CN_ZONE).date()
        return bar_date == cls._latest_completed_cn_daily_date(query.as_of)

    @classmethod
    def _is_latest_completed_cn_session_bar(
        cls,
        query: DataQuery,
        last_bar_at: datetime,
    ) -> bool:
        """Conservatively admit a completed CN session for chart research.

        This deliberately does not guess weekday holidays.  Same-day lunch and
        after-close bars are accepted, as are the immediately preceding weekday
        before the next open and Friday bars on a weekend.  A weekday after the
        close with only a prior-day bar remains stale until an exchange calendar
        is introduced.
        """

        reference = query.as_of.astimezone(cls._CN_ZONE)
        bar_at = last_bar_at.astimezone(cls._CN_ZONE)
        local_date = reference.date()
        local_time = reference.time().replace(tzinfo=None)

        if reference.weekday() < 5 and time(11, 30) <= local_time < time(13, 0):
            return cls._at_session_close(
                bar_at,
                session_date=local_date,
                session_close=time(11, 30),
            )

        if reference.weekday() < 5 and local_time >= time(15, 0):
            return cls._at_session_close(
                bar_at,
                session_date=local_date,
                session_close=time(15, 0),
            )

        before_open = reference.weekday() < 5 and local_time < time(9, 30)
        weekend = reference.weekday() >= 5
        if before_open or weekend:
            expected_date = cls._previous_weekday(local_date)
            return cls._at_session_close(
                bar_at,
                session_date=expected_date,
                session_close=time(15, 0),
            )
        return False

    def assess(self, query: DataQuery, last_bar_at: datetime | None) -> tuple[QualityStatus, float]:
        if last_bar_at is None:
            return QualityStatus.UNAVAILABLE, 0.0

        reference = query.as_of
        if query.purpose is DataPurpose.BACKTEST and query.end is not None:
            reference = min(reference, query.end)
        age = max(0.0, (reference - last_bar_at).total_seconds())

        if query.purpose is DataPurpose.BACKTEST:
            if query.start and last_bar_at < query.start:
                return QualityStatus.PARTIAL, age
            return QualityStatus.VALID, age

        if (
            query.instrument.market is Market.CN
            and query.instrument.exchange in self._CN_CASH_EXCHANGES
            and query.timeframe is Timeframe.D1
            and query.purpose in self._CN_DAILY_SESSION_PURPOSES
            and not self._is_latest_completed_cn_daily_bar(query, last_bar_at)
        ):
            return QualityStatus.STALE, age

        if (
            query.instrument.market is Market.CN
            and query.instrument.exchange in self._CN_CASH_EXCHANGES
            and query.timeframe in self._CN_INTRADAY
            and query.purpose in self._COMPLETED_SESSION_PURPOSES
        ):
            reference = query.as_of.astimezone(self._CN_ZONE)
            local_time = reference.time().replace(tzinfo=None)
            completed_session_expected = (
                reference.weekday() >= 5
                or local_time < time(9, 30)
                or time(11, 30) <= local_time < time(13, 0)
                or local_time >= time(15, 0)
            )
            if completed_session_expected:
                return (
                    QualityStatus.VALID
                    if self._is_latest_completed_cn_session_bar(query, last_bar_at)
                    else QualityStatus.STALE,
                    age,
                )

        max_age = self.max_age_seconds[query.timeframe]
        if age <= max_age:
            return QualityStatus.VALID, age
        return QualityStatus.STALE, age


class DataGateway:
    """Provider router that fails closed for formal research and alert workflows."""

    _FORMAL_PURPOSES = {
        DataPurpose.RESEARCH,
        DataPurpose.SCREENER,
        DataPurpose.ALERT,
        DataPurpose.REPORT,
    }
    _FORMAL_MODES = {DataMode.LIVE, DataMode.DELAYED, DataMode.CACHE}
    _TIMEFRAME_MINUTES = {
        Timeframe.M1: 1,
        Timeframe.M5: 5,
        Timeframe.M15: 15,
        Timeframe.M30: 30,
        Timeframe.H1: 60,
    }

    def __init__(
        self,
        providers: Iterable[MarketDataProvider],
        freshness_policy: FreshnessPolicy | None = None,
        market_bar_store: MarketBarStore | None = None,
        trading_calendar_service: TradingCalendarService | None = None,
        require_trading_calendar: bool = False,
    ) -> None:
        self._providers = tuple(providers)
        self._freshness = freshness_policy or FreshnessPolicy()
        self._market_bar_store = market_bar_store
        self._trading_calendar_service = trading_calendar_service
        self._require_trading_calendar = require_trading_calendar
        self._captured_reads: ContextVar[list[tuple[DataQuery, DataEnvelope | None]] | None] = (
            ContextVar("captured_market_reads", default=None)
        )
        if require_trading_calendar and trading_calendar_service is None:
            raise ValueError("required trading calendar service is missing")

    @property
    def providers(self) -> tuple[MarketDataProvider, ...]:
        return self._providers

    @property
    def trading_calendar_service(self) -> TradingCalendarService | None:
        return self._trading_calendar_service

    @property
    def requires_trading_calendar(self) -> bool:
        return self._require_trading_calendar

    @contextmanager
    def capture_reads(self) -> Iterator[list[tuple[DataQuery, DataEnvelope | None]]]:
        """Observe this request's admitted bars for a frozen research input.

        No provider, admission, cache, or strategy decision is changed.
        Context-local storage keeps other API requests outside the capture.
        """
        reads: list[tuple[DataQuery, DataEnvelope | None]] = []
        token = self._captured_reads.set(reads)
        try:
            yield reads
        finally:
            self._captured_reads.reset(token)

    def fetch_bars(self, query: DataQuery) -> DataEnvelope:
        attempts: list[str] = []
        for provider in self._providers:
            if not provider.supports(query):
                continue
            if not provider.production_ready and query.purpose is not DataPurpose.DEMO:
                attempts.append(f"{provider.name}: experimental/not admitted")
                continue
            try:
                envelope = provider.fetch_bars(query)
                envelope = self._exclude_incomplete_cn_daily(query, envelope)
                envelope = self._assess(query, envelope)
                violations = self._violations(query, envelope)
                if violations:
                    attempts.append(f"{provider.name}: {', '.join(violations)}")
                    continue
                self._cache_if_admitted(envelope, attempts)
                if attempts:
                    envelope = envelope.model_copy(
                        update={"warnings": [*envelope.warnings, *attempts]}
                    )
                captured = self._captured_reads.get()
                if captured is not None:
                    captured.append((query, envelope))
                return envelope
            except Exception as exc:  # provider boundary; never leak payloads or credentials
                attempts.append(f"{provider.name}: {type(exc).__name__}")
        captured = self._captured_reads.get()
        if captured is not None:
            captured.append((query, None))
        raise DataUnavailableError(query, attempts)

    @staticmethod
    def _exclude_incomplete_cn_daily(
        query: DataQuery, envelope: DataEnvelope
    ) -> DataEnvelope:
        """Remove the current CN D1 bar before freshness/calendar admission."""

        if (
            query.purpose not in {DataPurpose.BACKTEST, DataPurpose.RESEARCH, DataPurpose.REPORT}
            or query.timeframe is not Timeframe.D1
            or query.instrument.market is not Market.CN
            or query.instrument.exchange not in FreshnessPolicy._CN_CASH_EXCHANGES
            or not envelope.bars
        ):
            return envelope
        zone = ZoneInfo(envelope.timezone)
        local_as_of = query.as_of.astimezone(zone)
        as_of_day = local_as_of.date()
        current_session_complete = local_as_of.time() >= time(15, 0)
        completed_bars = [
            bar
            for bar in envelope.bars
            if (
                bar.timestamp.astimezone(zone).date() < as_of_day
                or (
                    current_session_complete
                    and bar.timestamp.astimezone(zone).date() == as_of_day
                )
            )
        ]
        if not completed_bars or len(completed_bars) == len(envelope.bars):
            return envelope
        warning = (
            f"excluded {len(envelope.bars) - len(completed_bars)} "
            "incomplete or future D1 bar(s)"
        )
        return envelope.model_copy(
            update={
                "bars": completed_bars,
                "last_bar_at": completed_bars[-1].timestamp,
                "warnings": list(dict.fromkeys([*envelope.warnings, warning])),
            }
        )

    def _cache_if_admitted(
        self, envelope: DataEnvelope, attempts: list[str]
    ) -> None:
        if (
            self._market_bar_store is None
            or "cache-provider" in envelope.source_chain
        ):
            return
        try:
            self._market_bar_store.save(envelope)
        except Exception as exc:
            attempts.append(f"cache write failed: {type(exc).__name__}")

    def _assess(self, query: DataQuery, envelope: DataEnvelope) -> DataEnvelope:
        if self._calendar_applies(query):
            if self._trading_calendar_service is None:
                if self._require_trading_calendar:
                    raise TradingCalendarUnavailableError(
                        "formal CN data requires an admitted trading calendar"
                    )
            else:
                quality, freshness, evidence, reason = self._calendar_assess(
                    query, envelope.last_bar_at
                )
                claimed = (
                    envelope.calendar_snapshot_id,
                    envelope.calendar_content_sha256,
                )
                admitted = (evidence.snapshot_id, evidence.content_sha256)
                if claimed != (None, None) and claimed != admitted:
                    raise TradingCalendarUnavailableError(
                        "provider calendar evidence conflicts with admitted snapshot"
                    )
                warning = (
                    f"trading calendar snapshot {evidence.snapshot_id} "
                    f"admitted; {reason}"
                )
                return envelope.model_copy(
                    update={
                        "quality": quality,
                        "freshness_seconds": freshness,
                        "calendar_snapshot_id": evidence.snapshot_id,
                        "calendar_content_sha256": evidence.content_sha256,
                        "warnings": list(dict.fromkeys([*envelope.warnings, warning])),
                    }
                )

        quality, freshness = self._freshness.assess(query, envelope.last_bar_at)
        warnings = list(envelope.warnings)
        if (
            quality is QualityStatus.VALID
            and freshness > self._freshness.max_age_seconds[query.timeframe]
            and query.instrument.market is Market.CN
            and query.timeframe in self._freshness._CN_INTRADAY
            and query.purpose in self._freshness._COMPLETED_SESSION_PURPOSES
            and envelope.last_bar_at is not None
            and self._freshness._is_latest_completed_cn_session_bar(
                query, envelope.last_bar_at
            )
        ):
            warnings.append(
                "latest completed CN trading-session bar accepted for research; "
                f"raw freshness is {int(freshness)} seconds"
            )
        return envelope.model_copy(
            update={
                "quality": quality,
                "freshness_seconds": freshness,
                "warnings": warnings,
            }
        )

    def _calendar_applies(self, query: DataQuery) -> bool:
        return (
            query.instrument.market is Market.CN
            and query.instrument.exchange in FreshnessPolicy._CN_CASH_EXCHANGES
            and query.purpose in self._FORMAL_PURPOSES | {DataPurpose.BACKTEST}
        )

    @staticmethod
    def _raw_freshness(query: DataQuery, last_bar_at: datetime) -> float:
        reference = query.as_of
        if query.purpose is DataPurpose.BACKTEST and query.end is not None:
            reference = min(reference, query.end)
        return max(0.0, (reference - last_bar_at).total_seconds())

    def _calendar_assess(
        self,
        query: DataQuery,
        last_bar_at: datetime | None,
    ) -> tuple[QualityStatus, float, CalendarSnapshotEvidence, str]:
        if last_bar_at is None or self._trading_calendar_service is None:
            raise TradingCalendarUnavailableError(
                "calendar freshness assessment requires a completed bar"
            )
        service = self._trading_calendar_service
        exchange = query.instrument.exchange
        freshness = self._raw_freshness(query, last_bar_at)
        zone = FreshnessPolicy._CN_ZONE

        if query.timeframe is Timeframe.D1:
            bar_date = last_bar_at.astimezone(zone).date()
            if query.purpose is DataPurpose.BACKTEST:
                status = service.is_trading_day(
                    exchange, bar_date, as_of=query.as_of
                )
                quality = (
                    QualityStatus.VALID
                    if status.is_trading_day
                    else QualityStatus.STALE
                )
                if query.start is not None and last_bar_at < query.start:
                    quality = QualityStatus.PARTIAL
                return quality, freshness, status.evidence, (
                    f"backtest final D1 label {bar_date.isoformat()} verified"
                )
            latest = service.latest_completed_trading_day(exchange, query.as_of)
            quality = (
                QualityStatus.VALID
                if bar_date == latest.trading_date
                else QualityStatus.STALE
            )
            return quality, freshness, latest.evidence, (
                "latest completed trading day is "
                f"{latest.trading_date.isoformat()}"
            )

        timeframe_minutes = self._TIMEFRAME_MINUTES.get(query.timeframe)
        if timeframe_minutes is not None:
            reference = query.as_of
            if query.purpose is DataPurpose.BACKTEST and query.end is not None:
                reference = min(reference, query.end)
            closure = service.is_minute_bar_closed(
                exchange,
                last_bar_at,
                timeframe_minutes=timeframe_minutes,
                as_of=reference,
                confirmation_delay_seconds=(
                    3.0 if query.purpose is DataPurpose.ALERT else 0.0
                ),
            )
            quality = (
                QualityStatus.VALID if closure.is_closed else QualityStatus.STALE
            )
            reason = f"minute bar closure={closure.reason.value}"
            if closure.is_closed and query.purpose in FreshnessPolicy._COMPLETED_SESSION_PURPOSES:
                expected, session_evidence = self._expected_completed_session_close(
                    query
                )
                if expected is not None:
                    quality = (
                        QualityStatus.VALID
                        if last_bar_at.astimezone(expected.tzinfo) == expected
                        else QualityStatus.STALE
                    )
                    reason = f"latest completed session close is {expected.isoformat()}"
                    closure = closure.model_copy(update={"evidence": session_evidence})
                elif freshness > self._freshness.max_age_seconds[query.timeframe]:
                    quality = QualityStatus.STALE
            elif closure.is_closed and query.purpose is not DataPurpose.BACKTEST:
                if freshness > self._freshness.max_age_seconds[query.timeframe]:
                    quality = QualityStatus.STALE
            if (
                closure.is_closed
                and query.purpose is DataPurpose.BACKTEST
                and query.start is not None
                and last_bar_at < query.start
            ):
                quality = QualityStatus.PARTIAL
            return quality, freshness, closure.evidence, reason

        state = service.session_at(exchange, query.as_of)
        quality, freshness = self._freshness.assess(query, last_bar_at)
        return quality, freshness, state.evidence, (
            f"calendar session phase={state.phase.value}"
        )

    def _expected_completed_session_close(
        self, query: DataQuery
    ) -> tuple[datetime | None, CalendarSnapshotEvidence]:
        if self._trading_calendar_service is None:
            raise TradingCalendarUnavailableError("trading calendar is unavailable")
        service = self._trading_calendar_service
        exchange = query.instrument.exchange
        state = service.session_at(exchange, query.as_of)
        if state.phase in {
            TradingSessionPhase.NON_TRADING_DAY,
            TradingSessionPhase.PRE_OPEN,
        }:
            previous = service.previous_trading_day(
                exchange, state.trading_date, as_of=query.as_of
            )
            expected = datetime.combine(
                previous.trading_date,
                time(15, 0),
                tzinfo=ZoneInfo(previous.evidence.timezone),
            )
            return expected, previous.evidence
        if state.phase is TradingSessionPhase.MIDDAY_BREAK:
            return state.windows[0].closes_at, state.evidence
        if state.phase is TradingSessionPhase.AFTER_CLOSE:
            return state.windows[-1].closes_at, state.evidence
        return None, state.evidence

    def _violations(self, query: DataQuery, envelope: DataEnvelope) -> list[str]:
        issues: list[str] = []
        if envelope.instrument != query.instrument:
            issues.append("instrument mismatch")
        if envelope.timeframe is not query.timeframe:
            issues.append("timeframe mismatch")
        if envelope.purpose is not query.purpose:
            issues.append("purpose mismatch")
        if envelope.as_of != query.as_of:
            issues.append("as_of mismatch")
        if envelope.adjustment is not query.adjustment:
            issues.append("adjustment mismatch")
        if not envelope.bars:
            issues.append("empty bars")
        if envelope.provider.strip().lower() in {"", "unknown", "none"}:
            issues.append("unknown provider")
        if not envelope.source_chain or any(
            not isinstance(source, str)
            or source.strip().lower() in {"", "unknown", "none", "n/a"}
            for source in envelope.source_chain
        ):
            issues.append("unknown source")
        try:
            ZoneInfo(envelope.timezone)
        except (TypeError, ValueError, ZoneInfoNotFoundError):
            issues.append("invalid timezone")
        upper_bound = min(query.as_of, query.end) if query.end else query.as_of
        if any(bar.timestamp > upper_bound for bar in envelope.bars):
            issues.append("bar after query upper bound")
        if query.start is not None and any(
            bar.timestamp < query.start for bar in envelope.bars
        ):
            issues.append("bar before query start")
        if envelope.is_synthetic and query.purpose is not DataPurpose.DEMO:
            issues.append("synthetic data forbidden")
        if query.purpose in self._FORMAL_PURPOSES:
            if envelope.mode not in self._FORMAL_MODES:
                issues.append(f"mode {envelope.mode.value} forbidden")
            if envelope.quality is not QualityStatus.VALID:
                issues.append(f"quality {envelope.quality.value}")
        elif query.purpose is DataPurpose.BACKTEST:
            if envelope.mode is DataMode.DEMO or envelope.is_synthetic:
                issues.append("synthetic backtest data forbidden")
            if envelope.quality is QualityStatus.UNAVAILABLE:
                issues.append("data unavailable")
        if (
            self._require_trading_calendar
            and self._calendar_applies(query)
            and (
                envelope.calendar_snapshot_id is None
                or envelope.calendar_content_sha256 is None
            )
        ):
            issues.append("calendar evidence unavailable")
        return issues
