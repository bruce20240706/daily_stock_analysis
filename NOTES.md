# 工作区说明 NOTES

> 本文件记录本工作区的来源、结构与运行方式，方便后续协作。它不是上游项目自带文档，是我们本地落地时新增的导览。

## 1. 项目来源与定位

本工作区的 AI 股票分析系统**基于开源项目 [daily_stock_analysis（DSA）](https://github.com/ZhuLinsen/daily_stock_analysis) 落地**，而非从零自研。

- 上游：`ZhuLinsen/daily_stock_analysis`
- 许可：MIT License（根目录 `LICENSE` 保留上游版权声明 `Copyright (c) 2026 ZhuLinsen`，请勿移除，以符合 MIT 署名要求）
- 能力定位：A股 / 港股 / 美股自选股智能分析，每日自动抓取数据 → 技术分析 + 新闻检索 → LLM 分析 → 生成「决策仪表盘」报告 → 推送到企业微信/飞书/Telegram/Discord/Slack/邮箱。

### 为什么基于 DSA 而不是自研

最初我们自建了一个名为 `quantpick` 的骨架（选股 + 择时 + ATR 价位等）。调研后确认 DSA 已经覆盖了需求的约 90%：

- A股 + 港股（本需求核心）原生支持；
- 结合**成交量 + 趋势 + 动量**给出**买入/卖出操作建议**（与本需求“结合成交量、趋势等技术指标给出买卖建议”高度吻合）；
- 自带定时调度、多渠道推送、回测（配套项目 AlphaEvo）、全市场扫描选股（配套项目 AlphaSift）；
- 多数据源 fallback、自然语言 YAML 策略（无需写代码即可自定义策略）。

因此采用「基于成熟开源项目」而非「重复造车」。

## 2. 目录结构速览

根目录即 DSA 主体；我们自建的 `quantpick` 骨架已归档，避免与 DSA 形成平行实现。

| 路径 | 说明 |
| --- | --- |
| `main.py` | 分析任务主入口（CLI） |
| `server.py` / `webui.py` | FastAPI 后端 / Web 管理界面入口 |
| `src/` | 主流程编排、服务层、数据访问、报告、schema、LLM 客户端 |
| `data_provider/` | 多数据源适配与 fallback（akshare/baostock 等） |
| `strategies/` | 自然语言 YAML 策略（“skill”），系统启动时自动加载 |
| `api/` | FastAPI API |
| `bot/` | 机器人接入 |
| `apps/dsa-web/` · `apps/dsa-desktop/` | Web 前端 / Electron 桌面端 |
| `scripts/` · `.github/` · `docker/` | 脚本、CI/发布工作流、容器化 |
| `docs/` | 上游完整文档（入口见 `docs/INDEX.md`、`docs/full-guide.md`） |
| `AGENTS.md` / `CLAUDE.md` | 仓库 AI 协作规则（`CLAUDE.md` 是指向 `AGENTS.md` 的软链接） |
| `legacy/quantpick/` | **我们早期自研骨架的归档**（含设计文档），仅作参考，不参与运行 |

归档中值得保留的设计文档：

- `legacy/quantpick/docs/architecture.md` — 早期架构设计
- `legacy/quantpick/docs/superpowers/specs/2026-06-04-ai-stock-picker-design.md` — 选股/择时设计稿（含 ATR 价位方案）

## 3. 快速开始

### 3.1 配置 `.env`

`.env` 已根据本工作区需求自动生成（来自 `.env.example`），并默认启用 **Claude（Anthropic）** 渠道。`.env` 已被 `.gitignore` 忽略，不会进入版本库。

你**只需填入自己的 Claude API Key**：打开 `.env`，找到

```bash
LLM_ANTHROPIC_API_KEY=                  # ← 在此填入你的 Claude API Key（sk-ant-...）
```

把 Key 填在 `=` 后面即可。相关默认值：

- `STOCK_LIST=600519,300750,hk00700`（A股茅台 + 宁德时代 + 港股腾讯，可自行替换）
- `LLM_CHANNELS=anthropic`、`LITELLM_MODEL=anthropic/claude-sonnet-4-6`
- `LLM_ANTHROPIC_MODELS=claude-sonnet-4-6,claude-opus-4-8`（主模型 sonnet，opus 作为 fallback）

> 安全提示：不要把真实 API Key 写入任何会被提交的文件、日志或注释里。Key 只放在本地 `.env`。

股票代码格式：A股 6 位数字；港股 `hk` + 5 位（如 `hk00700`）；美股直接用 ticker（如 `AAPL`）。

数字货币：`BASE/QUOTE`，如 `BTC/USDT`、`ETH/USDT`（只读行情，无需 API Key）。

### 3.2 安装依赖

建议使用虚拟环境（`.venv` 已被 gitignore）：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3.3 运行

```bash
python main.py --help                          # 查看所有参数
python main.py --dry-run                        # 只抓数据，不调用 LLM（无需 Key 也能跑通数据链路）
python main.py --debug --stocks 600519,hk00700  # 调试模式，分析指定 A股 + 港股
python main.py                                  # 正常运行（按 STOCK_LIST 全量分析）
python main.py --market-review                   # 仅大盘复盘
python main.py --schedule                        # 定时任务模式
python main.py --serve --port 8000               # 启动 FastAPI 后端
python main.py --webui                           # 启动 Web 管理界面
```

## 4. 买卖建议与“成交量 + 趋势”策略

DSA 的买卖建议由 LLM agent 结合技术指标 + 策略（skill）生成。`strategies/` 下已内置可直接启用的量价/趋势类策略，例如：

- `bull_trend.yaml`（多头趋势）
- `ma_golden_cross.yaml`（均线金叉 / 多头排列）
- `volume_breakout.yaml`（放量突破）
- `shrink_pullback.yaml`（缩量回踩）
- `bottom_volume.yaml`（底部放量）

启用方式（在 `.env` 中设置，默认 `AGENT_SKILLS=` 为空表示按 `AGENT_SKILL_ROUTING=auto` 自动路由）：

```bash
# 手动指定一组结合“成交量 + 趋势”的策略
AGENT_SKILLS=bull_trend,ma_golden_cross,volume_breakout,shrink_pullback
# 或启用全部内置策略
# AGENT_SKILLS=all
```

自定义策略**无需写代码**：在 `strategies/`（或 `AGENT_SKILL_DIR` 指定目录）新增一个 `.yaml`，用自然语言描述入场/出场条件即可。模板见 `strategies/README.md`。

## 5. 协作与验证约定

- 提交、文档、注释、docstring 使用**中文**（专业英文术语保留英文）；代码标识符用英文。
- commit message 用中文，遵循 `<类型>: <修改内容>`，**不添加** `Co-Authored-By`。
- 未经确认不执行 `git commit` / `git tag` / `git push`。
- 后端改动验证优先执行 `./scripts/ci_gate.sh`，最低 `python -m py_compile <改动文件>`。
- 更完整的规则见 `AGENTS.md`。
