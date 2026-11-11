"""两阶段采集任务：读 5 张样例表 → 先形成列变化报告，整批可读后才在单事务内替换目录。

阶段 1 快照（不碰目录）：
  对每张样例表取行数 + information_schema 列结构，与上一版目录列做集合差异，
  形成 added / missing / unchanged 与每表失败原因；任一表读不到 → 任务 failed，
  报告照常落 job.stats，目录一行不改（事务回滚，保留上一版）。

阶段 2 替换（单事务，全成功才提交）：
  added     ：新建 AssetColumn 并采样（进入检索目录）
  missing   ：软标记 sync_status=missing，物理保留（人工纠正 / 标签 / 证据不丢）
  unchanged ：重新采样，推断结果 / 人工纠正原样保留
重复运行且上游无变化时 added/missing 恒为空 —— 不新增变化（幂等）。
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from src.config.database import session_scope
from src.constants import error_codes as codes
from src.constants.enums import ColumnChangeType, ColumnSyncStatus, IngestJobStatus
from src.models.asset import Asset
from src.models.asset_column import AssetColumn
from src.models.ingest_job import IngestJob
from src.utils.logger import get_logger
from worker_src.config.celery_app import celery_app
from worker_src.core import catalog_sync, sampler
from worker_src.config.settings import settings

logger = get_logger("catmap.worker.ingest")

PROFILE_MODE = "sample_profile_v2"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _failure_reason(exc: Exception) -> str:
    """把底层 DB 异常收敛成可读的中文原因（截断入库）。"""
    message = str(exc).strip() or exc.__class__.__name__
    if isinstance(exc, SQLAlchemyError):
        orig = getattr(exc, "orig", None)
        if orig is not None and str(orig).strip():
            message = str(orig).strip()
    return f"表读取失败：{message}"[:255]


@celery_app.task(bind=True, name="catmap.run_ingest", max_retries=0)
def run_ingest(self, job_id: str) -> dict:  # noqa: ARG001
    started = time.perf_counter()
    with session_scope() as db:
        job = db.get(IngestJob, uuid.UUID(str(job_id)))
        if job is None:
            logger.warning("ingest job not found job=%s", job_id)
            return {"ok": False, "reason": "job_not_found"}
        if job.status in (IngestJobStatus.COMPLETED.value, IngestJobStatus.RUNNING.value):
            return {"ok": False, "reason": f"job_status={job.status}"}

        job.status = IngestJobStatus.RUNNING.value
        job.started_at = _utcnow()
        job.error_code = None
        job.error_message = None
        db.flush()

        # ------------------------------------------------ 阶段 1：快照 + 变化 ----
        try:
            snapshot = _snapshot_tables(db)
        except Exception as exc:  # noqa: BLE001 - 快照自身异常，目录未动
            logger.error("ingest snapshot crashed job=%s error=%s", job_id, exc, exc_info=True)
            job.status = IngestJobStatus.FAILED.value
            job.error_code = codes.INTERNAL_ERROR
            job.error_message = f"快照阶段异常：{exc}"[:255]
            job.finished_at = _utcnow()
            db.flush()
            return {"ok": False, "reason": "snapshot_failed", "error": str(exc)}

        reports = snapshot["reports"]
        summary = catalog_sync.summarize_reports(reports)
        unreadable = [report for report in reports if report["status"] == "unreadable"]
        if unreadable:
            # 整批不成功：只落报告，不替换目录，保留上一版
            reason = "；".join(
                f"{report['table_name']}：{report['failure_reason']}" for report in unreadable
            )[:255]
            logger.error(
                "ingest aborted job=%s unreadable=%s reason=%s", job_id, len(unreadable), reason
            )
            job.stats = {
                "mode": PROFILE_MODE,
                "changes_applied": False,
                "table_reports": reports,
                **summary,
            }
            job.status = IngestJobStatus.FAILED.value
            job.error_code = codes.INGEST_TABLE_UNREADABLE
            job.error_message = reason
            job.finished_at = _utcnow()
            db.flush()
            return {"ok": False, "reason": "table_unreadable", "unreadable": len(unreadable)}

        # ------------------------------------------- 阶段 2：单事务替换目录 ----
        # 用 SAVEPOINT 隔离目录写入：阶段 2 失败只回滚目录（保留上一版），
        # 外层事务仍可把任务状态持久化为 failed，不会卡在 running。
        try:
            with db.begin_nested():
                applied = _apply_catalog(db, reports)
        except Exception as exc:  # noqa: BLE001
            logger.error("ingest apply rolled back job=%s error=%s", job_id, exc, exc_info=True)
            job.stats = {
                "mode": PROFILE_MODE,
                "changes_applied": False,
                "table_reports": reports,
                **summary,
            }
            job.status = IngestJobStatus.FAILED.value
            job.error_code = codes.INTERNAL_ERROR
            job.error_message = f"目录替换已回滚：{exc}"[:255]
            job.finished_at = _utcnow()
            db.flush()
            return {"ok": False, "reason": "apply_rolled_back", "error": str(exc)}

        duration_ms = int((time.perf_counter() - started) * 1000)
        job.status = IngestJobStatus.COMPLETED.value
        job.error_code = None
        job.error_message = None
        job.finished_at = _utcnow()
        job.stats = {
            "mode": PROFILE_MODE,
            "changes_applied": applied["has_changes"],
            "duration_ms": duration_ms,
            "table_reports": reports,
            "tables_synced": summary["tables_readable"],
            "rows_scanned": applied["rows_scanned"],
            "columns_profiled": applied["columns_profiled"],
            **summary,
        }
        db.flush()
        logger.info("ingest completed job=%s summary=%s", job_id, summary)
        return {"ok": True, "job_id": str(job.id), "stats": job.stats}


def _snapshot_tables(db) -> dict:
    """阶段 1：只读快照 5 张样例表，形成逐表变化报告（不写任何目录行）。"""
    schema = settings.sample_schema
    assets = db.scalars(
        select(Asset).where(Asset.source_kind == "sample_table").order_by(Asset.table_name)
    ).all()
    connection = db.connection()
    reports: list[dict] = []
    rows_scanned = 0

    for asset in assets:
        table = asset.table_name
        catalog_columns = [
            {"column_name": column.column_name, "data_type": column.data_type}
            for column in db.scalars(
                select(AssetColumn)
                .where(AssetColumn.asset_id == asset.id)
                .order_by(AssetColumn.ordinal)
            ).all()
        ]
        try:
            row_count = sampler.table_row_count(connection, schema, table)
            upstream_columns = sampler.table_columns(connection, schema, table)
        except Exception as exc:  # noqa: BLE001 - 单表读不到：记录原因，留给整批判定
            logger.warning("sample table unreadable table=%s error=%s", table, exc)
            reports.append(
                {
                    "table_name": table,
                    "asset_key": asset.asset_key,
                    "status": "unreadable",
                    "row_count": None,
                    "upstream_column_count": None,
                    "catalog_column_count": len(catalog_columns),
                    ColumnChangeType.ADDED.value: [],
                    ColumnChangeType.MISSING.value: [],
                    ColumnChangeType.UNCHANGED.value: [],
                    "failure_reason": _failure_reason(exc),
                }
            )
            continue

        if not upstream_columns:
            reports.append(
                {
                    "table_name": table,
                    "asset_key": asset.asset_key,
                    "status": "unreadable",
                    "row_count": row_count,
                    "upstream_column_count": 0,
                    "catalog_column_count": len(catalog_columns),
                    ColumnChangeType.ADDED.value: [],
                    ColumnChangeType.MISSING.value: [],
                    ColumnChangeType.UNCHANGED.value: [],
                    "failure_reason": "表读取失败：information_schema 未返回任何列，疑似空表或权限不足",
                }
            )
            continue

        rows_scanned += row_count
        reports.append(
            catalog_sync.build_table_report(
                table_name=table,
                asset_key=asset.asset_key,
                catalog_columns=catalog_columns,
                upstream_columns=upstream_columns,
                row_count=row_count,
            )
        )

    summary = catalog_sync.summarize_reports(reports)
    return {"reports": reports, "summary": summary, "rows_scanned": rows_scanned}


def _apply_catalog(db, reports: list[dict]) -> dict:
    """阶段 2：按纯函数规划在单事务内执行目录替换；全部成功后由 session 统一提交。"""
    schema = settings.sample_schema
    connection = db.connection()
    rows_scanned = 0
    columns_profiled = 0
    has_changes = False

    assets = {
        asset.table_name: asset
        for asset in db.scalars(select(Asset).where(Asset.source_kind == "sample_table")).all()
    }
    columns_by_table: dict[str, list[AssetColumn]] = {}
    for asset in assets.values():
        columns_by_table[asset.table_name] = db.scalars(
            select(AssetColumn).where(AssetColumn.asset_id == asset.id)
        ).all()
    catalog_state = {
        table: [
            {"column_name": column.column_name, "sync_status": column.sync_status}
            for column in columns
        ]
        for table, columns in columns_by_table.items()
    }
    plan = catalog_sync.plan_apply_actions(reports, catalog_state)

    for report in reports:
        table = report["table_name"]
        asset = assets[table]
        table_columns = columns_by_table[table]
        existing = {column.column_name: column for column in table_columns}
        actions = plan[table]
        now = _utcnow()

        # 每轮先清空变化标记：detected_change 只表达"本次采集新发现的变化"，
        # 持久状态由 sync_status 承载。重复运行无新变化时，标记自然全空（幂等）。
        for column in table_columns:
            column.detected_change = None

        # 新增列：建档 + 立即采样，进入检索目录
        for spec in actions["create"]:
            name = spec["column_name"]
            profile = sampler.profile_column(connection, schema, table, name)
            db.add(
                AssetColumn(
                    asset_id=asset.id,
                    ordinal=spec["ordinal"],
                    column_name=name,
                    data_type=spec["data_type"],
                    nullable=spec["nullable"],
                    comment="",
                    sample_values=profile["sample_values"],
                    stats=profile["stats"],
                    sync_status=ColumnSyncStatus.ACTIVE.value,
                    detected_change=ColumnChangeType.ADDED.value,
                    profiled_at=now,
                )
            )
            columns_profiled += 1
            has_changes = True

        # 缺失列：软标记保留，不动推断 / 人工纠正 / 标签 / 证据
        for name in actions["mark_missing"]:
            column = existing[name]
            column.sync_status = ColumnSyncStatus.MISSING.value
            column.detected_change = ColumnChangeType.MISSING.value
            has_changes = True

        # 在册列（含曾缺失又回归）：重新采样、置 active
        for name in actions["resample"]:
            column = existing[name]
            profile = sampler.profile_column(connection, schema, table, name)
            stats = dict(column.stats or {})
            stats.update(profile["stats"])
            column.stats = stats
            column.sample_values = profile["sample_values"]
            column.profiled_at = now
            column.sync_status = ColumnSyncStatus.ACTIVE.value
            columns_profiled += 1

        # 列总数直接数实际行（含历史遗留的缺失列），避免幂等重跑时漏算
        total_column_rows = len(table_columns) + len(actions["create"])
        asset.row_count = report["row_count"]
        asset.column_count = total_column_rows
        asset.updated_at = now
        rows_scanned += report["row_count"]

    db.flush()
    return {"rows_scanned": rows_scanned, "columns_profiled": columns_profiled, "has_changes": has_changes}
