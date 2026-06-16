import type React from 'react';

interface StockWorkstationHeaderProps { code: string; }

export const StockWorkstationHeader: React.FC<StockWorkstationHeaderProps> = ({ code }) => (
  <header data-testid="ws-header">{code}</header>
);
