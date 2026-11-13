"""分支规划器单测（仅标准库）：python3 -I tests/test_branch_planner.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.services import branch_planner as bp  # noqa: E402


def step(seq, target, branch_id=None, path_id=None, when="", merge_seq=None, is_default=False):
    meta = {}
    if branch_id:
        meta = {"branch_id": branch_id, "path_id": path_id, "when": when,
                "merge_seq": merge_seq, "is_default": is_default}
    return {"seq": seq, "target_desc": target, "branch_meta": meta}


# 年报流程：主流程 1-5，审计分支组（汇合点 seq=9），主流程 9-11
ANNUAL = [
    step(1, "入口"), step(2, "信用代码"), step(3, "信用代码断言"),
    step(4, "企业名称"), step(5, "营业执照"),
    step(6, "上传审计报告", "audit", "audit.with", "attachment:审计报告 exists", 9),
    step(7, "审计报告断言", "audit", "audit.with", "attachment:审计报告 exists", 9),
    step(8, "免审计声明", "audit", "audit.without", "attachment:审计报告 missing", 9, True),
    step(9, "注册资本"), step(10, "提交"), step(11, "回执"),
]

MAT_WITH = {"attachments": ["营业执照", "审计报告"], "missing_flags": [], "credit_valid": True}
MAT_WITHOUT = {"attachments": ["营业执照"], "missing_flags": [], "credit_valid": True}
MAT_UNKNOWN = {"missing_flags": [], "credit_valid": True}  # 无 attachments 键


class ConditionTests(unittest.TestCase):
    def test_attachment_exists(self):
        self.assertEqual(bp.evaluate_condition("attachment:审计报告 exists", MAT_WITH).value, True)
        self.assertEqual(bp.evaluate_condition("attachment:审计报告 exists", MAT_WITHOUT).value, False)

    def test_missing_flag_forces_false(self):
        material = {"attachments": ["审计报告"], "missing_flags": ["attachment:审计报告"]}
        self.assertEqual(bp.evaluate_condition("attachment:审计报告 exists", material).value, False)
        self.assertEqual(bp.evaluate_condition("attachment:审计报告 missing", material).value, True)

    def test_unknown_when_attachments_absent(self):
        result = bp.evaluate_condition("attachment:审计报告 exists", MAT_UNKNOWN)
        self.assertIsNone(result.value)
        self.assertIn("无法判断", result.reason)

    def test_unknown_flag_in_missing_flags(self):
        material = {"attachments": [], "missing_flags": ["attachment:审计报告:unknown"]}
        self.assertIsNone(bp.evaluate_condition("attachment:审计报告 exists", material).value)
        self.assertIsNone(bp.evaluate_condition("attachment:审计报告 missing", material).value)
        # 预检规划应给出 blockers
        plan = bp.plan_branches(ANNUAL, material)
        self.assertFalse(plan.resolvable)
        self.assertTrue(plan.unknown_conditions)

    def test_credit_valid(self):
        self.assertEqual(bp.evaluate_condition("credit_valid == true", MAT_WITH).value, True)
        self.assertEqual(bp.evaluate_condition("credit_valid == false", MAT_WITH).value, False)

    def test_field_equality_and_unknown(self):
        material = {"material_fields": {"规模": "小型"}}
        self.assertEqual(bp.evaluate_condition("field:规模 == 小型", material).value, True)
        self.assertEqual(bp.evaluate_condition("field:规模 != 大型", material).value, True)
        self.assertIsNone(bp.evaluate_condition("field:行业 == 制造", material).value)

    def test_and_combinator(self):
        material = {"attachments": ["审计报告"], "missing_flags": [], "credit_valid": True}
        expr = "attachment:审计报告 exists && credit_valid == true"
        self.assertEqual(bp.evaluate_condition(expr, material).value, True)
        material["credit_valid"] = False
        self.assertEqual(bp.evaluate_condition(expr, material).value, False)

    def test_unknown_expression(self):
        self.assertIsNone(bp.evaluate_condition("cookie:口味 == 抹茶", MAT_WITH).value)


class PlanTests(unittest.TestCase):
    def test_with_audit_path_chosen(self):
        plan = bp.plan_branches(ANNUAL, MAT_WITH)
        self.assertTrue(plan.resolvable)
        self.assertEqual(plan.chosen_paths, {"audit": "audit.with"})
        self.assertEqual(plan.expected_seqs, [1, 2, 3, 4, 5, 6, 7, 9, 10, 11])
        self.assertEqual(plan.skipped_branch_seqs, [8])
        # 分支组在汇合点 9 之前整体展开：命中路径在前，未命中路径留痕在后
        self.assertEqual(plan.ordered_seqs, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11])
        self.assertEqual(plan.merge_points, [9])

    def test_without_audit_default_path(self):
        plan = bp.plan_branches(ANNUAL, MAT_WITHOUT)
        self.assertEqual(plan.chosen_paths, {"audit": "audit.without"})
        self.assertEqual(plan.expected_seqs, [1, 2, 3, 4, 5, 8, 9, 10, 11])
        self.assertEqual(plan.skipped_branch_seqs, [6, 7])
        self.assertEqual(plan.ordered_seqs, [1, 2, 3, 4, 5, 8, 6, 7, 9, 10, 11])

    def test_unknown_condition_blocks_and_falls_back_to_default(self):
        plan = bp.plan_branches(ANNUAL, MAT_UNKNOWN)
        self.assertFalse(plan.resolvable)
        # 两条路径的条件（exists / missing）都无法判断，清单必须完整列出
        self.assertEqual(len(plan.unknown_conditions), 2)
        self.assertEqual({item["branch_id"] for item in plan.unknown_conditions}, {"audit"})
        # 兜底默认路径，保证干跑仍可落痕；正式申报由 service 层按 resolvable 拦截
        self.assertEqual(plan.chosen_paths, {"audit": "audit.without"})
        self.assertTrue(plan.warnings)

    def test_two_groups_ordered_by_merge_seq(self):
        steps = [
            step(1, "入口"),
            step(2, "分支A1", "gA", "gA.yes", "attachment:甲 exists", 4),
            step(3, "分支A默认", "gA", "gA.no", "attachment:甲 missing", 4, True),
            step(4, "汇合A"),
            step(5, "分支B1", "gB", "gB.yes", "attachment:乙 exists", 7),
            step(6, "分支B默认", "gB", "gB.no", "attachment:乙 missing", 7, True),
            step(7, "汇合B"),
        ]
        material = {"attachments": ["甲", "乙"], "missing_flags": []}
        plan = bp.plan_branches(steps, material)
        self.assertEqual(plan.chosen_paths, {"gA": "gA.yes", "gB": "gB.yes"})
        self.assertEqual(plan.ordered_seqs, [1, 2, 3, 4, 5, 6, 7])
        self.assertEqual(plan.merge_points, [4, 7])
        self.assertEqual(plan.skipped_branch_seqs, [3, 6])

    def test_invalid_merge_point_raises(self):
        bad = [step(1, "入口"),
               step(2, "悬空分支", "g", "g.x", "true", 99)]
        with self.assertRaises(ValueError):
            bp.plan_branches(bad, MAT_WITH)

    def test_multiple_defaults_raises(self):
        bad = [
            step(1, "入口"),
            step(2, "a", "g", "g.a", "attachment:甲 exists", 4),
            step(3, "b", "g", "g.b", "attachment:甲 missing", 4, True),
            step(4, "汇合", None),
        ]
        # 两条路径都标默认时（手工篡改 is_default）
        bad[1]["branch_meta"]["is_default"] = True
        with self.assertRaises(ValueError):
            bp.plan_branches(bad, MAT_WITH)


class ReconcileTests(unittest.TestCase):
    def test_buckets_align_with_plan(self):
        plan = bp.plan_branches(ANNUAL, MAT_WITH)
        run_steps = [
            {"seq": 1, "status": "ok"}, {"seq": 2, "status": "ok"},
            {"seq": 3, "status": "ok"}, {"seq": 4, "status": "ok"},
            {"seq": 5, "status": "ok"}, {"seq": 6, "status": "ok"},
            {"seq": 7, "status": "retried"},
            {"seq": 8, "status": "skipped", "note": "未命中分支：无审计报告路径"},
        ]
        report = bp.reconcile(plan, run_steps)
        executed = {row["seq"] for row in report["executed"]}
        skipped = {row["seq"] for row in report["skipped"]}
        pending = {row["seq"] for row in report["expected"]}
        self.assertEqual(executed, {1, 2, 3, 4, 5, 6, 7})
        self.assertEqual(skipped, {8})
        self.assertEqual(pending, {9, 10, 11})  # 尚未执行的应执行步骤
        self.assertEqual(report["unmatched"], [])
        self.assertEqual(report["summary"]["planned_total"], 11)

    def test_unmatched_step_after_process_change(self):
        plan = bp.plan_branches(ANNUAL, MAT_WITHOUT)
        run_steps = [{"seq": 1, "status": "ok"}, {"seq": 99, "status": "ok"}]
        report = bp.reconcile(plan, run_steps, all_step_seqs=[s["seq"] for s in ANNUAL])
        self.assertEqual(report["unmatched"][0]["seq"], 99)

    def test_resume_does_not_duplicate_main_steps(self):
        # 续跑场景：已成功的主流程步骤再次对账时仍只算实际执行一次
        plan = bp.plan_branches(ANNUAL, MAT_WITH)
        run_steps = [{"seq": 1, "status": "ok"}, {"seq": 2, "status": "ok"}]
        first = bp.reconcile(plan, run_steps)
        second = bp.reconcile(plan, run_steps)  # 相同记录重新对账
        self.assertEqual(first["summary"]["executed_total"], second["summary"]["executed_total"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
