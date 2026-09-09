"""Bitable 空源保护（fail-closed，2026-09-09）。

源行数为 0（None/[]/生成失败/关键列全空）时禁止 delete/clear/全量覆盖，
返回 SKIPPED_EMPTY_SOURCE 且不写 SENT delivery；源有数据时维持既有增量同步。
全部走注入 Gateway，不打真实飞书。
"""

from __future__ import annotations

from pathlib import Path

from src.investment_research_supervisor.daily_brief_bitable_service import (
    MANAGED_SOURCE,
    DailyBriefBitablePublisher,
)
from tests.test_investment_research_daily_brief import AS_OF, _seed


class RecordingGateway:
    """最小飞书网关替身：记录全部调用，不触网。"""

    def __init__(self, records: list[dict] | None = None) -> None:
        self.fields: list[dict] = []
        self.records = records or []
        self.created: list[dict] = []
        self.updated: list[dict] = []
        self.deleted: list[str] = []
        self.deleted_fields: list[str] = []

    def list_fields(self) -> list[dict]:
        return list(self.fields)

    def create_text_field(self, name: str) -> None:
        self.fields.append({"field_name": name})

    def delete_field(self, field_id: str) -> None:
        self.deleted_fields.append(field_id)

    def list_records(self) -> list[dict]:
        return list(self.records)

    def batch_create(self, records: list[dict]) -> None:
        self.created.extend(records)

    def batch_update(self, records: list[dict]) -> None:
        self.updated.extend(records)

    def batch_delete(self, record_ids: list[str]) -> None:
        self.deleted.extend(record_ids)


def _managed_rows() -> list[dict]:
    return [
        {"record_id": "managed-1", "fields": {
            "同步来源": MANAGED_SOURCE, "股票代码": "999999.SZ", "研究日期": "2026-08-24"}},
        {"record_id": "managed-2", "fields": {
            "同步来源": MANAGED_SOURCE, "股票代码": "888888.SZ", "研究日期": "2026-08-24"}},
    ]


def test_empty_source_skips_all_deletes_and_overwrites(tmp_path: Path) -> None:
    service, repository = _seed(tmp_path)
    service.build(research_as_of=AS_OF)
    gateway = RecordingGateway(_managed_rows())
    publisher = DailyBriefBitablePublisher(repository=repository, gateway=gateway)

    # 用补丁把源压成空（模拟生成失败/空表）：直接断言门行为
    import unittest.mock as mock

    with mock.patch.object(DailyBriefBitablePublisher, "_source_rows", return_value={}):
        result = publisher.publish(research_as_of=AS_OF)

    assert result["status"] == "SKIPPED_EMPTY_SOURCE"
    assert "拒绝" in str(result.get("error"))
    assert gateway.deleted == []          # 0 次删除
    assert gateway.created == []          # 不整表覆盖
    assert gateway.updated == []
    # 不得把 SKIPPED 写成成功发送
    delivery = repository.delivery(
        research_as_of=AS_OF, channel="feishu_bitable", target_id=publisher.settings.table_id,
    )
    assert delivery is None or delivery.get("status") != "SENT"


def test_null_source_rows_same_gate(tmp_path: Path) -> None:
    service, repository = _seed(tmp_path)
    service.build(research_as_of=AS_OF)
    gateway = RecordingGateway(_managed_rows())
    publisher = DailyBriefBitablePublisher(repository=repository, gateway=gateway)

    import unittest.mock as mock

    with mock.patch.object(DailyBriefBitablePublisher, "_source_rows", return_value=None):
        result = publisher.publish(research_as_of=AS_OF)

    assert result["status"] == "SKIPPED_EMPTY_SOURCE"
    assert gateway.deleted == []


def test_key_columns_all_empty_counts_as_empty(tmp_path: Path) -> None:
    service, repository = _seed(tmp_path)
    service.build(research_as_of=AS_OF)
    gateway = RecordingGateway(_managed_rows())
    publisher = DailyBriefBitablePublisher(repository=repository, gateway=gateway)

    hollow = {"600456.SH": {
        "研究日期": AS_OF, "股票代码": "600456.SH", "公司": "", "行业": "",
        "估值状态": "", "现价": None, "合理价值范围": "", "相对中位值差距": None,
        "历史支撑范围": "", "研究重点": "", "关键反证": "",
        "日报版本": "daily-brief-v28", "同步来源": MANAGED_SOURCE,
    }}
    import unittest.mock as mock

    with mock.patch.object(DailyBriefBitablePublisher, "_source_rows", return_value=hollow):
        result = publisher.publish(research_as_of=AS_OF)

    assert result["status"] == "SKIPPED_EMPTY_SOURCE"  # 关键列全空 = 空源
    assert gateway.deleted == []


def test_two_source_rows_still_sync(tmp_path: Path) -> None:
    service, repository = _seed(tmp_path)
    service.build(research_as_of=AS_OF)
    gateway = RecordingGateway([
        {"record_id": "managed-stale", "fields": {
            "同步来源": MANAGED_SOURCE, "股票代码": "999999.SZ", "研究日期": "2026-08-24"}},
    ])
    publisher = DailyBriefBitablePublisher(repository=repository, gateway=gateway)

    import unittest.mock as mock

    real_rows = DailyBriefBitablePublisher._source_rows
    seeded_brief = repository.get_completed(AS_OF) or {}
    rows = DailyBriefBitablePublisher._source_rows(seeded_brief)
    assert len(rows) == 2  # _seed 世界：低估值池 2 家
    with mock.patch.object(DailyBriefBitablePublisher, "_source_rows", return_value=rows):
        result = publisher.publish(research_as_of=AS_OF)

    assert result["status"] == "READY"
    assert result["row_count"] == 2
    assert len(gateway.deleted) == 1       # 过期受管行仍按增量语义删除
    assert gateway.created                 # 2 行来源新建
    delivery = repository.delivery(
        research_as_of=AS_OF, channel="feishu_bitable", target_id=publisher.settings.table_id,
    )
    assert delivery is not None and delivery.get("status") == "SENT"
    _ = real_rows
