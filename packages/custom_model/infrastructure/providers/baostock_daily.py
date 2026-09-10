from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from threading import Lock
from zoneinfo import ZoneInfo

import pandas as pd

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
    QuoteQuery,
    QuoteTemporalMode,
    Timeframe,
)
from custom_model.infrastructure.providers.eastmoney_quote import EastmoneyQuoteProvider


_SHANGHAI = ZoneInfo("Asia/Shanghai")
_BAOSTOCK_LOCK = Lock()
_BAOSTOCK_CONNECTED = False


class BaostockDailyProvider:
    """China stock, ETF and index D1 bars from baostock."""

    name = "baostock-daily"
    production_ready = True
    capabilities = ProviderCapabilities(
        markets=frozenset({Market.CN}),
        asset_types=frozenset(
            {
                AssetType.STOCK,
                AssetType.ETF,
                AssetType.INDEX,
                AssetType.SECTOR_INDEX,
            }
        ),
        timeframes=frozenset({Timeframe.D1}),
        max_symbols_per_request=1,
    )

    def __init__(
        self,
        *,
        quote_provider: EastmoneyQuoteProvider | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._quote_provider = quote_provider or EastmoneyQuoteProvider(max_retries=0)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def supports(self, query: DataQuery) -> bool:
        return (
            query.instrument.market is Market.CN
            and query.instrument.exchange in {Exchange.SSE, Exchange.SZSE}
            and query.instrument.asset_type
            in {
                AssetType.STOCK,
                AssetType.ETF,
                AssetType.INDEX,
                AssetType.SECTOR_INDEX,
            }
            and query.timeframe is Timeframe.D1
            and query.adjustment in {Adjustment.NONE, Adjustment.FORWARD}
        )

    def fetch_bars(self, query: DataQuery) -> DataEnvelope:
        if not self.supports(query):
            raise ValueError("baostock daily query is unsupported")

        upper_bound = min(query.as_of, query.end) if query.end else query.as_of
        start_at = query.start or (upper_bound - timedelta(days=3 * 365))
        with _BAOSTOCK_LOCK:
            frame = self._fetch_frame(
                exchange=query.instrument.exchange,
                symbol=query.instrument.symbol,
                start_date=start_at.astimezone(_SHANGHAI).date().isoformat(),
                end_date=upper_bound.astimezone(_SHANGHAI).date().isoformat(),
                adjustment=query.adjustment,
            )
        if frame is None or frame.empty:
            raise RuntimeError("baostock returned no daily bars")

        bars = self._frame_bars(frame)
        source_chain = ["baostock:query_history_k_data_plus"]
        warnings: list[str] = []

        if (
            query.adjustment is Adjustment.FORWARD
            and query.instrument.asset_type in {AssetType.STOCK, AssetType.ETF}
            and query.purpose is not DataPurpose.BACKTEST
        ):
            try:
                quote = self._quote_provider.fetch_quote(
                    QuoteQuery(
                        instrument=query.instrument,
                        purpose=DataPurpose.RESEARCH,
                        temporal_mode=QuoteTemporalMode.LATEST,
                        requested_at=self._clock(),
                    )
                )
            except Exception:
                quote = None
            if (
                quote is not None
                and quote.quality is QualityStatus.VALID
                and not quote.is_synthetic
            ):
                quote_date = quote.data_time.astimezone(_SHANGHAI).date()
                start_date = start_at.astimezone(_SHANGHAI).date()
                end_date = upper_bound.astimezone(_SHANGHAI).date()
                if start_date <= quote_date <= end_date:
                    current_bar = Bar(
                        timestamp=datetime.combine(
                            quote_date,
                            datetime.min.time(),
                            tzinfo=_SHANGHAI,
                        ),
                        open=quote.open,
                        high=quote.high,
                        low=quote.low,
                        close=quote.last_price,
                        volume=quote.volume_lots,
                        amount=quote.amount,
                    )
                    bars = [
                        bar
                        for bar in bars
                        if bar.timestamp.astimezone(_SHANGHAI).date() != quote_date
                    ]
                    bars.append(current_bar)
                    bars.sort(key=lambda bar: bar.timestamp)
                    source_chain.extend(quote.source_chain)
                    warnings.append("current D1 bar completed from native quote OHLCV")

        bars = [
            bar
            for bar in bars
            if bar.timestamp <= upper_bound
            and (query.start is None or bar.timestamp >= query.start)
        ]
        if not bars:
            raise RuntimeError("baostock returned no bars inside the requested window")

        fetched_at = self._clock()
        last_bar_at = bars[-1].timestamp
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
            adjustment=query.adjustment,
            freshness_seconds=max(0.0, (query.as_of - last_bar_at).total_seconds()),
            is_synthetic=False,
            warnings=warnings,
            source_chain=list(dict.fromkeys(source_chain)),
        )

    @staticmethod
    def _fetch_frame(
        *,
        exchange: Exchange,
        symbol: str,
        start_date: str,
        end_date: str,
        adjustment: Adjustment,
    ) -> pd.DataFrame:
        global _BAOSTOCK_CONNECTED

        import baostock as bs

        if not _BAOSTOCK_CONNECTED:
            login = bs.login()
            if login.error_code != "0":
                raise RuntimeError(f"baostock login failed: {login.error_msg}")
            _BAOSTOCK_CONNECTED = True

        pure_symbol = symbol.upper().replace(".SS", "").replace(".SZ", "")
        market_prefix = "sh" if exchange is Exchange.SSE else "sz"
        result = bs.query_history_k_data_plus(
            f"{market_prefix}.{pure_symbol}",
            "date,open,high,low,close,volume,amount",
            start_date=start_date,
            end_date=end_date,
            frequency="d",
            adjustflag="2" if adjustment is Adjustment.FORWARD else "3",
        )
        if result.error_code != "0":
            _BAOSTOCK_CONNECTED = False
            raise RuntimeError(f"baostock query failed: {result.error_msg}")

        rows: list[list[str]] = []
        while result.next():
            rows.append(result.get_row_data())
        if not rows:
            return pd.DataFrame()

        frame = pd.DataFrame(
            rows,
            columns=["date", "open", "high", "low", "close", "volume", "amount"],
        )
        frame["date"] = pd.to_datetime(frame["date"])
        for column in ("open", "high", "low", "close", "volume", "amount"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        return frame.set_index("date")

    @staticmethod
    def _frame_bars(frame: pd.DataFrame) -> list[Bar]:
        normalized = frame.copy()
        normalized.columns = [str(column).strip().lower() for column in normalized.columns]
        normalized = normalized.dropna(subset=["open", "high", "low", "close", "volume"])
        normalized = normalized.sort_index()
        normalized = normalized[~normalized.index.duplicated(keep="last")]
        bars: list[Bar] = []
        for index, row in normalized.iterrows():
            timestamp = pd.Timestamp(index)
            if timestamp.tzinfo is None:
                timestamp = timestamp.tz_localize(_SHANGHAI)
            else:
                timestamp = timestamp.tz_convert(_SHANGHAI)
            amount = row.get("amount")
            bars.append(
                Bar(
                    timestamp=timestamp.to_pydatetime(),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=max(0.0, float(row["volume"]) / 100.0),
                    amount=None if amount is None or pd.isna(amount) else max(0.0, float(amount)),
                )
            )
        return bars

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.name,
            healthy=True,
            checked_at=self._clock(),
            message="baostock China stock, ETF and index daily history",
            production_ready=self.production_ready,
        )


__all__ = ["BaostockDailyProvider"]
