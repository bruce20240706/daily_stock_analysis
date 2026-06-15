import apiClient from './index';
import type { KLine } from '../types/kline';

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
