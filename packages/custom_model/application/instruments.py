from __future__ import annotations

from custom_model.application.ports import (
    InstrumentSearchMatch,
    InstrumentSearchProvider,
)
from custom_model.domain.models import AssetType, Exchange, InstrumentId, Market


class InstrumentSearchService:
    """Application boundary for security search.

    The UI never calls Eastmoney, Sina, Yahoo or Kimi directly. Search adapters
    normalize provider-specific results into complete ``InstrumentId`` values.
    """

    def __init__(self, provider: InstrumentSearchProvider) -> None:
        self.provider = provider

    def search(self, query: str, *, limit: int = 10) -> list[InstrumentSearchMatch]:
        normalized = query.strip()
        if not normalized:
            return []
        if len(normalized) > 64:
            raise ValueError("search query exceeds 64 characters")
        bounded_limit = max(1, min(int(limit), 20))
        matches = self.provider.search(normalized, limit=bounded_limit)

        token = normalized.upper()
        ticker, dot, suffix = token.partition(".")
        if (
            not matches
            and dot
            and suffix == "TI"
            and len(ticker) == 6
            and ticker.isdigit()
        ):
            matches = [
                InstrumentSearchMatch(
                    instrument=InstrumentId(
                        symbol=token,
                        exchange=Exchange.OTHER,
                        market=Market.CN,
                        asset_type=AssetType.SECTOR_INDEX,
                        currency="CNY",
                    ),
                    display_name=token,
                    legacy_code=token,
                    source="exact-ths-sector-code",
                    match_score=100.0,
                )
            ]

        unique: list[InstrumentSearchMatch] = []
        seen: set[str] = set()
        for match in matches:
            key = match.instrument.key
            if key in seen:
                continue
            seen.add(key)
            unique.append(match)
            if len(unique) >= bounded_limit:
                break
        return unique
