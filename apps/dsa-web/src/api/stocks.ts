import apiClient from './index';
import type { KLine, ResonanceLevel } from '../types/kline';
import type { SignalMarker, SignalsResponse } from '../types/kline';
import type { BoardEntry, SignalsBoardResponse } from '../types/kline';
import type { StockQuote } from '../types/kline';

export type ExtractItem = {
  code?: string | null;
  name?: string | null;
  confidence: string;
};

export type ExtractFromImageResponse = {
  codes: string[];
  items?: ExtractItem[];
  rawText?: string;
};

/** K 线抽屉默认回看天数，与后端 /history 端点 Query 默认对齐。 */
export const KLINE_DEFAULT_DAYS = 120;

type RawSignalMarker = {
  timestamp: number;
  price: number;
  anchor: SignalMarker['anchor'];
  direction: SignalMarker['direction'];
  signal_type: string;
  source: SignalMarker['source'];
  confidence: SignalMarker['confidence'];
  is_daily_approx: boolean;
  is_anomalous: boolean;
  reason: string;
  threshold: number | null;
  observed_value: number | null;
  hit_rate: number | null;
  hit_sample: number | null;
  verified: boolean;
  ci_low: number | null;
  ci_high: number | null;
  baseline_excess: number | null;
  ci_low_corrected?: number | null;
  family_size?: number | null;
  as_of: number | null;
  horizon_bars?: number | null;
  status?: 'active' | 'aging' | 'expired' | null;
};

type RawSignalsResponse = {
  status: SignalsResponse['status'];
  consistency: SignalsResponse['consistency'];
  degraded_reason: string | null;
  price_lines: { entry: number | null; stop: number | null; target: number | null } | null;
  markers: RawSignalMarker[] | null;
  resonance?: ResonanceLevel | null;
  plan_quality?: 'high' | 'medium' | 'low' | null;
};

const mapSignalMarker = (raw: RawSignalMarker): SignalMarker => ({
  timestamp: raw.timestamp,
  price: raw.price,
  anchor: raw.anchor,
  direction: raw.direction,
  signalType: raw.signal_type,
  source: raw.source,
  confidence: raw.confidence,
  isDailyApprox: raw.is_daily_approx,
  isAnomalous: raw.is_anomalous,
  reason: raw.reason,
  threshold: raw.threshold ?? null,
  observedValue: raw.observed_value ?? null,
  hitRate: raw.hit_rate ?? null,
  hitSample: raw.hit_sample ?? null,
  verified: raw.verified,
  ciLow: raw.ci_low ?? null,
  ciHigh: raw.ci_high ?? null,
  baselineExcess: raw.baseline_excess ?? null,
  ciLowCorrected: raw.ci_low_corrected ?? null,
  familySize: raw.family_size ?? null,
  asOf: raw.as_of ?? null,
  horizonBars: raw.horizon_bars ?? null,
  status: raw.status ?? null,
});

