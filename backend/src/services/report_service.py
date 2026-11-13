import csv
import io

from src.services.run_service import run_detail


def _branch_caption(item: dict) -> str:
    role = item.get("path_role") or "main"
    labels = {"main": "主流程", "branch_taken": "命中分支", "branch_skipped": "未命中分支·留痕"}
    caption = labels.get(role, role)
    if item.get("path_id"):
        caption += f"（{item['path_id']}）"
    return caption


def report_markdown(db, run_id: int) -> tuple[str, str]:
    data = run_detail(db, run_id)
    reconciliation = data.get("reconciliation") or {}
    summary = reconciliation.get("summary", {})
    lines = [
        f"# 执行报告 {data['run_code']}",
        "",
        f"- 流程：{data['process_name']}（v{data['process_version']}）",
        f"- 企业：{data['company_name']} / {data['credit_code']}",
        f"- 机器人：{data['robot_name']}",
        f"- 触发方式：{data['trigger']}",
        f"- 状态：{data['status']}",
        f"- 成功步骤：{data['success_steps']}，重试 {data['retry_count']} 次",
        f"- 开始：{data['started_at']} 结束：{data['ended_at']}",
        f"- 执行序列：{data['execution_sequence']}",
        f"- 断点续跑跳过：{data['skipped_steps']}",
        "",
        "## 分支路径对账",
        "",
        f"- 命中路径：{reconciliation.get('chosen_paths', {})}",
        f"- 汇合点（回到主流程位置）：{reconciliation.get('merge_points', [])}",
        f"- 应执行 {summary.get('expected_total', 0)} · 实际执行 {summary.get('executed_total', 0)} "
        f"· 跳过 {summary.get('skipped_total', 0)} · 未匹配 {summary.get('unmatched_total', 0)} "
        f"· 待执行 {summary.get('pending_total', 0)}（规划总步骤 {summary.get('planned_total', 0)}）",
        "",
        "| # | 动作 | 目标 | 状态 | 路径 | 对账桶 | 尝试 | 定位器 | 耗时ms | 跳过/错误原因 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for item in data["run_steps"]:
        lines.append(
            f"| {item['seq']} | {item['action']} | {item['target_desc']} | {item['status']} | "
            f"{_branch_caption(item)} | {item.get('reconcile_bucket', '')} | {item['attempt']} | "
            f"`{item['locator_strategy']}:{item['locator_used']}` | {item['duration_ms']} | "
            f"{item.get('skip_reason') or item.get('error_code') or ''} |"
        )

    def _seq_list(rows: list[dict]) -> str:
        return ", ".join(str(row["seq"]) for row in rows) or "（无）"

    lines.extend([
        "",
        f"- 应执行（含尚未执行）：{_seq_list(reconciliation.get('expected', []))}",
        f"- 实际执行：{_seq_list(reconciliation.get('executed', []))}",
        f"- 跳过（未命中分支/幂等/接管）：{_seq_list(reconciliation.get('skipped', []))}",
        f"- 未匹配（流程改版等规划外记录）：{_seq_list(reconciliation.get('unmatched', []))}",
    ])
    if data.get("preflight_blockers"):
        lines.extend(["", "## 发起前预检（无法判断的条件）", ""])
        for blocker in data["preflight_blockers"]:
            lines.append(f"- step {blocker.get('entry_seq')} [{blocker.get('when')}]：{blocker.get('reason')}")
    if data["interventions"]:
        lines.extend(["", "## 接管记录", ""])
        for item in data["interventions"]:
            lines.append(
                f"- {item['code']} {item['reason']} → {item['status']}"
                f"（{item['action'] or '待处理'}）{item['resolution']} by {item['resolved_by'] or '-'}"
            )
    lines.extend(["", "## 执行日志", "", "```", data["log_text"] or "(空)", "```"])
    return "\n".join(lines), f"report-{data['run_code']}.md"


def report_csv(db, run_id: int) -> tuple[str, str]:
    data = run_detail(db, run_id)
    reconciliation = data.get("reconciliation") or {}
    summary = reconciliation.get("summary", {})
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["run_code", "process", "version", "company", "credit_code", "robot", "trigger",
                     "run_status", "success_steps", "retry_count", "started_at", "ended_at",
                     "chosen_paths", "merge_points", "expected_total", "executed_total",
                     "skipped_total", "unmatched_total", "pending_total", "planned_total"])
    writer.writerow([data["run_code"], data["process_name"], data["process_version"], data["company_name"],
                     data["credit_code"], data["robot_name"], data["trigger"], data["status"],
                     data["success_steps"], data["retry_count"], data["started_at"], data["ended_at"],
                     reconciliation.get("chosen_paths", {}), reconciliation.get("merge_points", []),
                     summary.get("expected_total", 0), summary.get("executed_total", 0),
                     summary.get("skipped_total", 0), summary.get("unmatched_total", 0),
                     summary.get("pending_total", 0), summary.get("planned_total", 0)])
    writer.writerow([])
    writer.writerow(["seq", "action", "target_desc", "status", "path_role", "branch_id", "path_id",
                     "reconcile_bucket", "attempt", "locator_used", "duration_ms", "error_code",
                     "error_message", "skip_reason", "idempotency_key", "note"])
    for item in data["run_steps"]:
        writer.writerow([item["seq"], item["action"], item["target_desc"], item["status"],
                         item.get("path_role", ""), item.get("branch_id", ""), item.get("path_id", ""),
                         item.get("reconcile_bucket", ""), item["attempt"], item["locator_used"],
                         item["duration_ms"], item["error_code"] or "", item["error_message"],
                         item.get("skip_reason", ""), item["idempotency_key"], item["note"]])
    writer.writerow([])
    writer.writerow(["bucket", "seqs"])
    for bucket, label in (("expected", "应执行"), ("executed", "实际执行"),
                          ("skipped", "跳过"), ("unmatched", "未匹配")):
        writer.writerow([label, ",".join(str(row["seq"]) for row in reconciliation.get(bucket, []))])
    return buffer.getvalue(), f"report-{data['run_code']}.csv"
