import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { runApi } from '../../api/runs';
import type { Material, PreflightResult, ProcessItem, RunItem } from '../../types';

interface Props {
  process: ProcessItem;
  materials: Material[];
  onClose: () => void;
}

export default function RunLaunchDialog({ process, materials, onClose }: Props) {
  const navigate = useNavigate();
  const usable = materials.filter((item) => item.credit_valid);
  const [materialId, setMaterialId] = useState<number>(usable[0]?.id ?? materials[0]?.id ?? 0);
  const [trigger, setTrigger] = useState<'dry_run' | 'manual'>('dry_run');
  const [loading, setLoading] = useState(false);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState('');
  const [preflight, setPreflight] = useState<PreflightResult | null>(null);

  useEffect(() => {
    if (!materialId) {
      setPreflight(null);
      return;
    }
    let cancelled = false;
    setChecking(true);
    runApi.preflight(process.id, materialId)
      .then((result) => { if (!cancelled) setPreflight(result); })
      .catch((err) => { if (!cancelled) setError(err instanceof Error ? err.message : '预检失败'); })
      .finally(() => { if (!cancelled) setChecking(false); });
    return () => { cancelled = true; };
  }, [materialId, process.id]);

  const hasBranches = (preflight?.branch_groups.length ?? process.branch_groups?.length ?? 0) > 0;

  const selectedMaterial = materials.find((item) => item.id === materialId);
  const blocked = Boolean(preflight && !preflight.resolvable && trigger === 'manual');
  const creditBlocked = Boolean(selectedMaterial && !selectedMaterial.credit_valid);

  const start = async () => {
    if (!materialId) {
      setError('请选择企业材料');
      return;
    }
    if (blocked) {
      setError('存在无法判断适用条件的材料，已阻止正式申报；可先干跑校验或补全材料后再发起');
      return;
    }
    setLoading(true);
    setError('');
    try {
      const run: RunItem = await runApi.create(process.id, materialId, trigger);
      navigate(`/runs/${run.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : '发起运行失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className="panel modal">
        <header className="panel-head">
          <div>
            <div className="panel-kicker">LAUNCH ROBOT RUN</div>
            <div className="panel-title">{process.name} · v{process.version}</div>
          </div>
          <button className="btn btn-ghost btn-sm right" type="button" onClick={onClose}>关闭</button>
        </header>
        <div className="modal-body">
          <label className="field">
            <span className="field-label">企业材料（信用代码须通过 GB 32100 校验）</span>
            <select className="select" value={materialId} onChange={(event) => setMaterialId(Number(event.target.value))}>
              {materials.map((item) => (
                <option key={item.id} value={item.id} disabled={!item.credit_valid}>
                  {item.company_name} · {item.credit_code}
                  {item.credit_valid ? '' : '（校验失败）'}
                </option>
              ))}
            </select>
          </label>

          {hasBranches ? (
            <div className="panel" style={{ padding: 12, marginBottom: 12 }}>
              <div className="panel-kicker">BRANCH PREFLIGHT · 分支条件预检</div>
              {checking ? <div className="tiny muted">正在评估分支适用条件…</div> : null}
              {preflight ? (
                <div className="stack" style={{ marginTop: 8 }}>
                  <div className="flex gap-8" style={{ flexWrap: 'wrap' }}>
                    <span className={`chip ${preflight.resolvable ? 'ok' : 'danger'}`}>
                      {preflight.resolvable ? '条件均可判断' : '存在无法判断的条件'}
                    </span>
                    {Object.entries(preflight.chosen_paths).map(([branchId, pathId]) => (
                      <span key={branchId} className="chip">
                        {branchId} → {pathId}
                      </span>
                    ))}
                    {preflight.merge_points.map((seq) => (
                      <span key={seq} className="chip warn">汇合点 step {seq}</span>
                    ))}
                  </div>
                  <div className="tiny muted">
                    应执行 {preflight.expected_seqs.length} 步，未命中分支留痕跳过{' '}
                    {preflight.skipped_branch_seqs.length} 步
                  </div>
                  {preflight.blockers.length > 0 ? (
                    <div className="banner" style={{ marginTop: 8 }}>
                      <div style={{ marginBottom: 6 }}>无法判断适用条件的材料项（正式申报将被阻止）：</div>
                      {preflight.blockers.map((item) => (
                        <div key={`${item.branch_id}-${item.path_id}`} className="tiny" style={{ marginTop: 4 }}>
                          · step {item.entry_seq} 条件 <code>{item.when}</code>：{item.reason}
                        </div>
                      ))}
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div>
          ) : null}

          <label className="field">
            <span className="field-label">触发方式</span>
            <select className="select" value={trigger}
              onChange={(event) => setTrigger(event.target.value as 'dry_run' | 'manual')}>
              <option value="dry_run">干跑校验（不真实等待，验证步骤可达）</option>
              <option value="manual" disabled={process.status !== 'published'}>
                正式运行{process.status !== 'published' ? '（草稿流程不可正式运行）' : ''}
              </option>
            </select>
          </label>
          {trigger === 'manual' ? (
            <div className="banner warn">
              正式运行会占用该企业材料的并发锁：同一材料同一时间只允许 1 个进行中的 run；
              分支条件无法判断时将被阻止发起。
            </div>
          ) : null}
          {blocked ? (
            <div className="banner">正式申报已阻止：请补全材料后重新预检，或改用干跑校验。</div>
          ) : null}
          {creditBlocked ? <div className="banner">信用代码校验未通过，不能发起申报。</div> : null}
          {error ? <div className="banner">{error}</div> : null}
        </div>
        <div className="modal-foot">
          <button className="btn btn-ghost" type="button" onClick={onClose}>取消</button>
          <button className="btn btn-primary" type="button" onClick={start}
            disabled={loading || checking || blocked || creditBlocked}>
            {loading ? '下发中…' : checking ? '预检中…' : trigger === 'dry_run' ? '开始干跑' : '下发机器人'}
          </button>
        </div>
      </div>
    </div>
  );
}
