"""执行器分支端到端模拟（仅标准库）：python3 -I tests/test_executor_branch.py

用轻量假对象替代 SQLAlchemy session/模型，直接驱动 executor._execute，
验证：只走命中路径、未命中分支留痕、续跑不重复主流程步骤、对账四桶可对清。
"""
import os
import sys
import types
import unittest
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _install_stub(name: str, attrs: dict) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


# --- SQLAlchemy / 基础设施桩件 -------------------------------------------------
class _Condition:
    def __init__(self, column, op, value):
        self.column, self.op, self.value = column, op, value


class _Column:
    """列桩件：比较后保留列名与期望值，供假 DB 按条件过滤。"""
    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return _Condition(self.name, "eq", other)

    def __ne__(self, other):
        return _Condition(self.name, "ne", other)

    def in_(self, values):
        return _Condition(self.name, "in", list(values))


class _SelectStub:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.conditions: list[_Condition] = []
        self._text = " ".join(getattr(a, "__name__", type(a).__name__) for a in args)

    def where(self, *args, **kwargs):
        for arg in args:
            if isinstance(arg, _Condition):
                self.conditions.append(arg)
            self._text += " " + type(arg).__name__
        return self

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def with_for_update(self, *args, **kwargs):
        return self

    def __repr__(self):
        return self._text


class _ScalarsResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


def select(*args, **kwargs):
    return _SelectStub(*args, **kwargs)


class FakeDB:
    def __init__(self, process, material, steps):
        self.process = process
        self.material = material
        self.steps = steps
        self.run_steps: list = []
        self.interventions: list = []
        self.commits = 0

    def get(self, model, pk):
        name = getattr(model, "__name__", "")
        if name == "Process":
            return self.process if pk == self.process.id else None
        if name == "Material":
            return self.material if pk == self.material.id else None
        if name == "Run":
            return getattr(self, "_run", None) if getattr(self, "_run", None) and self._run.id == pk else None
        return None

    def scalars(self, query):
        text = repr(query)
        if "RunStep" in text:
            rows = self._apply_conditions(self.run_steps, query.conditions)
            return _ScalarsResult(rows)
        if "Intervention" in text:
            rows = self._apply_conditions(self.interventions, query.conditions)
            return _ScalarsResult(rows)
        if "Step" in text:  # 注意：RunStep 已在上面优先匹配
            return _ScalarsResult(sorted(self.steps, key=lambda s: s.seq))
        return _ScalarsResult([])

    @staticmethod
    def _apply_conditions(rows, conditions):
        result = list(rows)
        for cond in conditions:
            if cond.op == "eq":
                result = [r for r in result if getattr(r, cond.column, None) == cond.value]
            elif cond.op == "in":
                result = [r for r in result if getattr(r, cond.column, None) in cond.value]
        return result

    def add(self, obj):
        if obj.__class__.__name__ == "RunStep":
            obj.id = len(self.run_steps) + 1
            self.run_steps.append(obj)

    def flush(self):
        pass

    def commit(self):
        self.commits += 1

    def refresh(self, obj):
        pass

    def execute(self, *args, **kwargs):
        return None


class _Func:
    def __getattr__(self, name):
        return lambda *a, **k: None


class _MagicModule(types.ModuleType):
    def __getattr__(self, name):
        if name == "select":
            return select
        if name == "create_engine":
            return lambda *a, **k: None
        if name == "func":
            return _Func()
        if name == "mapped_column":
            def _mapped_column(*args, **kwargs):
                return None
            return _mapped_column
        if name == "Mapped":
            from typing import Generic, TypeVar
            T = TypeVar("T")

            class _Mapped(Generic[T]):
                pass

            return _Mapped
        # Integer/String/JSON/DateTime/Text/Float/Boolean/Date/UniqueConstraint 等全部返回通用桩
        return type(name, (), {"__init__": lambda self, *a, **k: None})


