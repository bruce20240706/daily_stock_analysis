/**
 * 前端 K 线渲染域类型。独立于分析记录域（types/analysis.ts），
 * 跨里程碑统一接口契约：KLine 供 klinecharts 适配，SignalMarker 在 M2a 引入。
 */

/** 单根 K 线（klinecharts applyNewData 入参形状的超集）。 */
export interface KLine {
  /** epoch ms，唯一权威时间锚（按 Asia/Shanghai 解析后端 date）。 */
  timestamp: number;
  open: number;
  high: number;
  low: number;
  close: number;
  /** 成交量（手/张），后端缺失时归零。 */
  volume: number;
  /** 成交额（元），来自后端 amount，缺失时归零。 */
  turnover: number;
}

// ============ Signals contract (mirrors api/v1/schemas/stocks.py SignalsResponse) ============

export type SignalSource = 'rule' | 'llm';
export type SignalDirection = 'bullish' | 'bearish' | 'neutral';
export type ResonanceLevel = 'none' | 'weekly' | 'weekly_monthly';
export type SignalConfidence = 'high' | 'medium' | 'low';
export type SignalAnchor = 'low' | 'high' | 'close';
export type Consistency = 'consistent' | 'divergent' | 'conflict' | 'unknown' | 'stale';

export interface SignalMarker {
  timestamp: number; // epoch ms
  price: number;
  anchor: SignalAnchor;
  direction: SignalDirection;
  signalType: string;
  source: SignalSource;
  confidence: SignalConfidence;
  isDailyApprox: boolean;
  isAnomalous: boolean;
  reason: string;
  threshold: number | null;
  observedValue: number | null;
  hitRate: number | null;
  hitSample: number | null;
  verified: boolean;
  ciLow: number | null;
  ciHigh: number | null;
  baselineExcess: number | null;
  ciLowCorrected: number | null;   // family-wise 校正后 CI 下界(Inc 1c);null=legacy/无样本
  familySize: number | null;       // 同检格子数 N(Inc 1c);null=legacy
  asOf: number | null; // epoch ms, llm only
  horizonBars: number | null;
  status: 'active' | 'aging' | 'expired' | null;
}

export interface PriceLines {
  entry: number | null;
  stop: number | null;
  target: number | null;
}

export interface SignalsResponse {
  status: 'ok' | 'degraded';
  consistency: Consistency;
  degradedReason: string | null;
  priceLines: PriceLines;
  markers: SignalMarker[];
  resonance: ResonanceLevel;
  planQuality: 'high' | 'medium' | 'low' | null;
}

// ============ Board contract (mirrors api/v1/schemas SignalsBoardResponse) ============

export type ActionGroup = 'buy' | 'hold' | 'sell' | 'unavailable';

export interface BoardEntry {
  code: string;
  name: string | null;
  market: string | null;
  actionGroup: ActionGroup;
  ruleDirection: SignalDirection | null;
  llmDirection: SignalDirection | null;
  consistency: Consistency;
  keySignals: string[];
  priceLines: PriceLines;
  latestClose: number | null;
  hitRate: number | null;
  hitSample: number | null;
  verified: boolean;
  ciLow: number | null;
  ciHigh: number | null;
  baselineExcess: number | null;
  ciLowCorrected: number | null;   // family-wise 校正后 CI 下界(Inc 1c);null=legacy/无样本
  familySize: number | null;       // 同检格子数 N(Inc 1c);null=legacy
  ggtEligible: boolean | null;    // 港股通可买性三态(Inc 2b);null=名单不可达/非HK/legacy
  status: 'ok' | 'degraded';
  degradedReason: string | null;
  resonance: ResonanceLevel;
  horizonBars: number | null;
  signalStatus: 'active' | 'aging' | 'expired' | null;
  planQuality: 'high' | 'medium' | 'low' | null;
}

export interface BoardCounts { buy: number; hold: number; sell: number; unavailable: number; }

export interface SignalsBoardResponse {
  asOf: number;
  entries: BoardEntry[];
  counts: BoardCounts;
  degradedCodes: string[];
}

// ============ Quote（/stocks/{code}/quote 实时行情） ============
export interface StockQuote {
  stockCode: string;
  stockName: string | null;
  currentPrice: number;
  change: number | null;
  changePercent: number | null;
  open: number | null;
  high: number | null;
  low: number | null;
  prevClose: number | null;
  volume: number | null;
  amount: number | null;
  updateTime: string | null;
}
