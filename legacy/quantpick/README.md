# QuantPick · AI 自动选股系统（个人版）

> 面向中国 **A 股**与**港股**的个人离线选股研究工具：以规则量化 + 技术指标打分为地基，叠加可插拔的 AI（LLM）定性分析，每日产出**带评分和理由的候选股清单**。

---

## ✨ 特性

- **多数据源容错**：akshare（主，A 股 + 港股）+ baostock（A 股历史备份）+ adata（A 股容错补充），本地 `parquet` + `SQLite` 缓存，增量更新。
- **丰富技术指标**：基于 `pandas-ta`（可选 `TA-Lib` 加速），覆盖趋势 / 动量 / 量价 / 波动 / K 线形态。
- **多因子打分选股**：硬过滤 + 因子加权打分 + 排序；**每个评分都能拆解到因子，完全可解释**，不做黑盒。
- **配置驱动策略**：一个策略 = 一份 YAML（过滤条件 + 因子权重），加策略不改代码。
- **可插拔 AI 增强**：对候选股用 LLM（Claude / DeepSeek / 通义 / 本地 Ollama）生成结构化分析卡；预留 ML 排序接口。
- **轻量回测**：用历史选股的前向收益 / 胜率 / 相对基准超额来验证策略有效性。
- **多种输出**：CLI 富文本表格 + Markdown 每日报告。

## 🏗️ 数据流

```
update : 多源拉取 ─▶ 本地 parquet/sqlite 缓存
   │
screen : 指标计算 ─▶ 因子加工 ─▶ 硬过滤 ─▶ 多因子打分 ─▶ 排序 ─▶ Top-N
   │
ai     : 对 Top-N 生成 LLM 结构化分析卡（可选）
   │
report : CLI 表格 + Markdown 报告
   │
backtest: 用历史选股验证胜率 / 超额（可选）
```

## 📁 目录结构

```
quantpick/
├── core/        配置 / 交易日历 / 类型 / 日志 / 错误码
├── data/        多源数据 adapter + 本地缓存（parquet + sqlite）
├── indicators/  技术指标计算（pandas-ta 封装，纯函数）
├── factors/     指标 + 基本面 → 标准化可比因子
├── screening/   硬过滤 + 多因子加权打分 + 排序
├── strategies/  具名策略（YAML：过滤 + 因子权重）
├── ai/          AI 增强层（LLM 定性分析，预留 ML 接口）
├── backtest/    前向收益回测，验证选股有效性
├── report/      CLI 表格 + Markdown 报告
└── cli.py       命令行入口
config/          默认配置与示例策略
docs/            架构文档与设计方案
tests/           pytest 测试
```

## 🚀 快速开始

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"                 # 核心依赖 + 开发工具
pip install -e ".[ai,baostock,adata]"   # 可选：AI 层 + 备份数据源
cp .env.example .env                    # 如启用 AI 层，填入你自己的 key

quantpick --help
quantpick update                        # 增量拉取行情到本地缓存
quantpick screen --strategy ma_trend    # 按策略选股
quantpick run                           # 一键：更新 → 选股 →（AI）→ 报告
```

> 说明：当前为**项目骨架**（阶段零）。各模块已就位接口、类型契约与配置，业务逻辑待按路线图实现（见各文件 `TODO`）。

## ⚙️ 配置

- `config/config.yaml`：数据源、universe（市场范围）、缓存路径、AI 设置。
- `config/strategies/*.yaml`：具名策略（过滤条件 + 因子权重 + Top-N）。
- `config/factors.yaml`：可用因子清单与默认参数。

## 🗺️ 路线图

- [x] **阶段零**：项目骨架 / 接口 / 类型契约 / 配置
- [ ] **阶段一（MVP）**：数据层 + 指标 + 因子 + 选股打分 + CLI 表格输出
- [ ] **阶段二**：回测验证 + Markdown 报告 + 更多策略
- [ ] **阶段三**：AI（LLM）增强层
- [ ] **阶段四（可选）**：港股深化 / 消息推送 / ML 排序 / C++ 加速热点计算

## 🙏 数据源与致谢

借鉴或使用的开源项目：[akshare](https://github.com/akfamily/akshare)、[baostock](http://baostock.com)、[adata](https://github.com/1nchaos/adata)、[pandas-ta](https://github.com/twopirllc/pandas-ta)、[InStock](https://github.com/ethqunzhong/InStock)、[QuantsPlaybook](https://github.com/hugo2046/QuantsPlaybook)、[daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis)、[Microsoft qlib](https://github.com/microsoft/qlib)。

## ⚠️ 免责声明

本项目仅用于**个人学习与量化研究**。所有输出（评分、清单、分析、建议）**不构成任何投资建议**，亦不保证准确性或盈利性。据此操作的风险由使用者自行承担。**股市有风险，投资需谨慎。**
