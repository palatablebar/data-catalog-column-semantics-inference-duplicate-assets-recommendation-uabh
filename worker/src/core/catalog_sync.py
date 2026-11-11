"""两阶段采集的列变化构建（纯函数，不依赖 DB / Celery）。

输入：上一版目录列 + 上游真实列快照，输出每张表的 added / missing / unchanged。
口径：
- added   ：上游有、目录没有（新列，需要建档并采样）
- missing ：目录有、上游没有（上游删列，目录软标记保留，不物理删除）
- unchanged：两边都有（重新采样，人工纠正/标签/证据由 ORM 层原样保留）
重复运行且上游无变化时，added/missing 均为空 —— 天然幂等。
"""
from __future__ import annotations

from typing import Any

from src.constants.enums import ColumnChangeType


def build_column_diff(catalog_names: list[str], upstream_names: list[str]) -> dict[str, list[str]]:
    """单表列名集合差异（保持各自顺序，便于报告稳定可复现）。"""
    catalog_set = set(catalog_names)
    upstream_set = set(upstream_names)
    return {
        ColumnChangeType.ADDED.value: [name for name in upstream_names if name not in catalog_set],
        ColumnChangeType.MISSING.value: [name for name in catalog_names if name not in upstream_set],
        ColumnChangeType.UNCHANGED.value: [name for name in catalog_names if name in upstream_set],
    }


def build_table_report(
    *,
    table_name: str,
    asset_key: str,
    catalog_columns: list[dict[str, Any]],
    upstream_columns: list[dict[str, Any]],
    row_count: int,
) -> dict[str, Any]:
    """单表变化报告：列级 added/missing/unchanged + 汇总计数。"""
    diff = build_column_diff(
        catalog_names=[column["column_name"] for column in catalog_columns],
        upstream_names=[column["name"] for column in upstream_columns],
    )
    upstream_by_name = {column["name"]: column for column in upstream_columns}
    return {
        "table_name": table_name,
        "asset_key": asset_key,
        "status": "readable",
        "row_count": row_count,
        "upstream_column_count": len(upstream_columns),
        "catalog_column_count": len(catalog_columns),
        ColumnChangeType.ADDED.value: [
            {
                "column_name": name,
                "data_type": upstream_by_name[name]["data_type"],
                "nullable": upstream_by_name[name]["nullable"],
                "ordinal": upstream_by_name[name]["ordinal"],
            }
            for name in diff[ColumnChangeType.ADDED.value]
        ],
        ColumnChangeType.MISSING.value: [
            {
                "column_name": name,
                "data_type": next(
                    column["data_type"]
                    for column in catalog_columns
                    if column["column_name"] == name
                ),
            }
            for name in diff[ColumnChangeType.MISSING.value]
        ],
        ColumnChangeType.UNCHANGED.value: diff[ColumnChangeType.UNCHANGED.value],
        "failure_reason": None,
    }


def summarize_reports(reports: list[dict[str, Any]]) -> dict[str, int]:
    """跨 5 张表汇总：读取成功/失败数与全局列变化计数。"""
    added = sum(len(report.get(ColumnChangeType.ADDED.value, [])) for report in reports)
    missing = sum(len(report.get(ColumnChangeType.MISSING.value, [])) for report in reports)
    unchanged = sum(len(report.get(ColumnChangeType.UNCHANGED.value, [])) for report in reports)
    unreadable = sum(1 for report in reports if report.get("status") == "unreadable")
    return {
        "tables_total": len(reports),
        "tables_readable": len(reports) - unreadable,
        "tables_unreadable": unreadable,
        "columns_added": added,
        "columns_missing": missing,
        "columns_unchanged": unchanged,
    }


def plan_apply_actions(
    reports: list[dict[str, Any]],
    catalog_state: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    """阶段 2 的纯函数规划：报告 + 当前目录列 → 每表 ORM 动作清单（不触碰 DB）。

    catalog_state: {table_name: [{"column_name": str, "sync_status": str}, ...]}
    返回每表三类动作：
      create       ：上游新增列（带 ordinal/类型/可空），需建档 + 采样
      mark_missing ：上游已删的在册列，软标记 missing（保留人工纠正/标签/证据）
      resample     ：两边都有的列，重新采样并置 active（曾缺失又回归的列在此复活）
    """
    plan: dict[str, dict[str, Any]] = {}
    for report in reports:
        if report.get("status") != "readable":
            plan[report["table_name"]] = {"create": [], "mark_missing": [], "resample": []}
            continue
        known = {column["column_name"]: column for column in catalog_state.get(report["table_name"], [])}
        plan[report["table_name"]] = {
            "create": list(report[ColumnChangeType.ADDED.value]),
            # 幂等关键：只对当前仍在册(active)的列下发 missing；上轮已标记的列不重复产生变化
            "mark_missing": [
                spec["column_name"]
                for spec in report[ColumnChangeType.MISSING.value]
                if known.get(spec["column_name"], {}).get("sync_status") != "missing"
            ],
            # 只对目录里确实存在的在册/曾缺失列下发重采样（新增列走 create，不在此列）
            "resample": [
                name
                for name in report[ColumnChangeType.UNCHANGED.value]
                if name in known
            ],
        }
    return plan

