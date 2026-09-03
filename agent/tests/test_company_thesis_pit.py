"""Thesis PIT semantics: valid_from/valid_to lifecycle and replay gate."""

from __future__ import annotations

from src.company_thesis.service import CompanyThesisService
from src.company_thesis.store import CompanyThesisRepository


def create(service: CompanyThesisService, **overrides):
    payload = {
        "market": "CN", "stock_code": "600001.SH", "title": "长期竞争力待验证",
        "core_thesis": "盈利质量、行业地位和现金流需要持续验证。",
        "status": "FORMING", "confidence": "LOW",
        "invalid_conditions": [{"condition": "核心产品验证持续失败", "status": "ACTIVE"}],
        "created_by": "HUMAN", "source_data_as_of": "2026-08-17",
    }
    payload.update(overrides)
    return service.create_initial_thesis(**payload)


def connect(tmp_path) -> tuple[CompanyThesisService, CompanyThesisRepository]:
    service = CompanyThesisService(db_path=tmp_path / "research.db")
    return service, CompanyThesisRepository(service.repository.db_path)


def test_creation_sets_valid_from_and_writes_lifecycle_event(tmp_path):
    service, repo = connect(tmp_path)
    try:
        created = create(service, source_data_as_of="2026-08-17")
        # valid_from is the creation day, not the older evidence date.
        assert created["valid_from"] == str(created["created_at"])[:10]
        events = repo._conn.execute(  # noqa: SLF001 - direct fixture assertion
            "SELECT change_type,version,valid_from FROM company_thesis_lifecycle_events WHERE thesis_id=?",
            (created["thesis_id"],),
        ).fetchall()
        assert [(row["change_type"], row["version"]) for row in events] == [("CREATE", 1)]
        assert events[0]["valid_from"] == created["valid_from"]
    finally:
        repo.close()
        service.close()


def test_new_version_closes_prior_valid_to(tmp_path):
    service, repo = connect(tmp_path)
    try:
        first = create(service, source_data_as_of="2026-08-17")
        second = service.create_new_version(
            market="CN", stock_code="600001.SH", title="逻辑更新",
            core_thesis="新事实强化研究逻辑。", status="STRENGTHENING", confidence="MEDIUM",
            invalid_conditions=[], change_reason="季度更新", updated_by="SYSTEM",
            source_data_as_of="2026-08-25", authority_status="HUMAN_CONFIRMED",
        )
        prior = repo.get_thesis_by_id(first["thesis_id"])
        assert prior["is_current"] is False
        assert prior["valid_to"] == second["valid_from"]
        assert second["valid_to"] is None
        events = repo._conn.execute(  # noqa: SLF001 - direct fixture assertion
            "SELECT change_type,version FROM company_thesis_lifecycle_events WHERE stock_code=? ORDER BY version",
            ("600001.SH",),
        ).fetchall()
        assert [(row["change_type"], row["version"]) for row in events] == [("CREATE", 1), ("VERSION_CREATED", 2)]
    finally:
        repo.close()
        service.close()


def test_older_evidence_date_alone_does_not_leak_conclusion_into_replay(tmp_path):
    service, repo = connect(tmp_path)
    try:
        # Evidence dated 08-17, conclusion reached (created) 08-27.
        created = create(service, source_data_as_of="2026-08-17")
        assert created["valid_from"] >= "2026-09"  # test runs on/after 2026-09-02

        # Replay on a day after the evidence but before the conclusion:
        assert repo.thesis_as_of("CN", "600001.SH", "2026-08-26") is None
        # ...and visible only from the creation day onward:
        assert repo.thesis_as_of("CN", "600001.SH", created["valid_from"])["thesis_id"] == created["thesis_id"]
    finally:
        repo.close()
        service.close()


def test_superseded_version_stops_being_selected_after_its_close_date(tmp_path):
    service, repo = connect(tmp_path)
    try:
        create(service, source_data_as_of="2026-08-17")
        second = service.create_new_version(
            market="CN", stock_code="600001.SH", title="逻辑更新",
            core_thesis="新事实强化研究逻辑。", status="STRENGTHENING", confidence="MEDIUM",
            invalid_conditions=[], change_reason="季度更新", updated_by="SYSTEM",
            source_data_as_of="2026-08-25",
        )
        selected = repo.thesis_as_of("CN", "600001.SH", second["valid_from"])
        assert selected["thesis_id"] == second["thesis_id"]
        assert selected["version"] == 2
    finally:
        repo.close()
        service.close()


def test_lifecycle_backfill_is_factual_and_idempotent(tmp_path):
    service, repo = connect(tmp_path)
    try:
        created = create(service, source_data_as_of="2026-08-17")
        # The CREATE event already occupies the version key, so a backfill
        # pass adds nothing and never duplicates existing provenance.
        first = repo.backfill_lifecycle_events()
        assert first == {"thesis_versions": 1, "events_written": 0}
        second = repo.backfill_lifecycle_events()
        assert second == {"thesis_versions": 1, "events_written": 0}
        rows = repo._conn.execute(  # noqa: SLF001 - direct fixture assertion
            "SELECT change_type FROM company_thesis_lifecycle_events WHERE thesis_id=?",
            (created["thesis_id"],),
        ).fetchall()
        assert [row["change_type"] for row in rows] == ["CREATE"]
    finally:
        repo.close()
        service.close()


def test_supporting_metrics_follow_thesis_version_not_latest_only(tmp_path):
    service, repo = connect(tmp_path)
    try:
        first = create(
            service, source_data_as_of="2026-08-17",
            supporting_conditions=[{"condition": "A1", "status": "ACTIVE"}],
            key_metrics_to_monitor=[{"text": "M1"}],
        )
        second = service.create_new_version(
            market="CN", stock_code="600001.SH", title="逻辑更新",
            core_thesis="新事实强化研究逻辑。", status="STRENGTHENING", confidence="MEDIUM",
            invalid_conditions=[{"condition": "I2", "status": "ACTIVE"}],
            supporting_conditions=[{"condition": "A2", "status": "ACTIVE"}],
            key_metrics_to_monitor=[{"text": "M2"}],
            change_reason="人工更新条件", updated_by="HUMAN",
            source_data_as_of="2026-08-25",
        )
        prior = repo.get_thesis_by_id(first["thesis_id"])
        assert prior["supporting_conditions"][0]["condition"] == "A1"
        assert "M1" in str(prior["key_metrics_to_monitor"])
        current = repo.get_thesis_by_id(second["thesis_id"])
        assert current["supporting_conditions"][0]["condition"] == "A2"
        assert "M2" in str(current["key_metrics_to_monitor"])
    finally:
        repo.close()
        service.close()