sqlalchemy_stub = _MagicModule("sqlalchemy")
sqlalchemy_stub.select = select
sqlalchemy_stub.create_engine = lambda *a, **k: None
sys.modules["sqlalchemy"] = sqlalchemy_stub
orm_stub = _MagicModule("sqlalchemy.orm")
sys.modules["sqlalchemy.orm"] = orm_stub
_install_stub("sqlalchemy.exc", {"IntegrityError": type("IntegrityError", (Exception,), {})})
class _Base:
    pass


_install_stub("src.config.database", {"SessionLocal": None, "Base": _Base,
                                      "engine": None})
# base 模块自身定义 DeclarativeBase，需要 orm 桩提供
class _DeclarativeBase:
    pass


orm_stub.DeclarativeBase = _DeclarativeBase
orm_stub.sessionmaker = lambda *a, **k: None
_install_stub("src.config.celery_app", {})
# celery 桩件：任务装饰器原样返回函数
celery_module = sys.modules["src.config.celery_app"]


class _Celery:
    def task(self, *args, **kwargs):
        def decorator(func):
            func.delay = lambda *a, **k: None
            return func
        if args and callable(args[0]):
            return decorator(args[0])
        return decorator


celery_module.celery_app = _Celery()
_install_stub("src.config.logging", {"logger": types.SimpleNamespace(
    info=lambda *a, **k: None, warning=lambda *a, **k: None, exception=lambda *a, **k: None)})
_install_stub("src.config.settings", {"settings": types.SimpleNamespace()})

from src.constants.enums import RunStatus, RunStepStatus, StepPathRole  # noqa: E402
from src.services import executor  # noqa: E402


def _loosen(model_cls):
    """让 ORM 模型在无 SQLAlchemy 环境下也能按 kwargs 构造，并给列属性默认值。"""
    original_init = model_cls.__init__

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)

    model_cls.__init__ = __init__
    return model_cls


for _model in (executor.RunStep, executor.Intervention, executor.Run, executor.Step,
               executor.Process, executor.Material):
    _loosen(_model)
# 让查询列的比较产生可识别的 _Condition，供假 DB 过滤
for _name in ("run_id", "step_id", "seq", "status", "process_id", "id", "code"):
    setattr(executor.RunStep, _name, _Column(_name))
for _name in ("run_id", "run_step_id", "code", "status"):
    setattr(executor.Intervention, _name, _Column(_name))
for _name in ("id", "process_id", "material_id", "status", "trigger"):
    setattr(executor.Run, _name, _Column(_name))
for _name in ("id", "process_id", "seq"):
    setattr(executor.Step, _name, _Column(_name))
# RunStep 缺省列值
for _name, _default in (("locator_used", ""), ("locator_strategy", ""), ("attempt", 1),
                        ("duration_ms", 0), ("error_code", None), ("error_message", ""),
                        ("idempotency_key", ""), ("output_digest", ""), ("note", ""),
                        ("path_role", "main"), ("branch_id", ""), ("path_id", ""),
                        ("reconcile_bucket", "executed"), ("skip_reason", "")):
    if not hasattr(executor.RunStep, _name):
        setattr(executor.RunStep, _name, _default)


@dataclass
class FakeStep:
    id: int
    seq: int
    action: str
    target_desc: str
    branch_meta: dict = field(default_factory=dict)
    locator_candidates: list = field(default_factory=list)
    assert_rule: str | None = None
    max_retries: int = 2
    depends_on: list = field(default_factory=list)
    timeout_ms: int = 15000

    def __post_init__(self):
        if not self.locator_candidates:
            self.locator_candidates = [{"strategy": "css", "selector": f"#step-{self.seq}"}]


@dataclass
class FakeMaterial:
    id: int = 1
    company_name: str = "测试企业"
    credit_code: str = "91320500MA1XY2K3AJ"
    credit_valid: bool = True
    attachments: list = field(default_factory=list)
    missing_flags: list = field(default_factory=list)


