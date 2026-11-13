"""分支预检：发起申报前评估分支条件，列出无法判断条件的材料并阻止申报。

- :func:`material_snapshot` 把 ORM 材料转成规划器需要的 dict（附件清单、缺失标记、信用校验）；
- :func:`preflight` 输出分支组评估、命中路径与 unknown 条件清单；
- :func:`validate_branch_definition` 在流程发布/保存时校验分支结构（汇合点存在、默认路径唯一等）。
"""
from __future__ import annotations

from sqlalchemy import select

from src.models.step import Step
from src.services.branch_planner import (
    describe_branch_groups,
    parse_branch_groups,
    plan_branches,
)
from src.utils.errors import AppError


def material_snapshot(material) -> dict:
    return {
        "material_id": material.id,
        "company_name": material.company_name,
        "credit_code": material.credit_code,
        "credit_valid": bool(material.credit_valid),
        "attachments": list(material.attachments or []),
        "missing_flags": list(material.missing_flags or []),
        # 预留：材料扩展字段（field:xxx 条件使用），当前演示数据从附件/标记判定
        "material_fields": {},
    }


def _step_dicts(steps: list[Step]) -> list[dict]:
    return [
        {
            "seq": step.seq,
            "target_desc": step.target_desc,
            "action": step.action,
            "branch_meta": step.branch_meta or {},
        }
        for step in steps
    ]


def validate_branch_definition(steps: list[Step]) -> None:
    """发布前校验：结构非法直接 422，避免运行时才发现分支不可解析。"""
    try:
        parse_branch_groups(_step_dicts(steps))
    except ValueError as exc:
        raise AppError(422, "BRANCH_DEFINITION_INVALID", f"分支定义不合法：{exc}") from exc


def preflight(db, process_id: int, material, steps: list[Step] | None = None) -> dict:
    if steps is None:
        steps = db.scalars(
            select(Step).where(Step.process_id == process_id).order_by(Step.seq)
        ).all()
    step_dicts = _step_dicts(steps)
    snapshot = material_snapshot(material)
    try:
        plan = plan_branches(step_dicts, snapshot)
    except ValueError as exc:
        raise AppError(422, "BRANCH_DEFINITION_INVALID", f"分支定义不合法：{exc}") from exc

    groups = describe_branch_groups(step_dicts) if any(s.branch_meta for s in steps) else []
    blockers = []
    for item in plan.unknown_conditions:
        blockers.append({
            "branch_id": item["branch_id"],
            "path_id": item["path_id"],
            "entry_seq": item["entry_seq"],
            "when": item["when"],
            "reason": item["reason"],
            "message": f"分支 {item['branch_id']} 条件无法判断：{item['reason']}（{item['when']}）",
        })

    return {
        "process_id": process_id,
        "material_id": material.id,
        "company_name": material.company_name,
        "resolvable": plan.resolvable,
        "blockers": blockers,
        "chosen_paths": plan.chosen_paths,
        "merge_points": plan.merge_points,
        "expected_seqs": plan.expected_seqs,
        "skipped_branch_seqs": plan.skipped_branch_seqs,
        "ordered_seqs": plan.ordered_seqs,
        "branch_groups": groups,
        "plan": _plan_snapshot(plan),
    }


def _plan_snapshot(plan) -> dict:
    return {
        "ordered_seqs": plan.ordered_seqs,
        "expected_seqs": plan.expected_seqs,
        "skipped_branch_seqs": plan.skipped_branch_seqs,
        "chosen_paths": plan.chosen_paths,
        "merge_points": plan.merge_points,
        "unknown_conditions": plan.unknown_conditions,
        "warnings": plan.warnings,
        "groups": plan.groups,
    }


def branch_steps_out(steps: list[Step]) -> list[dict]:
    """流程详情里附带的分支说明（条件 + 汇合点 + 默认路径）。"""
    step_dicts = _step_dicts(steps)
    if not any(item["branch_meta"] for item in step_dicts):
        return []
    return describe_branch_groups(step_dicts)
