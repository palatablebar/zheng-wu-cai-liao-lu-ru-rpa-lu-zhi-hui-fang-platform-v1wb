import Panel from '../common/Panel';
import { RECONCILE_BUCKET_LABELS } from '../../constants/enums';
import type { Reconciliation } from '../../types';

interface Props {
  reconciliation: Reconciliation;
}

const BUCKET_META: { key: keyof Omit<Reconciliation, 'summary' | 'chosen_paths' | 'merge_points' | 'branch_groups'>; tone: string }[] = [
  { key: 'expected', tone: 'ok' },
  { key: 'executed', tone: 'ok' },
  { key: 'skipped', tone: 'idle' },
  { key: 'unmatched', tone: 'danger' },
];

function seqText(rows: { seq: number; target_desc?: string }[]): string {
  if (!rows.length) return '（无）';
  return rows.map((row) => `${row.seq}${row.target_desc ? ` ${row.target_desc}` : ''}`).join('；');
}

export default function ReconciliationPanel({ reconciliation }: Props) {
  const { summary, chosen_paths: chosenPaths, merge_points: mergePoints } = reconciliation;
  return (
    <Panel kicker="BRANCH RECONCILIATION · 四桶对账"
      title="分支路径对账（应执行 / 实际执行 / 跳过 / 未匹配）">
      <div className="stat-row" style={{ marginBottom: 14 }}>
        <div className="stat-cell">
          <div className="stat-value">{summary.expected_total}</div>
          <div className="stat-label">应执行</div>
        </div>
        <div className="stat-cell">
          <div className="stat-value green">{summary.executed_total}</div>
          <div className="stat-label">实际执行</div>
        </div>
        <div className="stat-cell">
          <div className="stat-value amber">{summary.skipped_total}</div>
          <div className="stat-label">跳过</div>
        </div>
        <div className="stat-cell">
          <div className="stat-value red">{summary.unmatched_total}</div>
          <div className="stat-label">未匹配</div>
        </div>
        <div className="stat-cell">
          <div className="stat-value">{summary.pending_total}</div>
          <div className="stat-label">待执行 / 规划 {summary.planned_total}</div>
        </div>
      </div>

      <div className="flex gap-8" style={{ flexWrap: 'wrap', marginBottom: 12 }}>
        {Object.entries(chosenPaths).map(([branchId, pathId]) => (
          <span key={branchId} className="chip ok">{branchId} 命中 → {pathId}</span>
        ))}
        {mergePoints.map((seq) => (
          <span key={seq} className="chip warn">汇合点（回到主流程）step {seq}</span>
        ))}
      </div>

      <div className="stack">
        {BUCKET_META.map(({ key, tone }) => (
          <div key={key} className="rowitem" style={{ gridTemplateColumns: '120px 1fr', alignItems: 'start' }}>
            <span className={`chip ${tone}`}>{RECONCILE_BUCKET_LABELS[key]}</span>
            <div className="tiny" style={{ lineHeight: 1.7 }}>
              {seqText(reconciliation[key] as { seq: number; target_desc?: string }[])}
              {key === 'expected' && (reconciliation.expected?.length ?? 0) > 0 ? (
                <div className="tiny faint">
                  {reconciliation.expected.map((row) => row.reason).filter(Boolean).join('；')}
                </div>
              ) : null}
              {key === 'unmatched' && (reconciliation.unmatched?.length ?? 0) > 0 ? (
                <div className="tiny faint">
                  {reconciliation.unmatched.map((row) => row.reason).filter(Boolean).join('；')}
                </div>
              ) : null}
            </div>
          </div>
        ))}
      </div>
    </Panel>
  );
}
