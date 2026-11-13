"""分支规划器：把「有审计报告 / 无审计报告」这类条件分支建模为带汇合点的路径。

设计目标（对应需求）：
- 分支入口步骤用 ``branch_meta.when`` 标明**适用条件**，``branch_meta.merge_seq`` 标明
  **回到主流程的位置（汇合点 seq）**，``is_default`` 分支兜底；
- 同一次运行对每个分支组只选出一条命中路径，后续步骤严格按汇合点 seq 的顺序处理；
- 条件无法判断（材料字段缺失/未知变量/未知操作符）时返回 ``unknown``，发起正式申报前据此拦截；
- 规划结果给执行器使用：哪些 seq 应执行、哪些 seq 属于未命中分支（留痕跳过）；
- :func:`reconcile` 输出应执行 / 实际执行 / 跳过 / 未匹配 四类对账，运行详情与报告对清。

本模块只依赖标准库，输入输出均为普通 dict/list，便于在无第三方依赖的环境下单测。

条件表达式（when）文法（小写操作符，&& 组合，&& 两侧都必须满足）：
- ``attachment:<名称> exists``      附件清单中存在该附件（missing_flags 显式标记缺失时判 false）
- ``attachment:<名称> missing``     附件清单中不存在，或被 missing_flags 显式标记缺失
- ``field:<名称> == <值>`` / ``!=`` 材料扩展字段（material_fields）相等/不等
- ``credit_valid == true|false``    统一社会信用代码是否通过 GB 32100 校验
- ``true`` / ``else``               恒真（默认分支推荐使用 is_default 而不是写 true）

评估结果三态：``True``（条件成立）/ ``False``（条件不成立）/ ``None``（无法判断）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# 条件评估三态
TRUE = True
FALSE = False
UNKNOWN = None

# 规划/对账结果中的路径角色
ROLE_MAIN = "main"
ROLE_BRANCH = "branch"

# 对账桶
BUCKET_EXPECTED = "expected"        # 应执行（命中路径或主流程）
BUCKET_EXECUTED = "executed"        # 实际执行（产生了运行步骤记录）
BUCKET_SKIPPED = "skipped"          # 跳过（未命中分支 / 断点续跑幂等跳过 / 接管跳过）
BUCKET_UNMATCHED = "unmatched"      # 未匹配（运行记录里有、规划里没有，通常是流程改版）


@dataclass
class ConditionEvaluation:
    value: bool | None
    reason: str = ""
    expression: str = ""


@dataclass
class BranchPlan:
    """一次运行的分支规划结果。"""
    ordered_seqs: list[int] = field(default_factory=list)              # 按汇合点顺序展开的完整 seq 序列
    expected_seqs: list[int] = field(default_factory=list)             # 本次应执行的 seq
    skipped_branch_seqs: list[int] = field(default_factory=list)       # 未命中分支的 seq（留痕跳过）
    chosen_paths: dict[str, str] = field(default_factory=dict)         # branch_id -> 命中的 path_id
    groups: list[dict] = field(default_factory=list)                   # 分支组结构（含每路径评估结果）
    unknown_conditions: list[dict] = field(default_factory=list)       # 无法判断的条件清单
    merge_points: list[int] = field(default_factory=list)              # 全部汇合点 seq（升序）
    warnings: list[str] = field(default_factory=list)

    @property
    def resolvable(self) -> bool:
        return not self.unknown_conditions


# --------------------------------------------------------------------------- #
# 条件评估
# --------------------------------------------------------------------------- #
def _attachment_state(material: dict, name: str) -> bool | None:
    """附件是否存在：missing_flags 显式标记优先，其次查 attachments 清单。

    - ``attachment:<名称>`` 明确缺失 → False；
    - ``attachment:<名称>:unknown`` 表示档案未录入、状态未知 → None（无法判断）；
    - 清单为 None（数据缺字段）→ None；清单中存在 → True，否则 False。
    """
    name = (name or "").strip()
    if not name:
        return UNKNOWN
    missing_flags = material.get("missing_flags") or []
    if f"attachment:{name}:unknown" in missing_flags:
        return UNKNOWN
    if f"attachment:{name}" in missing_flags:
        return FALSE
    attachments = material.get("attachments")
    if attachments is None:
        return UNKNOWN
    return TRUE if name in attachments else FALSE


def evaluate_atom(expression: str, material: dict) -> ConditionEvaluation:
    expr = (expression or "").strip()
    lowered = expr.lower()
    if lowered in ("", "true", "else"):
        return ConditionEvaluation(TRUE, "恒真条件" if lowered else "空条件按恒真处理", expr)

    parts = [item.strip() for item in lowered.split("==", 1)]
    if len(parts) == 2:
        left, expected = parts[0].strip(), parts[1].strip().strip("'\"")
        if left == "credit_valid":
            if material.get("credit_valid") is None:
                return ConditionEvaluation(UNKNOWN, "材料缺少信用代码校验结果", expr)
            actual = bool(material.get("credit_valid"))
            wanted = expected == "true"
            return ConditionEvaluation(actual == wanted,
                                       f"credit_valid={actual}，期望 {wanted}", expr)
        if left.startswith("field:"):
            field_name = left.split(":", 1)[1].strip()
            fields = material.get("material_fields") or {}
            if field_name not in fields:
                return ConditionEvaluation(UNKNOWN, f"材料字段 {field_name} 缺失，无法判断", expr)
            return ConditionEvaluation(str(fields[field_name]) == expected,
                                       f"字段 {field_name}={fields[field_name]!r}，期望 {expected!r}", expr)
        return ConditionEvaluation(UNKNOWN, f"不支持的等值判断变量：{left}", expr)

    parts = [item.strip() for item in lowered.split("!=", 1)]
    if len(parts) == 2:
        left, expected = parts[0].strip(), parts[1].strip().strip("'\"")
        if left.startswith("field:"):
            field_name = left.split(":", 1)[1].strip()
            fields = material.get("material_fields") or {}
            if field_name not in fields:
                return ConditionEvaluation(UNKNOWN, f"材料字段 {field_name} 缺失，无法判断", expr)
            return ConditionEvaluation(str(fields[field_name]) != expected,
                                       f"字段 {field_name}={fields[field_name]!r}，不应等于 {expected!r}", expr)
        return ConditionEvaluation(UNKNOWN, f"不支持的不等判断变量：{left}", expr)

    if lowered.startswith("attachment:"):
        rest = lowered[len("attachment:"):].strip()
        if rest.endswith(" exists"):
            name = rest[: -len(" exists")].strip()
            state = _attachment_state(material, name)
            if state is UNKNOWN:
                return ConditionEvaluation(UNKNOWN, f"附件 {name} 的清单数据缺失，无法判断", expr)
            return ConditionEvaluation(state, f"附件 {name} {'存在' if state else '缺失'}", expr)
        if rest.endswith(" missing"):
            name = rest[: -len(" missing")].strip()
            state = _attachment_state(material, name)
            if state is UNKNOWN:
                return ConditionEvaluation(UNKNOWN, f"附件 {name} 的清单数据缺失，无法判断", expr)
            return ConditionEvaluation(not state, f"附件 {name} {'缺失' if not state else '存在'}", expr)
        return ConditionEvaluation(UNKNOWN, f"附件条件缺少 exists/missing 判定：{expr}", expr)

    return ConditionEvaluation(UNKNOWN, f"未知条件表达式：{expr}", expr)


def evaluate_condition(expression: str, material: dict) -> ConditionEvaluation:
    """评估条件，支持 ``&&`` 组合；任一分支未知则整体未知。"""
    expr = (expression or "").strip()
    if "&&" not in expr:
        return evaluate_atom(expr, material)
    atoms = [item.strip() for item in expr.split("&&") if item.strip()]
    if not atoms:
        return ConditionEvaluation(TRUE, "空条件按恒真处理", expr)
    unknown_reasons: list[str] = []
    for atom in atoms:
        result = evaluate_atom(atom, material)
        if result.value is UNKNOWN:
            unknown_reasons.append(result.reason)
        elif result.value is FALSE:
            return ConditionEvaluation(FALSE, f"条件段不成立：{result.reason}", expr)
    if unknown_reasons:
        return ConditionEvaluation(UNKNOWN, "；".join(unknown_reasons), expr)
    return ConditionEvaluation(TRUE, "全部条件段成立", expr)


# --------------------------------------------------------------------------- #
# 分支结构解析与规划
# --------------------------------------------------------------------------- #
def _meta(step: dict) -> dict:
    return step.get("branch_meta") or {}


def parse_branch_groups(steps: list[dict]) -> list[dict]:
    """按 branch_id 聚合分支组，校验结构并补齐路径信息。

    每组结构：
      {branch_id, merge_seq, paths: [{path_id, entry_seq, seqs, when, is_default, steps}]}
    主流程步骤不进入任何组。抛 ValueError 表示流程定义非法（发布前应拦截）。
    """
    by_seq = {step["seq"]: step for step in steps}
    groups: dict[str, dict] = {}
    for step in sorted(steps, key=lambda item: item["seq"]):
        meta = _meta(step)
        branch_id = meta.get("branch_id")
        if not branch_id:
            continue
        merge_seq = meta.get("merge_seq")
        if not isinstance(merge_seq, int) or merge_seq not in by_seq:
            raise ValueError(f"step {step['seq']} 的汇合点 merge_seq={merge_seq} 不存在")
        if merge_seq <= step["seq"]:
            raise ValueError(f"step {step['seq']} 的汇合点必须位于分支之后（merge_seq={merge_seq}）")
        path_id = meta.get("path_id") or f"{branch_id}.default"
        group = groups.setdefault(branch_id, {"branch_id": branch_id, "merge_seq": merge_seq, "paths": {}})
        if group["merge_seq"] != merge_seq:
            raise ValueError(f"分支组 {branch_id} 内汇合点不一致：{group['merge_seq']} != {merge_seq}")
        path = group["paths"].setdefault(path_id, {
            "path_id": path_id, "entry_seq": step["seq"], "seqs": [],
            "when": meta.get("when", ""), "is_default": bool(meta.get("is_default")),
        })
        path["seqs"].append(step["seq"])
        if meta.get("when"):
            path["when"] = meta["when"]
        if meta.get("is_default"):
            path["is_default"] = True

    result: list[dict] = []
    for branch_id, group in groups.items():
        paths = list(group["paths"].values())
        for path in paths:
            path["seqs"].sort()
        paths.sort(key=lambda item: item["entry_seq"])
        defaults = [path for path in paths if path["is_default"]]
        if len(defaults) > 1:
            raise ValueError(f"分支组 {branch_id} 存在多个默认路径")
        if not defaults and not all(path["when"] for path in paths):
            raise ValueError(f"分支组 {branch_id} 既无默认路径，又存在未写适用条件的路径")
        group["paths"] = paths
        result.append(group)
    result.sort(key=lambda item: min(path["entry_seq"] for path in item["paths"]))
    return result


def plan_branches(steps: list[dict], material: dict) -> BranchPlan:
    """根据材料评估全部条件，产出本次运行的分支规划。"""
    plan = BranchPlan()
    groups = parse_branch_groups(steps)
    plan.groups = groups
    branch_seq_to_path: dict[int, tuple[str, dict]] = {}
    chosen_seqs: set[int] = set()

    for group in groups:
        branch_id = group["branch_id"]
        evaluations: list[dict] = []
        default_path: dict | None = None
        hit_path: dict | None = None
        group_unknown = 0
        for path in group["paths"]:
            evaluation = evaluate_condition(path["when"], material) if path["when"] \
                else ConditionEvaluation(TRUE, "默认路径无条件", path["when"])
            evaluations.append({
                "path_id": path["path_id"], "when": path["when"],
                "is_default": path["is_default"], "entry_seq": path["entry_seq"],
                "value": evaluation.value, "reason": evaluation.reason,
            })
            if path["is_default"]:
                default_path = path
            if evaluation.value is TRUE and hit_path is None:
                hit_path = path
            if evaluation.value is UNKNOWN:
                group_unknown += 1
                plan.unknown_conditions.append({
                    "branch_id": branch_id, "path_id": path["path_id"],
                    "entry_seq": path["entry_seq"], "when": path["when"],
                    "reason": evaluation.reason,
                })
        group["evaluations"] = evaluations

        if hit_path is None and default_path is not None and group_unknown == 0:
            hit_path = default_path
        if hit_path is None:
            # 存在无法判断的条件且没有明确命中：交由发起方决定（正式申报拦截，干跑落痕）
            plan.warnings.append(
                f"分支组 {branch_id} 无法选定路径"
                + (f"，将回退默认路径 {default_path['path_id']}" if default_path else ""))
            if default_path is not None:
                hit_path = default_path

        if hit_path is not None:
            plan.chosen_paths[branch_id] = hit_path["path_id"]
            chosen_seqs.update(hit_path["seqs"])
        for path in group["paths"]:
            for seq in path["seqs"]:
                branch_seq_to_path[seq] = (branch_id, path)
        plan.merge_points.append(group["merge_seq"])

    plan.merge_points = sorted(set(plan.merge_points))
    skipped = sorted(seq for seq in branch_seq_to_path if seq not in chosen_seqs)
    plan.skipped_branch_seqs = skipped

    # 按「回到主流程的位置（汇合点）」顺序展开：主流程 seq 自然排序，
    # 分支组在其汇合点位置整体插入（先列命中路径，未命中路径随后留痕）。
    grouped: dict[int, list[dict]] = {}
    for group in groups:
        grouped.setdefault(group["merge_seq"], []).append(group)
    ordered: list[int] = []
    for step in sorted(steps, key=lambda item: item["seq"]):
        seq = step["seq"]
        meta = _meta(step)
        if meta.get("branch_id"):
            if seq == branch_seq_to_path[seq][1]["entry_seq"]:
                for group in grouped.get(seq, []):  # 理论上分支入口不会等于汇合点，保留兜底
                    ordered.extend(_expand_group(group, plan))
            continue
        if seq in grouped:
            for group in grouped[seq]:
                ordered.extend(_expand_group(group, plan))
        ordered.append(seq)
    plan.ordered_seqs = ordered
    plan.expected_seqs = [seq for seq in ordered if seq not in set(skipped)]
    return plan


def _expand_group(group: dict, plan: BranchPlan) -> list[int]:
    chosen = plan.chosen_paths.get(group["branch_id"])
    ordered: list[int] = []
    for path in group["paths"]:
        if path["path_id"] == chosen:
            ordered.extend(path["seqs"])
    for path in group["paths"]:
        if path["path_id"] != chosen:
            ordered.extend(path["seqs"])
    return ordered


def describe_branch_groups(steps: list[dict]) -> list[dict]:
    """供流程详情/编辑器展示：分支组、适用条件、汇合点（不依赖材料）。"""
    groups = parse_branch_groups(steps)
    by_seq = {step["seq"]: step for step in steps}
    result = []
    for group in groups:
        result.append({
            "branch_id": group["branch_id"],
            "merge_seq": group["merge_seq"],
            "merge_target": by_seq[group["merge_seq"]]["target_desc"],
            "paths": [
                {
                    "path_id": path["path_id"],
                    "entry_seq": path["entry_seq"],
                    "seqs": path["seqs"],
                    "when": path["when"],
                    "is_default": path["is_default"],
                }
                for path in group["paths"]
            ],
        })
    return result


# --------------------------------------------------------------------------- #
# 对账：应执行 / 实际执行 / 跳过 / 未匹配
# --------------------------------------------------------------------------- #
def reconcile(plan: BranchPlan, run_steps: list[dict], all_step_seqs: list[int] | None = None) -> dict:
    """把规划与实际 RunStep 记录对清。

    run_steps 元素至少含 ``seq`` 与 ``status``（ok/retried/failed/skipped/running）。
    返回四个桶及汇总计数，保证 应执行+未命中分支跳过 = 规划步骤总数；
    实际执行记录中规划外的 seq 进入 unmatched。
    """
    expected = set(plan.expected_seqs)
    branch_skipped = set(plan.skipped_branch_seqs)
    planned = expected | branch_skipped
    if all_step_seqs is not None:
        planned = planned | set(all_step_seqs)

    executed_rows: list[dict] = []
    skipped_rows: list[dict] = []
    unmatched_rows: list[dict] = []
    for row in sorted(run_steps, key=lambda item: item["seq"]):
        seq = row["seq"]
        if seq not in planned:
            unmatched_rows.append({"seq": seq, "status": row.get("status", ""),
                                   "reason": "流程版本中找不到该步骤（可能已改版）"})
            continue
        if row.get("status") == "skipped":
            skipped_rows.append({"seq": seq, "status": "skipped", "note": row.get("note", "")})
        else:
            executed_rows.append({"seq": seq, "status": row.get("status", ""),
                                  "note": row.get("note", "")})

    executed_seqs = {row["seq"] for row in executed_rows}
    skipped_seqs = {row["seq"] for row in skipped_rows}

    expected_rows: list[dict] = []
    for seq in plan.expected_seqs:
        if seq in executed_seqs:
            continue
        if seq in skipped_seqs:
            continue
        expected_rows.append({"seq": seq, "reason": "已规划但尚未产生执行记录"})

    # 未命中分支的留痕跳过（执行器应写 skipped 记录；没写时也在对账中显式列出）
    for seq in plan.skipped_branch_seqs:
        if seq not in skipped_seqs and seq not in executed_seqs:
            skipped_rows.append({"seq": seq, "status": "skipped", "note": "未命中分支，规划跳过（待留痕）"})

    return {
        "expected": sorted(expected_rows, key=lambda item: item["seq"]),
        "executed": sorted(executed_rows, key=lambda item: item["seq"]),
        "skipped": sorted(skipped_rows, key=lambda item: item["seq"]),
        "unmatched": sorted(unmatched_rows, key=lambda item: item["seq"]),
        "summary": {
            "planned_total": len(plan.ordered_seqs),
            "expected_total": len(plan.expected_seqs),
            "executed_total": len(executed_rows),
            "skipped_total": len(skipped_rows),
            "unmatched_total": len(unmatched_rows),
            "pending_total": len(expected_rows),
        },
        "chosen_paths": dict(plan.chosen_paths),
        "merge_points": list(plan.merge_points),
    }
