import apiClient from './index';
import type { KLine } from '../types/kline';
import type { SignalMarker, SignalsResponse } from '../types/kline';

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
  as_of: number | null;
};

type RawSignalsResponse = {
  status: SignalsResponse['status'];
  consistency: SignalsResponse['consistency'];
  degraded_reason: string | null;
  price_lines: { entry: number | null; stop: number | null; target: number | null } | null;
  markers: RawSignalMarker[] | null;
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
  asOf: raw.as_of ?? null,
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
  async getKlineHistory(code: string, days: number = KLINE_DEFAULT_DAYS): Promise<KLine[]> {
    const response = await apiClient.get(
      `/api/v1/stocks/${encodeURIComponent(code)}/history`,
      { params: { days } },
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
