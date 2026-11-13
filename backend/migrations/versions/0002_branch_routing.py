"""branch routing: step branch_meta, run_step path/reconcile columns, run branch_plan

Revision ID: 0002_branch_routing
Revises: 0001_initial
Create Date: 2026-11-13

条件分支（有/无审计报告两条路径）需要的落库字段：
- steps.branch_meta：分支组/适用条件/汇合点/默认路径；
- run_steps.path_role/branch_id/path_id/reconcile_bucket/skip_reason：分支留痕与对账；
- runs.branch_plan/preflight_blockers：本次运行的分支规划快照与发起前拦截清单。
列均为可空/带默认值的增量加列，使用 IF NOT EXISTS 保持对演示库幂等。
"""
import sqlalchemy as sa
from alembic import op

revision = "0002_branch_routing"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def _has_column(inspector, table: str, column: str) -> bool:
    return column in {item["name"] for item in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "steps" in inspector.get_table_names() and not _has_column(inspector, "steps", "branch_meta"):
        op.add_column("steps", sa.Column("branch_meta", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    if "run_steps" in inspector.get_table_names():
        alters = [
            ("path_role", sa.String(20), "'main'"),
            ("branch_id", sa.String(64), "''"),
            ("path_id", sa.String(64), "''"),
            ("reconcile_bucket", sa.String(20), "'executed'"),
            ("skip_reason", sa.String(255), "''"),
        ]
        for name, column_type, default_text in alters:
            if not _has_column(inspector, "run_steps", name):
                op.add_column("run_steps",
                              sa.Column(name, column_type, nullable=False, server_default=sa.text(default_text)))
    if "runs" in inspector.get_table_names():
        if not _has_column(inspector, "runs", "branch_plan"):
            op.add_column("runs", sa.Column("branch_plan", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
        if not _has_column(inspector, "runs", "preflight_blockers"):
            op.add_column("runs", sa.Column("preflight_blockers", sa.JSON(), nullable=False,
                                            server_default=sa.text("'[]'")))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table, columns in (
        ("run_steps", ["skip_reason", "reconcile_bucket", "path_id", "branch_id", "path_role"]),
        ("runs", ["preflight_blockers", "branch_plan"]),
        ("steps", ["branch_meta"]),
    ):
        if table in inspector.get_table_names():
            for name in columns:
                if _has_column(inspector, table, name):
                    op.drop_column(table, name)
