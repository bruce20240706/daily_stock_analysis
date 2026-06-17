// apps/dsa-web/src/components/workstation/StockAlertsPanel.tsx
import { useCallback, useEffect, useState } from 'react';
import type React from 'react';
import { Link } from 'react-router-dom';
import { alertsApi } from '../../api/alerts';
import type { AlertRuleCreateRequest, AlertRuleItem } from '../../types/alerts';
import { AlertRuleForm } from '../alerts/AlertRuleForm';
import { InlineAlert, Loading } from '../common';

export const StockAlertsPanel: React.FC<{ code: string }> = ({ code }) => {
  const [rules, setRules] = useState<AlertRuleItem[] | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [created, setCreated] = useState<string | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState(false);

  const load = useCallback(async () => {
    setLoadError(false);
    try {
      const r = await alertsApi.listRules({ target: code, targetScope: 'single_symbol' });
      setRules(r.items);
    } catch {
      setLoadError(true);
      setRules([]);
    }
  }, [code]);

  useEffect(() => { void load(); }, [load]);

  const onSubmit = useCallback(async (payload: AlertRuleCreateRequest) => {
    setSubmitting(true);
    setCreated(null);
    setCreateError(null);
    try {
      await alertsApi.createRule(payload);
      setCreated('告警规则已创建');
      await load();
      return true;
    } catch {
      setCreateError('创建告警失败，请重试');
      return false;
    } finally {
      setSubmitting(false);
    }
  }, [load]);

  return (
    <div className="space-y-4 text-sm">
      {created && <InlineAlert variant="success" message={created} />}
      {createError && <InlineAlert variant="danger" message={createError} />}
      <AlertRuleForm onSubmit={onSubmit} isSubmitting={submitting} lockedTarget={code} />
      <div>
        <div className="mb-2 text-xs uppercase text-secondary-text">本股告警规则</div>
        {rules === null ? (
          <Loading label="正在加载告警" />
        ) : loadError ? (
          <InlineAlert variant="warning" message="告警规则加载失败" />
        ) : rules.length === 0 ? (
          <div className="text-secondary-text">尚无告警，新建一条。</div>
        ) : (
          <ul className="divide-y divide-border/60">
            {rules.map((r) => (
              <li key={r.id} className="flex items-center justify-between py-2">
                <span className="text-foreground">{r.name}</span>
                <span className="text-xs text-secondary-text">
                  {r.alertType}{r.enabled ? '' : ' · 已停用'}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
      <Link to="/alerts" className="text-xs text-secondary-text underline">在告警页管理 →</Link>
    </div>
  );
};
