from sqlalchemy import Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin


class RunStep(Base, TimestampMixin):
    __tablename__ = "run_steps"
    __table_args__ = (UniqueConstraint("run_id", "step_id", name="uq_runstep_run_step"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    step_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    locator_used: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    locator_strategy: Mapped[str] = mapped_column(String(20), default="", nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    output_digest: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    note: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    # 分支留痕：main=主流程；branch_taken=命中分支实际执行；branch_skipped=未命中分支留痕跳过
    path_role: Mapped[str] = mapped_column(String(20), default="main", nullable=False)
    branch_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    path_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    # 对账桶：expected/executed/skipped/unmatched，运行详情与报告据此对清
    reconcile_bucket: Mapped[str] = mapped_column(String(20), default="executed", nullable=False)
    skip_reason: Mapped[str] = mapped_column(String(255), default="", nullable=False)
