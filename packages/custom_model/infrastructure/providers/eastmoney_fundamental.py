from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, time as datetime_time
from enum import Enum
import http.client
import json
import math
from numbers import Integral, Real
import re
import socket
import threading
import time
from typing import Any, Protocol
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

from custom_model.application.ports import ProviderHealth
from custom_model.domain.models import (
    AssetType,
    DataMode,
    DataPurpose,
    Exchange,
    FundamentalEnvelope,
    FundamentalEvidenceBasis,
    FundamentalField,
    FundamentalFieldStatus,
    FundamentalQuery,
    Market,
    QualityStatus,
)


_SHANGHAI = ZoneInfo("Asia/Shanghai")
_MISSING = object()
_DISPLAY_TIME = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})[ T]"
    r"(?P<clock>\d{2}:\d{2}:\d{2}):(?P<fraction>\d{1,6})$"
)
_CN_SYMBOL = re.compile(r"^(?P<code>\d{6})(?:\.(?P<suffix>SS|SH|SZ|BJ))?$")


class EastmoneyFailureKind(str, Enum):
    """Stable, sanitized failure categories exposed at the provider boundary."""

    TIMEOUT = "timeout"
    CONNECTION = "connection_error"
    RATE_LIMITED = "rate_limited"
    UPSTREAM = "upstream_error"
    INVALID_PAYLOAD = "invalid_payload"
    UNSUPPORTED = "unsupported_query"
    CIRCUIT_OPEN = "circuit_open"


class EastmoneyFundamentalError(RuntimeError):
    """A provider error that never includes upstream bodies, URLs or secrets."""

    def __init__(self, kind: EastmoneyFailureKind, message: str) -> None:
        self.kind = kind
        super().__init__(message)


class EastmoneyTransportError(RuntimeError):
    """Internal transport classification used by the default HTTP adapters."""

    def __init__(self, kind: EastmoneyFailureKind, status_code: int | None = None) -> None:
        self.kind = kind
        self.status_code = status_code
        super().__init__(kind.value)


class EastmoneyJsonTransport(Protocol):
    def get_json(
        self,
        url: str,
        params: Mapping[str, str | int],
        *,
        connect_timeout: float,
        read_timeout: float,
    ) -> Mapping[str, Any]: ...


