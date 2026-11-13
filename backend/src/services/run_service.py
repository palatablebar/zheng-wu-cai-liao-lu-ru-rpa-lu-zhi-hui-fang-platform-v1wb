import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.config.logging import logger
from src.constants.enums import ACTIVE_RUN_STATUSES, ProcessStatus, RunStatus
from src.models.intervention import Intervention
from src.models.material import Material
from src.models.process import Process
from src.models.run import Run
from src.models.run_step import RunStep
from src.models.step import Step
from src.services.branch_planner import BranchPlan, reconcile
from src.services.branch_service import preflight as branch_preflight
from src.services.executor import execute_run_task
from src.services.serializers import intervention_out, run_out, run_step_out
from src.utils.errors import AppError


def _new_run_code() -> str:
    return f"RUN-{datetime.now():%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"


def _enqueue(run_id: int) -> None:
    try:
        execute_run_task.delay(run_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("enqueue execute_run failed run_id=%s: %s", run_id, exc)


def preflight_run(db, process_id: int, material_id: int) -> dict:
    """发起前预检：返回分支条件评估结果与无法判断的材料清单（不创建 run）。"""
    process = db.get(Process, process_id)
    if process is None:
        raise AppError(404, "PROCESS_NOT_FOUND", f"流程 {process_id} 不存在")
    material = db.get(Material, material_id)
    if material is None:
        raise AppError(404, "MATERIAL_NOT_FOUND", f"企业材料 {material_id} 不存在")
    result = branch_preflight(db, process_id, material)
    result["credit_valid"] = bool(material.credit_valid)
    result["can_declare"] = bool(material.credit_valid) and result["resolvable"]
    return result


def create_run(db, actor, process_id: int, material_id: int, trigger: str) -> dict:
    process = db.get(Process, process_id)
    if process is None:
        raise AppError(404, "PROCESS_NOT_FOUND", f"流程 {process_id} 不存在")
    material = db.get(Material, material_id)
    if material is None:
        raise AppError(404, "MATERIAL_NOT_FOUND", f"企业材料 {material_id} 不存在")
    dry_run = trigger == "dry_run"
    if not dry_run and process.status != ProcessStatus.PUBLISHED.value:
        raise AppError(422, "PROCESS_NOT_PUBLISHED", "只有已发布流程才能发起正式运行")
    if not dry_run and not material.credit_valid:
        raise AppError(422, "E_CREDIT_INVALID",
                       f"信用代码校验未通过，不能发起申报：{material.credit_reason}")

    # 发起前分支预检：无法判断适用条件的材料先列出来，正式申报直接阻止
    check = branch_preflight(db, process_id, material)
    if not dry_run and check["blockers"]:
        messages = "；".join(item["message"] for item in check["blockers"])
        raise AppError(422, "BRANCH_CONDITION_UNKNOWN",
                       f"存在无法判断适用条件的材料，已阻止申报：{messages}",
                       detail={"blockers": check["blockers"]})

    db.execute(select(Material).where(Material.id == material_id).with_for_update())
    if not dry_run:
        active = db.scalars(
            select(Run).where(Run.material_id == material_id, Run.status.in_(ACTIVE_RUN_STATUSES))
        ).first()
        if active:
            raise AppError(409, "E_CONCURRENCY_CONFLICT",
                           f"该公司已有进行中的运行 {active.run_code}（同一材料仅允许 1 个并发 run）")

    run = Run(
        run_code=_new_run_code(),
        process_id=process.id,
        process_version=process.version,
        material_id=material.id,
        robot_name="SU-BOT-01",
        trigger=trigger,
        status=RunStatus.QUEUED.value,
        total_steps=process.step_count,
        active_lock=None if dry_run else str(material.id),
        preflight_blockers=check["blockers"],
    )
    db.add(run)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AppError(409, "E_CONCURRENCY_CONFLICT", "同一材料并发冲突，请稍后重试") from None
    run_id = run.id
    _enqueue(run_id)
    db.refresh(run)
    data = run_out(run)
    data["process_name"] = process.name
    data["company_name"] = material.company_name
    data["preflight"] = {"resolvable": check["resolvable"], "blockers": check["blockers"],
                         "chosen_paths": check["chosen_paths"]}
    return data


def list_runs(db, status: str | None = None, process_id: int | None = None,
              trigger: str | None = None, limit: int = 50) -> list[dict]:
    query = select(Run).order_by(Run.id.desc()).limit(limit)
    if status:
        query = query.where(Run.status == status)
    if process_id:
        query = query.where(Run.process_id == process_id)
    if trigger:
        query = query.where(Run.trigger == trigger)
    runs = db.scalars(query).all()
    process_map = {p.id: p for p in db.scalars(select(Process)).all()}
    material_map = {m.id: m for m in db.scalars(select(Material)).all()}
    result = []
    for run in runs:
        data = run_out(run)
        data["process_name"] = process_map[run.process_id].name if run.process_id in process_map else ""
        data["company_name"] = material_map[run.material_id].company_name if run.material_id in material_map else ""
        result.append(data)
    return result


def run_entities(db, run_id: int) -> tuple[Run, Process, Material]:
    run = db.get(Run, run_id)
    if run is None:
        raise AppError(404, "RUN_NOT_FOUND", f"运行 {run_id} 不存在")
    process = db.get(Process, run.process_id)
    material = db.get(Material, run.material_id)
    return run, process, material


def _reconcile_run(run: Run, run_steps: list[RunStep], steps: list[Step]) -> dict:
    """根据落库的分支规划快照与 RunStep 记录生成四桶对账；无分支流程按执行序列兜底。"""
    snapshot = run.branch_plan or {}
    if not snapshot:
        ordered = run.execution_sequence or [item.seq for item in run_steps]
        snapshot = {
            "ordered_seqs": ordered,
            "expected_seqs": ordered,
            "skipped_branch_seqs": [],
            "chosen_paths": {},
            "merge_points": [],
            "unknown_conditions": list(run.preflight_blockers or []),
            "warnings": [],
        }
    plan = BranchPlan(
        ordered_seqs=list(snapshot.get("ordered_seqs", [])),
        expected_seqs=list(snapshot.get("expected_seqs", [])),
        skipped_branch_seqs=list(snapshot.get("skipped_branch_seqs", [])),
        chosen_paths=dict(snapshot.get("chosen_paths", {})),
        unknown_conditions=list(snapshot.get("unknown_conditions", [])),
        merge_points=list(snapshot.get("merge_points", [])),
        warnings=list(snapshot.get("warnings", [])),
    )
    rows = [
        {"seq": item.seq, "status": item.status, "note": item.note or item.skip_reason}
        for item in run_steps
    ]
    report = reconcile(plan, rows, all_step_seqs=[step.seq for step in steps])
    step_desc = {step.seq: step.target_desc for step in steps}
    for bucket in ("expected", "executed", "skipped", "unmatched"):
        for row in report[bucket]:
            row["target_desc"] = step_desc.get(row["seq"], "")
    report["branch_groups"] = snapshot.get("groups", [])
    return report


def run_detail(db, run_id: int) -> dict:
    run, process, material = run_entities(db, run_id)
    run_steps = db.scalars(select(RunStep).where(RunStep.run_id == run_id).order_by(RunStep.seq)).all()
    steps = db.scalars(
        select(Step).where(Step.process_id == run.process_id).order_by(Step.seq)).all()
    step_map = {step.id: step for step in steps}
    interventions = db.scalars(
        select(Intervention).where(Intervention.run_id == run_id).order_by(Intervention.id)).all()
    data = run_out(run)
    data["process_name"] = process.name if process else ""
    data["matter_type"] = process.matter_type if process else ""
    data["company_name"] = material.company_name if material else ""
    data["credit_code"] = material.credit_code if material else ""
    data["credit_valid"] = bool(material.credit_valid) if material else False
    data["run_steps"] = [run_step_out(item, step_map.get(item.step_id)) for item in run_steps]
    data["reconciliation"] = _reconcile_run(run, run_steps, steps)
    data["interventions"] = [
        intervention_out(item, run=run, process=process, material=material,
                         run_step=next((rs for rs in run_steps if rs.id == item.run_step_id), None),
                         step=next((step_map.get(rs.step_id) for rs in run_steps
                                    if rs.id == item.run_step_id), None))
        for item in interventions
    ]
    return data


def cancel_run(db, actor, run_id: int, note: str = "") -> dict:
    run, _, _ = run_entities(db, run_id)
    if run.status not in ACTIVE_RUN_STATUSES:
        raise AppError(409, "RUN_NOT_ACTIVE", f"运行 {run.run_code} 当前状态 {run.status}，不能取消")
    stamp = datetime.now().strftime("%H:%M:%S")
    run.log_text = ((run.log_text or "") + f"\n[{stamp}] 操作员 {actor['email']} 取消运行：{note}").strip()[-12000:]
    run.status = RunStatus.CANCELLED.value
    run.ended_at = datetime.now()
    run.active_lock = None
    db.commit()
    return run_out(run)
