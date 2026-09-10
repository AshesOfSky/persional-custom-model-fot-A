from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
import http.client
import json
import math
import socket
import time
from typing import Any, Protocol
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

from custom_model.application.ports import ProviderCapabilities, ProviderHealth
from custom_model.domain.models import (
    Adjustment,
    AssetType,
    Bar,
    DataEnvelope,
    DataMode,
    DataPurpose,
    DataQuery,
    Exchange,
    Market,
    QualityStatus,
    Timeframe,
)


_SHANGHAI = ZoneInfo("Asia/Shanghai")


class EastmoneyIntradayError(RuntimeError):
    """Sanitized failure at the free Eastmoney intraday boundary."""


class EastmoneyIntradayTransport(Protocol):
    def get_json(
        self,
        url: str,
        params: Mapping[str, str],
        *,
        connect_timeout: float,
        read_timeout: float,
    ) -> Mapping[str, Any]: ...


class _StdlibJsonTransport:
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
        if parsed.scheme != "https" or not parsed.hostname:
            raise EastmoneyIntradayError("invalid endpoint configuration")
        path = f"{parsed.path}?{urlencode(params)}"
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
                    "Referer": "https://quote.eastmoney.com/",
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36"
                    ),
                },
            )
            if connection.sock is not None:
                connection.sock.settimeout(read_timeout)
            response = connection.getresponse()
            if response.status >= 400:
                raise EastmoneyIntradayError("upstream HTTP failure")
            body = response.read(self._MAX_RESPONSE_BYTES + 1)
            if len(body) > self._MAX_RESPONSE_BYTES:
                raise EastmoneyIntradayError("upstream payload too large")
            payload = json.loads(body.decode("utf-8-sig"))
            if not isinstance(payload, Mapping):
                raise EastmoneyIntradayError("upstream payload is not an object")
            return payload
        except (TimeoutError, socket.timeout) as exc:
            raise EastmoneyIntradayError("upstream timeout") from exc
        except (OSError, http.client.HTTPException) as exc:
            raise EastmoneyIntradayError("upstream connection failure") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EastmoneyIntradayError("upstream JSON failure") from exc
        finally:
            connection.close()


def create_default_eastmoney_intraday_transport() -> EastmoneyIntradayTransport:
    """Return the audited stdlib transport for instrumented shadow capture."""

    return _StdlibJsonTransport()


