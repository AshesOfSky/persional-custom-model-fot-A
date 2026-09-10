from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
import hashlib
import json
import math
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from custom_model.application.ports import FundamentalDataProvider
from custom_model.domain.models import (
    AssetType,
    DataMode,
    DataPurpose,
    FundamentalCoverage,
    FundamentalEnvelope,
    FundamentalEvidenceBasis,
    FundamentalField,
    FundamentalFieldStatus,
    FundamentalQuery,
    FundamentalSnapshot,
    QualityStatus,
)


class FundamentalDataUnavailableError(RuntimeError):
    """No provider produced admissible evidence for the requested workflow."""

    def __init__(self, query: FundamentalQuery, attempts: Sequence[str]) -> None:
        self.query = query
        self.attempts = tuple(attempts)
        detail = "; ".join(self.attempts) if self.attempts else "no provider supports query"
        super().__init__(
            f"fundamental data unavailable for {query.instrument.key}: {detail}"
        )


class FundamentalNotApplicableError(ValueError):
    """The instrument has no issuer-level fundamental contract."""


class FundamentalService:
    """Admit provider evidence and create a filter-only fundamental snapshot."""

    engine_version = "fundamental-filter-1.0.0"
    _FORMAL_MODES = frozenset({DataMode.LIVE, DataMode.DELAYED, DataMode.CACHE})

    def __init__(self, providers: Sequence[FundamentalDataProvider]) -> None:
        self._providers = tuple(providers)

    def analyze(self, query: FundamentalQuery) -> FundamentalSnapshot:
        if query.instrument.asset_type is not AssetType.STOCK:
            raise FundamentalNotApplicableError(
                f"issuer fundamentals do not apply to {query.instrument.asset_type.value}"
            )
        attempts: list[str] = []
        supported: list[FundamentalDataProvider] = []
        for provider in self._providers:
            provider_name = self._provider_name(provider)
            try:
                if provider.supports(query):
                    supported.append(provider)
            except Exception as exc:
                attempts.append(f"{provider_name}: supports {type(exc).__name__}")
        if not supported:
            raise FundamentalDataUnavailableError(query, attempts)

        formal = query.purpose is not DataPurpose.DEMO
        partial_field_workflow = query.purpose is DataPurpose.RESEARCH
        for provider in supported:
            provider_name = self._provider_name(provider)
            if formal and provider.production_ready is not True:
                attempts.append(f"{provider_name}: not production ready")
                continue
            try:
                envelope = provider.fetch_fundamentals(query)
            except Exception as exc:  # provider boundary: never expose payloads or secrets
                attempts.append(f"{provider_name}: {self._failure_label(exc)}")
                continue

            if not isinstance(envelope, FundamentalEnvelope):
                attempts.append(f"{provider_name}: invalid envelope type")
                continue

            try:
                coverage = self._coverage(query, envelope)
                violations = self._violations(
                    query,
                    envelope,
                    coverage=coverage,
                    formal=formal,
                )
                if partial_field_workflow:
                    violations = [
                        issue
                        for issue in violations
                        if issue != "insufficient verified financial coverage"
                        and not issue.startswith("missing verified required fields:")
                    ]
            except Exception as exc:
                attempts.append(
                    f"{provider_name}: invalid envelope {type(exc).__name__}"
                )
                continue
            if violations:
                attempts.append(f"{provider_name}: {', '.join(violations)}")
                continue

            if attempts:
                envelope = envelope.model_copy(
                    update={"warnings": [*envelope.warnings, *attempts]}
                )
            return FundamentalSnapshot(
                snapshot_id=self._snapshot_id(envelope),
                instrument=query.instrument,
                purpose=query.purpose,
                as_of=query.as_of,
                engine_version=self.engine_version,
                data=envelope,
                coverage=coverage,
                formal_use_eligible=formal and coverage.passed,
            )

        raise FundamentalDataUnavailableError(query, attempts)

    @staticmethod
    def verified_fields(
        snapshot: FundamentalSnapshot,
        field_names: Sequence[str],
    ) -> dict[str, FundamentalField]:
        """Return finite, provider-verified fields requested by a consumer."""

        requested = set(field_names)
        return {
            field.name: field
            for field in snapshot.data.fields
            if field.name in requested
            and field.status is FundamentalFieldStatus.VERIFIED
            and isinstance(field.normalized_value, (int, float))
            and not isinstance(field.normalized_value, bool)
            and math.isfinite(float(field.normalized_value))
        }

    @classmethod
    def _violations(
        cls,
        query: FundamentalQuery,
        envelope: FundamentalEnvelope,
        *,
        coverage: FundamentalCoverage,
        formal: bool,
    ) -> list[str]:
        issues: list[str] = []
        if envelope.instrument != query.instrument:
            issues.append("instrument mismatch")
        if envelope.purpose is not query.purpose:
            issues.append("purpose mismatch")
        if envelope.as_of != query.as_of:
            issues.append("as_of mismatch")
        if not isinstance(envelope.currency, str) or envelope.currency != query.instrument.currency:
            issues.append("currency mismatch")
        if cls._unknown(envelope.provider):
            issues.append("unknown provider")
        if not envelope.source_chain or any(
            cls._unknown(source) for source in envelope.source_chain
        ):
            issues.append("unknown source")
        if not envelope.fields:
            issues.append("empty fundamental fields")
        else:
            names = [getattr(field, "name", None) for field in envelope.fields]
            if len(names) != len(set(names)):
                issues.append("duplicate fundamental fields")
            for field in envelope.fields:
                if not isinstance(field, FundamentalField):
                    issues.append("invalid fundamental field type")
                else:
                    issues.extend(cls._field_violations(query, field))
            if not any(
                isinstance(field, FundamentalField)
                and
                field.name not in {"name", "currency", "sector", "industry"}
                and isinstance(field.normalized_value, (int, float))
                and not isinstance(field.normalized_value, bool)
                and math.isfinite(float(field.normalized_value))
                for field in envelope.fields
            ):
                issues.append("no analytical fundamental fields")

        for field_name in ("as_of", "fetched_at", "data_time"):
            value = getattr(envelope, field_name, None)
            if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
                issues.append(f"{field_name} must be timezone-aware")

        data_time = envelope.data_time
        if (
            isinstance(data_time, datetime)
            and data_time.tzinfo is not None
            and data_time.utcoffset() is not None
        ):
            if data_time > query.as_of:
                issues.append("future fundamental data")
            else:
                actual_freshness = (query.as_of - data_time).total_seconds()
                if not math.isfinite(actual_freshness):
                    issues.append("non-finite freshness")
                else:
                    try:
                        reported_freshness = float(envelope.freshness_seconds)
                    except (TypeError, ValueError, OverflowError):
                        reported_freshness = math.nan
                    if not math.isfinite(reported_freshness) or abs(
                        reported_freshness - actual_freshness
                    ) > 1.0:
                        issues.append("freshness mismatch")

        try:
            if not isinstance(envelope.timezone, str) or not envelope.timezone.strip():
                raise ValueError("invalid timezone")
            ZoneInfo(envelope.timezone)
        except (TypeError, ValueError, ZoneInfoNotFoundError):
            issues.append("invalid timezone")

        if formal:
            if not isinstance(envelope.mode, DataMode):
                issues.append("invalid data mode")
            elif envelope.mode not in cls._FORMAL_MODES:
                issues.append(f"mode {envelope.mode.value} forbidden")
            if envelope.quality is not QualityStatus.VALID:
                quality = getattr(envelope.quality, "value", "invalid")
                issues.append(f"quality {quality}")
            if envelope.is_synthetic is not False:
                issues.append("synthetic fundamentals forbidden")
            if coverage.missing_required_fields:
                issues.append(
                    "missing verified required fields: "
                    + ", ".join(coverage.missing_required_fields)
                )
            if (
                len(coverage.verified_financial_fields)
                < coverage.minimum_verified_financial_fields
            ):
                issues.append("insufficient verified financial coverage")
        return list(dict.fromkeys(issues))

    @staticmethod
    def _coverage(
        query: FundamentalQuery,
        envelope: FundamentalEnvelope,
    ) -> FundamentalCoverage:
        financial_bases = {
            FundamentalEvidenceBasis.FINANCIAL_STATEMENT,
            FundamentalEvidenceBasis.DERIVED_FINANCIAL_STATEMENT,
        }
        verified = sorted(
            {
                field.name
                for field in envelope.fields
                if isinstance(field, FundamentalField)
                and field.basis in financial_bases
                and field.status is FundamentalFieldStatus.VERIFIED
                and field.period_end is not None
                and field.published_at is not None
            }
        )
        required = list(query.required_filter_fields)
        missing = [field for field in required if field not in set(verified)]
        passed = (
            not missing
            and len(verified) >= query.minimum_verified_financial_fields
        )
        return FundamentalCoverage(
            required_fields=required,
            verified_financial_fields=verified,
            missing_required_fields=missing,
            minimum_verified_financial_fields=(
                query.minimum_verified_financial_fields
            ),
            passed=passed,
        )

    @staticmethod
    def _provider_name(provider: object) -> str:
        name = getattr(provider, "name", None)
        if isinstance(name, str) and name.strip():
            return name.strip()
        return type(provider).__name__

    @staticmethod
    def _failure_label(exc: Exception) -> str:
        kind = getattr(exc, "kind", None)
        value = getattr(kind, "value", None)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return type(exc).__name__

    @staticmethod
    def _unknown(value: Any) -> bool:
        return not isinstance(value, str) or value.strip().lower() in {
            "",
            "unknown",
            "none",
            "n/a",
        }

    @classmethod
    def _field_violations(
        cls,
        query: FundamentalQuery,
        field: FundamentalField,
    ) -> list[str]:
        issues: list[str] = []
        if cls._unknown(field.name) or cls._unknown(field.source_field):
            issues.append("unknown fundamental field source")
        if not cls._valid_evidence(field.raw_value):
            issues.append(f"invalid/non-finite raw value: {field.name}")
        if not cls._valid_scalar(field.normalized_value):
            issues.append(f"invalid/non-finite normalized value: {field.name}")
        if not isinstance(field.basis, FundamentalEvidenceBasis):
            issues.append(f"invalid evidence basis: {field.name}")
            return issues
        if not isinstance(field.status, FundamentalFieldStatus):
            issues.append(f"invalid field status: {field.name}")
            return issues

        evidence_times = (field.period_end, field.published_at, field.market_time)
        if any(
            value is not None
            and (
                not isinstance(value, datetime)
                or value.tzinfo is None
                or value.utcoffset() is None
            )
            for value in evidence_times
        ):
            issues.append(f"timezone-naive field evidence: {field.name}")
            return issues

        if field.status is FundamentalFieldStatus.VERIFIED:
            if field.basis is FundamentalEvidenceBasis.MARKET_SNAPSHOT:
                if field.market_time is None:
                    issues.append(f"verified market field missing market_time: {field.name}")
                else:
                    age = (query.as_of - field.market_time).total_seconds()
                    if age < 0:
                        issues.append(f"future market field: {field.name}")
                    elif age > query.max_market_age_seconds:
                        issues.append(f"stale market field: {field.name}")
            elif field.basis in {
                FundamentalEvidenceBasis.FINANCIAL_STATEMENT,
                FundamentalEvidenceBasis.DERIVED_FINANCIAL_STATEMENT,
            }:
                if field.period_end is None or field.published_at is None:
                    issues.append(
                        f"verified financial field missing publication evidence: {field.name}"
                    )
                else:
                    age = (query.as_of - field.published_at).total_seconds()
                    if age < 0:
                        issues.append(f"future financial field: {field.name}")
                    elif age > query.max_financial_age_seconds:
                        issues.append(f"stale financial field: {field.name}")
        return issues

    @staticmethod
    def _valid_scalar(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, bool):
            return True
        if isinstance(value, (int, float)):
            return math.isfinite(float(value))
        return isinstance(value, str) and bool(value.strip())

    @classmethod
    def _valid_evidence(cls, value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, bool):
            return True
        if isinstance(value, (int, float)):
            return math.isfinite(float(value))
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, Mapping):
            return bool(value) and all(
                isinstance(key, str)
                and bool(key.strip())
                and cls._valid_evidence(item)
                for key, item in value.items()
            )
        if isinstance(value, (list, tuple)):
            return bool(value) and all(cls._valid_evidence(item) for item in value)
        return False

    @classmethod
    def _snapshot_id(cls, envelope: FundamentalEnvelope) -> str:
        stable = envelope.model_dump(
            mode="json",
            exclude={"request_id", "fetched_at", "warnings"},
        )
        payload = json.dumps(
            {"engine_version": cls.engine_version, "data": stable},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(payload).hexdigest()}"
