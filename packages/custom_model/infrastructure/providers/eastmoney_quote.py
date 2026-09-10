from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from enum import Enum
import http.client
import json
import math
from numbers import Integral, Real
import re
import socket
import time
from typing import Any, Protocol
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from custom_model.application.ports import ProviderHealth
from custom_model.domain.models import (
    AssetType,
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


_SHANGHAI = ZoneInfo("Asia/Shanghai")
_CN_SYMBOL = re.compile(r"^(?P<code>\d{6})(?:\.(?P<suffix>SS|SH|SZ|BJ))?$")


class EastmoneyQuoteFailureKind(str, Enum):
    TIMEOUT = "timeout"
    CONNECTION = "connection_error"
    RATE_LIMITED = "rate_limited"
    UPSTREAM = "upstream_error"
    INVALID_PAYLOAD = "invalid_payload"
    UNSUPPORTED = "unsupported_query"


class EastmoneyQuoteError(RuntimeError):
    """Sanitized quote-provider failure; never includes URL or response body."""

    def __init__(self, kind: EastmoneyQuoteFailureKind, message: str) -> None:
        self.kind = kind
        super().__init__(message)


class EastmoneyQuoteTransportError(RuntimeError):
    def __init__(self, kind: EastmoneyQuoteFailureKind) -> None:
        self.kind = kind
        super().__init__(kind.value)


class EastmoneyQuoteJsonTransport(Protocol):
    def get_json(
        self,
        url: str,
        params: Mapping[str, str | int],
        *,
        connect_timeout: float,
        read_timeout: float,
    ) -> Mapping[str, Any]: ...


class _StdlibQuoteJsonTransport:
    _MAX_RESPONSE_BYTES = 2 * 1024 * 1024

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
            raise EastmoneyQuoteTransportError(
                EastmoneyQuoteFailureKind.INVALID_PAYLOAD
            )
        query = urlencode(params)
        path = parsed.path or "/"
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
                    "User-Agent": "Custom-Model/1.0 EastmoneyQuoteProvider",
                },
            )
            if connection.sock is not None:
                connection.sock.settimeout(read_timeout)
            response = connection.getresponse()
            if response.status == 429:
                raise EastmoneyQuoteTransportError(
                    EastmoneyQuoteFailureKind.RATE_LIMITED
                )
            if response.status >= 500:
                raise EastmoneyQuoteTransportError(EastmoneyQuoteFailureKind.UPSTREAM)
            if response.status >= 400:
                raise EastmoneyQuoteTransportError(
                    EastmoneyQuoteFailureKind.INVALID_PAYLOAD
                )
            body = response.read(self._MAX_RESPONSE_BYTES + 1)
            if len(body) > self._MAX_RESPONSE_BYTES:
                raise EastmoneyQuoteTransportError(
                    EastmoneyQuoteFailureKind.INVALID_PAYLOAD
                )
            try:
                decoded = json.loads(body.decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise EastmoneyQuoteTransportError(
                    EastmoneyQuoteFailureKind.INVALID_PAYLOAD
                ) from exc
            if not isinstance(decoded, Mapping):
                raise EastmoneyQuoteTransportError(
                    EastmoneyQuoteFailureKind.INVALID_PAYLOAD
                )
            return decoded
        except (TimeoutError, socket.timeout) as exc:
            raise EastmoneyQuoteTransportError(EastmoneyQuoteFailureKind.TIMEOUT) from exc
        except (OSError, http.client.HTTPException) as exc:
            raise EastmoneyQuoteTransportError(
                EastmoneyQuoteFailureKind.CONNECTION
            ) from exc
        finally:
            connection.close()


class EastmoneyQuoteProvider:
    """Native A-share snapshot adapter for Eastmoney's push2 quote endpoint."""

    name = "eastmoney-quote"
    production_ready = True
    _PRIMARY_URL = "https://91.push2.eastmoney.com/api/qt/stock/get"
    _DELAYED_URL = "https://push2delay.eastmoney.com/api/qt/stock/get"
    _FIELDS = (
        "f43,f44,f45,f46,f47,f48,f57,f58,f60,f86,f116,f117,"
        "f162,f167,f168,f169,f170,f171,f292"
    )
    _SUPPORTED_ASSETS = frozenset({AssetType.STOCK, AssetType.ETF, AssetType.INDEX})
    _SUPPORTED_PURPOSES = frozenset(
        {DataPurpose.RESEARCH, DataPurpose.SCREENER, DataPurpose.ALERT, DataPurpose.REPORT}
    )
    # Raw f292 values are session-dependent. These are the only combinations
    # verified against normally trading A-share snapshots in the admission
    # suite; every other combination remains display-only.
    # open/2: 2026-08-12 11:12 (+08:00) 连续竞价时段实证 —— 600519/000001/
    # 601318/300750（沪深两市 4 只正常交易标的）均返回 f292=2 且数据 0-1s 新鲜。
    # open/19 与 after_close/5 为 2026-08-11 实证组合。
    _VERIFIED_TRADE_STATUS_BY_SESSION = {
        QuoteSessionStatus.OPEN: frozenset({2, 19}),
        QuoteSessionStatus.AFTER_CLOSE: frozenset({5}),
    }
    _MARKET_ID = {
        Exchange.SSE: "1",
        Exchange.SZSE: "0",
        Exchange.BSE: "0",
    }

    def __init__(
        self,
        *,
        transport: EastmoneyQuoteJsonTransport | None = None,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        connect_timeout_seconds: float = 3.0,
        read_timeout_seconds: float = 8.0,
        max_retries: int = 1,
        backoff_seconds: float = 0.15,
    ) -> None:
        if connect_timeout_seconds <= 0 or read_timeout_seconds <= 0:
            raise ValueError("quote provider timeouts must be positive")
        if max_retries < 0 or backoff_seconds < 0:
            raise ValueError("quote provider retry settings are invalid")
        self._transport = transport or _StdlibQuoteJsonTransport()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._sleep = sleep
        self._connect_timeout = connect_timeout_seconds
        self._read_timeout = read_timeout_seconds
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds

    def supports(self, query: QuoteQuery) -> bool:
        return (
            query.instrument.market is Market.CN
            and query.instrument.exchange in self._MARKET_ID
            and query.instrument.asset_type in self._SUPPORTED_ASSETS
            and query.purpose in self._SUPPORTED_PURPOSES
            and self._security_code(query) is not None
        )

    def fetch_quote(self, query: QuoteQuery) -> QuoteEnvelope:
        if not self.supports(query):
            raise EastmoneyQuoteError(
                EastmoneyQuoteFailureKind.UNSUPPORTED,
                "quote query is unsupported",
            )
        code = self._security_code(query)
        assert code is not None
        secid = f"{self._MARKET_ID[query.instrument.exchange]}.{code}"
        attempts: list[str] = []
        for endpoint_kind, url in (
            ("primary", self._PRIMARY_URL),
            ("delayed", self._DELAYED_URL),
        ):
            try:
                payload = self._request_json(
                    url,
                    {"secid": secid, "invt": "2", "fltt": "2", "fields": self._FIELDS},
                )
                return self._map_payload(
                    query,
                    code=code,
                    payload=payload,
                    endpoint_kind=endpoint_kind,
                    prior_attempts=attempts,
                )
            except EastmoneyQuoteError as exc:
                attempts.append(f"{endpoint_kind}:{exc.kind.value}")
        detail = ", ".join(attempts) if attempts else "no endpoint response"
        raise EastmoneyQuoteError(
            EastmoneyQuoteFailureKind.UPSTREAM,
            f"quote endpoints unavailable ({detail})",
        )

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.name,
            healthy=self.production_ready,
            checked_at=self._aware_clock(),
            message="provider configured; upstream health is checked per request",
            production_ready=self.production_ready,
        )

    def _request_json(
        self,
        url: str,
        params: Mapping[str, str | int],
    ) -> Mapping[str, Any]:
        last_kind = EastmoneyQuoteFailureKind.UPSTREAM
        for attempt in range(self._max_retries + 1):
            try:
                payload = self._transport.get_json(
                    url,
                    params,
                    connect_timeout=self._connect_timeout,
                    read_timeout=self._read_timeout,
                )
                if not isinstance(payload, Mapping):
                    raise EastmoneyQuoteTransportError(
                        EastmoneyQuoteFailureKind.INVALID_PAYLOAD
                    )
                return payload
            except EastmoneyQuoteTransportError as exc:
                last_kind = exc.kind
            except (TimeoutError, socket.timeout):
                last_kind = EastmoneyQuoteFailureKind.TIMEOUT
            except (OSError, http.client.HTTPException):
                last_kind = EastmoneyQuoteFailureKind.CONNECTION
            except Exception:
                last_kind = EastmoneyQuoteFailureKind.INVALID_PAYLOAD
            if (
                last_kind is EastmoneyQuoteFailureKind.INVALID_PAYLOAD
                or attempt >= self._max_retries
            ):
                break
            self._sleep(self._backoff_seconds * (2**attempt))
        raise EastmoneyQuoteError(last_kind, f"quote request failed: {last_kind.value}")

    def _map_payload(
        self,
        query: QuoteQuery,
        *,
        code: str,
        payload: Mapping[str, Any],
        endpoint_kind: str,
        prior_attempts: list[str],
    ) -> QuoteEnvelope:
        raw = payload.get("data")
        if not isinstance(raw, Mapping) or str(raw.get("f57") or "").strip() != code:
            raise EastmoneyQuoteError(
                EastmoneyQuoteFailureKind.INVALID_PAYLOAD,
                "quote payload does not match the requested instrument",
            )
        try:
            provider_updated_at = datetime.fromtimestamp(
                int(self._required_number(raw, "f86")),
                tz=timezone.utc,
            ).astimezone(_SHANGHAI)
        except (OverflowError, OSError, ValueError) as exc:
            raise EastmoneyQuoteError(
                EastmoneyQuoteFailureKind.INVALID_PAYLOAD,
                "quote payload has invalid source time",
            ) from exc
        fetched_at = self._aware_clock()
        if query.temporal_mode is QuoteTemporalMode.EXACT:
            snapshot_as_of = query.as_of
            assert snapshot_as_of is not None
            capture_delay = (fetched_at - snapshot_as_of).total_seconds()
            if capture_delay < 0 or capture_delay > query.max_live_age_seconds:
                raise EastmoneyQuoteError(
                    EastmoneyQuoteFailureKind.UNSUPPORTED,
                    "current snapshot endpoint cannot satisfy a historical exact quote",
                )
        else:
            requested_at = query.requested_at
            assert requested_at is not None
            if requested_at > fetched_at:
                raise EastmoneyQuoteError(
                    EastmoneyQuoteFailureKind.INVALID_PAYLOAD,
                    "latest quote requested_at is after provider fetch completion",
                )
            snapshot_as_of = fetched_at
        if provider_updated_at > snapshot_as_of:
            raise EastmoneyQuoteError(
                EastmoneyQuoteFailureKind.INVALID_PAYLOAD,
                "quote source time is after the authoritative snapshot as_of",
            )

        session_status = self._session_status(snapshot_as_of)
        data_time, data_time_basis = self._market_data_time(
            snapshot_as_of,
            provider_updated_at,
            session_status,
        )
        freshness_seconds = (snapshot_as_of - data_time).total_seconds()
        quality, warnings = self._quality(
            query,
            snapshot_as_of=snapshot_as_of,
            provider_updated_at=provider_updated_at,
            data_time=data_time,
            session_status=session_status,
        )
        try:
            trade_status_code = self._required_int(raw, "f292")
        except (TypeError, ValueError) as exc:
            raise EastmoneyQuoteError(
                EastmoneyQuoteFailureKind.INVALID_PAYLOAD,
                "quote payload has invalid trade status",
            ) from exc
        trade_status_verified = trade_status_code in self._VERIFIED_TRADE_STATUS_BY_SESSION.get(
            session_status,
            frozenset(),
        )
        if not trade_status_verified:
            quality = QualityStatus.STALE
            warnings.append(
                "trade status is not verified as normally tradable; snapshot is display-only"
            )
        if query.temporal_mode is QuoteTemporalMode.EXACT:
            warnings.append(
                "exact snapshot is accepted only inside the live capture-age limit; "
                "the provider does not support historical quote replay"
            )
        if query.temporal_mode is QuoteTemporalMode.LATEST:
            warnings.append(
                "latest snapshot as_of is provider fetch completion time; "
                "client requested_at is preserved separately"
            )
        mode = (
            DataMode.LIVE
            if endpoint_kind == "primary"
            and session_status is QuoteSessionStatus.OPEN
            and quality is QualityStatus.VALID
            else DataMode.DELAYED
        )
        if endpoint_kind == "delayed":
            warnings.append("Eastmoney delayed quote endpoint used; mode is delayed")
        if prior_attempts:
            warnings.append("prior quote endpoint attempts: " + ", ".join(prior_attempts))

        try:
            return QuoteEnvelope(
                instrument=query.instrument,
                purpose=query.purpose,
                display_name=self._required_text(raw, "f58"),
                provider=self.name,
                mode=mode,
                quality=quality,
                temporal_mode=query.temporal_mode,
                requested_at=query.requested_at,
                as_of=snapshot_as_of,
                fetched_at=fetched_at,
                data_time=data_time,
                provider_updated_at=provider_updated_at,
                data_time_basis=data_time_basis,
                timezone="Asia/Shanghai",
                currency=query.instrument.currency,
                freshness_seconds=freshness_seconds,
                session_status=session_status,
                trade_status_code=trade_status_code,
                trade_status_verified=trade_status_verified,
                last_price=self._required_number(raw, "f43"),
                previous_close=self._required_number(raw, "f60"),
                open=self._required_number(raw, "f46"),
                high=self._required_number(raw, "f44"),
                low=self._required_number(raw, "f45"),
                change=self._required_number(raw, "f169"),
                change_percent=self._required_number(raw, "f170"),
                volume_lots=self._required_number(raw, "f47", non_negative=True),
                amount=self._required_number(raw, "f48", non_negative=True),
                turnover_rate=self._optional_number(raw, "f168", non_negative=True),
                amplitude=self._optional_number(raw, "f171", non_negative=True),
                pe_dynamic=self._optional_number(raw, "f162"),
                pb=self._optional_number(raw, "f167"),
                total_market_cap=self._optional_number(raw, "f116", non_negative=True),
                float_market_cap=self._optional_number(raw, "f117", non_negative=True),
                warnings=warnings,
                is_synthetic=False,
                source_chain=[
                    (
                        "eastmoney:91.push2/api/qt/stock/get"
                        if endpoint_kind == "primary"
                        else "eastmoney:push2delay/api/qt/stock/get"
                    ),
                    "eastmoney:source_time:f86",
                    "eastmoney:trade_status:f292",
                ],
            )
        except (ValidationError, TypeError, ValueError) as exc:
            raise EastmoneyQuoteError(
                EastmoneyQuoteFailureKind.INVALID_PAYLOAD,
                "quote payload failed field validation",
            ) from exc

    @staticmethod
    def _session_status(as_of: datetime) -> QuoteSessionStatus:
        local = as_of.astimezone(_SHANGHAI)
        if local.weekday() >= 5:
            return QuoteSessionStatus.NON_TRADING_DAY_UNVERIFIED
        clock = local.time().replace(tzinfo=None)
        if datetime_time(9, 30) <= clock < datetime_time(11, 30) or (
            datetime_time(13, 0) <= clock < datetime_time(15, 0)
        ):
            return QuoteSessionStatus.OPEN
        if datetime_time(11, 30) <= clock < datetime_time(13, 0):
            return QuoteSessionStatus.MIDDAY_BREAK
        if clock < datetime_time(9, 30):
            return QuoteSessionStatus.PRE_OPEN
        return QuoteSessionStatus.AFTER_CLOSE

    @classmethod
    def _market_data_time(
        cls,
        as_of: datetime,
        provider_updated_at: datetime,
        status: QuoteSessionStatus,
    ) -> tuple[datetime, str]:
        reference = as_of.astimezone(_SHANGHAI)
        upstream = provider_updated_at.astimezone(_SHANGHAI)
        if status is QuoteSessionStatus.MIDDAY_BREAK and upstream.date() == reference.date():
            midday = datetime.combine(reference.date(), datetime_time(11, 30), tzinfo=_SHANGHAI)
            if upstream >= midday:
                return midday, "cn_midday_close"
        if status is QuoteSessionStatus.AFTER_CLOSE and upstream.date() == reference.date():
            close = datetime.combine(reference.date(), datetime_time(15, 0), tzinfo=_SHANGHAI)
            if upstream >= close:
                return close, "cn_session_close"
        if status in {
            QuoteSessionStatus.PRE_OPEN,
            QuoteSessionStatus.NON_TRADING_DAY_UNVERIFIED,
        }:
            previous = cls._previous_weekday(reference.date())
            previous_close = datetime.combine(previous, datetime_time(15, 0), tzinfo=_SHANGHAI)
            if upstream >= previous_close:
                return previous_close, "cn_prior_session_close"
        return upstream, "provider_timestamp"

    @staticmethod
    def _previous_weekday(value: date) -> date:
        candidate = value - timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate -= timedelta(days=1)
        return candidate

    @staticmethod
    def _quality(
        query: QuoteQuery,
        *,
        snapshot_as_of: datetime,
        provider_updated_at: datetime,
        data_time: datetime,
        session_status: QuoteSessionStatus,
    ) -> tuple[QualityStatus, list[str]]:
        reference = snapshot_as_of.astimezone(_SHANGHAI)
        upstream = provider_updated_at.astimezone(_SHANGHAI)
        warnings = [
            "session classification uses weekday/time rules without an exchange holiday calendar",
            f"raw quote age is {int((snapshot_as_of - data_time).total_seconds())} seconds",
        ]
        if session_status is QuoteSessionStatus.OPEN:
            age = (snapshot_as_of - provider_updated_at).total_seconds()
            if upstream.date() == reference.date() and 0 <= age <= query.max_live_age_seconds:
                return QualityStatus.VALID, warnings
            warnings.append("intraday source timestamp exceeds the live freshness limit")
            return QualityStatus.STALE, warnings
        if session_status is QuoteSessionStatus.MIDDAY_BREAK:
            if (
                upstream.date() == reference.date()
                and upstream.time().replace(tzinfo=None) >= datetime_time(11, 25)
            ):
                warnings.append("midday completed-session quote; not live")
                return QualityStatus.VALID, warnings
            warnings.append("midday quote cannot be tied to the current morning session")
            return QualityStatus.STALE, warnings
        if session_status is QuoteSessionStatus.AFTER_CLOSE:
            if (
                upstream.date() == reference.date()
                and upstream.time().replace(tzinfo=None) >= datetime_time(14, 55)
            ):
                warnings.append("same-day completed-session close accepted as delayed")
                return QualityStatus.VALID, warnings
            warnings.append("after-close quote cannot be tied to the current completed session")
            return QualityStatus.STALE, warnings
        if session_status is QuoteSessionStatus.PRE_OPEN:
            warnings.append("pre-open snapshot is display-only until a trading calendar is available")
        else:
            warnings.append(
                "weekend/non-trading-day snapshot is display-only and calendar-unverified"
            )
        return QualityStatus.STALE, warnings

    @classmethod
    def _security_code(cls, query: QuoteQuery) -> str | None:
        match = _CN_SYMBOL.fullmatch(query.instrument.symbol.strip().upper())
        if match is None:
            return None
        suffix = match.group("suffix")
        allowed_suffixes = {
            Exchange.SSE: {None, "SS", "SH"},
            Exchange.SZSE: {None, "SZ"},
            Exchange.BSE: {None, "BJ"},
        }
        if suffix not in allowed_suffixes.get(query.instrument.exchange, set()):
            return None
        return match.group("code")

    @staticmethod
    def _required_text(raw: Mapping[str, Any], field: str) -> str:
        value = raw.get(field)
        if not isinstance(value, str) or not value.strip() or value.strip() == "-":
            raise ValueError(f"missing {field}")
        return value.strip()

    @classmethod
    def _required_number(
        cls,
        raw: Mapping[str, Any],
        field: str,
        *,
        non_negative: bool = False,
    ) -> float:
        value = cls._number(raw.get(field), field)
        if non_negative and value < 0:
            raise ValueError(f"negative {field}")
        return value

    @classmethod
    def _required_int(cls, raw: Mapping[str, Any], field: str) -> int:
        value = cls._number(raw.get(field), field)
        if value < 0 or not value.is_integer():
            raise ValueError(f"invalid {field}")
        return int(value)

    @classmethod
    def _optional_number(
        cls,
        raw: Mapping[str, Any],
        field: str,
        *,
        non_negative: bool = False,
    ) -> float | None:
        value = raw.get(field)
        if value is None or (isinstance(value, str) and value.strip() in {"", "-"}):
            return None
        parsed = cls._number(value, field)
        if non_negative and parsed < 0:
            raise ValueError(f"negative {field}")
        return parsed

    @staticmethod
    def _number(value: Any, field: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (Integral, Real, str)):
            raise ValueError(f"invalid {field}")
        try:
            parsed = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"invalid {field}") from exc
        if not math.isfinite(parsed):
            raise ValueError(f"non-finite {field}")
        return parsed

    def _aware_clock(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("quote provider clock must be timezone-aware")
        return value
