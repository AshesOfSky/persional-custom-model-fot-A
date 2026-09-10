from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from dataclasses import dataclass

from datetime import datetime, timedelta, timezone

import http.client

import json

import math

import os

import socket

from typing import Any, Protocol

from urllib.parse import urlencode

from zoneinfo import ZoneInfo

from custom_model.application.ports import InstrumentSearchMatch, InstrumentSearchProvider, ProviderCapabilities, ProviderHealth

from custom_model.domain.models import Adjustment, AssetType, Bar, DataEnvelope, DataMode, DataPurpose, DataQuery, Exchange, FundamentalEnvelope, FundamentalEvidenceBasis, FundamentalField, FundamentalFieldStatus, FundamentalQuery, InstrumentId, Market, QualityStatus, QuoteBatchQuery, QuoteBatchRoute, QuoteEnvelope, QuoteSessionStatus, QuoteTemporalMode, Timeframe

SHANGHAI = ZoneInfo("Asia/Shanghai")


_BASE_HOST = "fuyao.aicubes.cn"


class AICubesError(RuntimeError):
    """A sanitized failure from the official HiThink REST boundary."""

    error_category = "provider"
    retryable = False


class AICubesTransportError(AICubesError):
    """Sanitized transport failure with an explicit retry policy."""

    error_category = "transport"


class AICubesTimeoutError(AICubesTransportError):
    error_category = "timeout"
    retryable = True

    def __init__(self) -> None:
        super().__init__("AICubes request timed out")


class AICubesConnectionError(AICubesTransportError):
    error_category = "connection"
    retryable = True

    def __init__(self) -> None:
        super().__init__("AICubes connection failed")


class AICubesHTTPError(AICubesTransportError):
    error_category = "http"
    _RETRYABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})

    def __init__(self, status: int) -> None:
        self.status = int(status)
        self.retryable = self.status in self._RETRYABLE_STATUSES
        super().__init__(f"AICubes HTTP failure ({self.status})")


class AICubesInvalidJSONError(AICubesTransportError):
    error_category = "invalid_json"

    def __init__(self) -> None:
        super().__init__("AICubes returned invalid JSON")


class AICubesBusinessError(AICubesError):
    """Typed business-code failure without echoing request parameters or secrets."""

    def __init__(self, business_code: int, request_id: str | None) -> None:
        self.business_code = business_code
        self.request_id = request_id
        suffix = f"; request_id={request_id}" if request_id else ""
        super().__init__(f"AICubes business error {business_code}{suffix}")


class AICubesRateLimitError(AICubesBusinessError):
    """AICubes business code 4001: the agreed QPS was exceeded."""


class AICubesBatchResponseError(AICubesError):
    """Exact-code batch mismatch retained for audit and retry decisions."""

    def __init__(
        self,
        *,
        requested_codes: tuple[str, ...],
        returned_codes: tuple[str, ...],
        duplicate_codes: tuple[str, ...],
        missing_codes: tuple[str, ...],
        missing_instrument_keys: tuple[str, ...],
        unexpected_codes: tuple[str, ...],
        request_id: str | None,
    ) -> None:
        self.requested_codes = requested_codes
        self.returned_codes = returned_codes
        self.duplicate_codes = duplicate_codes
        self.missing_codes = missing_codes
        self.missing_instrument_keys = missing_instrument_keys
        self.unexpected_codes = unexpected_codes
        self.request_id = request_id
        suffix = f"; request_id={request_id}" if request_id else ""
        super().__init__(
            "AICubes batch response code mismatch "
            f"(duplicate={len(duplicate_codes)}, missing={len(missing_codes)}, "
            f"unexpected={len(unexpected_codes)}){suffix}"
        )


class AICubesTransport(Protocol):
    def get_json(
        self,
        path: str,
        params: Mapping[str, str],
        *,
        api_key: str,
        timeout: float,
    ) -> Mapping[str, Any]: ...


