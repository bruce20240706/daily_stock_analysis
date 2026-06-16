import type React from 'react';
import type { ActionGroup, BoardEntry } from '../../types/kline';
import { SignalBoardGroup } from './SignalBoardGroup';

const GROUPS: { key: ActionGroup; title: string }[] = [
  { key: 'buy', title: '买入候选' }, { key: 'hold', title: '观望' },
  { key: 'sell', title: '卖出候选' }, { key: 'unavailable', title: '数据不可用' },
];

interface SignalBoardProps { entries: BoardEntry[]; onRowClick: (code: string, name?: string) => void; }

export const SignalBoard: React.FC<SignalBoardProps> = ({ entries, onRowClick }) => (
  <div>
    {GROUPS.map((g) => (
      <SignalBoardGroup key={g.key} groupKey={g.key} title={g.title}
        entries={entries.filter((e) => e.actionGroup === g.key)} onRowClick={onRowClick} />
    ))}
  </div>
);