type RawBoardEntry = {
  code: string; name: string | null; market: string | null;
  action_group: BoardEntry['actionGroup'];
  rule_direction: BoardEntry['ruleDirection']; llm_direction: BoardEntry['llmDirection'];
  consistency: BoardEntry['consistency']; key_signals: string[];
  price_lines: { entry: number | null; stop: number | null; target: number | null };
  latest_close: number | null; hit_rate: number | null; hit_sample: number | null;
  verified: boolean; ci_low: number | null; ci_high: number | null; baseline_excess: number | null;
  ci_low_corrected?: number | null;
  family_size?: number | null;
  status: BoardEntry['status']; degraded_reason: string | null;
  resonance?: ResonanceLevel | null;
  horizon_bars?: number | null;
  signal_status?: 'active' | 'aging' | 'expired' | null;
  plan_quality?: 'high' | 'medium' | 'low' | null;
};
type RawBoardResponse = {
  as_of: number; entries: RawBoardEntry[] | null;
  counts: { buy: number; hold: number; sell: number; unavailable: number };
  degraded_codes: string[] | null;
};
const mapBoardEntry = (r: RawBoardEntry): BoardEntry => ({
  code: r.code, name: r.name ?? null, market: r.market ?? null,
  actionGroup: r.action_group, ruleDirection: r.rule_direction ?? null,
  llmDirection: r.llm_direction ?? null, consistency: r.consistency,
  keySignals: r.key_signals ?? [],
  priceLines: { entry: r.price_lines?.entry ?? null, stop: r.price_lines?.stop ?? null, target: r.price_lines?.target ?? null },
  latestClose: r.latest_close ?? null, hitRate: r.hit_rate ?? null, hitSample: r.hit_sample ?? null,
  verified: r.verified, ciLow: r.ci_low ?? null, ciHigh: r.ci_high ?? null, baselineExcess: r.baseline_excess ?? null,
  ciLowCorrected: r.ci_low_corrected ?? null,
  familySize: r.family_size ?? null,
  status: r.status, degradedReason: r.degraded_reason ?? null,
  resonance: r.resonance ?? 'none',
  horizonBars: r.horizon_bars ?? null,
  signalStatus: r.signal_status ?? null,
  planQuality: r.plan_quality ?? null,
});

