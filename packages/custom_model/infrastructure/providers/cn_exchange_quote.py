from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, time as datetime_time, timedelta, timezone
import http.client
import json
import math
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


SHANGHAI = ZoneInfo("Asia/Shanghai")


class CNExchangeQuoteError(RuntimeError):
    """Sanitized failure at an official mainland-exchange quote boundary."""


class CNExchangeQuoteTransport(Protocol):
    def get_json(
        self,
        url: str,
        params: Mapping[str, str],
        *,
        connect_timeout: float,
        read_timeout: float,
    ) -> Mapping[str, Any]: ...


class _StdlibCNExchangeQuoteTransport:
    _MAX_RESPONSE_BYTES = 8 * 1024 * 1024

    def get_json(
        self,
        url: str,
        params: Mapping[str, str],
        *,
        connect_timeout: float,
        read_timeout: float,
    ) -> Mapping[str, Any]:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in {
            "yunhq.sse.com.cn",
            "www.szse.cn",
        }:
            raise CNExchangeQuoteError("invalid official quote endpoint")
        path = parsed.path
        query = urlencode(params)
        if query:
            path = f"{path}?{query}"
        referer = (
            "https://www.sse.com.cn/"
            if parsed.hostname == "yunhq.sse.com.cn"
            else "https://www.szse.cn/market/trend/index.html"
        )
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
                    "Accept-Language": "zh-CN,zh;q=0.9",
                    "Referer": referer,
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/140 Safari/537.36"
                    ),
                },
            )
            if connection.sock is not None:
                connection.sock.settimeout(read_timeout)
            response = connection.getresponse()
            if response.status >= 400:
                raise CNExchangeQuoteError("official quote HTTP failure")
            body = response.read(self._MAX_RESPONSE_BYTES + 1)
            if len(body) > self._MAX_RESPONSE_BYTES:
                raise CNExchangeQuoteError("official quote payload too large")
            payload = json.loads(body.decode("utf-8-sig"))
            if not isinstance(payload, Mapping):
                raise CNExchangeQuoteError("official quote payload is not an object")
            return payload
        except CNExchangeQuoteError:
            raise
        except (TimeoutError, socket.timeout) as exc:
            raise CNExchangeQuoteError("official quote timeout") from exc
        except (OSError, http.client.HTTPException) as exc:
            raise CNExchangeQuoteError("official quote connection failure") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CNExchangeQuoteError("official quote JSON failure") from exc
        finally:
            connection.close()