@dataclass
class FakeProcess:
    id: int = 1
    name: str = "年报分支流程"


@dataclass
class FakeRun:
    id: int = 100
    run_code: str = "RUN-TEST-001"
    process_id: int = 1
    process_version: int = 1
    material_id: int = 1
    robot_name: str = "SU-BOT-01"
    trigger: str = "dry_run"
    status: str = RunStatus.QUEUED.value
    started_at=None
    ended_at=None
    total_steps: int = 0
    success_steps: int = 0
    retry_count: int = 0
    attempt_count: int = 0
    execution_sequence: list = field(default_factory=list)
    skipped_steps: list = field(default_factory=list)
    compensation: list = field(default_factory=list)
    log_text: str = ""
    active_lock=None
    branch_plan: dict = field(default_factory=dict)
    preflight_blockers: list = field(default_factory=list)


def build_steps():
    return [
        FakeStep(1, 1, "click", "入口"),
        FakeStep(2, 2, "upload", "营业执照"),
        FakeStep(3, 3, "upload", "上传审计报告",
                 {"branch_id": "audit", "path_id": "audit.with",
                  "when": "attachment:审计报告 exists", "merge_seq": 6, "is_default": False}),
        FakeStep(4, 4, "assert", "审计报告断言",
                 {"branch_id": "audit", "path_id": "audit.with",
                  "when": "attachment:审计报告 exists", "merge_seq": 6, "is_default": False}),
        FakeStep(5, 5, "assert", "免审计声明",
                 {"branch_id": "audit", "path_id": "audit.without",
                  "when": "attachment:审计报告 missing", "merge_seq": 6, "is_default": True}),
        FakeStep(6, 6, "click", "提交（汇合点后主流程）"),
    ]


