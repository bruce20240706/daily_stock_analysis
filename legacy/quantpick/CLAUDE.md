# 项目级开发约定（QuantPick）

> 本文件供后续 AI / 开发会话快速建立上下文。全局规则见 `~/.claude/CLAUDE.md`。

## 项目定位
个人离线 A 股 / 港股选股工具。**规则量化打分为地基 + 可插拔 AI(LLM) 增强**，输出每日候选股清单 + 评分 + 理由，并结合量价/趋势给出**买卖操作建议（择时）**。**输出不构成投资建议。**

## 技术栈
Python 3.10+ ｜ pandas / numpy / pandas-ta ｜ pydantic / pyyaml ｜ typer / rich ｜ pyarrow(parquet) + sqlite3 ｜ akshare(主) + baostock/adata(可选) ｜ anthropic + openai 兼容(可选 AI 层)

## 架构（分层，单向依赖）
`data → indicators → factors → screening → strategies → signals → ai → backtest → report`，外加 `portfolio`(持仓上下文)；入口 `quantpick/cli.py`。
- core: 配置 / 交易日历 / 类型 / 错误码 / 日志
- data: `DataSource` 接口 + akshare/baostock/adata 实现 + `DataManager`(多源降级、缓存、增量)
- indicators: pandas-ta 封装，**纯函数无 I/O**
- factors: 指标 + 基本面 → 截面标准化因子（z-score / 分位），可插拔注册表
- screening: 硬过滤 + 多因子加权打分 + 排序
- strategies: YAML 定义的具名策略（过滤 + 因子权重 + Top-N）
- signals: 择时层——trend 趋势过滤(闸门) + triggers 量价/动量触发 + levels 价位(ATR+摆动点) + engine 编排；产出 BUY/SELL/HOLD + 强度(0-100) + 参考价位
- portfolio: 自选/持仓(读 `config/holdings.yaml`)，卖出信号据此评估
- ai: `Analyst` 接口 + `LLMAnalyst`；预留 `MLRanker`
- backtest: 前向收益 / 胜率 / 超额，轻量验证（非撮合级）；亦用于验证信号有效性
- report: CLI 表格 + Markdown 报告（含"操作建议"区）

## 编码约定（重要）
- **代码 / 注释 / docstring / 标识符 / 提交信息：英文**（与全局规则一致）。
- **错误处理**：在数据获取 / 批处理**边界**用 `core/errors.py` 的 `Result` / 错误码——单只股票失败不中断整批、可降级；内部纯逻辑可用异常。
- **惰性导入**：重依赖（akshare / baostock / adata / anthropic / talib）必须在函数体内 import，保证 `import quantpick` 与 `quantpick --help` 只需轻量依赖。
- 全文件 `from __future__ import annotations`；类型注解齐全；indicators/factors/signals 计算保持纯函数，便于单测。
- **存储**：行情用 parquet、元数据用 SQLite，**不引入 MySQL**。
- **配置驱动**：策略 / 因子权重 / 信号阈值写在 `config/*.yaml`，改参数优先改配置而非代码。
- **密钥**：仅从环境变量 / `.env` 读取，**绝不出现在代码 / 日志 / 测试的字面量中**。
- **持仓隐私**：`config/holdings.yaml` 含个人持仓，已 gitignore；仓库只跟踪 `holdings.example.yaml`。

## 当前状态
阶段零（骨架）已完成，并已追加**择时/信号层 + 持仓模块**骨架：接口、类型契约、配置(`config/signals.yaml` / `holdings.example.yaml`)、`advise` 命令、测试均就位，业务实现待填（见各文件 `TODO`）。下一步：阶段一 MVP + 择时层实现（见 `docs/superpowers/specs/`）。

## 常用命令
```bash
pip install -e ".[dev]"      # 安装(含开发工具)
pytest                       # 跑测试
ruff check quantpick         # 静态检查
quantpick --help             # 看命令
quantpick run                # 一键流程
```