class _StdlibAICubesTransport:
    def get_json(
        self,
        path: str,
        params: Mapping[str, str],
        *,
        api_key: str,
        timeout: float,
    ) -> Mapping[str, Any]:
        query = urlencode(params)
        target = f"{path}?{query}" if query else path
        connection = http.client.HTTPSConnection(_BASE_HOST, timeout=timeout)
        try:
            connection.request(
                "GET",
                target,
                headers={
                    "Accept": "application/json",
                    "X-api-key": api_key,
                    "User-Agent": "custom-model/aicubes-fallback",
                },
            )
            response = connection.getresponse()
            if response.status >= 400:
                raise AICubesHTTPError(response.status)
            payload = json.loads(response.read().decode("utf-8"))
        except AICubesError:
            raise
        except (TimeoutError, socket.timeout) as exc:
            raise AICubesTimeoutError() from exc
        except (OSError, http.client.HTTPException) as exc:
            raise AICubesConnectionError() from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AICubesInvalidJSONError() from exc
        finally:
            connection.close()
        if not isinstance(payload, Mapping):
            raise AICubesInvalidJSONError()
        return payload


@dataclass(frozen=True)
class AICubesResponse:
    data: Mapping[str, Any]
    request_id: str | None


class AICubesClient:
    def __init__(
        self,
        api_key: str,
        transport: AICubesTransport | None = None,
        *,
        timeout_seconds: float = 15.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("AICubes API key is required")
        self._api_key = api_key.strip()
        self._transport = transport or _StdlibAICubesTransport()
        self._timeout = float(timeout_seconds)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def get(self, path: str, params: Mapping[str, str]) -> AICubesResponse:
        payload = self._transport.get_json(
            path,
            params,
            api_key=self._api_key,
            timeout=self._timeout,
        )
        try:
            code = int(payload.get("code"))
        except (TypeError, ValueError) as exc:
            raise AICubesError("AICubes response has no business code") from exc
        request_id = _optional_text(payload.get("request_id"))
        if code != 0:
            error_type = AICubesRateLimitError if code == 4001 else AICubesBusinessError
            raise error_type(code, request_id)
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise AICubesError("AICubes success response has no data object")
        return AICubesResponse(data=data, request_id=request_id)

    def now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise AICubesError("AICubes clock must be timezone-aware")
        return value


def create_aicubes_client_from_env() -> AICubesClient | None:
    api_key = os.getenv("HITHINK_FINANCE_API_KEY") or os.getenv("AICUBES_API_KEY")
    if (not api_key or not api_key.strip()) and os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                for name in ("HITHINK_FINANCE_API_KEY", "AICUBES_API_KEY"):
                    try:
                        candidate, _ = winreg.QueryValueEx(key, name)
                    except OSError:
                        continue
                    if isinstance(candidate, str) and candidate.strip():
                        api_key = candidate
                        break
        except OSError:
            api_key = None
    if not api_key or not api_key.strip():
        return None
    return AICubesClient(api_key)


class AICubesDailyProvider:
    name = "aicubes-daily"
    production_ready = True
    capabilities = ProviderCapabilities(
        markets=frozenset({Market.CN}),
        asset_types=frozenset(
            {AssetType.STOCK, AssetType.INDEX, AssetType.SECTOR_INDEX}
        ),
        timeframes=frozenset({Timeframe.D1}),
        max_symbols_per_request=1,
    )
    _PURPOSES = frozenset(
        {
            DataPurpose.RESEARCH,
            DataPurpose.SCREENER,
            DataPurpose.ALERT,
            DataPurpose.REPORT,
            DataPurpose.BACKTEST,
        }
    )

    def __init__(self, client: AICubesClient) -> None:
        self._client = client

    def supports(self, query: DataQuery) -> bool:
        supported_asset = query.instrument.asset_type in {
            AssetType.STOCK,
            AssetType.INDEX,
            AssetType.SECTOR_INDEX,
        }
        adjustment_supported = (
            query.instrument.asset_type is AssetType.STOCK
            or query.adjustment is Adjustment.NONE
        )
        return (
            query.instrument.market is Market.CN
            and supported_asset
            and adjustment_supported
            and query.timeframe is Timeframe.D1
            and query.purpose in self._PURPOSES
            and _thscode(query.instrument) is not None
        )

    def fetch_bars(self, query: DataQuery) -> DataEnvelope:
        thscode = _thscode(query.instrument)
        if not self.supports(query) or thscode is None:
            raise AICubesError("AICubes daily query is unsupported")
        end = min(query.end or query.as_of, query.as_of)
        start = query.start or (end - timedelta(days=3650))
        is_index = query.instrument.asset_type in {
            AssetType.INDEX,
            AssetType.SECTOR_INDEX,
        }
        path = (
            "/api/a-share-index/prices/historical"
            if is_index
            else "/api/a-share/prices/historical"
        )
        params = {
            "thscode": thscode,
            "interval": "1d",
            "start": str(_to_millis(start)),
            "end": str(_to_millis(end)),
        }
        if not is_index:
            params.update(
                {
                    "adjust": query.adjustment.value,
                    "offset": "0",
                }
            )
        response = self._client.get(
            path,
            params,
        )
        rows = _items(response.data)
        bars: list[Bar] = []
        for row in rows:
            row_code = _optional_text(row.get("thscode"))
            if row_code and row_code.upper() != thscode:
                raise AICubesError("AICubes daily response instrument mismatch")
            timestamp = _from_millis(row.get("date_ms"), "date_ms")
            if timestamp < start or timestamp > end:
                continue
            bars.append(
                Bar(
                    timestamp=timestamp,
                    open=_number(row.get("open_price"), "open_price"),
                    high=_number(row.get("high_price"), "high_price"),
                    low=_number(row.get("low_price"), "low_price"),
                    close=_number(row.get("close_price"), "close_price"),
                    volume=_number(row.get("volume"), "volume", non_negative=True),
                    amount=_number(
                        row.get("turnover"), "turnover", non_negative=True
                    ),
                )
            )
        bars.sort(key=lambda bar: bar.timestamp)
        if not bars:
            raise AICubesError("AICubes returned no daily bars")
        fetched_at = self._client.now()
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
            warnings=[
                "Official HiThink AICubes REST index daily bars without adjustment."
                if is_index
                else "Official HiThink AICubes REST daily bars."
            ],
            request_id=response.request_id or "aicubes",
            source_chain=[
                f"aicubes:{path.removeprefix('/')}",
                "aicubes:data.timestamp:latest-upstream-bar",
            ],
        )

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.name,
            healthy=True,
            checked_at=self._client.now(),
            message="configured; requests are demand-driven",
            production_ready=True,
        )


