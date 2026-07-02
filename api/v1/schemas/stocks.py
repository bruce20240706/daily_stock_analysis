# -*- coding: utf-8 -*-
"""
===================================
股票数据相关模型
===================================

职责：
1. 定义股票实时行情模型
2. 定义历史 K 线数据模型
"""

from typing import Literal, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class StockQuote(BaseModel):
    """股票实时行情"""
    
    stock_code: str = Field(..., description="股票代码")
    stock_name: Optional[str] = Field(None, description="股票名称")
    current_price: float = Field(..., description="当前价格")
    change: Optional[float] = Field(None, description="涨跌额")
    change_percent: Optional[float] = Field(None, description="涨跌幅 (%)")
    open: Optional[float] = Field(None, description="开盘价")
    high: Optional[float] = Field(None, description="最高价")
    low: Optional[float] = Field(None, description="最低价")
    prev_close: Optional[float] = Field(None, description="昨收价")
    volume: Optional[float] = Field(None, description="成交量（股）")
    amount: Optional[float] = Field(None, description="成交额（元）")
    update_time: Optional[str] = Field(None, description="更新时间")
    
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "stock_code": "600519",
            "stock_name": "贵州茅台",
            "current_price": 1800.00,
            "change": 15.00,
            "change_percent": 0.84,
            "open": 1785.00,
            "high": 1810.00,
            "low": 1780.00,
            "prev_close": 1785.00,
            "volume": 10000000,
            "amount": 18000000000,
            "update_time": "2024-01-01T15:00:00"
        }
    })


class KLineData(BaseModel):
    """K 线数据点"""
    
    date: str = Field(..., description="日期")
    open: float = Field(..., description="开盘价")
    high: float = Field(..., description="最高价")
    low: float = Field(..., description="最低价")
    close: float = Field(..., description="收盘价")
    volume: Optional[float] = Field(None, description="成交量")
    amount: Optional[float] = Field(None, description="成交额")
    change_percent: Optional[float] = Field(None, description="涨跌幅 (%)")
    
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "date": "2024-01-01",
            "open": 1785.00,
            "high": 1810.00,
            "low": 1780.00,
            "close": 1800.00,
            "volume": 10000000,
            "amount": 18000000000,
            "change_percent": 0.84
        }
    })


class ExtractItem(BaseModel):
    """单条提取结果（代码、名称、置信度）"""

    code: Optional[str] = Field(None, description="股票代码，None 表示解析失败")
    name: Optional[str] = Field(None, description="股票名称（如有）")
    confidence: str = Field("medium", description="置信度：high/medium/low")


class ExtractFromImageResponse(BaseModel):
    """图片股票代码提取响应"""

    codes: List[str] = Field(..., description="提取的股票代码（已去重，向后兼容）")
    items: List[ExtractItem] = Field(default_factory=list, description="提取结果明细（代码+名称+置信度）")
    raw_text: Optional[str] = Field(None, description="原始 LLM 响应（调试用）")


class StockHistoryResponse(BaseModel):
    """股票历史行情响应"""
    
    stock_code: str = Field(..., description="股票代码")
    stock_name: Optional[str] = Field(None, description="股票名称")
    period: str = Field(..., description="K 线周期")
    data: List[KLineData] = Field(default_factory=list, description="K 线数据列表")
    
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "stock_code": "600519",
            "stock_name": "贵州茅台",
            "period": "daily",
            "data": []
        }
    })


class SignalMarker(BaseModel):
    """图上买卖信号标注点（追加契约，不影响旧 SniperPoints）"""

    timestamp: int = Field(..., description="epoch ms，唯一权威时间锚（前端按 timestamp 匹配蜡烛）")
    price: float = Field(..., description="标注价位")
    anchor: Literal["low", "high", "close"] = Field(..., description="price 锚定语义")
    direction: Literal["bullish", "bearish", "neutral"] = Field(..., description="信号方向")
    signal_type: str = Field(..., description="信号类型，如 volume_breakout / obv_top_divergence / llm_advice")
    source: Literal["rule", "llm"] = Field(..., description="信号来源")
    confidence: Literal["high", "medium", "low"] = Field(..., description="置信度（可排序，B 类<=low）")
    is_daily_approx: bool = Field(..., description="是否日线近似（VSA 等 B 类为 True）")
    is_anomalous: bool = Field(..., description="是否处于一字板/涨跌停等异常 bar")
    reason: str = Field(..., description="触发依据文案（用于钻取）")
    threshold: Optional[float] = Field(None, description="触发阈值，无则 null")
    observed_value: Optional[float] = Field(None, description="实际观测值，无则 null")
    hit_rate: Optional[float] = Field(None, description="历史方向命中率（M2c 回填），无样本则 null")
    hit_sample: Optional[int] = Field(None, description="命中率样本数（M2c 回填），无则 null")
    verified: bool = Field(False, description="hit_sample 达阈值且校正后下界 ci_low_corrected(family-wise Bonferroni;legacy 行回退 ci_low)> baseline 则 True(M3-A6/Inc 1c)")
    ci_low: Optional[float] = Field(None, description="命中率置信区间下界（M3-A6），无则 null")
    ci_high: Optional[float] = Field(None, description="命中率置信区间上界（M3-A6），无则 null")
    baseline_excess: Optional[float] = Field(None, description="相对基准超额（M3-A6），无则 null")
    ci_low_corrected: Optional[float] = Field(None, description="family-wise 多重检验校正后的 CI 下界(Inc 1c);null=legacy 行未重跑或无样本")
    family_size: Optional[int] = Field(None, description="该统计所在 family 的同检格子数 N(Inc 1c);null=legacy 行")
    as_of: Optional[int] = Field(None, description="仅 source=llm：该 LLM 结论生成时间 epoch ms")
    horizon_bars: Optional[int] = Field(None, description="该信号 hit_rate/CI 的验证前看窗口(bar 数);仅 rule、命中 signal_stats 时有值")
    status: Optional[Literal["active", "aging", "expired"]] = Field(None, description="信号生命周期;仅 rule。active=最新bar/aging=窗口内/expired=窗口已过")


