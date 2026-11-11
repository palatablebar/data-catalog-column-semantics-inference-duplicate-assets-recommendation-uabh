"""两阶段采集纯逻辑测试（仅标准库，无 DB / 第三方依赖）。

运行（从仓库根目录，使用隔离模式避免工作目录污染）：
    python3 -I worker/tests/test_catalog_sync.py

覆盖：
1. 列差异与逐表报告（退货金额 added / 客户编号 missing 的真实漂移）
2. 阶段 2 动作规划 + 内存态替换：新增建档、缺失软保留（人工纠正不丢）、在册重采样
3. 重复运行幂等：第二次 added/missing 恒为空，变化标记被清除
4. 任一表未读取 → 整批阻断，目录一行不改（保留上一版）
5. 曾缺失列上游回归 → 复活为 active
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

# python -I 下隔离 sys.path，这里显式复刻容器 PYTHONPATH 口径：
#   backend 作为 `src` 包；worker/src 在容器里被 COPY 成 /app/worker_src。
# 本地目录名是 worker/src，用临时符号链接起 worker_src 别名，避免与 backend 的 src 撞名。
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
_ALIAS_DIR = tempfile.mkdtemp(prefix="catmap_worker_")
os.symlink(ROOT / "worker" / "src", os.path.join(_ALIAS_DIR, "worker_src"))
sys.path.insert(0, _ALIAS_DIR)

from worker_src.core import catalog_sync  # noqa: E402
from src.constants.enums import ColumnChangeType, ColumnSyncStatus  # noqa: E402

CRM = "ods_crm_customer"


def upstream_crm_columns() -> list[dict]:
    """上游当前形态：customer_id 已下线，refund_amount 退货金额已上线。"""
    names = [
        "cust_name",
        "mobile_no",
        "email_addr",
        "id_card_no",
        "city",
        "register_date",
        "member_level",
        "total_amount",
        "updated_at",
        "refund_amount",
    ]
    return [
        {"name": name, "data_type": "text", "nullable": True, "ordinal": index + 1}
        for index, name in enumerate(names)
    ]


def catalog_crm_columns() -> list[dict]:
    """上一版目录：仍含旧 customer_id，尚无 refund_amount。"""
    names = [
        "customer_id",
        "cust_name",
        "mobile_no",
        "email_addr",
        "id_card_no",
        "city",
        "register_date",
        "member_level",
        "total_amount",
        "updated_at",
    ]
    return [{"column_name": name, "data_type": "text"} for name in names]


class InMemoryCatalog:
    """用字典复刻 _apply_catalog 对 AssetColumn 的状态转移（决策仍来自纯函数规划）。"""

    def __init__(self, tables: dict[str, list[dict]]):
        # 每个列记录完整生命周期字段，模拟 ORM 行
        self.tables = {
            table: {
                spec["column_name"]: {
                    "column_name": spec["column_name"],
                    "data_type": spec.get("data_type", "text"),
                    "sync_status": ColumnSyncStatus.ACTIVE.value,
                    "detected_change": None,
                    "manual_note": "人工纠正保留" if spec["column_name"] == "customer_id" else None,
                    "sample_values": ["old"],
                }
                for spec in specs
            }
            for table, specs in tables.items()
        }

    def state(self) -> dict[str, list[dict]]:
        return {
            table: [
                {"column_name": col["column_name"], "sync_status": col["sync_status"]}
                for col in columns.values()
            ]
            for table, columns in self.tables.items()
        }

    def apply(self, reports: list[dict]) -> None:
        plan = catalog_sync.plan_apply_actions(reports, self.state())
        for report in reports:
            table = report["table_name"]
            columns = self.tables[table]
            # 与任务执行一致：每轮先清空变化标记，detected_change 只表达本轮新发现
            for column in columns.values():
                column["detected_change"] = None
            for spec in plan[table]["create"]:
                columns[spec["column_name"]] = {
                    "column_name": spec["column_name"],
                    "data_type": spec["data_type"],
                    "sync_status": ColumnSyncStatus.ACTIVE.value,
                    "detected_change": ColumnChangeType.ADDED.value,
                    "manual_note": None,
                    "sample_values": ["new"],
                }
            for name in plan[table]["mark_missing"]:
                columns[name]["sync_status"] = ColumnSyncStatus.MISSING.value
                columns[name]["detected_change"] = ColumnChangeType.MISSING.value
            for name in plan[table]["resample"]:
                columns[name]["sync_status"] = ColumnSyncStatus.ACTIVE.value
                columns[name]["sample_values"] = ["refreshed"]


class CatalogSyncTest(unittest.TestCase):
    def test_column_diff_detects_added_and_missing(self):
        diff = catalog_sync.build_column_diff(
            catalog_names=[c["column_name"] for c in catalog_crm_columns()],
            upstream_names=[c["name"] for c in upstream_crm_columns()],
        )
        self.assertEqual(diff[ColumnChangeType.ADDED.value], ["refund_amount"])
        self.assertEqual(diff[ColumnChangeType.MISSING.value], ["customer_id"])
        self.assertNotIn("customer_id", diff[ColumnChangeType.UNCHANGED.value])
        self.assertIn("mobile_no", diff[ColumnChangeType.UNCHANGED.value])

    def test_table_report_structure(self):
        report = catalog_sync.build_table_report(
            table_name=CRM,
            asset_key=f"ods.{CRM}",
            catalog_columns=catalog_crm_columns(),
            upstream_columns=upstream_crm_columns(),
            row_count=300,
        )
        self.assertEqual(report["status"], "readable")
        self.assertEqual(len(report["added"]), 1)
        self.assertEqual(report["added"][0]["column_name"], "refund_amount")
        self.assertEqual(report["missing"][0]["column_name"], "customer_id")
        self.assertEqual(report["upstream_column_count"], 10)
        self.assertEqual(report["catalog_column_count"], 10)
        self.assertIsNone(report["failure_reason"])

    def test_two_phase_apply_soft_keeps_missing_and_preserves_manual(self):
        catalog = InMemoryCatalog({CRM: catalog_crm_columns()})
        report = catalog_sync.build_table_report(
            table_name=CRM,
            asset_key=f"ods.{CRM}",
            catalog_columns=catalog_crm_columns(),
            upstream_columns=upstream_crm_columns(),
            row_count=300,
        )
        catalog.apply([report])
        columns = catalog.tables[CRM]

        # 新增列进入目录
        self.assertIn("refund_amount", columns)
        self.assertEqual(columns["refund_amount"]["sync_status"], ColumnSyncStatus.ACTIVE.value)
        self.assertEqual(
            columns["refund_amount"]["detected_change"], ColumnChangeType.ADDED.value
        )
        # 缺失列物理保留 + 软标记，人工纠正结果不丢
        self.assertIn("customer_id", columns)
        self.assertEqual(
            columns["customer_id"]["sync_status"], ColumnSyncStatus.MISSING.value
        )
        self.assertEqual(
            columns["customer_id"]["detected_change"], ColumnChangeType.MISSING.value
        )
        self.assertEqual(columns["customer_id"]["manual_note"], "人工纠正保留")

    def test_rerun_is_idempotent_and_clears_change_markers(self):
        catalog = InMemoryCatalog({CRM: catalog_crm_columns()})

        def snapshot() -> list[dict]:
            return [
                catalog_sync.build_table_report(
                    table_name=CRM,
                    asset_key=f"ods.{CRM}",
                    catalog_columns=[
                        {"column_name": col["column_name"], "data_type": col["data_type"]}
                        for col in catalog.tables[CRM].values()
                    ],
                    upstream_columns=upstream_crm_columns(),
                    row_count=300,
                )
            ]

        first = snapshot()
        summary_first = catalog_sync.summarize_reports(first)
        self.assertEqual(summary_first["columns_added"], 1)
        self.assertEqual(summary_first["columns_missing"], 1)
        catalog.apply(first)

        # 第二次采集：目录已与上游对齐。原始 diff 仍能列出"目录有/上游无"的列，
        # 但动作规划不应再产生任何状态转移 —— 这才是任务执行层的幂等口径。
        second = snapshot()
        plan = catalog_sync.plan_apply_actions(second, catalog.state())
        self.assertEqual(plan[CRM]["create"], [])
        self.assertEqual(plan[CRM]["mark_missing"], [])
        catalog.apply(second)

        # 变化标记被清除：新增列不再挂 added，整表无残留变化
        for column in catalog.tables[CRM].values():
            self.assertIsNone(column["detected_change"])
        # 客户编号仍保持 missing（上游确实没有它），但不再带 missing 变化标记
        self.assertEqual(
            catalog.tables[CRM]["customer_id"]["sync_status"],
            ColumnSyncStatus.MISSING.value,
        )

    def test_unreadable_table_aborts_whole_batch_and_keeps_previous(self):
        catalog = InMemoryCatalog({CRM: catalog_crm_columns()})
        before = {name: dict(col) for name, col in catalog.tables[CRM].items()}
        failed_report = {
            "table_name": CRM,
            "asset_key": f"ods.{CRM}",
            "status": "unreadable",
            "row_count": None,
            "upstream_column_count": None,
            "catalog_column_count": 10,
            ColumnChangeType.ADDED.value: [],
            ColumnChangeType.MISSING.value: [],
            ColumnChangeType.UNCHANGED.value: [],
            "failure_reason": "表读取失败：relation does not exist",
        }
        summary = catalog_sync.summarize_reports([failed_report])
        self.assertEqual(summary["tables_unreadable"], 1)
        self.assertEqual(summary["tables_readable"], 0)

        # 任务侧门禁：存在未读取表就不进入阶段 2（这里断言规划为空动作，且目录未被修改）
        plan = catalog_sync.plan_apply_actions([failed_report], catalog.state())
        self.assertEqual(plan[CRM], {"create": [], "mark_missing": [], "resample": []})
        self.assertEqual(
            {name: col["sync_status"] for name, col in catalog.tables[CRM].items()},
            {name: col["sync_status"] for name, col in before.items()},
        )

    def test_missing_column_resurrects_when_upstream_readds_it(self):
        catalog = InMemoryCatalog({CRM: catalog_crm_columns()})
        # 第一次：customer_id 在上游消失 → missing
        report = catalog_sync.build_table_report(
            table_name=CRM,
            asset_key=f"ods.{CRM}",
            catalog_columns=catalog_crm_columns(),
            upstream_columns=upstream_crm_columns(),
            row_count=300,
        )
        catalog.apply([report])
        self.assertEqual(
            catalog.tables[CRM]["customer_id"]["sync_status"],
            ColumnSyncStatus.MISSING.value,
        )

        # 上游重新加回 customer_id（列名回归）
        restored_upstream = [
            {"name": "customer_id", "data_type": "text", "nullable": True, "ordinal": 1},
            *upstream_crm_columns(),
        ]
        report2 = catalog_sync.build_table_report(
            table_name=CRM,
            asset_key=f"ods.{CRM}",
            catalog_columns=[
                {"column_name": col["column_name"], "data_type": col["data_type"]}
                for col in catalog.tables[CRM].values()
            ],
            upstream_columns=restored_upstream,
            row_count=300,
        )
        # 此时 customer_id 在目录中存在（曾缺失），上游也有 → 归为 unchanged 并复活
        self.assertIn("customer_id", report2[ColumnChangeType.UNCHANGED.value])
        catalog.apply([report2])
        self.assertEqual(
            catalog.tables[CRM]["customer_id"]["sync_status"],
            ColumnSyncStatus.ACTIVE.value,
        )
        self.assertIsNone(catalog.tables[CRM]["customer_id"]["detected_change"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