class AICubesBatchQuoteProvider:
    """Strict one-route AICubes snapshot batches for stocks, ETFs and indexes."""

    name = "aicubes-batch-quote"
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
        max_symbols_per_request=30,
    )
    _PURPOSES = frozenset({DataPurpose.RESEARCH, DataPurpose.REPORT})
    _STOCK_PATH = "/api/a-share/prices/snapshot"
    _INDEX_PATH = "/api/a-share-index/prices/snapshot"

    def __init__(self, client: AICubesClient) -> None:
        self._client = client

    def supports(self, query: QuoteBatchQuery) -> bool:
        return (
            len(query.instruments) <= self.capabilities.max_symbols_per_request
            and query.purpose in self._PURPOSES
            and all(
                instrument.market is Market.CN and _thscode(instrument) is not None
                for instrument in query.instruments
            )
        )

    def fetch_quotes(self, query: QuoteBatchQuery) -> tuple[QuoteEnvelope, ...]:
        if not self.supports(query):
            raise AICubesError("AICubes batch quote query is unsupported")
        requested_codes = tuple(
            code
            for instrument in query.instruments
            if (code := _thscode(instrument)) is not None
        )
        if len(requested_codes) != len(query.instruments):
            raise AICubesError("AICubes batch quote query has an invalid instrument")
        path = (
            self._STOCK_PATH
            if query.route is QuoteBatchRoute.EQUITY_SNAPSHOT
            else self._INDEX_PATH
        )
        response = self._client.get(path, {"thscodes": ",".join(requested_codes)})
        rows = _items(response.data)
        returned_codes = tuple(
            (_optional_text(row.get("thscode")) or "").upper() for row in rows
        )
        if any(not code for code in returned_codes):
            raise AICubesError("AICubes batch quote row has no thscode")

        seen: set[str] = set()
        duplicate_codes: list[str] = []
        for code in returned_codes:
            if code in seen and code not in duplicate_codes:
                duplicate_codes.append(code)
            seen.add(code)
        requested_set = set(requested_codes)
        missing_codes = tuple(code for code in requested_codes if code not in seen)
        missing_instrument_keys = tuple(
            instrument.key
            for instrument, code in zip(
                query.instruments, requested_codes, strict=True
            )
            if code not in seen
        )
        unexpected_codes = tuple(
            dict.fromkeys(code for code in returned_codes if code not in requested_set)
        )
        if duplicate_codes or missing_codes or unexpected_codes:
            raise AICubesBatchResponseError(
                requested_codes=requested_codes,
                returned_codes=returned_codes,
                duplicate_codes=tuple(duplicate_codes),
                missing_codes=missing_codes,
                missing_instrument_keys=missing_instrument_keys,
                unexpected_codes=unexpected_codes,
                request_id=response.request_id,
            )

        provider_updated_at = _from_millis(
            response.data.get("timestamp"), "timestamp"
        )
        fetched_at = self._client.now()
        provider_clock_capped = provider_updated_at > fetched_at
        if provider_clock_capped:
            provider_updated_at = fetched_at
        row_by_code = {
            str(row.get("thscode") or "").strip().upper(): row for row in rows
        }
        return tuple(
            self._quote_from_row(
                instrument=instrument,
                thscode=thscode,
                row=row_by_code[thscode],
                query=query,
                path=path,
                provider_updated_at=provider_updated_at,
                fetched_at=fetched_at,
                provider_clock_capped=provider_clock_capped,
                request_id=response.request_id,
            )
            for instrument, thscode in zip(
                query.instruments, requested_codes, strict=True
            )
        )

    def _quote_from_row(
        self,
        *,
        instrument: InstrumentId,
        thscode: str,
        row: Mapping[str, Any],
        query: QuoteBatchQuery,
        path: str,
        provider_updated_at: datetime,
        fetched_at: datetime,
        provider_clock_capped: bool,
        request_id: str | None,
    ) -> QuoteEnvelope:
        previous_close = _number(row.get("prev_price"), "prev_price")
        high = _number(row.get("high_price"), "high_price")
        low = _number(row.get("low_price"), "low_price")
        warnings = [
            "Official HiThink AICubes batch snapshot; trade phase is not supplied."
        ]
        if provider_clock_capped:
            warnings.append(
                "AICubes snapshot clock was ahead of the client clock and was capped at fetch time."
            )
        return QuoteEnvelope(
            instrument=instrument,
            purpose=query.purpose,
            display_name=thscode,
            provider=self.name,
            mode=DataMode.DELAYED,
            quality=QualityStatus.STALE,
            temporal_mode=QuoteTemporalMode.LATEST,
            requested_at=query.requested_at,
            as_of=fetched_at,
            fetched_at=fetched_at,
            data_time=provider_updated_at,
            provider_updated_at=provider_updated_at,
            data_time_basis="provider_timestamp",
            timezone="Asia/Shanghai",
            currency=instrument.currency,
            freshness_seconds=(fetched_at - provider_updated_at).total_seconds(),
            session_status=_cn_session_status(fetched_at),
            trade_status_code=0,
            trade_status_verified=False,
            last_price=_number(row.get("last_price"), "last_price"),
            previous_close=previous_close,
            open=_number(row.get("open_price"), "open_price"),
            high=high,
            low=low,
            change=_number(row.get("price_change"), "price_change"),
            change_percent=_number(
                row.get("price_change_ratio_pct"), "price_change_ratio_pct"
            ),
            volume_lots=_number(row.get("volume"), "volume", non_negative=True)
            / 100.0,
            amount=_number(row.get("turnover"), "turnover", non_negative=True),
            amplitude=(high - low) / previous_close * 100.0,
            warnings=warnings,
            is_synthetic=False,
            source_chain=[
                f"aicubes:{path.removeprefix('/')}",
                "aicubes:data.timestamp:batch-snapshot",
            ],
            request_id=request_id or "aicubes",
        )

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.name,
            healthy=True,
            checked_at=self._client.now(),
            message="configured; requests are demand-driven",
            production_ready=True,
        )


