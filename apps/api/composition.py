"""Standalone composition for the market page; runtime state starts empty."""
from pathlib import Path
from custom_model.application.data_gateway import DataGateway
from custom_model.application.fundamental_service import FundamentalService
from custom_model.application.instruments import InstrumentSearchService
from custom_model.application.research import ResearchAnalysisUseCase
from custom_model.application.quote_view import QuoteViewService
from custom_model.application.quote_refresh_service import QuoteRefreshService
from custom_model.application.monitor_snapshot_service import MonitorSnapshotService
from custom_model.application.watchlist_service import WatchlistService
from custom_model.application.technical_structure_adapter import build_shared_technical_structure_service
from custom_model.infrastructure.providers.aicubes import AICubesDailyProvider, AICubesFundamentalProvider, AICubesInstrumentSearchProvider, AICubesBatchQuoteProvider, FallbackInstrumentSearchProvider, create_aicubes_client_from_env
from custom_model.infrastructure.providers.eastmoney_daily import EastmoneyDailyProvider
from custom_model.infrastructure.providers.eastmoney_intraday import EastmoneyIntradayProvider
from custom_model.infrastructure.providers.eastmoney_fundamental import EastmoneyFundamentalProvider
from custom_model.infrastructure.providers.eastmoney_quote import EastmoneyQuoteProvider
from custom_model.infrastructure.providers.cn_exchange_quote import CNExchangeOfficialQuoteProvider
from custom_model.infrastructure.providers.baostock_daily import BaostockDailyProvider
from custom_model.infrastructure.providers.cached_market_provider import CachedMarketProvider
from custom_model.infrastructure.providers.cn_trading_calendar import build_cn_trading_calendar_service
from custom_model.infrastructure.providers.legacy_search import LegacyInstrumentSearchProvider
from custom_model.infrastructure.parquet_market_store import ParquetMarketStore
from custom_model.infrastructure.watchlist_store import SQLiteWatchlistStore
from custom_model.infrastructure.monitor_snapshot_store import SQLiteMonitorSnapshotStore
from .app import create_app

ROOT = Path(__file__).resolve().parents[2]

def create_production_app():
    calendar = build_cn_trading_calendar_service(ROOT / 'calendar/cn-cash')
    cache = ParquetMarketStore(ROOT / 'runtime/market')
    client = create_aicubes_client_from_env()
    providers = [AICubesDailyProvider(client)] if client else []
    providers += [CachedMarketProvider(cache, admitted_source_prefixes=('aicubes:', 'baostock:', 'eastmoney:', 'sse:', 'szse:', 'cn-exchange-official-daily')), BaostockDailyProvider(), EastmoneyIntradayProvider(), EastmoneyDailyProvider()]
    gateway = DataGateway(providers, market_bar_store=cache, trading_calendar_service=calendar, require_trading_calendar=True)
    research = ResearchAnalysisUseCase(gateway)
    quote_providers = (CNExchangeOfficialQuoteProvider(), EastmoneyQuoteProvider())
    search = LegacyInstrumentSearchProvider()
    fundamentals = [EastmoneyFundamentalProvider()]
    refresh = None
    if client:
        search = FallbackInstrumentSearchProvider(AICubesInstrumentSearchProvider(client), search)
        fundamentals.insert(0, AICubesFundamentalProvider(client))
        store = SQLiteMonitorSnapshotStore(ROOT / 'runtime/quote-refresh.sqlite3')
        snapshots = MonitorSnapshotService(AICubesBatchQuoteProvider(client), store)
        refresh = QuoteRefreshService(snapshots, store)
    return create_app(research_use_case=research, data_gateway=gateway,
        instrument_search_service=InstrumentSearchService(search),
        technical_structure_service=build_shared_technical_structure_service(research),
        fundamental_service=FundamentalService(fundamentals),
        quote_view_service=QuoteViewService(quote_providers, calendar, require_trading_calendar=True),
        quote_refresh_service=refresh,
        watchlist_service=WatchlistService(SQLiteWatchlistStore(ROOT / 'runtime/watchlist.sqlite3')),
        providers=tuple(providers) + quote_providers)