class EastmoneyIntradayProvider:
    """Real, unadjusted CN minute bars from Eastmoney's 1-minute endpoint.

    The upstream 1-minute bars use *closing* timestamps (09:31 is the first
    continuous-auction minute).  Higher timeframes are derived only by exact,
    session-aligned OHLCV aggregation.  A bucket with even one missing minute
    is discarded; daily interpolation is never used.

    Only unadjusted RESEARCH/REPORT queries are admitted.  ALERT, SCREENER and
    BACKTEST remain fail-closed until an entire-session shadow admission has
    measured delay, quota and reliability.
    """

    name = "eastmoney-intraday"
    production_ready = True
    capabilities = ProviderCapabilities(
        markets=frozenset({Market.CN}),
        asset_types=frozenset({AssetType.STOCK, AssetType.ETF, AssetType.INDEX}),
        timeframes=frozenset(
            {Timeframe.M1, Timeframe.M5, Timeframe.M15, Timeframe.M30, Timeframe.H1}
        ),
        max_symbols_per_request=1,
    )

    _ENDPOINTS = (
        ("primary", "https://91.push2.eastmoney.com/api/qt/stock/trends2/get"),
        ("delayed", "https://push2delay.eastmoney.com/api/qt/stock/trends2/get"),
    )
    _PURPOSES = frozenset({DataPurpose.RESEARCH, DataPurpose.REPORT})
    _MINUTES = {
        Timeframe.M1: 1,
        Timeframe.M5: 5,
        Timeframe.M15: 15,
        Timeframe.M30: 30,
        Timeframe.H1: 60,
    }
    _MARKET_ID = {Exchange.SSE: "1", Exchange.SZSE: "0"}

    def __init__(
        self,
        transport: EastmoneyIntradayTransport | None = None,
        *,
        connect_timeout_seconds: float = 3.0,
        read_timeout_seconds: float = 8.0,
        close_confirmation_seconds: float = 3.0,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 2,
        backoff_seconds: float = 0.15,
    ) -> None:
        for value, label in (
            (connect_timeout_seconds, "connect_timeout_seconds"),
            (read_timeout_seconds, "read_timeout_seconds"),
            (close_confirmation_seconds, "close_confirmation_seconds"),
        ):
            if not math.isfinite(float(value)) or float(value) < 0:
                raise ValueError(f"{label} must be finite and nonnegative")
        if connect_timeout_seconds == 0 or read_timeout_seconds == 0:
            raise ValueError("network timeouts must be positive")
        if max_retries < 0 or backoff_seconds < 0:
            raise ValueError("retry settings must be nonnegative")
        self._transport = transport or _StdlibJsonTransport()
        self._connect_timeout = float(connect_timeout_seconds)
        self._read_timeout = float(read_timeout_seconds)
        self._close_confirmation = float(close_confirmation_seconds)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._sleep = sleep
        self._max_retries = max_retries
        self._backoff_seconds = float(backoff_seconds)

    @staticmethod
    def _code(query: DataQuery) -> str | None:
        raw = query.instrument.symbol.strip().upper()
        code, dot, suffix = raw.partition(".")
        if not (len(code) == 6 and code.isdigit()):
            return None
        expected = {
            Exchange.SSE: {"", "SH", "SS"},
            Exchange.SZSE: {"", "SZ"},
        }.get(query.instrument.exchange)
        if expected is None or (dot and suffix not in expected):
            return None
        return code

    def supports(self, query: DataQuery) -> bool:
        return (
            query.instrument.market is Market.CN
            and query.instrument.exchange in self._MARKET_ID
            and query.instrument.asset_type in self.capabilities.asset_types
            and query.timeframe in self.capabilities.timeframes
            and query.adjustment is Adjustment.NONE
            and query.purpose in self._PURPOSES
            and self._code(query) is not None
        )

    def fetch_bars(self, query: DataQuery) -> DataEnvelope:
        if not self.supports(query):
            raise EastmoneyIntradayError("unsupported intraday query")
        fetched_at = self._clock()
        if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
            raise EastmoneyIntradayError("provider clock must be timezone-aware")
        code = self._code(query)
        assert code is not None
        params = {
            "secid": f"{self._MARKET_ID[query.instrument.exchange]}.{code}",
            "ndays": "1",
            "iscr": "0",
            "iscca": "0",
            "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        }
        payload, endpoint_kind = self._request_json(params)

        raw_bars = self._parse_payload(payload, query, code)
        upper = min(
            query.as_of.astimezone(_SHANGHAI),
            fetched_at.astimezone(_SHANGHAI),
        ) - timedelta(seconds=self._close_confirmation)
        if query.end is not None:
            upper = min(upper, query.end.astimezone(_SHANGHAI))
        closed = [bar for bar in raw_bars if bar.timestamp <= upper]
        bars = self._aggregate(closed, self._MINUTES[query.timeframe])
        if query.start is not None:
            start = query.start.astimezone(_SHANGHAI)
            bars = [bar for bar in bars if bar.timestamp >= start]
        bars = [bar for bar in bars if bar.timestamp <= upper]
        if not bars:
            raise EastmoneyIntradayError("no completed bars inside query window")

        warnings = [
            "unadjusted intraday bars; multi-day charts can cross corporate-action dates",
            "free trends endpoint currently supplies the latest CN session only",
        ]
        if query.timeframe is not Timeframe.M1:
            warnings.append(
                f"{query.timeframe.value} bars aggregated from complete real 1m bars; no interpolation"
            )
        if endpoint_kind == "delayed":
            warnings.append("Eastmoney delayed trends endpoint used")
        last_bar_at = bars[-1].timestamp
        freshness = max(
            0.0,
            (query.as_of - last_bar_at.astimezone(query.as_of.tzinfo)).total_seconds(),
        )
        source_chain = [
            (
                "eastmoney:91.push2/trends2:raw-unadjusted-1m"
                if endpoint_kind == "primary"
                else "eastmoney:push2delay/trends2:raw-unadjusted-1m"
            )
        ]
        if query.timeframe is not Timeframe.M1:
            source_chain.append(
                f"custom-model:cn-session-aggregate:{query.timeframe.value}"
            )
        return DataEnvelope(
            instrument=query.instrument,
            timeframe=query.timeframe,
            purpose=query.purpose,
            bars=bars,
            provider=self.name,
            mode=DataMode.DELAYED,
            quality=QualityStatus.VALID,
            as_of=query.as_of,
            fetched_at=fetched_at,
            last_bar_at=last_bar_at,
            timezone="Asia/Shanghai",
            adjustment=Adjustment.NONE,
            freshness_seconds=freshness,
            is_synthetic=False,
            warnings=warnings,
            source_chain=source_chain,
        )

    def _request_json(
        self, params: Mapping[str, str]
    ) -> tuple[Mapping[str, Any], str]:
        for endpoint_kind, url in self._ENDPOINTS:
            for attempt in range(self._max_retries + 1):
                try:
                    return (
                        self._transport.get_json(
                            url,
                            params,
                            connect_timeout=self._connect_timeout,
                            read_timeout=self._read_timeout,
                        ),
                        endpoint_kind,
                    )
                except Exception as exc:
                    if attempt >= self._max_retries:
                        break
                    self._sleep(self._backoff_seconds * (2**attempt))
        raise EastmoneyIntradayError("upstream transport failure") from None

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.name,
            healthy=True,
            checked_at=self._clock(),
            message=(
                "research/report raw-minute adapter configured; alert/screener/backtest "
                "await full-session admission"
            ),
            production_ready=True,
        )

    def _parse_payload(
        self,
        payload: Mapping[str, Any],
        query: DataQuery,
        code: str,
    ) -> list[Bar]:
        if payload.get("rc") not in (0, "0"):
            raise EastmoneyIntradayError("upstream returned a failure code")
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise EastmoneyIntradayError("upstream data is missing")
        if str(data.get("code", "")) != code:
            raise EastmoneyIntradayError("upstream instrument mismatch")
        if str(data.get("market", "")) != self._MARKET_ID[query.instrument.exchange]:
            raise EastmoneyIntradayError("upstream market mismatch")
        rows = data.get("trends")
        if rows is None:  # offline fixtures and a bounded historical fallback schema
            rows = data.get("klines")
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)) or not rows:
            raise EastmoneyIntradayError("upstream minute rows are missing")

        bars: list[Bar] = []
        seen: set[datetime] = set()
        for row in rows:
            if not isinstance(row, str):
                raise EastmoneyIntradayError("upstream minute row is not text")
            fields = row.split(",")
            if len(fields) < 7:
                raise EastmoneyIntradayError("upstream minute row has too few fields")
            try:
                naive = datetime.strptime(fields[0], "%Y-%m-%d %H:%M")
                timestamp = naive.replace(tzinfo=_SHANGHAI)
                values = [float(item) for item in fields[1:7]]
            except (TypeError, ValueError) as exc:
                raise EastmoneyIntradayError("upstream minute row is malformed") from exc
            if any(not math.isfinite(value) for value in values):
                raise EastmoneyIntradayError("upstream minute row is non-finite")
            if timestamp.hour == 9 and timestamp.minute == 30:
                # trends2 includes one call-auction/open seed.  Continuous
                # one-minute evidence begins at the 09:31 closing bucket.
                continue
            if timestamp in seen or self._session_start(timestamp) is None:
                raise EastmoneyIntradayError("upstream minute timestamps are invalid")
            seen.add(timestamp)
            open_, close, high, low, volume, amount = values
            try:
                bars.append(
                    Bar(
                        timestamp=timestamp,
                        open=open_,
                        high=high,
                        low=low,
                        close=close,
                        volume=volume,
                        amount=amount,
                    )
                )
            except ValueError as exc:
                raise EastmoneyIntradayError("upstream OHLCV row is invalid") from exc
        if [bar.timestamp for bar in bars] != sorted(bar.timestamp for bar in bars):
            raise EastmoneyIntradayError("upstream minute rows are not sorted")
        return bars

    @staticmethod
    def _session_start(timestamp: datetime) -> int | None:
        if timestamp.weekday() >= 5:
            return None
        minute = timestamp.hour * 60 + timestamp.minute
        if 9 * 60 + 31 <= minute <= 11 * 60 + 30:
            return 9 * 60 + 30
        if 13 * 60 + 1 <= minute <= 15 * 60:
            return 13 * 60
        return None

    @classmethod
    def _aggregate(cls, bars: list[Bar], minutes: int) -> list[Bar]:
        if minutes == 1:
            return list(bars)
        groups: dict[tuple[datetime.date, int, int], list[Bar]] = {}
        for bar in bars:
            session_start = cls._session_start(bar.timestamp)
            if session_start is None:
                continue
            minute = bar.timestamp.hour * 60 + bar.timestamp.minute
            elapsed = minute - session_start
            bucket_close = session_start + ((elapsed + minutes - 1) // minutes) * minutes
            groups.setdefault(
                (bar.timestamp.date(), session_start, bucket_close), []
            ).append(bar)

        result: list[Bar] = []
        for (day, _session_start, bucket_close), members in sorted(groups.items()):
            members.sort(key=lambda item: item.timestamp)
            expected = list(range(bucket_close - minutes + 1, bucket_close + 1))
            actual = [item.timestamp.hour * 60 + item.timestamp.minute for item in members]
            if len(members) != minutes or actual != expected:
                continue
            close_at = datetime.combine(day, datetime.min.time(), tzinfo=_SHANGHAI) + timedelta(
                minutes=bucket_close
            )
            result.append(
                Bar(
                    timestamp=close_at,
                    open=members[0].open,
                    high=max(item.high for item in members),
                    low=min(item.low for item in members),
                    close=members[-1].close,
                    volume=sum(item.volume for item in members),
                    amount=sum(item.amount or 0.0 for item in members),
                )
            )
        return result