class AICubesFundamentalProvider:
    name = "aicubes-fundamental"
    production_ready = True
    _PURPOSES = frozenset(
        {DataPurpose.RESEARCH, DataPurpose.SCREENER, DataPurpose.ALERT, DataPurpose.REPORT}
    )

    def __init__(self, client: AICubesClient) -> None:
        self._client = client

    def supports(self, query: FundamentalQuery) -> bool:
        return (
            query.instrument.market is Market.CN
            and query.instrument.asset_type is AssetType.STOCK
            and query.purpose in self._PURPOSES
            and _thscode(query.instrument) is not None
        )

    def fetch_fundamentals(self, query: FundamentalQuery) -> FundamentalEnvelope:
        thscode = _thscode(query.instrument)
        if not self.supports(query) or thscode is None:
            raise AICubesError("AICubes fundamental query is unsupported")
        params = {"thscode": thscode, "period": "quarterly", "limit": "20"}
        income_response = self._client.get(
            "/api/a-share/financials/income-statements", params
        )
        balance_response = self._client.get(
            "/api/a-share/financials/balance-sheets", params
        )
        income_rows = _items(income_response.data)
        balance_rows = _items(balance_response.data)
        income, balance = _latest_common_statements(
            income_rows, balance_rows, thscode=thscode, as_of=query.as_of
        )
        period_end = _from_millis(income.get("period_end_ms"), "period_end_ms")
        income_published = _from_millis(
            income.get("report_date_ms"), "report_date_ms"
        )
        balance_published = _from_millis(
            balance.get("report_date_ms"), "report_date_ms"
        )
        fields: list[FundamentalField] = []

        def add_statement_field(
            name: str,
            row: Mapping[str, Any],
            source_field: str,
            published_at: datetime,
        ) -> None:
            raw = row.get(source_field)
            if raw is None:
                return
            value = _number(raw, source_field)
            fields.append(
                FundamentalField(
                    name=name,
                    source_field=source_field,
                    raw_value=raw,
                    normalized_value=value,
                    unit=query.instrument.currency,
                    basis=FundamentalEvidenceBasis.FINANCIAL_STATEMENT,
                    status=FundamentalFieldStatus.VERIFIED,
                    period_end=period_end,
                    published_at=published_at,
                )
            )

        add_statement_field(
            "revenue", income, "operating_income", income_published
        )
        net_source = (
            "parent_holder_net_profit"
            if income.get("parent_holder_net_profit") is not None
            else "net_profit"
        )
        add_statement_field("net_income", income, net_source, income_published)
        add_statement_field(
            "total_assets", balance, "assets_total", balance_published
        )
        add_statement_field(
            "total_liabilities", balance, "total_debt", balance_published
        )
        add_statement_field(
            "equity", balance, "holder_equity_total", balance_published
        )

        net_income = _number(income.get(net_source), net_source)
        total_assets = _number(balance.get("assets_total"), "assets_total")
        total_debt = _number(balance.get("total_debt"), "total_debt")
        equity = _number(balance.get("holder_equity_total"), "holder_equity_total")
        derived_published = max(income_published, balance_published)
        fields.extend(
            [
                FundamentalField(
                    name="ROE",
                    source_field=f"{net_source}/holder_equity_total",
                    raw_value=net_income / equity,
                    normalized_value=net_income / equity * 100.0,
                    unit="percent",
                    basis=FundamentalEvidenceBasis.DERIVED_FINANCIAL_STATEMENT,
                    status=FundamentalFieldStatus.VERIFIED,
                    period_end=period_end,
                    published_at=derived_published,
                ),
                FundamentalField(
                    name="debt_ratio",
                    source_field="total_debt/assets_total",
                    raw_value=total_debt / total_assets,
                    normalized_value=total_debt / total_assets * 100.0,
                    unit="percent",
                    basis=FundamentalEvidenceBasis.DERIVED_FINANCIAL_STATEMENT,
                    status=FundamentalFieldStatus.VERIFIED,
                    period_end=period_end,
                    published_at=derived_published,
                ),
            ]
        )

        warnings = [
            "ROE is derived from reported parent net income and period-end equity.",
        ]
        source_chain = [
            "aicubes:api/a-share/financials/income-statements",
            "aicubes:api/a-share/financials/balance-sheets",
        ]
        evidence_times = [income_published, balance_published, derived_published]

        try:
            cash_response = self._client.get(
                "/api/a-share/financials/cash-flow-statements", params
            )
            cash_by_period = {
                int(row["period_end_ms"]): row
                for row in _items(cash_response.data)
                if row.get("period_end_ms") is not None
                and row.get("report_date_ms") is not None
                and int(row["report_date_ms"]) <= _to_millis(query.as_of)
            }
            cash = cash_by_period.get(int(income["period_end_ms"]))
            if cash is not None and cash.get("act_cash_flow_net") is not None:
                cash_published = _from_millis(
                    cash.get("report_date_ms"), "report_date_ms"
                )
                fields.append(
                    FundamentalField(
                        name="operating_cash_flow",
                        source_field="act_cash_flow_net",
                        raw_value=cash["act_cash_flow_net"],
                        normalized_value=_number(
                            cash["act_cash_flow_net"], "act_cash_flow_net"
                        ),
                        unit=query.instrument.currency,
                        basis=FundamentalEvidenceBasis.FINANCIAL_STATEMENT,
                        status=FundamentalFieldStatus.VERIFIED,
                        period_end=period_end,
                        published_at=cash_published,
                    )
                )
                evidence_times.append(cash_published)
                source_chain.append(
                    "aicubes:api/a-share/financials/cash-flow-statements"
                )
        except AICubesError:
            warnings.append("AICubes cash-flow statement was unavailable.")

        try:
            valuation_response = self._client.get(
                "/api/a-share/valuations/snapshot", {"thscodes": thscode}
            )
            valuation_time_raw = valuation_response.data.get("timestamp")
            if valuation_time_raw is not None:
                valuation_time = _from_millis(valuation_time_raw, "timestamp")
                if valuation_time <= query.as_of:
                    valuation_rows = _items(valuation_response.data)
                    valuation = valuation_rows[0] if valuation_rows else None
                    if valuation is not None:
                        for name, source_field in (
                            ("PE_TTM", "pe_ttm"),
                            ("PE_MRQ", "pe_mrq"),
                            ("PB_MRQ", "pb_mrq"),
                            ("PS_TTM", "ps_ttm"),
                            ("PCF_TTM", "pcf_ttm"),
                        ):
                            raw = valuation.get(source_field)
                            if raw is None:
                                continue
                            fields.append(
                                FundamentalField(
                                    name=name,
                                    source_field=source_field,
                                    raw_value=raw,
                                    normalized_value=_number(raw, source_field),
                                    unit="multiple",
                                    basis=FundamentalEvidenceBasis.MARKET_SNAPSHOT,
                                    status=FundamentalFieldStatus.VERIFIED,
                                    market_time=valuation_time,
                                )
                            )
                        evidence_times.append(valuation_time)
                        source_chain.append(
                            "aicubes:api/a-share/valuations/snapshot"
                        )
        except AICubesError:
            warnings.append("AICubes valuation snapshot was unavailable.")

        fetched_at = self._client.now()
        data_time = max(evidence_times)
        return FundamentalEnvelope(
            instrument=query.instrument,
            purpose=query.purpose,
            provider=self.name,
            mode=DataMode.DELAYED,
            quality=QualityStatus.VALID,
            as_of=query.as_of,
            fetched_at=fetched_at,
            data_time=data_time,
            timezone="Asia/Shanghai",
            currency=query.instrument.currency,
            freshness_seconds=max(0.0, (query.as_of - data_time).total_seconds()),
            fields=fields,
            warnings=warnings,
            is_synthetic=False,
            source_chain=source_chain,
            request_id=income_response.request_id or "aicubes",
        )

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.name,
            healthy=True,
            checked_at=self._client.now(),
            message="configured; requests are demand-driven",
            production_ready=True,
        )


