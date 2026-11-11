"""采集两阶段化：asset_column 增加 sync_status / detected_change（新增/缺失列可见，缺失列软保留）。

Revision ID: 0002_column_sync_status

注意：0001 用 Base.metadata.create_all 建表，会按"当前模型"建表，因此全新库跑完 0001
时这两个列/索引可能已经存在；而从旧库升级时它们尚不存在。这里统一用 inspector 做
存在性判断，保证两条路径都幂等、不报错。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_column_sync_status"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

TABLE = "asset_column"
COLUMNS = ("sync_status", "detected_change")
INDEX = "ix_asset_column_sync_status"


def _existing_columns(bind) -> set[str]:
    inspector = sa.inspect(bind)
    return {column["name"] for column in inspector.get_columns(TABLE)}


def _existing_indexes(bind) -> set[str]:
    inspector = sa.inspect(bind)
    return {index["name"] for index in inspector.get_indexes(TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _existing_columns(bind)

    if "sync_status" not in columns:
        op.add_column(
            TABLE,
            sa.Column(
                "sync_status",
                sa.String(length=12),
                nullable=False,
                server_default="active",
            ),
        )
    if "detected_change" not in columns:
        op.add_column(
            TABLE,
            sa.Column("detected_change", sa.String(length=12), nullable=True),
        )

    if INDEX not in _existing_indexes(bind):
        op.create_index(INDEX, TABLE, ["sync_status"])


def downgrade() -> None:
    bind = op.get_bind()
    if INDEX in _existing_indexes(bind):
        op.drop_index(INDEX, table_name=TABLE)
    columns = _existing_columns(bind)
    if "detected_change" in columns:
        op.drop_column(TABLE, "detected_change")
    if "sync_status" in columns:
        op.drop_column(TABLE, "sync_status")
