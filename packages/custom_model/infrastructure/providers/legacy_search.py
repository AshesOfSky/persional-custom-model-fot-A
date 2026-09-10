from __future__ import annotations

from custom_model.application.ports import InstrumentSearchMatch
from custom_model.domain.models import Exchange, Market, instrument_from_legacy


class LegacyInstrumentSearchProvider:
    """Adapter around the legacy multi-source security search engine."""

    name = "legacy-stock-search"

    def __init__(self, engine: object | None = None) -> None:
        if engine is None:
            from modules.stock_search import StockSearchEngine

            engine = StockSearchEngine()
        self._engine = engine

    def search(self, query: str, *, limit: int = 10) -> list[InstrumentSearchMatch]:
        # Ask for extra candidates because low-quality API matches are filtered
        # before they cross the application boundary.
        raw_matches = self._engine.search(query, limit=max(limit * 2, 10))
        ranked_matches: list[tuple[int, InstrumentSearchMatch]] = []
        for raw in raw_matches:
            score = float(getattr(raw, "score", 0.0) or 0.0)
            if score < 35:
                continue
            full_code = str(
                getattr(raw, "full_code", "") or getattr(raw, "code", "")
            ).strip()
            legacy_code = str(getattr(raw, "code", "") or full_code).strip()
            display_name = str(getattr(raw, "name", "") or legacy_code).strip()
            market_label = str(getattr(raw, "market", "") or "").strip()
            if not full_code or not display_name:
                continue
            # A numeric symbol labelled as a US equity is a known malformed
            # legacy-source shape (often a misclassified HK result). Reject it
            # instead of silently assigning a false market identity.
            if "美股" in market_label and legacy_code.isdigit():
                continue
            instrument = instrument_from_legacy(
                full_code,
                asset_label=f"{display_name} {market_label}",
            )
            explicitly_qualified = int("." in full_code)
            if instrument.market is Market.CN:
                suffix = {
                    Exchange.SSE: ".SS",
                    Exchange.SZSE: ".SZ",
                    Exchange.BSE: ".BJ",
                }.get(instrument.exchange)
                base_symbol = instrument.symbol.split(".", 1)[0]
                if suffix and base_symbol.isdigit():
                    instrument = instrument.model_copy(
                        update={"symbol": f"{base_symbol}{suffix}"}
                    )
            ranked_matches.append(
                (
                    explicitly_qualified,
                    InstrumentSearchMatch(
                        instrument=instrument,
                        display_name=display_name,
                        legacy_code=legacy_code,
                        source=self.name,
                        match_score=max(0.0, min(100.0, score)),
                    ),
                )
            )
        ranked_matches.sort(
            key=lambda item: (
                -item[1].match_score,
                -item[0],
                item[1].instrument.key,
                item[1].display_name,
            )
        )
        unique: list[InstrumentSearchMatch] = []
        seen: set[str] = set()
        for _qualified, match in ranked_matches:
            if match.instrument.key in seen:
                continue
            seen.add(match.instrument.key)
            unique.append(match)
            if len(unique) >= limit:
                break
        return unique
