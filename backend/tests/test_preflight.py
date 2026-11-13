"""发起前预检与申报拦截测试（仅标准库）：python3 -I tests/test_preflight.py

复用执行器测试的桩件设施，验证：
- 条件可判断时 can_declare=True、给出命中路径；
- 条件无法判断时 blockers 非空，正式申报抛 BRANCH_CONDITION_UNKNOWN，干跑放行。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_executor_branch import (  # noqa: E402
    FakeDB, FakeMaterial, FakeProcess, FakeStep, build_steps,
)
from src.services import branch_service  # noqa: E402
from src.services.branch_planner import plan_branches  # noqa: E402
from src.utils.errors import AppError  # noqa: E402


def branch_steps():
    return [
        FakeStep(1, 1, "click", "入口"),
        FakeStep(2, 2, "upload", "上传审计报告",
                 {"branch_id": "audit", "path_id": "audit.with",
                  "when": "attachment:审计报告 exists", "merge_seq": 4, "is_default": False}),
        FakeStep(3, 3, "assert", "免审计声明",
                 {"branch_id": "audit", "path_id": "audit.without",
                  "when": "attachment:审计报告 missing", "merge_seq": 4, "is_default": True}),
        FakeStep(4, 4, "click", "提交"),
    ]


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.process = FakeProcess()
        self.steps = branch_steps()

    def _preflight(self, material):
        db = FakeDB(self.process, material, self.steps)
        return branch_service.preflight(db, self.process.id, material, self.steps)

    def test_resolvable_material(self):
        result = self._preflight(FakeMaterial(attachments=["审计报告"]))
        self.assertTrue(result["resolvable"])
        self.assertEqual(result["blockers"], [])
        self.assertEqual(result["chosen_paths"], {"audit": "audit.with"})
        self.assertEqual(result["merge_points"], [4])

    def test_unknown_condition_produces_blockers(self):
        material = FakeMaterial(attachments=[], missing_flags=["attachment:审计报告:unknown"])
        result = self._preflight(material)
        self.assertFalse(result["resolvable"])
        self.assertTrue(result["blockers"])
        self.assertIn("无法判断", result["blockers"][0]["message"])

    def test_formal_run_blocked_but_dry_run_allowed(self):
        """复刻 run_service.create_run 的预检闸门判定。"""
        material = FakeMaterial(attachments=[], missing_flags=["attachment:审计报告:unknown"])
        check = self._preflight(material)

        def gate(dry_run: bool):
            if not dry_run and check["blockers"]:
                raise AppError(422, "BRANCH_CONDITION_UNKNOWN",
                               "存在无法判断适用条件的材料，已阻止申报",
                               detail={"blockers": check["blockers"]})

        with self.assertRaises(AppError) as ctx:
            gate(dry_run=False)
        self.assertEqual(ctx.exception.code, "BRANCH_CONDITION_UNKNOWN")
        # 干跑不抛异常（允许落痕演示）
        gate(dry_run=True)

    def test_plan_only_selects_one_path(self):
        material = FakeMaterial(attachments=["审计报告"])
        plan = plan_branches(
            [{"seq": s.seq, "target_desc": s.target_desc, "action": s.action,
              "branch_meta": s.branch_meta} for s in self.steps],
            branch_service.material_snapshot(material),
        )
        self.assertEqual(len(plan.chosen_paths), 1)  # 每组只选一条
        self.assertEqual(plan.skipped_branch_seqs, [3])


if __name__ == "__main__":
    unittest.main(verbosity=2)
