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