class ExecutorBranchTests(unittest.TestCase):
    def _run_once(self, material, run=None, db=None):
        if db is None:
            db = FakeDB(FakeProcess(), material, build_steps())
        else:
            db.material = material
        run = run or FakeRun()
        db._run = run
        executor._execute(db, run, dry_run=True)
        return db, run

    def test_with_audit_only_takes_branch_with(self):
        material = FakeMaterial(attachments=["审计报告"])
        db, run = self._run_once(material)
        self.assertEqual(run.status, RunStatus.SUCCEEDED.value)
        self.assertEqual(run.branch_plan["chosen_paths"], {"audit": "audit.with"})
        by_seq = {rs.seq: rs for rs in db.run_steps}
        # 命中路径实际执行
        self.assertEqual(by_seq[3].status, RunStepStatus.OK.value)
        self.assertEqual(by_seq[3].path_role, StepPathRole.BRANCH_TAKEN.value)
        self.assertEqual(by_seq[4].path_role, StepPathRole.BRANCH_TAKEN.value)
        # 未命中路径留痕跳过，不产生页面动作
        self.assertEqual(by_seq[5].status, RunStepStatus.SKIPPED.value)
        self.assertEqual(by_seq[5].path_role, StepPathRole.BRANCH_SKIPPED.value)
        self.assertEqual(by_seq[5].reconcile_bucket, "skipped")
        self.assertTrue(by_seq[5].skip_reason)
        # 汇合点后主流程执行
        self.assertEqual(by_seq[6].status, RunStepStatus.OK.value)
        self.assertEqual(by_seq[6].path_role, StepPathRole.MAIN.value)

    def test_without_audit_takes_default_path(self):
        material = FakeMaterial(attachments=[])
        db, run = self._run_once(material)
        self.assertEqual(run.branch_plan["chosen_paths"], {"audit": "audit.without"})
        by_seq = {rs.seq: rs for rs in db.run_steps}
        self.assertEqual(by_seq[5].status, RunStepStatus.OK.value)
        self.assertEqual(by_seq[5].path_role, StepPathRole.BRANCH_TAKEN.value)
        for seq in (3, 4):
            self.assertEqual(by_seq[seq].status, RunStepStatus.SKIPPED.value)
            self.assertEqual(by_seq[seq].path_role, StepPathRole.BRANCH_SKIPPED.value)

    def test_resume_does_not_repeat_main_steps(self):
        material = FakeMaterial(attachments=["审计报告"])
        db, run = self._run_once(material)
        first_digests = {rs.seq: rs.output_digest for rs in db.run_steps if rs.status == "ok"}
        # 模拟补录后重新运行：同一个 run、同一批 RunStep（同一 db）再次进入 _execute
        run.status = RunStatus.QUEUED.value
        db2, run2 = self._run_once(material, run=run, db=db)
        by_seq = {rs.seq: rs for rs in db2.run_steps}
        # 主流程/已成功步骤保持 ok，且 output_digest 未被重新执行覆盖
        for seq, digest in first_digests.items():
            self.assertEqual(by_seq[seq].output_digest, digest)
            self.assertIn(seq, run2.skipped_steps)
        # 没有新增 RunStep 行（不重复建步骤）
        self.assertEqual(len(db2.run_steps), len(first_digests) + 1)  # 含 1 条留痕 skipped

    def test_resume_keeps_path_after_material_backfill(self):
        # 首跑：无审计报告 → 命中默认路径 audit.without
        material = FakeMaterial(attachments=[])
        db, run = self._run_once(material)
        self.assertEqual(run.branch_plan["chosen_paths"], {"audit": "audit.without"})
        by_seq_first = {rs.seq: rs.status for rs in db.run_steps}
        self.assertEqual(by_seq_first[5], "ok")          # 免审计声明执行
        self.assertEqual(by_seq_first[3], "skipped")     # 审计报告路径留痕

        # 中途补录审计报告，重新运行同一 run：路径不得翻转
        material.attachments = ["审计报告"]
        run.status = RunStatus.QUEUED.value
        db2, run2 = self._run_once(material, run=run, db=db)
        self.assertEqual(run2.branch_plan["chosen_paths"], {"audit": "audit.without"})
        by_seq = {rs.seq: rs for rs in db2.run_steps}
        # 原留痕步骤仍为留痕（path_role 不变），原命中步骤保持通过，没有翻转成审计报告路径
        self.assertEqual(by_seq[3].status, RunStepStatus.SKIPPED.value)
        self.assertEqual(by_seq[3].path_role, StepPathRole.BRANCH_SKIPPED.value)
        self.assertEqual(by_seq[5].status, RunStepStatus.OK.value)
        self.assertEqual(by_seq[5].path_role, StepPathRole.BRANCH_TAKEN.value)
        # 没有新增 RunStep 行（路径稳定，不重建步骤）
        self.assertEqual(len(db2.run_steps), 6)

    def test_reconciliation_buckets_align(self):
        from src.services.branch_planner import BranchPlan, reconcile
        material = FakeMaterial(attachments=[])
        db, run = self._run_once(material)
        snapshot = run.branch_plan
        plan = BranchPlan(
            ordered_seqs=snapshot["ordered_seqs"], expected_seqs=snapshot["expected_seqs"],
            skipped_branch_seqs=snapshot["skipped_branch_seqs"],
            chosen_paths=snapshot["chosen_paths"], merge_points=snapshot["merge_points"])
        report = reconcile(plan, [{"seq": rs.seq, "status": rs.status, "note": rs.note}
                                  for rs in db.run_steps])
        self.assertEqual(report["summary"]["planned_total"], 6)
        self.assertEqual(report["summary"]["executed_total"], 4)   # 1,2,5,6
        self.assertEqual(report["summary"]["skipped_total"], 2)    # 3,4 留痕
        self.assertEqual(report["summary"]["unmatched_total"], 0)
        self.assertEqual(report["summary"]["pending_total"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