class AICubesInstrumentSearchProvider:
    name = "aicubes-instrument-search"

    def __init__(self, client: AICubesClient) -> None:
        self._client = client

    def search(self, query: str, *, limit: int = 10) -> list[InstrumentSearchMatch]:
        normalized = query.strip()
        if normalized.upper().endswith(".SS"):
            normalized = normalized[:-3] + ".SH"
        search_params = {
            "q": normalized,
            "asset_type": "a-share,a-share-index",
            "limit": str(limit),
        }
        response = self._client.get("/api/meta/tickers/search", search_params)
        rows = _items(response.data)
        if not rows and len(normalized) == 6 and normalized.isdigit():
            response = self._client.get(
                "/api/meta/tickers/search",
                {**search_params, "q": f"{normalized}.TI"},
            )
            rows = _items(response.data)
        matches: list[InstrumentSearchMatch] = []
        for row in rows:
            asset_type_value = str(row.get("asset_type") or "").lower()
            if asset_type_value not in {"a-share", "a-share-index"}:
                continue
            thscode = str(row.get("thscode") or "").strip().upper()
            ticker = str(row.get("ticker") or thscode.split(".", 1)[0]).strip()
            name = str(row.get("name") or "").strip()
            if asset_type_value == "a-share-index" and thscode.endswith(".TI"):
                exchange = Exchange.OTHER
                asset_type = AssetType.SECTOR_INDEX
                legacy_code = thscode
            else:
                exchange_value = str(row.get("exchange") or "").upper()
                exchange = {
                    "SH": Exchange.SSE,
                    "SZ": Exchange.SZSE,
                    "BJ": Exchange.BSE,
                }.get(exchange_value)
                suffix = {
                    "SH": ".SS" if asset_type_value == "a-share" else ".SH",
                    "SZ": ".SZ",
                    "BJ": ".BJ",
                }.get(exchange_value)
                asset_type = (
                    AssetType.STOCK
                    if asset_type_value == "a-share"
                    else AssetType.INDEX
                )
                legacy_code = f"{ticker}{suffix}" if suffix else ""
            if exchange is None or not legacy_code or not ticker or not name:
                continue
            exact_tokens = {
                normalized.casefold(),
                ticker.casefold(),
                thscode.casefold(),
                name.casefold(),
            }
            matches.append(
                InstrumentSearchMatch(
                    instrument=InstrumentId(
                        symbol=legacy_code,
                        exchange=exchange,
                        market=Market.CN,
                        asset_type=asset_type,
                        currency=str(row.get("currency") or "CNY").upper(),
                    ),
                    display_name=name,
                    legacy_code=legacy_code,
                    source=self.name,
                    match_score=100.0 if normalized.casefold() in exact_tokens else 80.0,
                )
            )
            if len(matches) >= limit:
                break
        return matches


