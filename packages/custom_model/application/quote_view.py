from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
import math
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from custom_model.application.ports import QuoteDataProvider
from custom_model.application.trading_calendar import (
    TradingCalendarService,
    TradingCalendarUnavailableError,
)
from custom_model.domain.models import (
    DataMode,
    DataPurpose,
    Exchange,
    Market,
    QualityStatus,
    QuoteEnvelope,
    QuoteQuery,
    QuoteSessionStatus,
    QuoteTemporalMode,
)


QUOTE_VIEW_SCHEMA_VERSION = "quote-view/v2"


class QuoteUnavailableError(RuntimeError):
    """No provider produced an admissible native quote for this request."""

    def __init__(self, query: QuoteQuery, attempts: Sequence[str]) -> None:
        self.query = query
        self.attempts = tuple(attempts)
        detail = "; ".join(self.attempts) if self.attempts else "no provider supports query"
        super().__init__(f"quote unavailable for {query.instrument.key}: {detail}")


@dataclass(frozen=True)
class QuoteViewResult:
    schema_version: str
    quote: QuoteEnvelope
    formal_use_eligible: bool


class QuoteViewService:
    """Admit a native quote without reconstructing it from historical bars."""

    _ADMITTED_MODES = frozenset({DataMode.LIVE, DataMode.DELAYED})
    _DISPLAY_PURPOSES = frozenset({DataPurpose.RESEARCH, DataPurpose.REPORT})
    _CN_CASH_EXCHANGES = frozenset({Exchange.SSE, Exchange.SZSE, Exchange.BSE})

    def __init__(
        self,
        providers: Sequence[QuoteDataProvider],
        trading_calendar_service: TradingCalendarService | None = None,
        *,
        require_trading_calendar: bool = False,
    ) -> None:
        self._providers = tuple(providers)
        self._trading_calendar_service = trading_calendar_service
        self._require_trading_calendar = require_trading_calendar
        if require_trading_calendar and trading_calendar_service is None:
            raise ValueError("required trading calendar service is missing")

    @property
    def providers(self) -> tuple[QuoteDataProvider, ...]:
        return self._providers

    @property
    def trading_calendar_service(self) -> TradingCalendarService | None:
        return self._trading_calendar_service

    @property
    def requires_trading_calendar(self) -> bool:
        return self._require_trading_calendar

    def query(self, query: QuoteQuery) -> QuoteViewResult:
        attempts: list[str] = []
        supported: list[QuoteDataProvider] = []
        for provider in self._providers:
            name = self._provider_name(provider)
            try:
                if provider.supports(query):
                    supported.append(provider)
            except Exception as exc:
                attempts.append(f"{name}: supports {type(exc).__name__}")
        if not supported:
            raise QuoteUnavailableError(query, attempts)

        for provider in supported:
            name = self._provider_name(provider)
            if provider.production_ready is not True:
                attempts.append(f"{name}: not production ready")
                continue
            try:
                quote = provider.fetch_quote(query)
            except Exception as exc:  # provider boundary; never expose bodies or URLs
                attempts.append(f"{name}: {self._failure_label(exc)}")
                continue
            if not isinstance(quote, QuoteEnvelope):
                attempts.append(f"{name}: invalid envelope type")
                continue

            violations = self._violations(query, quote)
            if violations:
                attempts.append(f"{name}: {', '.join(violations)}")
                continue
            try:
                quote = self._attach_calendar_evidence(query, quote)
            except Exception as exc:
                attempts.append(f"{name}: calendar {self._failure_label(exc)}")
                continue
            if attempts:
                quote = quote.model_copy(
                    update={"warnings": [*quote.warnings, *attempts]}
                )
            return QuoteViewResult(
                schema_version=QUOTE_VIEW_SCHEMA_VERSION,
                quote=quote,
                formal_use_eligible=self._formal_use_eligible(query, quote),
            )

        raise QuoteUnavailableError(query, attempts)

    @classmethod
    def _violations(cls, query: QuoteQuery, quote: QuoteEnvelope) -> list[str]:
        issues: list[str] = []
        if quote.instrument != query.instrument:
            issues.append("instrument mismatch")
        if quote.purpose is not query.purpose:
            issues.append("purpose mismatch")
        if quote.temporal_mode is not query.temporal_mode:
            issues.append("temporal mode mismatch")
        if quote.requested_at != query.requested_at:
            issues.append("requested_at mismatch")
        if query.temporal_mode is QuoteTemporalMode.EXACT:
            if quote.as_of != query.as_of:
                issues.append("exact as_of mismatch")
            capture_delay = (quote.fetched_at - quote.as_of).total_seconds()
            if capture_delay < 0 or capture_delay > query.max_live_age_seconds:
                issues.append("exact snapshot exceeds the live capture-age limit")
        else:
            if query.requested_at is None or quote.as_of < query.requested_at:
                issues.append("latest snapshot precedes requested_at")
            if quote.as_of != quote.fetched_at:
                issues.append("latest as_of is not fetch completion time")
        if quote.currency != query.instrument.currency:
            issues.append("currency mismatch")
        if cls._unknown(quote.provider):
            issues.append("unknown provider")
        if not quote.source_chain or any(cls._unknown(item) for item in quote.source_chain):
            issues.append("unknown source")
        if quote.mode not in cls._ADMITTED_MODES:
            issues.append("mode forbidden")
        if quote.quality not in {QualityStatus.VALID, QualityStatus.STALE}:
            issues.append("quality unavailable")
        if quote.is_synthetic:
            issues.append("synthetic quote forbidden")

        for field_name in ("as_of", "fetched_at", "data_time", "provider_updated_at"):
            value = getattr(quote, field_name, None)
            if (
                not isinstance(value, datetime)
                or value.tzinfo is None
                or value.utcoffset() is None
            ):
                issues.append(f"{field_name} must be timezone-aware")
        if quote.as_of > quote.fetched_at:
            issues.append("as_of after fetch completion")
        if quote.data_time > quote.as_of or quote.provider_updated_at > quote.as_of:
            issues.append("future quote evidence")
        else:
            actual_age = (quote.as_of - quote.data_time).total_seconds()
            if not math.isfinite(actual_age) or abs(actual_age - quote.freshness_seconds) > 1.0:
                issues.append("freshness mismatch")

        try:
            ZoneInfo(quote.timezone)
        except (TypeError, ValueError, ZoneInfoNotFoundError):
            issues.append("invalid timezone")

        finite_fields = (
            "last_price",
            "previous_close",
            "open",
            "high",
            "low",
            "change",
            "change_percent",
            "volume_lots",
            "amount",
        )
        for field_name in finite_fields:
            value = getattr(quote, field_name, None)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                issues.append(f"invalid {field_name}")
            elif not math.isfinite(float(value)):
                issues.append(f"non-finite {field_name}")

        if query.purpose is DataPurpose.ALERT and (
            quote.mode is not DataMode.LIVE
            or quote.quality is not QualityStatus.VALID
            or quote.session_status is not QuoteSessionStatus.OPEN
            or not quote.trade_status_verified
        ):
            issues.append("alert quote must be current live session data")
        elif query.purpose is DataPurpose.SCREENER and (
            quote.quality is not QualityStatus.VALID
            or not quote.trade_status_verified
        ):
            issues.append("screener quote must be valid with verified trade status")
        elif (
            quote.quality is QualityStatus.STALE
            and query.purpose not in cls._DISPLAY_PURPOSES
        ):
            issues.append("stale quote forbidden for this purpose")
        return list(dict.fromkeys(issues))

    def _formal_use_eligible(self, query: QuoteQuery, quote: QuoteEnvelope) -> bool:
        if (
            quote.quality is not QualityStatus.VALID
            or quote.is_synthetic
            or not quote.trade_status_verified
        ):
            return False
        if self._calendar_applies(query) and self._require_trading_calendar and (
            quote.calendar_snapshot_id is None
            or quote.calendar_content_sha256 is None
        ):
            return False
        if query.purpose is DataPurpose.ALERT:
            return (
                quote.mode is DataMode.LIVE
                and quote.session_status is QuoteSessionStatus.OPEN
            )
        return quote.mode in {DataMode.LIVE, DataMode.DELAYED}

    def _calendar_applies(self, query: QuoteQuery) -> bool:
        return (
            query.instrument.market is Market.CN
            and query.instrument.exchange in self._CN_CASH_EXCHANGES
        )

    def _attach_calendar_evidence(
        self, query: QuoteQuery, quote: QuoteEnvelope
    ) -> QuoteEnvelope:
        if not self._calendar_applies(query):
            return quote
        if self._trading_calendar_service is None:
            if self._require_trading_calendar:
                raise TradingCalendarUnavailableError(
                    "formal CN quote requires an admitted trading calendar"
                )
            return quote
        state = self._trading_calendar_service.session_at(
            query.instrument.exchange, quote.as_of
        )
        evidence = state.evidence
        claimed = (quote.calendar_snapshot_id, quote.calendar_content_sha256)
        admitted = (evidence.snapshot_id, evidence.content_sha256)
        if claimed != (None, None) and claimed != admitted:
            raise TradingCalendarUnavailableError(
                "provider quote calendar evidence conflicts with admitted snapshot"
            )
        warning = (
            f"trading calendar snapshot {evidence.snapshot_id} "
            f"admitted; session phase={state.phase.value}"
        )
        return quote.model_copy(
            update={
                "calendar_snapshot_id": evidence.snapshot_id,
                "calendar_content_sha256": evidence.content_sha256,
                "warnings": list(dict.fromkeys([*quote.warnings, warning])),
            }
        )

    @staticmethod
    def _provider_name(provider: object) -> str:
        name = getattr(provider, "name", None)
        if isinstance(name, str) and name.strip():
            return name.strip()
        return type(provider).__name__

    @staticmethod
    def _failure_label(exc: Exception) -> str:
        kind = getattr(exc, "kind", None)
        value = getattr(kind, "value", None)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return type(exc).__name__

    @staticmethod
    def _unknown(value: object) -> bool:
        return not isinstance(value, str) or value.strip().lower() in {
            "",
            "unknown",
            "none",
            "n/a",
        }