class _StdlibJsonTransport:
    """Small dependency-free HTTPS transport with separate hard timeouts."""

    _MAX_RESPONSE_BYTES = 16 * 1024 * 1024

    def get_json(
        self,
        url: str,
        params: Mapping[str, str | int],
        *,
        connect_timeout: float,
        read_timeout: float,
    ) -> Mapping[str, Any]:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise EastmoneyTransportError(EastmoneyFailureKind.INVALID_PAYLOAD)
        query = urlencode(params)
        path = parsed.path or "/"
        if parsed.query:
            query = f"{parsed.query}&{query}" if query else parsed.query
        if query:
            path = f"{path}?{query}"

        connection = http.client.HTTPSConnection(
            parsed.hostname,
            port=parsed.port,
            timeout=connect_timeout,
        )
        try:
            connection.request(
                "GET",
                path,
                headers={
                    "Accept": "application/json,text/plain,*/*",
                    "Accept-Encoding": "identity",
                    "User-Agent": "Custom-Model/1.0 EastmoneyFundamentalProvider",
                },
            )
            if connection.sock is not None:
                connection.sock.settimeout(read_timeout)
            response = connection.getresponse()
            if response.status == 429:
                raise EastmoneyTransportError(
                    EastmoneyFailureKind.RATE_LIMITED, response.status
                )
            if response.status >= 500:
                raise EastmoneyTransportError(
                    EastmoneyFailureKind.UPSTREAM, response.status
                )
            if response.status >= 400:
                raise EastmoneyTransportError(
                    EastmoneyFailureKind.INVALID_PAYLOAD, response.status
                )
            body = response.read(self._MAX_RESPONSE_BYTES + 1)
            if len(body) > self._MAX_RESPONSE_BYTES:
                raise EastmoneyTransportError(EastmoneyFailureKind.INVALID_PAYLOAD)
            try:
                payload = json.loads(body.decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise EastmoneyTransportError(
                    EastmoneyFailureKind.INVALID_PAYLOAD
                ) from exc
            if not isinstance(payload, Mapping):
                raise EastmoneyTransportError(EastmoneyFailureKind.INVALID_PAYLOAD)
            return payload
        finally:
            connection.close()


class _SessionJsonTransport:
    """Adapter for an injected requests-compatible session."""

    def __init__(self, session: object) -> None:
        self._session = session

    def get_json(
        self,
        url: str,
        params: Mapping[str, str | int],
        *,
        connect_timeout: float,
        read_timeout: float,
    ) -> Mapping[str, Any]:
        get = getattr(self._session, "get", None)
        if not callable(get):
            raise EastmoneyTransportError(EastmoneyFailureKind.INVALID_PAYLOAD)
        response = get(
            url,
            params=dict(params),
            timeout=(connect_timeout, read_timeout),
            headers={
                "Accept": "application/json,text/plain,*/*",
                "User-Agent": "Custom-Model/1.0 EastmoneyFundamentalProvider",
            },
        )
        status = getattr(response, "status_code", None)
        if status == 429:
            raise EastmoneyTransportError(EastmoneyFailureKind.RATE_LIMITED, status)
        if isinstance(status, int) and status >= 500:
            raise EastmoneyTransportError(EastmoneyFailureKind.UPSTREAM, status)
        if isinstance(status, int) and status >= 400:
            raise EastmoneyTransportError(EastmoneyFailureKind.INVALID_PAYLOAD, status)
        decode = getattr(response, "json", None)
        if not callable(decode):
            raise EastmoneyTransportError(EastmoneyFailureKind.INVALID_PAYLOAD)
        try:
            payload = decode()
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise EastmoneyTransportError(EastmoneyFailureKind.INVALID_PAYLOAD) from exc
        if not isinstance(payload, Mapping):
            raise EastmoneyTransportError(EastmoneyFailureKind.INVALID_PAYLOAD)
        return payload


@dataclass(frozen=True)
class _FinanceSelection:
    row: Mapping[str, Any]
    period_end: datetime
    update_date: datetime
    notice_date: datetime


@dataclass(frozen=True)
class _ValuationSelection:
    row: Mapping[str, Any]
    market_time: datetime


class EastmoneyFundamentalProvider:
    """Point-in-time A-share fundamentals from three Eastmoney JSON endpoints.

    Financial fields are verified only when the selected report can be joined
    to the issuer's full-report announcement and its millisecond-resolution
    ``display_time``.  ``NOTICE_DATE`` and ``UPDATE_DATE`` are selection gates,
    never substitutes for publication evidence.
    """

    name = "eastmoney"
    _FINANCE_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    _VALUATION_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    _ANNOUNCEMENT_URL = "https://np-anotice-stock.eastmoney.com/api/security/ann"
    _SOURCE_CHAIN = (
        "eastmoney:RPT_F10_FINANCE_MAINFINADATA",
        "eastmoney:RPT_VALUEANALYSIS_DET",
        "eastmoney:np-anotice/security/ann",
    )
    _SUPPORTED_PURPOSES = frozenset(
        {DataPurpose.RESEARCH, DataPurpose.SCREENER, DataPurpose.REPORT}
    )
    _SUFFIX_BY_EXCHANGE = {
        Exchange.SSE: "SH",
        Exchange.SZSE: "SZ",
        Exchange.BSE: "BJ",
    }
    _TRANSIENT_FAILURES = frozenset(
        {
            EastmoneyFailureKind.TIMEOUT,
            EastmoneyFailureKind.CONNECTION,
            EastmoneyFailureKind.RATE_LIMITED,
            EastmoneyFailureKind.UPSTREAM,
        }
    )

    def __init__(
        self,
        transport: EastmoneyJsonTransport | None = None,
        *,
        session: object | None = None,
        connect_timeout_seconds: float = 3.0,
        read_timeout_seconds: float = 6.0,
        max_retries: int = 2,
        backoff_seconds: float = 0.25,
        circuit_failure_threshold: int = 3,
        circuit_reset_seconds: float = 60.0,
        debt_ratio_tolerance: float = 0.005,
        announcement_page_size: int = 100,
        max_announcement_pages: int = 3,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
        production_ready: bool = True,
    ) -> None:
        if transport is not None and session is not None:
            raise ValueError("provide either transport or session, not both")
        self._validate_positive_finite(
            connect_timeout_seconds, "connect_timeout_seconds"
        )
        self._validate_positive_finite(read_timeout_seconds, "read_timeout_seconds")
        self._validate_nonnegative_finite(backoff_seconds, "backoff_seconds")
        self._validate_positive_finite(circuit_reset_seconds, "circuit_reset_seconds")
        self._validate_nonnegative_finite(debt_ratio_tolerance, "debt_ratio_tolerance")
        if not isinstance(max_retries, int) or isinstance(max_retries, bool):
            raise ValueError("max_retries must be an integer")
        if not 0 <= max_retries <= 2:
            raise ValueError("max_retries must be between zero and two")
        if (
            not isinstance(circuit_failure_threshold, int)
            or isinstance(circuit_failure_threshold, bool)
            or circuit_failure_threshold < 1
        ):
            raise ValueError("circuit_failure_threshold must be a positive integer")
        if not isinstance(announcement_page_size, int) or not 1 <= announcement_page_size <= 100:
            raise ValueError("announcement_page_size must be between 1 and 100")
        if not isinstance(max_announcement_pages, int) or not 1 <= max_announcement_pages <= 10:
            raise ValueError("max_announcement_pages must be between 1 and 10")

        if session is not None:
            self._transport: EastmoneyJsonTransport = _SessionJsonTransport(session)
        else:
            self._transport = transport or _StdlibJsonTransport()
        self._connect_timeout = float(connect_timeout_seconds)
        self._read_timeout = float(read_timeout_seconds)
        self._max_retries = max_retries
        self._backoff_seconds = float(backoff_seconds)
        self._circuit_failure_threshold = circuit_failure_threshold
        self._circuit_reset_seconds = float(circuit_reset_seconds)
        self._debt_ratio_tolerance = float(debt_ratio_tolerance)
        self._announcement_page_size = announcement_page_size
        self._max_announcement_pages = max_announcement_pages
        self._clock = clock or (lambda: datetime.now(tz=ZoneInfo("UTC")))
        self._monotonic = monotonic or time.monotonic
        self._sleep = sleep or time.sleep
        self.production_ready = production_ready

        self._breaker_lock = threading.Lock()
        self._consecutive_failures = 0
        self._breaker_open_until: float | None = None
        self._last_failure_kind: EastmoneyFailureKind | None = None

    def supports(self, query: FundamentalQuery) -> bool:
        return (
            query.instrument.market is Market.CN
            and query.instrument.asset_type is AssetType.STOCK
            and query.instrument.exchange in self._SUFFIX_BY_EXCHANGE
            and query.instrument.currency == "CNY"
            and query.purpose in self._SUPPORTED_PURPOSES
            and self._security_code(query) is not None
        )

    def fetch_fundamentals(self, query: FundamentalQuery) -> FundamentalEnvelope:
        code = self._security_code(query)
        if not self.supports(query) or code is None:
            raise EastmoneyFundamentalError(
                EastmoneyFailureKind.UNSUPPORTED,
                "eastmoney fundamentals support only CNY A-share stocks for research, screening and reports",
            )

        secucode = f"{code}.{self._SUFFIX_BY_EXCHANGE[query.instrument.exchange]}"
        finance_payload = self._request_json(
            "finance",
            self._FINANCE_URL,
            {
                "reportName": "RPT_F10_FINANCE_MAINFINADATA",
                "columns": "ALL",
                "filter": f'(SECUCODE="{secucode}")',
                "pageNumber": 1,
                "pageSize": 50,
                "sortColumns": "REPORT_DATE,UPDATE_DATE",
                "sortTypes": "-1,-1",
                "source": "WEB",
                "client": "WEB",
            },
        )
        finance_rows = self._datacenter_rows(finance_payload, "finance")
        finance = self._select_finance_row(
            finance_rows,
            code=code,
            secucode=secucode,
            as_of=query.as_of,
        )

        valuation_payload = self._request_json(
            "valuation",
            self._VALUATION_URL,
            {
                "reportName": "RPT_VALUEANALYSIS_DET",
                "columns": "ALL",
                "filter": (
                    f'(SECURITY_CODE="{code}")'
                    f"(TRADE_DATE<='{query.as_of.astimezone(_SHANGHAI):%Y-%m-%d}')"
                ),
                "pageNumber": 1,
                "pageSize": 50,
                "sortColumns": "TRADE_DATE",
                "sortTypes": "-1",
                "source": "WEB",
                "client": "WEB",
            },
        )
        valuation_rows = self._datacenter_rows(valuation_payload, "valuation")
        valuation = self._select_valuation_row(
            valuation_rows,
            code=code,
            secucode=secucode,
            as_of=query.as_of,
        )

        published_at, announcement_warnings = self._find_publication_time(
            code=code,
            finance=finance,
            as_of=query.as_of,
        )
        fields, field_warnings = self._build_fields(
            finance,
            valuation,
            published_at=published_at,
        )
        if not fields:
            raise EastmoneyFundamentalError(
                EastmoneyFailureKind.INVALID_PAYLOAD,
                "eastmoney normalization produced no evidenced fundamental fields",
            )

        evidence_times = [
            evidence_time
            for field in fields
            for evidence_time in (field.published_at, field.market_time)
            if evidence_time is not None
        ]
        if not evidence_times:
            raise EastmoneyFundamentalError(
                EastmoneyFailureKind.INVALID_PAYLOAD,
                "eastmoney payload has no trustworthy evidence timestamp",
            )
        data_time = max(evidence_times)
        if data_time > query.as_of:
            raise EastmoneyFundamentalError(
                EastmoneyFailureKind.INVALID_PAYLOAD,
                "eastmoney evidence is after the requested as_of",
            )

        fetched_at = self._clock()
        if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
            raise EastmoneyFundamentalError(
                EastmoneyFailureKind.INVALID_PAYLOAD,
                "provider clock returned a timezone-naive timestamp",
            )
        quality = self._quality(query, fields)
        warnings = [
            "Eastmoney A-share fundamentals and end-of-day valuation data are labeled delayed",
            "financial publication evidence comes only from an exact full-report announcement display_time",
            *announcement_warnings,
            *field_warnings,
        ]
        try:
            return FundamentalEnvelope(
                instrument=query.instrument,
                purpose=query.purpose,
                provider=self.name,
                mode=DataMode.DELAYED,
                quality=quality,
                as_of=query.as_of,
                fetched_at=fetched_at,
                data_time=data_time,
                data_time_basis="latest_field_evidence_time",
                timezone="Asia/Shanghai",
                currency="CNY",
                freshness_seconds=(query.as_of - data_time).total_seconds(),
                fields=fields,
                warnings=warnings,
                is_synthetic=False,
                source_chain=list(self._SOURCE_CHAIN),
            )
        except EastmoneyFundamentalError:
            raise
        except Exception as exc:
            raise EastmoneyFundamentalError(
                EastmoneyFailureKind.INVALID_PAYLOAD,
                f"eastmoney envelope violated the contract with {type(exc).__name__}",
            ) from exc

    def health(self) -> ProviderHealth:
        """Return local admission/breaker state without making a network call."""

        now = self._monotonic()
        with self._breaker_lock:
            circuit_open = (
                self._breaker_open_until is not None
                and now < self._breaker_open_until
            )
            last_failure = self._last_failure_kind
        if not self.production_ready:
            message = "provider is not admitted for production"
        elif circuit_open:
            suffix = f" after {last_failure.value}" if last_failure is not None else ""
            message = f"provider circuit is open{suffix}"
        else:
            message = "provider configured; upstream health is checked per request"
        return ProviderHealth(
            provider=self.name,
            healthy=self.production_ready and not circuit_open,
            checked_at=self._clock(),
            message=message,
            production_ready=self.production_ready,
        )

    def _request_json(
        self,
        endpoint: str,
        url: str,
        params: Mapping[str, str | int],
    ) -> Mapping[str, Any]:
        self._admit_request()
        for attempt in range(self._max_retries + 1):
            try:
                payload = self._transport.get_json(
                    url,
                    params,
                    connect_timeout=self._connect_timeout,
                    read_timeout=self._read_timeout,
                )
                if not isinstance(payload, Mapping):
                    raise EastmoneyTransportError(
                        EastmoneyFailureKind.INVALID_PAYLOAD
                    )
            except Exception as exc:
                kind = self._classify_transport_failure(exc)
                self._record_failure(kind)
                can_retry = (
                    kind in self._TRANSIENT_FAILURES
                    and attempt < self._max_retries
                    and not self._circuit_is_open()
                )
                if can_retry:
                    self._sleep(self._backoff_seconds * (2**attempt))
                    continue
                raise EastmoneyFundamentalError(
                    kind,
                    f"eastmoney {endpoint} request failed ({kind.value})",
                ) from exc
            self._record_success()
            return payload
        raise AssertionError("unreachable retry state")

    def _admit_request(self) -> None:
        now = self._monotonic()
        with self._breaker_lock:
            if self._breaker_open_until is None:
                return
            if now < self._breaker_open_until:
                raise EastmoneyFundamentalError(
                    EastmoneyFailureKind.CIRCUIT_OPEN,
                    "eastmoney provider circuit is open",
                )
            self._breaker_open_until = None
            self._consecutive_failures = max(
                0, self._circuit_failure_threshold - 1
            )

    def _record_failure(self, kind: EastmoneyFailureKind) -> None:
        with self._breaker_lock:
            self._last_failure_kind = kind
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._circuit_failure_threshold:
                self._breaker_open_until = (
                    self._monotonic() + self._circuit_reset_seconds
                )

    def _record_success(self) -> None:
        with self._breaker_lock:
            self._consecutive_failures = 0
            self._breaker_open_until = None
            self._last_failure_kind = None

    def _circuit_is_open(self) -> bool:
        now = self._monotonic()
        with self._breaker_lock:
            return (
                self._breaker_open_until is not None
                and now < self._breaker_open_until
            )

    @staticmethod
    def _classify_transport_failure(exc: Exception) -> EastmoneyFailureKind:
        if isinstance(exc, EastmoneyTransportError):
            return exc.kind
        if isinstance(exc, EastmoneyFundamentalError):
            return exc.kind
        if isinstance(exc, (TimeoutError, socket.timeout)):
            return EastmoneyFailureKind.TIMEOUT
        if isinstance(exc, (ConnectionError, OSError, http.client.HTTPException)):
            return EastmoneyFailureKind.CONNECTION
        exception_names = {
            candidate.__name__.lower() for candidate in type(exc).__mro__
        }
        if any("timeout" in name for name in exception_names):
            return EastmoneyFailureKind.TIMEOUT
        if any("connection" in name for name in exception_names):
            return EastmoneyFailureKind.CONNECTION
        return EastmoneyFailureKind.UPSTREAM

    @staticmethod
    def _datacenter_rows(
        payload: Mapping[str, Any], endpoint: str
    ) -> list[Mapping[str, Any]]:
        result = payload.get("result")
        if payload.get("success") is not True or not isinstance(result, Mapping):
            raise EastmoneyFundamentalError(
                EastmoneyFailureKind.INVALID_PAYLOAD,
                f"eastmoney {endpoint} response has an invalid result envelope",
            )
        data = result.get("data")
        if not isinstance(data, Sequence) or isinstance(data, (str, bytes)):
            raise EastmoneyFundamentalError(
                EastmoneyFailureKind.INVALID_PAYLOAD,
                f"eastmoney {endpoint} response has no data rows",
            )
        rows = [row for row in data if isinstance(row, Mapping)]
        if len(rows) != len(data):
            raise EastmoneyFundamentalError(
                EastmoneyFailureKind.INVALID_PAYLOAD,
                f"eastmoney {endpoint} response contains invalid rows",
            )
        return rows

    @classmethod
    def _select_finance_row(
        cls,
        rows: Sequence[Mapping[str, Any]],
        *,
        code: str,
        secucode: str,
        as_of: datetime,
    ) -> _FinanceSelection:
        eligible: list[_FinanceSelection] = []
        for row in rows:
            if not cls._matches_security(row, code=code, secucode=secucode):
                continue
            period_end = cls._local_datetime(row.get("REPORT_DATE"))
            update_date = cls._update_date_gate(row.get("UPDATE_DATE"))
            notice_date = cls._local_datetime(row.get("NOTICE_DATE"))
            if period_end is None or update_date is None or notice_date is None:
                continue
            if period_end > as_of or update_date > as_of:
                continue
            eligible.append(
                _FinanceSelection(
                    row=row,
                    period_end=period_end,
                    update_date=update_date,
                    notice_date=notice_date,
                )
            )
        if not eligible:
            raise EastmoneyFundamentalError(
                EastmoneyFailureKind.INVALID_PAYLOAD,
                "eastmoney has no finance row available at the requested as_of",
            )
        return max(eligible, key=lambda item: (item.period_end, item.update_date))

    @classmethod
    def _select_valuation_row(
        cls,
        rows: Sequence[Mapping[str, Any]],
        *,
        code: str,
        secucode: str,
        as_of: datetime,
    ) -> _ValuationSelection:
        eligible: list[_ValuationSelection] = []
        for row in rows:
            if not cls._matches_security(row, code=code, secucode=secucode):
                continue
            market_time = cls._market_close(row.get("TRADE_DATE"))
            if market_time is None or market_time > as_of:
                continue
            eligible.append(_ValuationSelection(row=row, market_time=market_time))
        if not eligible:
            raise EastmoneyFundamentalError(
                EastmoneyFailureKind.INVALID_PAYLOAD,
                "eastmoney has no completed market close available at the requested as_of",
            )
        return max(eligible, key=lambda item: item.market_time)

    def _find_publication_time(
        self,
        *,
        code: str,
        finance: _FinanceSelection,
        as_of: datetime,
    ) -> tuple[datetime | None, list[str]]:
        matching_without_exact_time = False
        matching_after_as_of = False
        for page_index in range(1, self._max_announcement_pages + 1):
            payload = self._request_json(
                "announcement",
                self._ANNOUNCEMENT_URL,
                {
                    "page_size": self._announcement_page_size,
                    "page_index": page_index,
                    "ann_type": "A",
                    "client_source": "web",
                    "stock_list": code,
                    "begin_time": f"{finance.notice_date.astimezone(_SHANGHAI):%Y-%m-%d}",
                    "end_time": f"{finance.notice_date.astimezone(_SHANGHAI):%Y-%m-%d}",
                },
            )
            data = payload.get("data")
            if not isinstance(data, Mapping):
                raise EastmoneyFundamentalError(
                    EastmoneyFailureKind.INVALID_PAYLOAD,
                    "eastmoney announcement response has an invalid data envelope",
                )
            items = data.get("list")
            if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
                raise EastmoneyFundamentalError(
                    EastmoneyFailureKind.INVALID_PAYLOAD,
                    "eastmoney announcement response has no announcement list",
                )
            if any(not isinstance(item, Mapping) for item in items):
                raise EastmoneyFundamentalError(
                    EastmoneyFailureKind.INVALID_PAYLOAD,
                    "eastmoney announcement response contains invalid items",
                )
            candidates: list[datetime] = []
            for item in items:
                if not self._announcement_matches(item, code=code, finance=finance):
                    continue
                display_time = self._exact_display_time(item.get("display_time"))
                if display_time is None:
                    matching_without_exact_time = True
                    continue
                if display_time > as_of:
                    matching_after_as_of = True
                    continue
                candidates.append(display_time)
            if candidates:
                return max(candidates), []

            total_hits = self._nonnegative_integer(data.get("total_hits"))
            if len(items) < self._announcement_page_size:
                break
            if total_hits is not None and page_index * self._announcement_page_size >= total_hits:
                break

        if matching_without_exact_time:
            reason = "matching full-report announcement has no exact display_time"
        elif matching_after_as_of:
            reason = "matching full-report announcement display_time is after as_of"
        else:
            reason = "no exact code/period/full-report announcement display_time was found"
        return None, [f"{reason}; financial fields remain unverified"]

    @classmethod
    def _announcement_matches(
        cls,
        item: Mapping[str, Any],
        *,
        code: str,
        finance: _FinanceSelection,
    ) -> bool:
        codes = item.get("codes")
        if not isinstance(codes, Sequence) or isinstance(codes, (str, bytes)):
            return False
        if not any(
            isinstance(candidate, Mapping)
            and str(candidate.get("stock_code", "")).strip() == code
            for candidate in codes
        ):
            return False

        title = item.get("title")
        if not isinstance(title, str) or not title.strip():
            return False
        normalized_title = "".join(title.split()).replace("：", ":")
        columns = item.get("columns")
        column_names: list[str] = []
        if isinstance(columns, Sequence) and not isinstance(columns, (str, bytes)):
            column_names = [
                "".join(str(column.get("column_name", "")).split())
                for column in columns
                if isinstance(column, Mapping)
            ]
        combined = normalized_title + "|" + "|".join(column_names)
        if any(token in combined for token in ("摘要", "英文版", "取消")):
            return False

        expected = cls._expected_report_title(finance)
        if expected is None or expected not in normalized_title:
            return False
        suffix = normalized_title.rsplit(":", 1)[-1]
        allowed_suffixes = {
            expected,
            f"{expected}(修订版)",
            f"{expected}（修订版）",
            f"{expected}(更正版)",
            f"{expected}（更正版）",
        }
        if suffix not in allowed_suffixes:
            return False

        if "半年度报告" in expected or "年度报告" in expected:
            return any("报告全文" in name for name in column_names)
        return True

    @staticmethod
    def _expected_report_title(finance: _FinanceSelection) -> str | None:
        row = finance.row
        report_type = "".join(
            (
                str(row.get("REPORT_TYPE", "")),
                str(row.get("REPORT_DATE_NAME", "")),
            )
        )
        year = finance.period_end.astimezone(_SHANGHAI).year
        if "一季" in report_type or "第一季度" in report_type:
            period_name = "第一季度报告"
        elif "中报" in report_type or "半年度" in report_type:
            period_name = "半年度报告"
        elif "三季" in report_type or "第三季度" in report_type:
            period_name = "第三季度报告"
        elif "年报" in report_type or "年度" in report_type:
            period_name = "年度报告"
        else:
            period_name = {
                3: "第一季度报告",
                6: "半年度报告",
                9: "第三季度报告",
                12: "年度报告",
            }.get(finance.period_end.astimezone(_SHANGHAI).month)
        return f"{year}年{period_name}" if period_name else None

    def _build_fields(
        self,
        finance: _FinanceSelection,
        valuation: _ValuationSelection,
        *,
        published_at: datetime | None,
    ) -> tuple[list[FundamentalField], list[str]]:
        fields: list[FundamentalField] = []
        warnings: list[str] = []
        row = finance.row
        currency = str(row.get("CURRENCY", "")).strip().upper()
        if currency != "CNY":
            raise EastmoneyFundamentalError(
                EastmoneyFailureKind.INVALID_PAYLOAD,
                "eastmoney finance row has no verified CNY currency",
            )
        financial_status = (
            FundamentalFieldStatus.VERIFIED
            if published_at is not None
            else FundamentalFieldStatus.UNVERIFIED
        )

        name = self._native_text(row.get("SECURITY_NAME_ABBR"))
        if name is not None:
            fields.append(
                FundamentalField(
                    name="name",
                    source_field="RPT_F10_FINANCE_MAINFINADATA.SECURITY_NAME_ABBR",
                    raw_value=name,
                    normalized_value=name,
                    basis=FundamentalEvidenceBasis.PROVIDER_METADATA,
                    status=FundamentalFieldStatus.VERIFIED,
                )
            )

        direct_financial = (
            ("ROE", "ROEJQ", "percent", 1.0),
            ("debt_ratio", "ZCFZL", "percent", 1.0),
            ("revenue", "TOTALOPERATEREVE", "CNY", 1.0),
            ("net_income", "PARENTNETPROFIT", "CNY", 1.0),
            ("total_assets", "TOTAL_ASSETS_PK", "CNY", 1.0),
            ("total_liabilities", "LIABILITY", "CNY", 1.0),
            ("equity", "TOTAL_EQUITY_PK", "CNY", 1.0),
            ("shares", "TOTAL_SHARE", "shares", 1.0),
        )
        for name_key, source_key, unit, scale in direct_financial:
            raw = self._native_number(row.get(source_key))
            if raw is _MISSING:
                warnings.append(f"omitted {name_key}: {source_key} is missing or non-finite")
                continue
            normalized: int | float
            if name_key == "shares" and float(raw).is_integer():
                normalized = int(raw)
            else:
                normalized = float(raw) * scale
            status = financial_status
            if name_key == "debt_ratio" and not self._debt_ratio_consistent(row, normalized):
                status = FundamentalFieldStatus.UNVERIFIED
                warnings.append(
                    "debt_ratio cross-check against LIABILITY/TOTAL_ASSETS_PK failed; field is unverified"
                )
            fields.append(
                FundamentalField(
                    name=name_key,
                    source_field=f"RPT_F10_FINANCE_MAINFINADATA.{source_key}",
                    raw_value=raw,
                    normalized_value=normalized,
                    unit=unit,
                    basis=FundamentalEvidenceBasis.FINANCIAL_STATEMENT,
                    status=status,
                    period_end=finance.period_end,
                    published_at=published_at,
                )
            )

        market_specs = (
            ("PE_TTM", "PE_TTM", "multiple"),
            ("PB_MRQ", "PB_MRQ", "multiple"),
            ("PS_TTM", "PS_TTM", "multiple"),
            ("market_cap", "TOTAL_MARKET_CAP", "CNY"),
            ("current_price", "CLOSE_PRICE", "CNY_per_share"),
        )
        for name_key, source_key, unit in market_specs:
            raw = self._native_number(valuation.row.get(source_key))
            if raw is _MISSING:
                warnings.append(f"omitted {name_key}: {source_key} is missing or non-finite")
                continue
            fields.append(
                FundamentalField(
                    name=name_key,
                    source_field=f"RPT_VALUEANALYSIS_DET.{source_key}",
                    raw_value=raw,
                    normalized_value=raw,
                    unit=unit,
                    basis=FundamentalEvidenceBasis.MARKET_SNAPSHOT,
                    status=FundamentalFieldStatus.VERIFIED,
                    market_time=valuation.market_time,
                )
            )
        return fields, warnings

    def _debt_ratio_consistent(
        self, row: Mapping[str, Any], reported_ratio: int | float
    ) -> bool:
        assets = self._native_number(row.get("TOTAL_ASSETS_PK"))
        liabilities = self._native_number(row.get("LIABILITY"))
        if assets is _MISSING or liabilities is _MISSING or float(assets) <= 0:
            return True
        computed = float(liabilities) / float(assets)
        reported_fraction = float(reported_ratio) / 100.0
        return math.isfinite(computed) and abs(computed - reported_fraction) <= self._debt_ratio_tolerance

    @staticmethod
    def _quality(
        query: FundamentalQuery, fields: Sequence[FundamentalField]
    ) -> QualityStatus:
        financial_bases = {
            FundamentalEvidenceBasis.FINANCIAL_STATEMENT,
            FundamentalEvidenceBasis.DERIVED_FINANCIAL_STATEMENT,
        }
        for field in fields:
            if field.status is not FundamentalFieldStatus.VERIFIED:
                continue
            if (
                field.basis is FundamentalEvidenceBasis.MARKET_SNAPSHOT
                and field.market_time is not None
                and (query.as_of - field.market_time).total_seconds()
                > query.max_market_age_seconds
            ):
                return QualityStatus.STALE
            if (
                field.basis in financial_bases
                and field.published_at is not None
                and (query.as_of - field.published_at).total_seconds()
                > query.max_financial_age_seconds
            ):
                return QualityStatus.STALE
        verified_financial = {
            field.name
            for field in fields
            if field.status is FundamentalFieldStatus.VERIFIED
            and field.basis in financial_bases
            and field.period_end is not None
            and field.published_at is not None
        }
        return (
            QualityStatus.VALID
            if set(query.required_filter_fields).issubset(verified_financial)
            and len(verified_financial) >= query.minimum_verified_financial_fields
            else QualityStatus.PARTIAL
        )

    @classmethod
    def _matches_security(
        cls, row: Mapping[str, Any], *, code: str, secucode: str
    ) -> bool:
        row_code = str(row.get("SECURITY_CODE", "")).strip().upper()
        row_secucode = str(row.get("SECUCODE", "")).strip().upper()
        return row_code == code and row_secucode == secucode

    @staticmethod
    def _local_datetime(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return (
                value.replace(tzinfo=_SHANGHAI)
                if value.tzinfo is None or value.utcoffset() is None
                else value.astimezone(_SHANGHAI)
            )
        if not isinstance(value, str) or not value.strip():
            return None
        text = value.strip().replace("/", "-")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return (
            parsed.replace(tzinfo=_SHANGHAI)
            if parsed.tzinfo is None or parsed.utcoffset() is None
            else parsed.astimezone(_SHANGHAI)
        )

    @classmethod
    def _market_close(cls, value: Any) -> datetime | None:
        parsed = cls._local_datetime(value)
        if parsed is None:
            return None
        return datetime.combine(
            parsed.astimezone(_SHANGHAI).date(),
            datetime_time(hour=15),
            tzinfo=_SHANGHAI,
        )

    @classmethod
    def _update_date_gate(cls, value: Any) -> datetime | None:
        """Treat a date-only UPDATE_DATE as unavailable until that local day ends."""

        parsed = cls._local_datetime(value)
        if parsed is None:
            return None
        is_date_only = False
        if isinstance(value, str):
            native = value.strip()
            is_date_only = bool(
                re.fullmatch(r"\d{4}[-/]\d{2}[-/]\d{2}", native)
                or re.fullmatch(
                    r"\d{4}[-/]\d{2}[-/]\d{2}[ T]00:00:00(?:\.0+)?",
                    native,
                )
            )
        if is_date_only:
            return datetime.combine(
                parsed.astimezone(_SHANGHAI).date(),
                datetime_time.max,
                tzinfo=_SHANGHAI,
            )
        return parsed

    @staticmethod
    def _exact_display_time(value: Any) -> datetime | None:
        if not isinstance(value, str):
            return None
        match = _DISPLAY_TIME.fullmatch(value.strip())
        if match is None:
            return None
        fraction = match.group("fraction").ljust(6, "0")
        try:
            parsed = datetime.strptime(
                f"{match.group('date')} {match.group('clock')}.{fraction}",
                "%Y-%m-%d %H:%M:%S.%f",
            )
        except ValueError:
            return None
        return parsed.replace(tzinfo=_SHANGHAI)

    @classmethod
    def _security_code(cls, query: FundamentalQuery) -> str | None:
        match = _CN_SYMBOL.fullmatch(query.instrument.symbol.strip().upper())
        if match is None:
            return None
        expected_suffix = cls._SUFFIX_BY_EXCHANGE.get(query.instrument.exchange)
        supplied_suffix = match.group("suffix")
        # Yahoo/legacy search identifies Shanghai securities with ``.SS``;
        # Eastmoney uses ``.SH``.  Both suffixes describe the same exchange,
        # so normalize only for validation and keep the canonical six-digit
        # code at the provider boundary.
        normalized_suffix = "SH" if supplied_suffix == "SS" else supplied_suffix
        if normalized_suffix is not None and normalized_suffix != expected_suffix:
            return None
        return match.group("code")

    @staticmethod
    def _native_text(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    @staticmethod
    def _native_number(value: Any) -> int | float | object:
        if value is None or isinstance(value, bool):
            return _MISSING
        if isinstance(value, Integral):
            return int(value)
        if isinstance(value, Real):
            native = float(value)
            return native if math.isfinite(native) else _MISSING
        if isinstance(value, str) and value.strip():
            try:
                native = float(value.strip())
            except ValueError:
                return _MISSING
            return native if math.isfinite(native) else _MISSING
        return _MISSING

    @staticmethod
    def _nonnegative_integer(value: Any) -> int | None:
        if isinstance(value, Integral) and not isinstance(value, bool) and value >= 0:
            return int(value)
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
        return None

    @staticmethod
    def _validate_positive_finite(value: float, name: str) -> None:
        if isinstance(value, bool) or not isinstance(value, Real):
            raise ValueError(f"{name} must be numeric")
        if not math.isfinite(float(value)) or float(value) <= 0:
            raise ValueError(f"{name} must be finite and above zero")

    @staticmethod
    def _validate_nonnegative_finite(value: float, name: str) -> None:
        if isinstance(value, bool) or not isinstance(value, Real):
            raise ValueError(f"{name} must be numeric")
        if not math.isfinite(float(value)) or float(value) < 0:
            raise ValueError(f"{name} must be finite and non-negative")
