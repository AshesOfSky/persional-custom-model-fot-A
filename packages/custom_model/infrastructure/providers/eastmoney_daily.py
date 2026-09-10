from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from datetime import time as time_of_day
from datetime import timezone
import http.client
import json
import math
import re
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
_CN_SYMBOL = re.compile(r"^(?P<code>\d{6})(?:\.(?P<suffix>SS|SH|SZ|BJ))?$")


class EastmoneyDailyError(RuntimeError):
    """Sanitized failure at the strict Eastmoney daily boundary."""


class EastmoneyDailyTransport(Protocol):
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
            raise EastmoneyDailyError("invalid endpoint configuration")
        query = urlencode(params)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}&{query}" if query else parsed.query
        else:
            path = f"{path}?{query}" if query else path
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
                raise EastmoneyDailyError("upstream HTTP failure")
            body = response.read(self._MAX_RESPONSE_BYTES + 1)
            if len(body) > self._MAX_RESPONSE_BYTES:
                raise EastmoneyDailyError("upstream payload too large")
            try:
                payload = json.loads(body.decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise EastmoneyDailyError("upstream JSON failure") from exc
            if not isinstance(payload, Mapping):
                raise EastmoneyDailyError("upstream payload is not an object")
            return payload
        except (TimeoutError, socket.timeout) as exc:
            raise EastmoneyDailyError("upstream timeout") from exc
        except (OSError, http.client.HTTPException, socket.error) as exc:
            raise EastmoneyDailyError("upstream connection failure") from exc
        finally:
            connection.close()


class EastmoneyDailyProvider:
    """Strict A-share daily provider for shared D1 research/report use-cases."""

    name = "eastmoney-daily"
    production_ready = True
    capabilities = ProviderCapabilities(
        markets=frozenset({Market.CN}),
        asset_types=frozenset({AssetType.STOCK, AssetType.ETF, AssetType.INDEX}),
        timeframes=frozenset({Timeframe.D1}),
        max_symbols_per_request=1,
        supports_constituents=False,
        supports_historical_constituents=False,
    )

    _ENDPOINTS = (
        ("primary", "https://push2his.eastmoney.com/api/qt/stock/kline/get"),
        ("delayed", "https://push2delay.eastmoney.com/api/qt/stock/kline/get"),
    )
    _PURPOSES = frozenset({DataPurpose.RESEARCH, DataPurpose.REPORT})
    _MARKET_ID = {Exchange.SSE: "1", Exchange.SZSE: "0"}
    _SUFFIX_BY_EXCHANGE = {
        Exchange.SSE: {"", "SH", "SS"},
        Exchange.SZSE: {"", "SZ"},
    }
    _ADJUSTMENT_MAP = {
        Adjustment.NONE: "0",
        Adjustment.FORWARD: "1",
        Adjustment.BACKWARD: "2",
    }

    def __init__(
        self,
        transport: EastmoneyDailyTransport | None = None,
        *,
        connect_timeout_seconds: float = 3.0,
        read_timeout_seconds: float = 8.0,
        max_retries: int = 2,
        backoff_seconds: float = 0.2,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        for value, label in (
            (connect_timeout_seconds, "connect_timeout_seconds"),
            (read_timeout_seconds, "read_timeout_seconds"),
        ):
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"{label} must be numeric")
            if value <= 0 or not math.isfinite(float(value)):
                raise ValueError(f"{label} must be finite and positive")
        if not isinstance(max_retries, int) or isinstance(max_retries, bool) or max_retries < 0:
            raise ValueError("max_retries must be a non-negative integer")
        if not isinstance(backoff_seconds, (int, float)) or isinstance(backoff_seconds, bool):
            raise ValueError("backoff_seconds must be numeric")
        if backoff_seconds < 0 or not math.isfinite(float(backoff_seconds)):
            raise ValueError("backoff_seconds must be finite and non-negative")
        self._transport = transport or _StdlibJsonTransport()
        self._connect_timeout = float(connect_timeout_seconds)
        self._read_timeout = float(read_timeout_seconds)
        self._max_retries = max_retries
        self._backoff_seconds = float(backoff_seconds)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._sleep = sleep

    def supports(self, query: DataQuery) -> bool:
        return (
            query.instrument.market is Market.CN
            and query.instrument.exchange in self._MARKET_ID
            and query.instrument.asset_type in self.capabilities.asset_types
            and query.timeframe is Timeframe.D1
            and query.purpose in self._PURPOSES
            and query.adjustment in self._ADJUSTMENT_MAP
            and self._security_code(query) is not None
        )

    def fetch_bars(self, query: DataQuery) -> DataEnvelope:
        code = self._security_code(query)
        if not self.supports(query) or code is None:
            raise EastmoneyDailyError("unsupported daily query")
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise EastmoneyDailyError("provider clock must be timezone-aware")

        params = self._params(query, code=code)
        payload, endpoint_kind = self._request_json(params)
        bars = self._parse_payload(payload, query, code)

        upper_bound = min(query.as_of, query.end) if query.end else query.as_of
        clipped = [
            bar
            for bar in bars
            if (query.start is None or bar.timestamp >= query.start)
            and bar.timestamp <= upper_bound
        ]
        if not clipped:
            raise EastmoneyDailyError("no daily bars in requested query window")

        last_bar_at = clipped[-1].timestamp
        freshness = max(0.0, (query.as_of - last_bar_at).total_seconds())
        source_chain = [
            (
                "eastmoney:push2his/api/qt/stock/kline:daily-kline:" +
                f"{query.adjustment.value}:"
                f"fqt={self._ADJUSTMENT_MAP[query.adjustment]}"
                if endpoint_kind == "primary"
                else "eastmoney:push2delay/api/qt/stock/kline:daily-kline:fqt=" +
                self._ADJUSTMENT_MAP[query.adjustment]
            )
        ]
        warnings = [
            "D1 bar evidence keeps raw Eastmoney OHLCV with timezone Asia/Shanghai",
        ]
        if endpoint_kind == "delayed":
            warnings.append("eastmoney delayed K-line endpoint used")
        return DataEnvelope(
            instrument=query.instrument,
            timeframe=query.timeframe,
            purpose=query.purpose,
            bars=clipped,
            provider=self.name,
            mode=DataMode.DELAYED,
            quality=QualityStatus.VALID,
            as_of=query.as_of,
            fetched_at=now,
            last_bar_at=last_bar_at,
            timezone="Asia/Shanghai",
            adjustment=query.adjustment,
            freshness_seconds=freshness,
            is_synthetic=False,
            warnings=warnings,
            source_chain=source_chain,
        )

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.name,
            healthy=True,
            checked_at=self._clock(),
            production_ready=self.production_ready,
            message=(
                "D1 adapter provides strict, non-interpolated daily bars for"
                " research/report only"
            ),
        )

    @classmethod
    def _params(cls, query: DataQuery, code: str) -> dict[str, str]:
        return {
            "secid": f"{cls._MARKET_ID[query.instrument.exchange]}.{code}",
            "fields1": "f1,f2,f3,f4,f5,f6,f7,f8",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
            "klt": "101",
            "fqt": cls._ADJUSTMENT_MAP[query.adjustment],
            "beg": "0",
            "end": "20500000",
        }

    @classmethod
    def _security_code(cls, query: DataQuery) -> str | None:
        match = _CN_SYMBOL.fullmatch(query.instrument.symbol.strip().upper())
        if match is None:
            return None
        expected_suffixes = cls._SUFFIX_BY_EXCHANGE.get(query.instrument.exchange)
        if expected_suffixes is None:
            return None
        suffix = match.group("suffix")
        normalized_suffix = "SH" if suffix == "SS" else suffix
        if suffix and normalized_suffix not in expected_suffixes:
            return None
        return match.group("code")

    def _request_json(self, params: Mapping[str, str]) -> tuple[Mapping[str, Any], str]:
        for endpoint_kind, url in self._ENDPOINTS:
            for attempt in range(self._max_retries + 1):
                try:
                    payload = self._transport.get_json(
                        url,
                        params,
                        connect_timeout=self._connect_timeout,
                        read_timeout=self._read_timeout,
                    )
                    return payload, endpoint_kind
                except Exception:
                    if attempt >= self._max_retries:
                        if endpoint_kind == "primary":
                            continue
                        raise EastmoneyDailyError("upstream transport failure")
                    self._sleep(self._backoff_seconds * (2**attempt))
                    continue
            # only reached on primary if primary exhausted, allow delayed path to start
        raise EastmoneyDailyError("upstream transport failure")

    def _parse_payload(
        self, payload: Mapping[str, Any], query: DataQuery, code: str
    ) -> list[Bar]:
        if payload.get("rc") not in (0, "0"):
            raise EastmoneyDailyError("upstream returned a failed daily payload")
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise EastmoneyDailyError("upstream daily payload is missing data")
        if str(data.get("code", "")) != code:
            raise EastmoneyDailyError("upstream instrument mismatch")
        if str(data.get("market", "")) != self._MARKET_ID[query.instrument.exchange]:
            raise EastmoneyDailyError("upstream market mismatch")
        rows = data.get("klines")
        if rows is None:
            rows = data.get("data")
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            raise EastmoneyDailyError("no daily bars")
        rows_list = list(rows)
        if not rows_list:
            raise EastmoneyDailyError("no daily bars")

        bars: list[Bar] = []
        seen: set[datetime] = set()
        for row in rows_list:
            if not isinstance(row, str):
                raise EastmoneyDailyError("upstream daily row is not text")
            fields = row.split(",")
            if len(fields) < 7:
                raise EastmoneyDailyError("upstream daily row has too few fields")
            timestamp = self._parse_daily_timestamp(fields[0].strip())
            if timestamp is None:
                raise EastmoneyDailyError("upstream daily row timestamp is invalid")
            if timestamp in seen:
                raise EastmoneyDailyError("upstream daily rows contain duplicates")
            open_, close, high, low, volume, amount = self._numeric_fields(fields, row)
            seen.add(timestamp)
            try:
                bars.append(
                    Bar(
                        timestamp=timestamp,
                        open=open_,
                        close=close,
                        high=high,
                        low=low,
                        volume=volume,
                        amount=amount,
                    )
                )
            except ValueError as exc:
                raise EastmoneyDailyError(
                    "upstream daily row failed OHLC normalization"
                ) from exc

        if [bar.timestamp for bar in bars] != sorted(bar.timestamp for bar in bars):
            raise EastmoneyDailyError("upstream daily rows are not sorted")
        return bars

    @staticmethod
    def _parse_daily_timestamp(value: str) -> datetime | None:
        for format_name in ("%Y-%m-%d", "%Y%m%d"):
            try:
                parsed = datetime.strptime(value, format_name)
            except ValueError:
                continue
            return datetime.combine(
                parsed.date(),
                time_of_day(15, 0),
                tzinfo=_SHANGHAI,
            )
        return None

    @staticmethod
    def _numeric_fields(
        fields: Sequence[str],
        row: str,
    ) -> tuple[float, float, float, float, float, float]:
        values: list[float] = []
        try:
            for value in fields[1:7]:
                parsed = float(value)
                if not math.isfinite(parsed):
                    raise ValueError
                values.append(parsed)
        except (TypeError, ValueError):
            raise EastmoneyDailyError(
                f"upstream daily row has non-numeric values: {row}"
            ) from None
        if len(values) < 6:
            raise EastmoneyDailyError("upstream daily row has insufficient numeric fields")
        open_, close, high, low, volume, amount = values[:6]
        if open_ <= 0 or close <= 0 or high <= 0 or low <= 0:
            raise EastmoneyDailyError("upstream daily row has non-positive price")
        if high < max(open_, close, low) or low > min(open_, close, high):
            raise EastmoneyDailyError("upstream daily row has inconsistent OHLC")
        if volume < 0 or amount < 0:
            raise EastmoneyDailyError("upstream daily row has negative quantity")
        return open_, close, high, low, volume, amount