class CNExchangeOfficialQuoteProvider:
    """Native SSE/SZSE snapshots with explicit time, unit and phase semantics."""

    name = "cn-exchange-official-quote"
    production_ready = True
    _SSE_FIELDS = (
        "name",
        "last",
        "open",
        "high",
        "low",
        "prev_close",
        "volume",
        "amount",
        "tradephase",
        "change",
        "chg_rate",
        "date",
        "time",
    )
    _ASSETS = frozenset({AssetType.STOCK, AssetType.ETF, AssetType.INDEX})
    _PURPOSES = frozenset(
        {DataPurpose.RESEARCH, DataPurpose.SCREENER, DataPurpose.ALERT, DataPurpose.REPORT}
    )

    def __init__(
        self,
        transport: CNExchangeQuoteTransport | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        connect_timeout_seconds: float = 3.0,
        read_timeout_seconds: float = 8.0,
        max_retries: int = 1,
        backoff_seconds: float = 0.15,
    ) -> None:
        if connect_timeout_seconds <= 0 or read_timeout_seconds <= 0:
            raise ValueError("official quote timeouts must be positive")
        if max_retries < 0 or backoff_seconds < 0:
            raise ValueError("official quote retry settings are invalid")
        self._transport = transport or _StdlibCNExchangeQuoteTransport()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._sleep = sleep
        self._connect_timeout = float(connect_timeout_seconds)
        self._read_timeout = float(read_timeout_seconds)
        self._max_retries = max_retries
        self._backoff_seconds = float(backoff_seconds)

    @staticmethod
    def _code(query: QuoteQuery) -> str | None:
        raw = query.instrument.symbol.strip().upper()
        code, dot, suffix = raw.partition(".")
        if len(code) != 6 or not code.isdigit():
            return None
        allowed = {
            Exchange.SSE: {"", "SH", "SS"},
            Exchange.SZSE: {"", "SZ"},
        }.get(query.instrument.exchange)
        if allowed is None or (dot and suffix not in allowed):
            return None
        return code

    def supports(self, query: QuoteQuery) -> bool:
        return (
            query.instrument.market is Market.CN
            and query.instrument.exchange in {Exchange.SSE, Exchange.SZSE}
            and query.instrument.asset_type in self._ASSETS
            and query.purpose in self._PURPOSES
            and self._code(query) is not None
        )

    def fetch_quote(self, query: QuoteQuery) -> QuoteEnvelope:
        if not self.supports(query):
            raise CNExchangeQuoteError("official quote query is unsupported")
        code = self._code(query)
        assert code is not None
        if query.instrument.exchange is Exchange.SSE:
            payload = self._request(
                f"https://yunhq.sse.com.cn:32042/v1/sh1/snap/{code}",
                {"select": ",".join(self._SSE_FIELDS)},
            )
            mapped = self._map_sse(query, code, payload)
        else:
            payload = self._request(
                "https://www.szse.cn/api/market/ssjjhq/getTimeData",
                {"marketId": "1", "code": code},
            )
            mapped = self._map_szse(query, code, payload)
        return self._envelope(query, **mapped)

    def _request(self, url: str, params: Mapping[str, str]) -> Mapping[str, Any]:
        for attempt in range(self._max_retries + 1):
            try:
                return self._transport.get_json(
                    url,
                    params,
                    connect_timeout=self._connect_timeout,
                    read_timeout=self._read_timeout,
                )
            except Exception:
                if attempt >= self._max_retries:
                    break
                self._sleep(self._backoff_seconds * (2**attempt))
        raise CNExchangeQuoteError("official quote request failed") from None

    def _map_sse(
        self,
        query: QuoteQuery,
        code: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        snap = payload.get("snap")
        if str(payload.get("code", "")) != code or not self._sequence(snap):
            raise CNExchangeQuoteError("SSE quote payload identity is invalid")
        if len(snap) != len(self._SSE_FIELDS):
            raise CNExchangeQuoteError("SSE quote field count is invalid")
        values = dict(zip(self._SSE_FIELDS, snap, strict=True))
        source_time = self._compact_time(values["date"], values["time"])
        response_time = self._compact_time(payload.get("date"), payload.get("time"))
        response_lag = (response_time - source_time).total_seconds()
        if not 0 <= response_lag <= 5:
            raise CNExchangeQuoteError("SSE quote source time is inconsistent")
        phase = str(values["tradephase"] or "").strip()
        phase_digits = phase[1:] if phase.startswith("T") else ""
        phase_code = int(phase_digits) if phase_digits.isdigit() else 0
        volume = self._number(values["volume"], "volume", non_negative=True)
        if query.instrument.asset_type in {AssetType.STOCK, AssetType.ETF}:
            volume /= 100.0
        return {
            "display_name": self._text(values["name"], "name"),
            "provider_updated_at": source_time,
            "last_price": self._number(values["last"], "last"),
            "previous_close": self._number(values["prev_close"], "prev_close"),
            "open": self._number(values["open"], "open"),
            "high": self._number(values["high"], "high"),
            "low": self._number(values["low"], "low"),
            "change": self._number(values["change"], "change"),
            "change_percent": self._number(values["chg_rate"], "chg_rate"),
            "volume_lots": volume,
            "amount": self._number(values["amount"], "amount", non_negative=True),
            "phase_code": phase_code,
            "phase_verified": (
                query.instrument.asset_type in {AssetType.STOCK, AssetType.ETF}
                and phase == "T111"
            ),
            "source_chain": [
                "sse:yunhq/v1/sh1/snap:native-snapshot",
                "sse:source-time:date-time",
                "sse:trade-phase:tradephase",
            ],
        }

    def _map_szse(
        self,
        query: QuoteQuery,
        code: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        data = payload.get("data")
        if not isinstance(data, Mapping) or str(data.get("code", "")) != code:
            raise CNExchangeQuoteError("SZSE quote payload identity is invalid")
        source_time = self._formatted_time(data.get("marketTime"))
        phase1 = str(data.get("tradingPhaseCode1") or "").strip()
        phase2 = str(data.get("tradingPhaseCode2") or "").strip()
        combined = f"{phase1}{phase2}"
        phase_code = int(combined) if combined.isdigit() else 0
        return {
            "display_name": self._text(data.get("name"), "name"),
            "provider_updated_at": source_time,
            "last_price": self._number(data.get("now"), "now"),
            "previous_close": self._number(data.get("close"), "close"),
            "open": self._number(data.get("open"), "open"),
            "high": self._number(data.get("high"), "high"),
            "low": self._number(data.get("low"), "low"),
            "change": self._number(data.get("delta"), "delta"),
            "change_percent": self._number(
                data.get("deltaPercent"), "deltaPercent"
            ),
            "volume_lots": self._number(
                data.get("volume"), "volume", non_negative=True
            ),
            "amount": self._number(data.get("amount"), "amount", non_negative=True),
            "phase_code": phase_code,
            "phase_verified": (
                query.instrument.asset_type in {AssetType.STOCK, AssetType.ETF}
                and (phase1, phase2) == ("03", "08")
            ),
            "source_chain": [
                "szse:api/market/ssjjhq/getTimeData:native-snapshot",
                "szse:source-time:marketTime",
                "szse:trade-phase:tradingPhaseCode1+2",
            ],
        }

    def _envelope(self, query: QuoteQuery, **values: Any) -> QuoteEnvelope:
        fetched_at = self._clock()
        if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
            raise CNExchangeQuoteError("official quote clock must be timezone-aware")
        if query.temporal_mode is QuoteTemporalMode.EXACT:
            as_of = query.as_of
            assert as_of is not None
            capture_age = (fetched_at - as_of).total_seconds()
            if capture_age < 0 or capture_age > query.max_live_age_seconds:
                raise CNExchangeQuoteError(
                    "official current snapshot cannot satisfy historical exact quote"
                )
        else:
            requested_at = query.requested_at
            assert requested_at is not None
            if requested_at > fetched_at:
                raise CNExchangeQuoteError("latest quote requested_at is in the future")
        provider_updated_at = values["provider_updated_at"]
        if (
            query.temporal_mode is QuoteTemporalMode.LATEST
            and provider_updated_at > fetched_at
        ):
            clock_skew = (provider_updated_at - fetched_at).total_seconds()
            if clock_skew > 3:
                raise CNExchangeQuoteError("official quote source clock is too far ahead")
            self._sleep(clock_skew + 0.01)
            fetched_at = self._clock()
            if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
                raise CNExchangeQuoteError("official quote clock must be timezone-aware")
        if query.temporal_mode is QuoteTemporalMode.LATEST:
            as_of = fetched_at
        if provider_updated_at > as_of:
            raise CNExchangeQuoteError("official quote source time is after snapshot as_of")
        session_status = self._session_status(as_of)
        data_time, basis = self._data_time(as_of, provider_updated_at, session_status)
        freshness = (as_of - data_time).total_seconds()
        phase_verified = bool(values.pop("phase_verified")) and session_status is QuoteSessionStatus.OPEN
        quality = QualityStatus.VALID
        warnings = [
            "official exchange native snapshot; no daily-bar interpolation",
            "trade phase is admitted only for directly observed stock/ETF open-session codes",
        ]
        if session_status is not QuoteSessionStatus.OPEN or freshness > query.max_live_age_seconds:
            quality = QualityStatus.STALE
        if not phase_verified:
            quality = QualityStatus.STALE
            warnings.append("trade phase is unverified; snapshot is display-only")
        self._validate_market_values(values)
        mode = (
            DataMode.LIVE
            if session_status is QuoteSessionStatus.OPEN and quality is QualityStatus.VALID
            else DataMode.DELAYED
        )
        try:
            return QuoteEnvelope(
                instrument=query.instrument,
                purpose=query.purpose,
                display_name=values["display_name"],
                provider=self.name,
                mode=mode,
                quality=quality,
                temporal_mode=query.temporal_mode,
                requested_at=query.requested_at,
                as_of=as_of,
                fetched_at=fetched_at,
                data_time=data_time,
                provider_updated_at=provider_updated_at,
                data_time_basis=basis,
                timezone="Asia/Shanghai",
                currency=query.instrument.currency,
                freshness_seconds=freshness,
                session_status=session_status,
                trade_status_code=values["phase_code"],
                trade_status_verified=phase_verified,
                last_price=values["last_price"],
                previous_close=values["previous_close"],
                open=values["open"],
                high=values["high"],
                low=values["low"],
                change=values["change"],
                change_percent=values["change_percent"],
                volume_lots=values["volume_lots"],
                amount=values["amount"],
                warnings=warnings,
                is_synthetic=False,
                source_chain=values["source_chain"],
            )
        except (ValidationError, TypeError, ValueError) as exc:
            raise CNExchangeQuoteError("official quote fields failed validation") from exc

    @staticmethod
    def _validate_market_values(values: Mapping[str, Any]) -> None:
        last = values["last_price"]
        previous = values["previous_close"]
        open_ = values["open"]
        high = values["high"]
        low = values["low"]
        change = values["change"]
        percent = values["change_percent"]
        if min(last, previous, open_, high, low) <= 0:
            raise CNExchangeQuoteError("official quote prices must be positive")
        if high < max(open_, last, low) or low > min(open_, last, high):
            raise CNExchangeQuoteError("official quote OHLC is inconsistent")
        price_tolerance = max(1e-6, abs(last) * 1e-6)
        if abs((last - previous) - change) > price_tolerance:
            raise CNExchangeQuoteError("official quote change is inconsistent")
        expected_percent = change / previous * 100
        if abs(expected_percent - percent) > 0.02:
            raise CNExchangeQuoteError("official quote percentage is inconsistent")

    @staticmethod
    def _sequence(value: Any) -> bool:
        return isinstance(value, Sequence) and not isinstance(value, (str, bytes))

    @staticmethod
    def _number(value: Any, name: str, *, non_negative: bool = False) -> float:
        if isinstance(value, bool):
            raise CNExchangeQuoteError(f"official quote {name} is not numeric")
        try:
            result = float(value)
        except (TypeError, ValueError) as exc:
            raise CNExchangeQuoteError(f"official quote {name} is not numeric") from exc
        if not math.isfinite(result) or (non_negative and result < 0):
            raise CNExchangeQuoteError(f"official quote {name} is invalid")
        return result

    @staticmethod
    def _text(value: Any, name: str) -> str:
        result = str(value or "").strip()
        if not result:
            raise CNExchangeQuoteError(f"official quote {name} is blank")
        return result

    @staticmethod
    def _compact_time(day: Any, clock: Any) -> datetime:
        try:
            return datetime.strptime(
                f"{int(day):08d}{int(clock):06d}", "%Y%m%d%H%M%S"
            ).replace(tzinfo=SHANGHAI)
        except (TypeError, ValueError) as exc:
            raise CNExchangeQuoteError("SSE quote source time is invalid") from exc

    @staticmethod
    def _formatted_time(value: Any) -> datetime:
        try:
            return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=SHANGHAI
            )
        except (TypeError, ValueError) as exc:
            raise CNExchangeQuoteError("SZSE quote source time is invalid") from exc

    @staticmethod
    def _session_status(as_of: datetime) -> QuoteSessionStatus:
        local = as_of.astimezone(SHANGHAI)
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

    @staticmethod
    def _data_time(
        as_of: datetime,
        provider_updated_at: datetime,
        status: QuoteSessionStatus,
    ) -> tuple[datetime, str]:
        local = as_of.astimezone(SHANGHAI)
        if status is QuoteSessionStatus.OPEN:
            return provider_updated_at, "provider_timestamp"
        if status is QuoteSessionStatus.MIDDAY_BREAK:
            close = local.replace(hour=11, minute=30, second=0, microsecond=0)
            return min(provider_updated_at, close), "cn_midday_close"
        if status is QuoteSessionStatus.AFTER_CLOSE:
            close = local.replace(hour=15, minute=0, second=0, microsecond=0)
            return min(provider_updated_at, close), "cn_session_close"
        candidate = local.replace(hour=15, minute=0, second=0, microsecond=0)
        candidate -= timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate -= timedelta(days=1)
        return candidate, "cn_prior_session_close"

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.name,
            healthy=True,
            checked_at=self._clock(),
            message="official SSE/SZSE native quote adapters configured; verified per request",
            production_ready=True,
        )
