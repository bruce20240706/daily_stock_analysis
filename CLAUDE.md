# 项目级开发约定（QuantPick）

> 本文件供后续 AI / 开发会话快速建立上下文。全局规则见 `~/.claude/CLAUDE.md`。

## 项目定位
个人离线 A 股 / 港股选股工具。**规则量化打分为地基 + 可插拔 AI(LLM) 增强**，输出每日候选股清单 + 评分 + 理由。**输出不构成投资建议。**

## 技术栈
Python 3.10+ ｜ pandas / numpy / pandas-ta ｜ pydantic / pyyaml ｜ typer / rich ｜ pyarrow(parquet) + sqlite3 ｜ akshare(主) + baostock/adata(可选) ｜ anthropic + openai 兼容(可选 AI 层)

## 架构（分层，单向依赖）
`data → indicators → factors → screening → strategies → ai → backtest → report`，入口 `quantpick/cli.py`。
- core: 配置 / 交易日历 / 类型 / 日志 / 错误码
- data: `DataSource` 接口 + akshare/baostock/adata 实现 + `DataManager`(多源降级、缓存、增量)
- indicators: pandas-ta 封装，**纯函数无 I/O**
- factors: 指标 + 基本面 → 截面标准化因子（z-score / 分位），可插拔注册表
- screening: 硬过滤 + 多因子加权打分 + 排序
- strategies: YAML 定义的具名策略（过滤 + 因子权重 + Top-N）
- ai: `Analyst` 接口 + `LLMAnalyst`；预留 `MLRanker`
- backtest: 前向收益 / 胜率 / 超额，轻量验证（非撮合级）
- report: CLI 表格 + Markdown 报告

## 编码约定（重要）
- **代码 / 注释 / docstring / 标识符 / 提交信息：英文**（与全局规则一致）。
- **错误处理**：在数据获取 / 批处理**边界**用 `core/errors.py` 的 `Result` / 错误码——单只股票失败不中断整批、可降级；内部纯逻辑可用异常。
- **惰性导入**：重依赖（akshare / baostock / adata / anthropic / talib）必须在函数体内 import，保证 `import quantpick` 与 `quantpick --help` 只需轻量依赖。
- 全文件 `from __future__ import annotations`；类型注解齐全；indicators/factors 保持纯函数，便于单测。
- **存储**：行情用 parquet、元数据用 SQLite，**不引入 MySQL**。
- **配置驱动**：策略 / 因子权重写在 `config/*.yaml`，改策略优先改配置而非代码。
- **密钥**：仅从环境变量 / `.env` 读取，**绝不出现在代码 / 日志 / 测试的字面量中**。

## 当前状态
阶段零（骨架）已完成：接口、类型契约、配置、CLI 命令壳、示例策略、测试骨架均就位，业务实现待填（见各文件 `TODO`）。下一步：阶段一 MVP（见 `docs/superpowers/specs/`）。

## 常用命令
```bash
pip install -e ".[dev]"      # 安装(含开发工具)
pytest                       # 跑测试
ruff check quantpick         # 静态检查
quantpick --help             # 看命令
quantpick run                # 一键流程
```