class FallbackInstrumentSearchProvider:
    """Use the preferred search and call the existing source only as fallback."""

    name = "preferred-then-existing-search"

    def __init__(
        self,
        primary: InstrumentSearchProvider,
        fallback: InstrumentSearchProvider,
    ) -> None:
        self._primary = primary
        self._fallback = fallback

    def search(self, query: str, *, limit: int = 10) -> list[InstrumentSearchMatch]:
        try:
            primary_matches = self._primary.search(query, limit=limit)
        except Exception:
            primary_matches = []
        if primary_matches:
            return primary_matches[:limit]
        return self._fallback.search(query, limit=limit)[:limit]


def _latest_common_statements(
    income_rows: Sequence[Mapping[str, Any]],
    balance_rows: Sequence[Mapping[str, Any]],
    *,
    thscode: str,
    as_of: datetime,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    as_of_ms = _to_millis(as_of)

    def admitted(rows: Sequence[Mapping[str, Any]]) -> dict[int, Mapping[str, Any]]:
        result: dict[int, Mapping[str, Any]] = {}
        for row in rows:
            row_code = str(row.get("thscode") or "").upper()
            period = row.get("period_end_ms")
            report = row.get("report_date_ms")
            if row_code != thscode or period is None or report is None:
                continue
            if int(period) <= as_of_ms and int(report) <= as_of_ms:
                result[int(period)] = row
        return result

    income_by_period = admitted(income_rows)
    balance_by_period = admitted(balance_rows)
    common = sorted(set(income_by_period) & set(balance_by_period), reverse=True)
    if not common:
        raise AICubesError("AICubes has no disclosed common financial period")
    period = common[0]
    return income_by_period[period], balance_by_period[period]


def _thscode(instrument: InstrumentId) -> str | None:
    raw = instrument.symbol.strip().upper()
    ticker, dot, supplied_suffix = raw.partition(".")
    if len(ticker) != 6 or not ticker.isdigit():
        return None
    if (
        instrument.asset_type is AssetType.SECTOR_INDEX
        and dot
        and supplied_suffix == "TI"
    ):
        return f"{ticker}.TI"
    expected = {
        Exchange.SSE: "SH",
        Exchange.SZSE: "SZ",
        Exchange.BSE: "BJ",
    }.get(instrument.exchange)
    if expected is None:
        return None
    if dot and supplied_suffix not in {expected, "SS" if expected == "SH" else expected}:
        return None
    return f"{ticker}.{expected}"


def _cn_session_status(value: datetime) -> QuoteSessionStatus:
    local = value.astimezone(SHANGHAI)
    if local.weekday() >= 5:
        return QuoteSessionStatus.NON_TRADING_DAY_UNVERIFIED
    minute = local.hour * 60 + local.minute
    if minute < 570:
        return QuoteSessionStatus.PRE_OPEN
    if minute < 690:
        return QuoteSessionStatus.OPEN
    if minute < 780:
        return QuoteSessionStatus.MIDDAY_BREAK
    if minute < 900:
        return QuoteSessionStatus.OPEN
    return QuoteSessionStatus.AFTER_CLOSE


def _items(data: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = data.get("item")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise AICubesError("AICubes data.item is not an array")
    return [row for row in raw if isinstance(row, Mapping)]


def _from_millis(value: object, field: str) -> datetime:
    if isinstance(value, bool):
        raise AICubesError(f"AICubes {field} is invalid")
    try:
        millis = int(value)
    except (TypeError, ValueError) as exc:
        raise AICubesError(f"AICubes {field} is invalid") from exc
    return datetime.fromtimestamp(millis / 1000.0, tz=timezone.utc).astimezone(
        SHANGHAI
    )


def _to_millis(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def _number(value: object, field: str, *, non_negative: bool = False) -> float:
    if isinstance(value, bool):
        raise AICubesError(f"AICubes {field} is invalid")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise AICubesError(f"AICubes {field} is invalid") from exc
    if not math.isfinite(number) or (non_negative and number < 0):
        raise AICubesError(f"AICubes {field} is invalid")
    return number


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