export const stocksApi = {
  async extractFromImage(file: File): Promise<ExtractFromImageResponse> {
    const formData = new FormData();
    formData.append('file', file);

    const headers: { [key: string]: string | undefined } = { 'Content-Type': undefined };
    const response = await apiClient.post(
      '/api/v1/stocks/extract-from-image',
      formData,
      {
        headers,
        timeout: 60000, // Vision API can be slow; 60s
      },
    );

    const data = response.data as { codes?: string[]; items?: ExtractItem[]; raw_text?: string };
    return {
      codes: data.codes ?? [],
      items: data.items,
      rawText: data.raw_text,
    };
  },

  async parseImport(file?: File, text?: string): Promise<ExtractFromImageResponse> {
    if (file) {
      const formData = new FormData();
      formData.append('file', file);
      const headers: { [key: string]: string | undefined } = { 'Content-Type': undefined };
      const response = await apiClient.post('/api/v1/stocks/parse-import', formData, { headers });
      const data = response.data as { codes?: string[]; items?: ExtractItem[] };
      return { codes: data.codes ?? [], items: data.items };
    }
    if (text) {
      const response = await apiClient.post('/api/v1/stocks/parse-import', { text });
      const data = response.data as { codes?: string[]; items?: ExtractItem[] };
      return { codes: data.codes ?? [], items: data.items };
    }
    throw new Error('请提供文件或粘贴文本');
  },

  /**
   * 拉取日线 K 线，映射为 klinecharts 所需的 KLine[]。
   * 与 /signals 同源同 days（M0 暂只用 /history）；不复用 api/history.ts（分析记录域）。
   * code 经 encodeURIComponent 以兼容带 '/' 的 crypto 代码（后端 {code:path} 路由）。
   */
  async getKlineHistory(
    code: string,
    days: number = KLINE_DEFAULT_DAYS,
    period: 'daily' | 'weekly' | 'monthly' = 'daily',
  ): Promise<KLine[]> {
    const response = await apiClient.get(
      `/api/v1/stocks/${encodeURIComponent(code)}/history`,
      { params: { days, period } },
    );
    const data = response.data as { data?: KLineDataRaw[] };
    const rows = data.data ?? [];
    return rows
      .map(mapKLineDataToKLine)
      .sort((a, b) => a.timestamp - b.timestamp);
  },

  /**
   * 拉取量价信号（规则 + LLM 双轨），映射 snake_case → camelCase。
   * 与 getKlineHistory 同源同 days；degraded/空数据保持后端形状，不静默吞错。
   */
  async getSignals(code: string, days?: number): Promise<SignalsResponse> {
    const params: { days?: number } = {};
    if (days !== undefined) {
      params.days = days;
    }
    // code 经 encodeURIComponent 兼容带 '/' 的 crypto 代码（后端 {code:path} 路由），与 getKlineHistory 同源。
    const response = await apiClient.get(
      `/api/v1/stocks/${encodeURIComponent(code)}/signals`,
      { params },
    );
    const data = response.data as RawSignalsResponse;
    return {
      status: data.status,
      consistency: data.consistency,
      degradedReason: data.degraded_reason ?? null,
      priceLines: {
        entry: data.price_lines?.entry ?? null,
        stop: data.price_lines?.stop ?? null,
        target: data.price_lines?.target ?? null,
      },
      markers: (data.markers ?? []).map(mapSignalMarker),
      resonance: data.resonance ?? 'none',
      planQuality: data.plan_quality ?? null,
    };
  },

  /**
   * 拉取自选股信号看板（多股聚合），映射 snake_case → camelCase。
   * days 透传至单股信号窗口；refresh=true 时跳过后端 TTL 缓存。
   */
  async getBoard(days?: number, refresh?: boolean): Promise<SignalsBoardResponse> {
    const params: { days?: number; refresh?: boolean } = {};
    if (days !== undefined) params.days = days;
    if (refresh) params.refresh = true;
    const response = await apiClient.get('/api/v1/signals/board', { params });
    const data = response.data as RawBoardResponse;
    return {
      asOf: data.as_of,
      entries: (data.entries ?? []).map(mapBoardEntry),
      counts: data.counts,
      degradedCodes: data.degraded_codes ?? [],
    };
  },

  /**
   * 拉取个股实时行情快照，映射 snake_case → camelCase。
   * code 经 encodeURIComponent 兼容带 '/' 的 crypto 代码（后端 {code:path} 路由），与 getSignals 同源。
   */
  async getQuote(code: string): Promise<StockQuote> {
    const response = await apiClient.get(`/api/v1/stocks/${encodeURIComponent(code)}/quote`);
    const d = response.data as {
      stock_code: string; stock_name: string | null; current_price: number;
      change: number | null; change_percent: number | null; open: number | null;
      high: number | null; low: number | null; prev_close: number | null;
      volume: number | null; amount: number | null; update_time: string | null;
    };
    return {
      stockCode: d.stock_code, stockName: d.stock_name ?? null, currentPrice: d.current_price,
      change: d.change ?? null, changePercent: d.change_percent ?? null, open: d.open ?? null,
      high: d.high ?? null, low: d.low ?? null, prevClose: d.prev_close ?? null,
      volume: d.volume ?? null, amount: d.amount ?? null, updateTime: d.update_time ?? null,
    };
  },
};

/** 后端 /history 返回的单根 K 线原始形状（snake_case，date 为 'YYYY-MM-DD'）。 */
export type KLineDataRaw = {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number | null;
  amount?: number | null;
  change_percent?: number | null;
};

/**
 * 把后端 KLineData 映射为 klinecharts 所需的 KLine。
 *
 * - date 'YYYY-MM-DD'（无时区）统一锚 Asia/Shanghai 当日 00:00 → epoch ms，
 *   三市场（A股/港股/美股）一致，与 utils/format.ts 的 Asia/Shanghai 约定对齐；
 *   拼固定 +08:00 偏移而非依赖运行环境 TZ。
 * - amount → turnover；volume/amount 缺失归零（klinecharts 量副图需数值）。
 */
export const mapKLineDataToKLine = (raw: KLineDataRaw): KLine => ({
  timestamp: Date.parse(`${raw.date}T00:00:00+08:00`),
  open: raw.open,
  high: raw.high,
  low: raw.low,
  close: raw.close,
  volume: raw.volume ?? 0,
  turnover: raw.amount ?? 0,
});
