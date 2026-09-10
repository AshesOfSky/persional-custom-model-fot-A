from __future__ import annotations

from custom_model.application.technical_structure_service import TechnicalStructureMapper


def test_validated_structure_remains_locked_out_of_scores_and_alerts() -> None:
    warnings: list[str] = []
    item = TechnicalStructureMapper._item(
        kind="impulse",
        source="apps.web.server.elliott_local",
        method_version="elliott-reliability-1",
        raw={"confirmed": True, "status": "confirmed", "validated": True},
        warnings=warnings,
        path="elliott.reliability.confirmedStructures[0]",
        admit_validation=True,
        structure_id="elliott-impulse-fixture",
        validation_basis="golden_and_strict_rules",
        confirmed_time="2026-01-07",
    )

    assert item.validated is True
    assert item.score_eligible is False
    assert item.alert_eligible is False
    assert warnings == []


def test_unconfirmed_validation_claim_is_fail_closed() -> None:
    warnings: list[str] = []
    item = TechnicalStructureMapper._item(
        kind="current_count",
        source="apps.web.server.elliott_local",
        method_version="elliott-reliability-1",
        raw={"confirmed": False, "status": "provisional", "validated": True},
        warnings=warnings,
        path="elliott.reliability.provisionalStructures[0]",
        admit_validation=True,
        structure_id="elliott-provisional-fixture",
        validation_basis="current_count",
        confirmed_time=None,
    )

    assert item.validated is False
    assert item.score_eligible is False
    assert item.alert_eligible is False
    assert warnings == [
        "elliott.reliability.provisionalStructures[0] validation claim was not admitted"
    ]