class PriceLines(BaseModel):
    """买卖价位线（M2a 全 null，M2b 由价位反算器填值）"""

    entry: Optional[float] = Field(None, description="入场价位线，反算不出为 null")
    stop: Optional[float] = Field(None, description="止损价位线，反算不出为 null")
    target: Optional[float] = Field(None, description="目标价位线，反算不出为 null")


class SignalsResponse(BaseModel):
    """/signals 端点响应契约"""

    status: Literal["ok", "degraded"] = Field(..., description="整体状态；degraded 仍返回 200 + 部分结果")
    markers: List[SignalMarker] = Field(default_factory=list, description="信号标注点（rule 逐 bar + LLM 最新 1 点）")
    price_lines: PriceLines = Field(default_factory=PriceLines, description="买卖价位线（子字段允许 null）")
    consistency: Literal["consistent", "divergent", "conflict", "unknown", "stale"] = Field(
        ..., description="量价/规则与 LLM 一致性，仅在 LLM 点邻域计算"
    )
    degraded_reason: Optional[str] = Field(None, description="status=degraded 时的原因说明")
    resonance: Literal["none", "weekly", "weekly_monthly"] = Field(
        "none", description="多周期共振档位（M4-A）：高周期趋势与日线信号同向"
    )
    plan_quality: Optional[Literal["high", "medium", "low"]] = Field(None, description="交易计划质量:price_lines 完整度 + consistency;无 price_lines→null")

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "status": "ok",
            "markers": [],
            "price_lines": {"entry": None, "stop": None, "target": None},
            "consistency": "consistent",
            "degraded_reason": None,
            "resonance": "none",
            "plan_quality": None,
        }
    })


class BoardEntry(BaseModel):
    """信号看板单行条目（容器 C / N1，追加契约）"""

    code: str = Field(..., description="股票代码")
    name: Optional[str] = Field(None, description="股票名称")
    market: Optional[str] = Field(None, description="市场，如 A/HK/US")
    action_group: Literal["buy", "hold", "sell", "unavailable"] = Field(
        ..., description="动作分组（看板分栏依据）"
    )
    rule_direction: Optional[Literal["bullish", "bearish", "neutral"]] = Field(
        None, description="规则/量价方向，无则 null"
    )
    llm_direction: Optional[Literal["bullish", "bearish", "neutral"]] = Field(
        None, description="LLM 方向，无则 null"
    )
    consistency: Literal["consistent", "divergent", "conflict", "unknown", "stale"] = Field(
        ..., description="规则与 LLM 一致性"
    )
    key_signals: List[str] = Field(default_factory=list, description="关键信号类型列表")
    price_lines: PriceLines = Field(..., description="买卖价位线（子字段允许 null）")
    latest_close: Optional[float] = Field(None, description="最新收盘价，无则 null")
    hit_rate: Optional[float] = Field(None, description="历史方向命中率，无样本则 null")
    hit_sample: Optional[int] = Field(None, description="命中率样本数，无则 null")
    verified: bool = Field(False, description="hit_sample 达阈值且校正后下界 ci_low_corrected(family-wise Bonferroni;legacy 行回退 ci_low)> baseline 则 True(M3-A6/Inc 1c)")
    ci_low: Optional[float] = Field(None, description="命中率置信区间下界（M3-A6），无则 null")
    ci_high: Optional[float] = Field(None, description="命中率置信区间上界（M3-A6），无则 null")
    baseline_excess: Optional[float] = Field(None, description="相对基准超额（M3-A6），无则 null")
    ci_low_corrected: Optional[float] = Field(None, description="family-wise 多重检验校正后的 CI 下界(Inc 1c);null=legacy 行未重跑或无样本")
    family_size: Optional[int] = Field(None, description="该统计所在 family 的同检格子数 N(Inc 1c);null=legacy 行")
    resonance: Literal["none", "weekly", "weekly_monthly"] = Field(
        "none", description="多周期共振档位（M4-A）"
    )
    status: Literal["ok", "degraded"] = Field(..., description="单条状态")
    degraded_reason: Optional[str] = Field(None, description="status=degraded 时的原因说明")
    horizon_bars: Optional[int] = Field(None, description="代表信号的验证前看窗口(bar 数),与本行 hit_rate 同源")
    signal_status: Optional[Literal["active", "aging", "expired"]] = Field(None, description="代表信号的生命周期(区别于 status 的 ok/degraded)")
    plan_quality: Optional[Literal["high", "medium", "low"]] = Field(None, description="交易计划质量(该股响应级)")


class BoardCounts(BaseModel):
    """看板各动作分组计数（容器 C / N1）"""

    buy: int = Field(0, description="buy 分组数量")
    hold: int = Field(0, description="hold 分组数量")
    sell: int = Field(0, description="sell 分组数量")
    unavailable: int = Field(0, description="unavailable 分组数量")


class SignalsBoardResponse(BaseModel):
    """信号看板端点响应契约（容器 C / N1）"""

    as_of: int = Field(..., description="看板数据基准时间 epoch ms")
    entries: List[BoardEntry] = Field(default_factory=list, description="看板条目列表")
    counts: BoardCounts = Field(..., description="各动作分组计数")
    degraded_codes: List[str] = Field(default_factory=list, description="降级的股票代码列表")
