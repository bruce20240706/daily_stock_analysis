# 链路B verified 多重检验校正(Inc 1c)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给链路B `verified` 加 family-wise(Bonferroni-CI)多重检验校正,并全栈透出 `ci_low_corrected` + `family_size` 两个透明字段,消解"raw 超额却未验证"的矛盾展示。

**Architecture:** 写时(aggregate)按 family N 收紧 z 计算 `ci_low_corrected` 落库;读时 verified 改用校正下界(老行 NULL 回退 raw);两字段经 resolver→marker→board→Pydantic schema→前端 mapper/组件 全链 6 层透出。默认 byte-identical(N≤1 用字面量 1.96)。

**Tech Stack:** Python(statistics.NormalDist,stdlib 无新依赖)/ SQLAlchemy+SQLite 幂等补列 / Pydantic v2 / React+TS+vitest。

**Spec:** `docs/superpowers/specs/2026-07-01-chainb-multiple-testing-design.md`(两轮对抗审查定稿,本 plan 按其 §4-§7 落地)。

## Global Constraints

- **工作区**:主仓路径含空格,执行前用 git worktree 建无空格持久路径 `/root/chainb-mtc`(勿用 /tmp);venv 用主仓 `.venv`,所有 python/pytest 命令前置 `PATH="/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin:$PATH"`(下文简写 `$PY_PATH`);ci_gate.sh 同理前置,**勿用 `| tail` 掩盖退出码**。
- **commit**:英文类型前缀 + 中文描述体,**不加 Co-Authored-By**;每 task 一 commit。
- **byte-identical 默认**:N≤1 时 z 必用**字面量 `1.96`**,禁用 `inv_cdf(0.975)`(=1.9599639…,第 5 位差开会翻 verified)。
- **alpha 域 [0.0001, 0.05]**:loader 钳制 + aggregate 纯函数内二次钳制("校正只收紧"不可绕过;0/极小 alpha 会致 `inv_cdf(1.0)` StatisticsError)。
- **DB 新列 plain nullable 无 default**(house style 同 `ci_low`/`excess`;老行 NULL=legacy 哨兵)。
- **resolver 返回 dict 键集一致**:`_none` 分支与正常分支都含 `ci_low_corrected`/`family_size` 两 key。
- **`.env.example` 用注释行样例** `# SIGNAL_BACKTEST_FWER_ALPHA=0.05`(实际仓库惯例,见 `.env.example:703-705` 既有 SIGNAL_BACKTEST_* 均为注释行;spec §4.7"裸 KEY"措辞与仓库惯例不符,以惯例为准)。
- **config_registry `display_order=71`**(已核验 backtest 类占用:10/20/30/40/50/60/70/80/81…,71 空)。
- **alpha 文案**:是"双尾族水平(等价单尾≈alpha/2)",**不得**写"5% 假阳率"(spec M-2)。
- 不 push、不 tag;合并方式待用户确认。

---

### Task 1: 统计核心 — Bonferroni z + aggregate 双遍 + SignalStat 追加字段

**Files:**
- Modify: `src/services/signal_backtest.py`(imports:22 区、`SignalStat`:232-243、`wilson_ci`:246 之后新增函数、`aggregate_signal_stats`:271-341)
- Test: `tests/test_signal_backtest_stats.py`(文件末尾追加)

**Interfaces:**
- Produces: `bonferroni_z(family_n: int, fwer_alpha: float = 0.05) -> float`(模块级纯函数);`aggregate_signal_stats(..., fwer_alpha: float = 0.05, min_sample: int = 10)` 新 keyword 参数;`SignalStat.ci_low_corrected: Optional[float]`、`SignalStat.family_size: int`(追加末尾带默认,既有位置/关键字构造不破)。
- Consumes: 既有 `wilson_ci(wins, n, z=1.96)`、`SignalOutcome`。

- [ ] **Step 1: 写失败测试**(追加到 `tests/test_signal_backtest_stats.py` 末尾)

```python
# =====================================================================
# Inc 1c: family-wise Bonferroni-CI 多重检验校正(spec §4.2/§4.3/§7)
# =====================================================================
from src.services.signal_backtest import bonferroni_z


def _cells(*spec_triples):
    """(signal_type, win, loss) 列表 → cn 市场 SignalOutcome 列表。"""
    outs = []
    for st, w, l in spec_triples:
        outs += [SignalOutcome(st, "cn", "win")] * w + [SignalOutcome(st, "cn", "loss")] * l
    return outs


_BASE_CN_50 = [SignalOutcome("__baseline__", "cn", "win")] * 5 + \
              [SignalOutcome("__baseline__", "cn", "loss")] * 5   # baseline=0.50


def test_bonferroni_z_values():
    # N<=1 → 字面量 1.96(byte-identical 承重;禁 inv_cdf(0.975)=1.95996…)
    assert bonferroni_z(0) == 1.96
    assert bonferroni_z(1) == 1.96
    # N=2/20 双尾均摊 pin(手算 NormalDist().inv_cdf)
    assert abs(bonferroni_z(2) - 2.2414027276049464) < 1e-12
    assert abs(bonferroni_z(20) - 3.0233414397391534) < 1e-12


def test_corrected_equals_raw_for_single_cell_family():
    """§7.1 退化 N=1 byte-identical:win=6/sample=8(0<win<sample)钉死判别式。

    wilson_ci(6,8,1.96)[0]=0.40926987… 与 wilson_ci(6,8,inv_cdf(0.975))[0]=0.40927543…
    第 5 位差开 → 精确 == 真能证伪"N≤1 误用 inv_cdf(0.975)"。win=0 clamp 到 0 恒等,禁用。
    """
    stats = aggregate_signal_stats(
        _cells(("volume_breakout", 6, 2)), _BASE_CN_50, horizon=10, min_sample=5)
    s = stats[0]
    assert s.family_size == 1
    assert s.ci_low_corrected == s.ci_low                       # 精确 ==,非 approx
    assert abs(s.ci_low_corrected - 0.40926987910258916) < 1e-12  # pin 1.96 字面量


def test_family_zero_no_crash_and_degenerates():
    """§7.5 N=0:全格 sample<min_sample → 不崩、family_size=0、校正值==raw。"""
    stats = aggregate_signal_stats(
        _cells(("a", 2, 2), ("b", 3, 1)), _BASE_CN_50, horizon=10,
        min_sample=10, fwer_alpha=0.0001)
    assert all(s.family_size == 0 for s in stats)
    for s in stats:
        assert s.ci_low_corrected == s.ci_low       # N=0 → z=1.96 → 恒等
        assert s.ci_low_corrected is not None


def test_correction_bites_at_n20():
    """§7.2 咬合:N=20、边界格子 raw>baseline 但校正后≤baseline;强格子仍>。"""
    fillers = [(f"filler_{i}", 5, 5) for i in range(18)]        # 18×sample=10
    outs = _cells(*fillers, ("edge", 15, 5), ("strong", 90, 10))  # N=20
    stats = aggregate_signal_stats(outs, _BASE_CN_50, horizon=10, min_sample=10)
    by = {s.signal_type: s for s in stats}
    assert by["edge"].family_size == 20
    z20 = bonferroni_z(20)
    # 边界格子:raw 0.5313 > 0.5 但 corr 0.4167 ≤ 0.5(手算 pin)
    assert by["edge"].ci_low > 0.5
    assert by["edge"].ci_low_corrected <= 0.5
    assert abs(by["edge"].ci_low_corrected - wilson_ci(15, 20, z20)[0]) < 1e-12
    # 强格子:校正后仍 > baseline(0.7734)
    assert by["strong"].ci_low_corrected > 0.5


def test_correction_bites_at_n2():
    """§7.3 N=2 首次生效点:z=2.2414,同一边界格子 corr 0.4994 ≤ 0.5。"""
    outs = _cells(("filler_0", 5, 5), ("edge", 15, 5))
    stats = aggregate_signal_stats(outs, _BASE_CN_50, horizon=10, min_sample=10)
    edge = next(s for s in stats if s.signal_type == "edge")
    assert edge.family_size == 2
    assert edge.ci_low > 0.5 and edge.ci_low_corrected <= 0.5
    assert abs(edge.ci_low_corrected - wilson_ci(15, 20, bonferroni_z(2))[0]) < 1e-12


def test_alpha_cap_only_tightens_incl_out_of_domain():
    """§7.4 "只收紧"不变式:域内遍历 + 域外 0.5 被纯函数内钳制(不可绕过)。"""
    for n in (2, 5, 20):
        for alpha in (0.0001, 0.01, 0.05, 0.5):     # 0.5 为域外,应钳到 0.05
            z = bonferroni_z(n, alpha)
            assert z >= 1.96, f"反转陷阱: n={n} alpha={alpha} z={z}"
    assert bonferroni_z(2, 0.5) == bonferroni_z(2, 0.05)     # 域外钳制到上限
    assert bonferroni_z(2, 0.0) == bonferroni_z(2, 0.0001)   # 0 钳到下限,不进 inv_cdf(1.0)
    # aggregate 级:每格 ci_low_corrected ≤ ci_low
    outs = _cells(*[(f"t{i}", 6, 4) for i in range(5)])
    for s in aggregate_signal_stats(outs, _BASE_CN_50, horizon=10, min_sample=10):
        assert s.ci_low_corrected <= s.ci_low


def test_alpha_monotone_stricter_when_smaller():
    """§7.10 域内单调:alpha 越小 → z 越大 → 校正下界越低。"""
    outs = _cells(*[(f"t{i}", 6, 4) for i in range(9)], ("probe", 15, 5))
    lows = []
    for alpha in (0.05, 0.01, 0.0001):
        stats = aggregate_signal_stats(outs, _BASE_CN_50, horizon=10,
                                       min_sample=10, fwer_alpha=alpha)
        lows.append(next(s for s in stats if s.signal_type == "probe").ci_low_corrected)
    assert lows[0] > lows[1] > lows[2]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/chainb-mtc && PATH="..." python -m pytest tests/test_signal_backtest_stats.py -x -q`
Expected: FAIL — `ImportError: cannot import name 'bonferroni_z'`

- [ ] **Step 3: 实现**(`src/services/signal_backtest.py`)

imports 区(`:22` 邻位)追加:

```python
from statistics import NormalDist
```

`SignalStat` dataclass 末尾(`:243` `excess` 之后)追加,并在类 docstring 字段表补两行:

```python
    ci_low_corrected: Optional[float] = None  # family-wise 校正后 Wilson 下界;N<=1 时==ci_low;sample==0 时 None
    family_size: int = 0                      # 本次聚合可检验格子数 N(sample>=min_sample 的格子数)
```

`wilson_ci` 之后新增模块级函数:

```python
def bonferroni_z(family_n: int, fwer_alpha: float = 0.05) -> float:
    """family-wise Bonferroni 校正后的 Wilson z 值。

    N<=1 返回字面量 1.96(与 wilson_ci 默认 z 逐字节一致,保证退化恒等;
    不得改用 NormalDist().inv_cdf(0.975)=1.95996…,razor-edge 下会翻 verified)。
    N>=2 按双尾族水平 alpha 均摊:z = inv_cdf(1 - (alpha/2)/N)。
    alpha 在函数内钳制到 [0.0001, 0.05]:上限 0.05 保证 z >= 2.2414 > 1.96
    ("校正只收紧"不可绕过);下限 0.0001 防 inv_cdf(1.0) StatisticsError。
    注意 alpha 是双尾口径,等价单尾族错误率约 alpha/2(verified 为单尾判据)。
    """
    if family_n <= 1:
        return 1.96
    alpha = min(max(float(fwer_alpha), 0.0001), 0.05)
    return NormalDist().inv_cdf(1.0 - (alpha / 2.0) / family_n)
```

`aggregate_signal_stats`:签名加两 keyword 参数,docstring Args 补两条 + 生产必传提示(M-6),Step 3 拆两遍:

```python
def aggregate_signal_stats(
    outcomes: List[SignalOutcome],
    baseline_outcomes: List[SignalOutcome],
    *,
    horizon: int,
    interval: str = "1d",
    fwer_alpha: float = 0.05,
    min_sample: int = 10,
) -> List[SignalStat]:
```

docstring Args 追加(生产必传措辞照抄):

```
        fwer_alpha:        family-wise 双尾族水平(Bonferroni-CI 校正),函数内钳制到
                           [0.0001, 0.05]。默认 0.05 仅供纯函数独测;生产路径必须显式传
                           config.signal_backtest_fwer_alpha。
        min_sample:        可检验格子的最小样本阈值(family N 的口径)。默认 10 仅供独测;
                           生产路径必须显式传 resolve_verified_min_sample(cfg),
                           否则 N 与读路径 verified 判据漂移。
```

Step 3 主体替换为(Step 1/2 不动):

```python
    # Step 3a: 先算各格 win/loss/sample(第一遍,确定 family N)
    cells = []
    for (sig_type, market), wl in buckets.items():
        win = wl["win"]
        loss = wl["loss"]
        cells.append((sig_type, market, win, loss, win + loss))

    # Step 3b: family N = sample >= min_sample 的格子数;z_corr 每 family 只算一次
    family_n = sum(1 for _, _, _, _, sample in cells if sample >= min_sample)
    z_corr = bonferroni_z(family_n, fwer_alpha)

    # Step 3c: 构造 SignalStat(第二遍;raw ci_low/ci_high/excess 计算与现状逐字节一致)
    stats: List[SignalStat] = []
    for sig_type, market, win, loss, sample in cells:
        wr = _winrate(win, sample)
        if sample > 0:
            ci_low, ci_high = wilson_ci(win, sample)
            ci_low_corrected: Optional[float] = wilson_ci(win, sample, z_corr)[0]
        else:
            ci_low, ci_high = None, None
            ci_low_corrected = None
        base = baseline_rate.get(market)
        if ci_low is not None and base is not None:
            excess: Optional[float] = round(ci_low - base, 4)
        else:
            excess = None
        stats.append(
            SignalStat(
                signal_type=sig_type,
                market=market,
                interval=interval,
                horizon=horizon,
                win=win,
                loss=loss,
                sample=sample,
                win_rate=wr,
                ci_low=ci_low,
                ci_high=ci_high,
                baseline_win_rate=base,
                excess=excess,
                ci_low_corrected=ci_low_corrected,
                family_size=family_n,
            )
        )
    return stats
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_signal_backtest_stats.py -q`
Expected: 全绿(既有 8 个 + 新 7 个)。既有测试不传新参 → 默认值 → raw 字段逐字节不变。

- [ ] **Step 5: flake8 + commit**

```bash
flake8 src/services/signal_backtest.py tests/test_signal_backtest_stats.py
git add src/services/signal_backtest.py tests/test_signal_backtest_stats.py
git commit -m "feat: 链路B aggregate 增加 family-wise Bonferroni-CI 校正(bonferroni_z+ci_low_corrected+family_size,N<=1 字面量 1.96 保 byte-identical,alpha 函数内钳 [0.0001,0.05] 保只收紧)"
```

---

### Task 2: 存储两列 + 幂等迁移 + min_sample 共享 helper + service 接线

**Files:**
- Modify: `src/storage.py`(`SignalStatRow`:433 `excess` 后追加两列;`:899` 邻位加调用;`_ensure_backtest_intraday_columns`:943 之后新增方法)
- Modify: `src/services/signal_hit_rate.py`(新增 `resolve_verified_min_sample`;`:95-96` 改用它——**仅等价重构,verified 判定本 task 不动**)
- Modify: `src/services/signal_backtest_service.py`(`:167` 接线两参;`:170-186` ORM 构造追加两字段;imports 追加)
- Test: `tests/test_signal_stats_migration.py`(新建,仿 `tests/test_backtest_intraday_migration.py`)
- Test: `tests/test_signal_backtest_service.py`(追加接线测试)
- Test: `tests/test_signal_backtest_config.py`(追加 helper 测试)

**Interfaces:**
- Consumes: Task 1 的 `SignalStat.ci_low_corrected/family_size`、`aggregate_signal_stats(fwer_alpha=, min_sample=)`。
- Produces: `SignalStatRow.ci_low_corrected`(Float nullable)、`SignalStatRow.family_size`(Integer nullable);`resolve_verified_min_sample(cfg) -> int`(位于 `src.services.signal_hit_rate`,Task 3/5 复用);`DatabaseManager._ensure_signal_stats_columns()`。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_signal_stats_migration.py`:

```python
# -*- coding: utf-8 -*-
"""Inc 1c: signal_stats 幂等补列迁移 + 两新列落库读回(spec §4.4/§7.7)。"""
import sqlite3

from sqlalchemy import inspect

from src.storage import DatabaseManager, SignalStatRow


def _columns(db) -> set:
    insp = inspect(db._engine)
    return {c["name"] for c in insp.get_columns("signal_stats")}


def test_new_columns_present_on_fresh_db(tmp_path):
    db = DatabaseManager(db_url=f"sqlite:///{tmp_path/'fresh.db'}")
    cols = _columns(db)
    assert "ci_low_corrected" in cols
    assert "family_size" in cols


def test_guarded_alter_adds_columns_to_legacy_db_and_is_idempotent(tmp_path):
    # 造"老库":手工建缺两新列的 signal_stats 表(唯一键与现 schema 一致)
    legacy = tmp_path / "legacy.db"
    con = sqlite3.connect(legacy)
    con.execute(
        "CREATE TABLE signal_stats ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, signal_type VARCHAR(64) NOT NULL, "
        "market VARCHAR(16) NOT NULL, interval VARCHAR(8) NOT NULL DEFAULT '1d', "
        "horizon INTEGER NOT NULL, win INTEGER, loss INTEGER, sample INTEGER, "
        "win_rate FLOAT, ci_low FLOAT, ci_high FLOAT, baseline_win_rate FLOAT, "
        "excess FLOAT, computed_at DATETIME)"
    )
    con.execute(
        "INSERT INTO signal_stats (signal_type, market, interval, horizon, win, loss, "
        "sample, win_rate, ci_low, ci_high, baseline_win_rate, excess) "
        "VALUES ('volume_breakout','cn','1d',10,7,3,10,0.7,0.60,0.82,0.50,0.10)"
    )
    con.commit()
    con.close()

    db = DatabaseManager(db_url=f"sqlite:///{legacy}")   # init 触发 guarded ALTER
    cols = _columns(db)
    assert "ci_low_corrected" in cols and "family_size" in cols

    # 老行补列后为 NULL(legacy 哨兵,plain nullable 无 default)
    with db.get_session() as s:
        row = s.query(SignalStatRow).first()
        assert row.ci_low_corrected is None
        assert row.family_size is None

    # 幂等:再次调用不抛
    db._ensure_signal_stats_columns()
    assert "ci_low_corrected" in _columns(db)


def test_two_new_columns_roundtrip(tmp_path):
    db = DatabaseManager(db_url=f"sqlite:///{tmp_path/'rt.db'}")
    row = SignalStatRow(signal_type="volume_breakout", market="cn", interval="1d",
                        horizon=10, win=7, loss=3, sample=10, win_rate=0.7,
                        ci_low=0.60, ci_high=0.82, baseline_win_rate=0.50,
                        excess=0.10, ci_low_corrected=0.55, family_size=20)
    with db.get_session() as s:
        s.add(row)
        s.commit()
        fetched = s.query(SignalStatRow).first()
        assert fetched.ci_low_corrected == 0.55
        assert fetched.family_size == 20
```

`tests/test_signal_backtest_service.py` 末尾追加:

```python
def test_run_passes_fwer_alpha_and_min_sample_to_aggregate():
    """Inc 1c: run() 把 config 的 fwer_alpha 与共享 min_sample 显式传给 aggregate(spec §4.7)。"""
    captured = {}

    def _fake_aggregate(sig, base, *, horizon, interval="1d", fwer_alpha=None, min_sample=None):
        captured.update(fwer_alpha=fwer_alpha, min_sample=min_sample)
        return []

    with patch(
        "src.services.signal_backtest_service._read_watchlist_codes", return_value=[],
    ), patch(
        "src.services.signal_backtest_service.StockService"
    ), patch(
        "src.services.signal_backtest_service.aggregate_signal_stats",
        side_effect=_fake_aggregate,
    ), patch(
        "src.services.signal_backtest_service.SignalStatsRepository"
    ) as Repo:
        Repo.return_value.save_batch.return_value = 0
        SignalBacktestService().run(horizon=10)
    # 未配 SIGNAL_BACKTEST_FWER_ALPHA 时默认 0.05;min_sample 走 resolve_verified_min_sample(默认 10)
    assert captured["fwer_alpha"] == 0.05
    assert captured["min_sample"] == 10


def test_run_persists_corrected_fields_to_orm_rows():
    """Inc 1c: SignalStatRow 构造带 ci_low_corrected/family_size(spec §4.4)。"""
    with patch(
        "src.services.signal_backtest_service._read_watchlist_codes",
        return_value=["600519", "600036"],
    ), patch(
        "src.services.signal_backtest_service.StockService"
    ) as SS, patch(
        "src.services.signal_backtest_service.get_market_for_stock",
        side_effect=["cn", "cn"],
    ), patch(
        "src.services.signal_backtest_service.evaluate_signal_outcomes",
        return_value=[SignalOutcome("volume_breakout", "cn", "win")] * 6
        + [SignalOutcome("volume_breakout", "cn", "loss")] * 6,
    ), patch(
        "src.services.signal_backtest_service.evaluate_baseline_outcomes",
        return_value=[SignalOutcome("__baseline__", "cn", "win")] * 5
        + [SignalOutcome("__baseline__", "cn", "loss")] * 5,
    ), patch(
        "src.services.signal_backtest_service.SignalStatsRepository"
    ) as Repo:
        SS.return_value.get_history_data.return_value = _hist()
        Repo.return_value.save_batch.return_value = 1
        SignalBacktestService().run(horizon=10)
        rows = Repo.return_value.save_batch.call_args[0][0]
        assert rows, "应有落库行"
        # evaluate mock 每股返回同批 outcome,聚合成单格(sample=24>=10 → N=1)
        assert rows[0].family_size == 1
        assert rows[0].ci_low_corrected == rows[0].ci_low   # N=1 恒等
```

`tests/test_signal_backtest_config.py` 末尾追加:

```python
def test_resolve_verified_min_sample_shared_helper(monkeypatch):
    """Inc 1c: 读写共用 min_sample 解析(spec §4.7);显式设 SIGNAL_HIT_VERIFIED_MIN_SAMPLE 优先。"""
    from src.services.signal_hit_rate import resolve_verified_min_sample
    monkeypatch.delenv("SIGNAL_HIT_VERIFIED_MIN_SAMPLE", raising=False)
    monkeypatch.delenv("BACKTEST_EVAL_WINDOW_DAYS", raising=False)
    c = Config._load_from_env()
    # 未显式设置时 loader 已把 signal_hit_verified_min_sample 回落为 eval_window(=10)
    assert resolve_verified_min_sample(c) == 10
    monkeypatch.setenv("SIGNAL_HIT_VERIFIED_MIN_SAMPLE", "3")
    assert resolve_verified_min_sample(Config._load_from_env()) == 3
    monkeypatch.delenv("SIGNAL_HIT_VERIFIED_MIN_SAMPLE", raising=False)
    monkeypatch.setenv("BACKTEST_EVAL_WINDOW_DAYS", "15")
    assert resolve_verified_min_sample(Config._load_from_env()) == 15
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_signal_stats_migration.py tests/test_signal_backtest_service.py tests/test_signal_backtest_config.py -x -q`
Expected: FAIL — SignalStatRow 无 `ci_low_corrected` 属性 / `_ensure_signal_stats_columns` 不存在 / import 错误。

- [ ] **Step 3: 实现**

`src/storage.py` — `SignalStatRow` `excess = Column(Float)`(`:433`)之后追加(house style plain nullable,同 `ci_low`/`excess`;老行 NULL=legacy):

```python
    ci_low_corrected = Column(Float)   # family-wise 校正后 Wilson 下界;NULL=legacy 行(未重跑)
    family_size = Column(Integer)      # 写时 family 可检验格子数 N;NULL=legacy 行
```

`_ensure_backtest_intraday_columns`(`:943`)之后新增方法:

```python
    def _ensure_signal_stats_columns(self) -> None:
        """幂等补列:老库的 signal_stats 缺 ci_low_corrected/family_size 时 ALTER 补上。

        与 _ensure_backtest_intraday_columns 同款守卫;两列 plain nullable 无 DEFAULT
        (SQLite 对已填充表加 NOT NULL 列须带 DEFAULT,nullable 规避且 NULL=legacy 哨兵)。
        """
        try:
            with self._engine.begin() as conn:
                from sqlalchemy import text
                existing = {
                    r[1] for r in conn.execute(text("PRAGMA table_info(signal_stats)"))
                }
                if not existing:
                    return  # 表尚未建(理论上 create_all 已建);留给 create_all
                if "ci_low_corrected" not in existing:
                    conn.execute(text(
                        "ALTER TABLE signal_stats ADD COLUMN ci_low_corrected FLOAT"
                    ))
                if "family_size" not in existing:
                    conn.execute(text(
                        "ALTER TABLE signal_stats ADD COLUMN family_size INTEGER"
                    ))
        except Exception as exc:
            logger.warning("补全 signal_stats 校正列失败: %s", exc)
```

`:899` 调用点(`self._ensure_backtest_intraday_columns()` 之后)追加一行:

```python
            self._ensure_signal_stats_columns()
```

`src/services/signal_hit_rate.py` — `resolve_marker_hit_fields` 之前新增模块级函数,并把 `:95-96` 原地改为调用(**verified 判定逻辑本 task 一个字不动**):

```python
def resolve_verified_min_sample(cfg) -> int:
    """verified 判定与 family N 共用的 min_sample 解析(读写路径唯一真源,spec §4.7)。

    = signal_hit_verified_min_sample(>0 时优先)否则 backtest_eval_window_days(默认 10)。
    """
    return int(getattr(cfg, "signal_hit_verified_min_sample", 0) or 0) \
        or int(getattr(cfg, "backtest_eval_window_days", 10))
```

`:95-96` 原两行替换为:

```python
    min_sample = resolve_verified_min_sample(cfg)
```

`src/services/signal_backtest_service.py` — imports 追加:

```python
from src.services.signal_hit_rate import resolve_verified_min_sample
```

`:167` 聚合调用改为(getattr 防御:Task 5 才加 config 字段,先默认 0.05):

```python
        stats = aggregate_signal_stats(
            all_sig, all_base, horizon=hz, interval=interval,
            fwer_alpha=float(getattr(cfg, "signal_backtest_fwer_alpha", 0.05)),
            min_sample=resolve_verified_min_sample(cfg),
        )
```

`:183` `excess=s.excess,` 之后 ORM 构造追加:

```python
                excess=s.excess,
                ci_low_corrected=s.ci_low_corrected,
                family_size=s.family_size,
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_signal_stats_migration.py tests/test_signal_backtest_service.py tests/test_signal_backtest_config.py tests/test_backtest_intraday_migration.py tests/test_signal_hit_rate.py -q`
Expected: 全绿(含既有;signal_hit_rate 仅等价重构不破既有)。

- [ ] **Step 5: flake8 + commit**

```bash
flake8 src/storage.py src/services/signal_hit_rate.py src/services/signal_backtest_service.py tests/test_signal_stats_migration.py tests/test_signal_backtest_service.py tests/test_signal_backtest_config.py
git add -A
git commit -m "feat: signal_stats 追加 ci_low_corrected/family_size 两列(幂等补列,NULL=legacy)并接线 service(共享 resolve_verified_min_sample 防读写 min_sample 漂移)"
```

---

### Task 3: 读路径 verified 改用校正下界 + 既有桩必修

**Files:**
- Modify: `src/services/signal_hit_rate.py`(`:87-88` `_none`、`:103-117` verified 与返回 dict)
- Modify: `tests/test_signal_hit_rate.py`(`_make_stat`:195-205、`_stat`:372-380、`:405/:424` 精确 dict 断言、`:512-513` SimpleNamespace、roundtrip `:487-493` 追加断言;末尾追加新测试)
- Modify: `tests/test_signal_finer_fields.py`(`:91-92` SimpleNamespace 桩)

**Interfaces:**
- Consumes: Task 2 的 `SignalStatRow.ci_low_corrected/family_size`(ORM 属性,老行 None)。
- Produces: `resolve_marker_hit_fields` 返回 dict **新增 2 个 key**:`"ci_low_corrected": Optional[float]`、`"family_size": Optional[int]`(所有分支键集一致);verified 语义=effective_low(校正值优先,None 回退 raw)> baseline。Task 4 消费这两个 key。

- [ ] **Step 1: 修既有桩 + 写失败测试**

`tests/test_signal_hit_rate.py` `_make_stat`(`:195-205`)在 `m.excess = ...` 之后追加两行(= `ci_low` 同值保退化语义;spec §7.11 plan-mandated):

```python
        m.ci_low_corrected = ci_low
        m.family_size = 1
```

`_stat`(`:372-380`)`d.update(kw)` 之后追加(kw 可覆盖;默认随 ci_low 同值):

```python
    d.setdefault("ci_low_corrected", d["ci_low"])
    d.setdefault("family_size", 1)
```

`:405` 与 `:424` 两处精确 dict 断言各补两 key(§7.12(a) 预言的红点):

```python
        assert f == {"hit_rate": None, "hit_sample": None, "verified": False,
                     "ci_low": None, "ci_high": None, "baseline_excess": None,
                     "horizon": None, "ci_low_corrected": None, "family_size": None}
```

`:512-513` `SimpleNamespace(...)` 追加 `ci_low_corrected=None, family_size=None`(显式清晰;getattr 本可回退)。

`tests/test_signal_finer_fields.py:91-92` `SimpleNamespace(...)` stat 同样追加 `ci_low_corrected=None, family_size=None`。

roundtrip(`:456` SignalStatRow 构造)追加 `ci_low_corrected=0.55, family_size=20`,断言区(`:493` 后)追加:

```python
        assert fields["ci_low_corrected"] == 0.55
        assert fields["family_size"] == 20
```

`tests/test_signal_hit_rate.py` 末尾追加新测试:

```python
# --- Inc 1c: verified 改用校正下界 + 老行回退(spec §4.5/§7.6) ---

def test_verified_uses_corrected_low_when_present():
    """校正下界驱动 verified:raw ci_low>baseline 但校正后≤baseline → False。"""
    with patch("src.services.signal_hit_rate.get_market_for_stock", return_value="cn"), \
         patch("src.services.signal_hit_rate.SignalStatsRepository") as Repo:
        Repo.return_value.get.return_value = _stat(
            ci_low=0.55, ci_low_corrected=0.48, family_size=20)  # baseline=0.50
        f = resolve_marker_hit_fields("volume_breakout", "600519")
        assert f["verified"] is False          # 0.48 <= 0.50,尽管 raw 0.55 > 0.50
        assert f["ci_low"] == 0.55             # raw 展示不变
        assert f["ci_low_corrected"] == 0.48
        assert f["family_size"] == 20


def test_verified_true_when_corrected_still_above_baseline():
    with patch("src.services.signal_hit_rate.get_market_for_stock", return_value="cn"), \
         patch("src.services.signal_hit_rate.SignalStatsRepository") as Repo:
        Repo.return_value.get.return_value = _stat(
            ci_low=0.60, ci_low_corrected=0.52, family_size=20)
        assert resolve_marker_hit_fields("volume_breakout", "600519")["verified"] is True


def test_legacy_row_null_corrected_falls_back_to_raw():
    """§7.6 老行回退:ci_low_corrected=None → verified 用 raw ci_low(升级前行为)。"""
    with patch("src.services.signal_hit_rate.get_market_for_stock", return_value="cn"), \
         patch("src.services.signal_hit_rate.SignalStatsRepository") as Repo:
        Repo.return_value.get.return_value = _stat(
            ci_low=0.55, ci_low_corrected=None, family_size=None)
        f = resolve_marker_hit_fields("volume_breakout", "600519")
        assert f["verified"] is True           # 回退 raw 0.55 > 0.50
        assert f["ci_low_corrected"] is None
        assert f["family_size"] is None


def test_magicmock_stub_missing_attr_would_typeerror_documented():
    """§4.5 Blocker 回归锚:桩缺 ci_low_corrected 时 getattr(MagicMock) 返回子 mock,
    verified 比较会 TypeError——证桩必须显式补属性(_make_stat/_stat 已补)。"""
    from unittest.mock import MagicMock
    bare = MagicMock()
    bare.sample = 20
    bare.win_rate = 0.68
    bare.ci_low = 0.55
    bare.ci_high = 0.80
    bare.baseline_win_rate = 0.50
    bare.excess = 0.05
    # 不给 ci_low_corrected → 自动子 mock,> 比较抛 TypeError
    import pytest as _pytest
    with patch("src.services.signal_hit_rate.get_market_for_stock", return_value="cn"), \
         patch("src.services.signal_hit_rate.SignalStatsRepository") as Repo:
        Repo.return_value.get.return_value = bare
        with _pytest.raises(TypeError):
            resolve_marker_hit_fields("volume_breakout", "600519")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_signal_hit_rate.py tests/test_signal_finer_fields.py -x -q`
Expected: FAIL — 新测试 `f["ci_low_corrected"]` KeyError(读路径未返回);`:405` 精确 dict 断言红(改了期望但实现未加 key)。

- [ ] **Step 3: 实现**(`src/services/signal_hit_rate.py`)

`_none`(`:87-88`)补两 key:

```python
    _none = {"hit_rate": None, "hit_sample": None, "verified": False,
             "ci_low": None, "ci_high": None, "baseline_excess": None, "horizon": None,
             "ci_low_corrected": None, "family_size": None}
```

verified 块(`:103-108`)与返回 dict(`:109-117`)替换为:

```python
    # Inc 1c: verified 改用 family-wise 校正下界;老行(migration 前)NULL → 回退 raw ci_low
    # getattr 容错非 ORM 桩缺属性(MagicMock 桩必须显式补属性,否则子 mock 比较 TypeError)
    _corr = getattr(stat, "ci_low_corrected", None)
    effective_low = _corr if _corr is not None else stat.ci_low
    verified = bool(
        stat.sample >= min_sample
        and effective_low is not None
        and stat.baseline_win_rate is not None
        and effective_low > stat.baseline_win_rate
    )
    return {
        "hit_rate": stat.win_rate,
        "hit_sample": stat.sample,
        "verified": verified,
        "ci_low": stat.ci_low,
        "ci_high": stat.ci_high,
        "baseline_excess": stat.excess,
        "horizon": horizon,
        "ci_low_corrected": _corr,
        "family_size": getattr(stat, "family_size", None),
    }
```

模块 docstring(`:8-10` M3-A6 段)之后补一行 Inc 1c 说明:`verified 自 Inc 1c 起用 family-wise 校正下界(ci_low_corrected,老行回退 raw ci_low);详见 docs/superpowers/specs/2026-07-01-chainb-multiple-testing-design.md`。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_signal_hit_rate.py tests/test_signal_finer_fields.py -q`
Expected: 全绿(既有 + 新 4 个;桩已补属性)。

- [ ] **Step 5: flake8 + commit**

```bash
flake8 src/services/signal_hit_rate.py tests/test_signal_hit_rate.py tests/test_signal_finer_fields.py
git add -A
git commit -m "feat: 读路径 verified 改用校正下界 ci_low_corrected(老行 NULL 回退 raw,不倒退不假收紧),resolver dict 追加 ci_low_corrected/family_size 两 key,既有 MagicMock/SimpleNamespace 桩补属性"
```

---

### Task 4: surfacing 后端三层 — marker 白名单 + board + Pydantic schema(含 verified 描述订正)

**Files:**
- Modify: `src/services/signals_service.py`(`:135-158` 基础 marker、`:162-168` 回填块、`:177-214` `_llm_marker`)
- Modify: `src/services/signal_board_service.py`(`:190-205` `_hit_fields_from_markers` 两处 return、`:227-237` `_degraded_entry`)
- Modify: `api/v1/schemas/stocks.py`(`SignalMarker`:128-131 区、`BoardEntry`:196-199 区)
- Test: `tests/test_signal_finer_fields.py`(追加 marker 贯通)
- Test: `tests/test_signal_board_service.py`(追加 board 贯通)
- Test: `tests/test_signals_board_endpoint.py`(追加 Pydantic 序列化保留 + 描述订正断言)

**Interfaces:**
- Consumes: Task 3 resolver dict 的 `ci_low_corrected`/`family_size` key。
- Produces: marker dict 两键 `ci_low_corrected`/`family_size`;board entry dict 两键;`SignalMarker.ci_low_corrected: Optional[float]`、`.family_size: Optional[int]`(BoardEntry 同)——Task 6 前端消费的 wire 字段名即此 snake_case。

- [ ] **Step 1: 写失败测试**

`tests/test_signal_finer_fields.py` 末尾追加:

```python
# --- Inc 1c: ci_low_corrected/family_size 过 marker 白名单一跳(spec §4.6-1) ---

def test_marker_from_vpsignal_carries_corrected_fields():
    sig = _types.SimpleNamespace(
        timestamp=1000, price=10.0, anchor="low", direction="bullish",
        signal_type="volume_breakout", confidence="high",
        is_daily_approx=False, is_anomalous=False, reason="x",
        threshold=None, observed_value=None,
    )
    resolver = lambda st, code: {"hit_rate": 0.6, "hit_sample": 30, "verified": False,
                                 "ci_low": 0.55, "ci_high": 0.7, "baseline_excess": 0.05,
                                 "horizon": 10, "ci_low_corrected": 0.48, "family_size": 20}
    m = _ss._marker_from_vpsignal(sig, code="600519", hit_fields_resolver=resolver)
    assert m["ci_low_corrected"] == 0.48
    assert m["family_size"] == 20


def test_marker_from_vpsignal_no_resolver_corrected_none():
    sig = _types.SimpleNamespace(
        timestamp=1, price=1.0, anchor="low", direction="bullish",
        signal_type="x", confidence="low", is_daily_approx=False,
        is_anomalous=False, reason="r", threshold=None, observed_value=None,
    )
    m = _ss._marker_from_vpsignal(sig)
    assert m["ci_low_corrected"] is None and m["family_size"] is None
```

`tests/test_signal_board_service.py` 末尾追加(import 处补 `_hit_fields_from_markers`、`_degraded_entry` 若未导入):

```python
# --- Inc 1c: board 层透传 ci_low_corrected/family_size(spec §4.6-2) ---
from src.services.signal_board_service import _hit_fields_from_markers, _degraded_entry


def test_hit_fields_from_markers_carries_corrected_fields():
    marker = {"source": "rule", "hit_rate": 0.6, "hit_sample": 30, "verified": False,
              "ci_low": 0.55, "ci_high": 0.7, "baseline_excess": 0.05,
              "horizon_bars": 10, "status": "active",
              "ci_low_corrected": 0.48, "family_size": 20}
    f = _hit_fields_from_markers([marker])
    assert f["ci_low_corrected"] == 0.48 and f["family_size"] == 20
    # 无 rule marker 的 fallback 分支同样含两键(键集一致)
    empty = _hit_fields_from_markers([])
    assert empty["ci_low_corrected"] is None and empty["family_size"] is None


def test_degraded_entry_contains_corrected_keys():
    e = _degraded_entry("600519", "信号计算失败")
    assert e["ci_low_corrected"] is None and e["family_size"] is None
```

`tests/test_signals_board_endpoint.py` 末尾追加(§7.8 Pydantic 静默丢弃层 + §7.13(b) 描述订正):

```python
# --- Inc 1c: Pydantic 层保留两新字段 + verified 描述订正(spec §4.6-3/4) ---
from api.v1.schemas.stocks import BoardEntry, SignalMarker


def test_board_entry_pydantic_preserves_corrected_fields():
    """schema 未声明字段会被 Pydantic 静默丢弃——本测试锁死声明存在。"""
    entry = BoardEntry(
        code="600519", action_group="buy", consistency="consistent",
        price_lines={"entry": None, "stop": None, "target": None},
        status="ok", ci_low_corrected=0.48, family_size=20,
    )
    dumped = entry.model_dump()
    assert dumped["ci_low_corrected"] == 0.48
    assert dumped["family_size"] == 20


def test_signal_marker_pydantic_preserves_corrected_fields():
    m = SignalMarker(
        timestamp=1, price=1.0, anchor="low", direction="bullish",
        signal_type="x", source="rule", confidence="low",
        is_daily_approx=False, is_anomalous=False, reason="r",
        ci_low_corrected=0.48, family_size=20,
    )
    dumped = m.model_dump()
    assert dumped["ci_low_corrected"] == 0.48
    assert dumped["family_size"] == 20


def test_verified_description_mentions_correction():
    """§4.6-4 防文档假话:verified 描述须写明校正后下界口径。"""
    for model in (SignalMarker, BoardEntry):
        desc = model.model_fields["verified"].description
        assert "校正" in desc, f"{model.__name__}.verified 描述未订正: {desc}"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_signal_finer_fields.py tests/test_signal_board_service.py tests/test_signals_board_endpoint.py -x -q`
Expected: FAIL — marker 无 `ci_low_corrected` key / Pydantic model_dump 无两字段 / 描述断言红。

- [ ] **Step 3: 实现**

`src/services/signals_service.py`:
1. 基础 marker dict `:156-157`(`"horizon_bars": None,` 之前)追加两键:

```python
        "ci_low_corrected": None,
        "family_size": None,
```

2. 回填块 `:168`(`marker["horizon_bars"] = fields.get("horizon")` 之后)追加:

```python
            marker["ci_low_corrected"] = fields.get("ci_low_corrected")
            marker["family_size"] = fields.get("family_size")
```

3. `_llm_marker` 返回 dict(`:210` `"baseline_excess": None,` 之后)追加同两键 `"ci_low_corrected": None, "family_size": None,`。

`src/services/signal_board_service.py` `_hit_fields_from_markers` 两处 return 各追加(键序放 `baseline_excess` 后):

```python
                "ci_low_corrected": m.get("ci_low_corrected"),
                "family_size": m.get("family_size"),
```

```python
    return {"hit_rate": None, "hit_sample": None, "verified": False,
            "ci_low": None, "ci_high": None, "baseline_excess": None,
            "ci_low_corrected": None, "family_size": None,
            "horizon_bars": None, "signal_status": None}
```

`_degraded_entry`(`:234` `"baseline_excess": None,` 之后)追加 `"ci_low_corrected": None, "family_size": None,`。

`api/v1/schemas/stocks.py`:
1. `SignalMarker.baseline_excess`(`:131`)之后追加:

```python
    ci_low_corrected: Optional[float] = Field(None, description="family-wise 多重检验校正后的 CI 下界(Inc 1c);null=legacy 行未重跑或无样本")
    family_size: Optional[int] = Field(None, description="该统计所在 family 的同检格子数 N(Inc 1c);null=legacy 行")
```

2. `BoardEntry.baseline_excess`(`:199`)之后追加同两行。
3. verified 描述订正两处(`:128` 与 `:196`),统一改为:

```python
    verified: bool = Field(False, description="hit_sample 达阈值且校正后下界 ci_low_corrected(family-wise Bonferroni;legacy 行回退 ci_low)> baseline 则 True(M3-A6/Inc 1c)")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_signal_finer_fields.py tests/test_signal_board_service.py tests/test_signals_board_endpoint.py tests/test_signal_board_resonance.py -q`
Expected: 全绿。

- [ ] **Step 5: flake8 + commit**

```bash
flake8 src/services/signals_service.py src/services/signal_board_service.py api/v1/schemas/stocks.py tests/test_signal_finer_fields.py tests/test_signal_board_service.py tests/test_signals_board_endpoint.py
git add -A
git commit -m "feat: ci_low_corrected/family_size 透出后端三层(marker 白名单+board+Pydantic schema)并订正 verified 字段描述为校正后口径"
```

---

### Task 5: 配置 — SIGNAL_BACKTEST_FWER_ALPHA(config + registry + locale + .env.example)

**Files:**
- Modify: `src/config.py`(`:900` `signal_backtest_horizon_bars` 之后加字段;loader `:1750` 之后加解析)
- Modify: `src/core/config_registry.py`(`SIGNAL_BACKTEST_HORIZON_BARS` 条目之后插新条目,display_order=71)
- Modify: `apps/dsa-web/src/locales/settingsHelp.ts`(`settings.backtest.SIGNAL_BACKTEST_HORIZON_BARS` 条目之后插 locale)
- Modify: `.env.example`(`:705` `# SIGNAL_BACKTEST_HORIZON_BARS=10` 之后加注释行)
- Test: `tests/test_signal_backtest_config.py`(追加解析钳制测试)

**Interfaces:**
- Produces: `config.signal_backtest_fwer_alpha: float = 0.05`(Task 2 的 getattr 防御自动升级为真实字段);registry key `SIGNAL_BACKTEST_FWER_ALPHA` + help_key `settings.backtest.SIGNAL_BACKTEST_FWER_ALPHA`。
- 覆盖门:`tests/test_config_registry.py::test_registry_help_keys_exist_in_locales` 等自动校验 registry↔locale;实现后必须跑该文件确认。

- [ ] **Step 1: 写失败测试**(`tests/test_signal_backtest_config.py` 末尾追加)

```python
def test_fwer_alpha_default(monkeypatch):
    monkeypatch.delenv("SIGNAL_BACKTEST_FWER_ALPHA", raising=False)
    assert Config._load_from_env().signal_backtest_fwer_alpha == 0.05


@pytest.mark.parametrize("raw,expected", [
    ("0.01", 0.01),        # 域内原样
    ("0.9", 0.05),         # 超上限 → 钳 0.05(保"只收紧")
    ("0", 0.0001),         # 0 → 钳下限(防 inv_cdf(1.0) 崩溃)
    ("-1", 0.0001),        # 负 → 钳下限
    ("1e-100", 0.0001),    # 极小 → 钳下限,不崩
    ("abc", 0.05),         # 非数字 → 回退默认
])
def test_fwer_alpha_clamped_not_fallback(monkeypatch, raw, expected):
    """spec §4.7: 钳制(clamp)非回退;仅非数字才回退默认。"""
    monkeypatch.setenv("SIGNAL_BACKTEST_FWER_ALPHA", raw)
    assert Config._load_from_env().signal_backtest_fwer_alpha == expected
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_signal_backtest_config.py -x -q`
Expected: FAIL — ConfigModel 无 `signal_backtest_fwer_alpha` 属性。

- [ ] **Step 3: 实现**

`src/config.py` `:900`(`signal_backtest_horizon_bars: int = 10` 之后)追加:

```python
    # 信号级回测 family-wise 多重检验族水平(Inc 1c):双尾口径,等价单尾假 verified 率≈alpha/2;
    # 域 [0.0001, 0.05],上限保证校正只收紧,下限防 inv_cdf(1.0) 崩溃
    signal_backtest_fwer_alpha: float = 0.05
```

loader `:1750`(`signal_backtest_horizon_bars=parse_env_int(...)` 之后)追加:

```python
            signal_backtest_fwer_alpha=parse_env_float(
                os.getenv('SIGNAL_BACKTEST_FWER_ALPHA'),
                0.05,
                field_name='SIGNAL_BACKTEST_FWER_ALPHA',
                minimum=0.0001,
                maximum=0.05,
            ),
```

`src/core/config_registry.py` — `SIGNAL_BACKTEST_HORIZON_BARS` 条目(display_order 70)整块之后插入:

```python
    "SIGNAL_BACKTEST_FWER_ALPHA": {
        "title": "Signal Backtest FWER Alpha",
        "description": "Family-wise error rate (two-sided) for the Bonferroni-CI multiple-testing correction on signal 'verified' (Inc 1c). Smaller = stricter. Capped at 0.05 so the correction can only tighten; one-sided equivalent is about alpha/2.",
        "category": "backtest",
        "data_type": "number",
        "ui_control": "number",
        "is_sensitive": False,
        "is_required": False,
        "is_editable": True,
        "default_value": "0.05",
        "options": [],
        "validation": {"min": 0.0001, "max": 0.05},
        "display_order": 71,
        "help_key": "settings.backtest.SIGNAL_BACKTEST_FWER_ALPHA",
        "examples": [
            "SIGNAL_BACKTEST_FWER_ALPHA=0.05",
            "SIGNAL_BACKTEST_FWER_ALPHA=0.01",
        ],
        "docs": [
            {
                "label": "完整指南：回测配置",
                "href": "https://github.com/ZhuLinsen/daily_stock_analysis/blob/main/docs/full-guide.md#回测功能",
            },
        ],
        "warning_codes": [],
    },
```

`apps/dsa-web/src/locales/settingsHelp.ts` — `settings.backtest.SIGNAL_BACKTEST_HORIZON_BARS` 条目之后插入(M-2 文案纪律:双尾/等价单尾≈alpha/2,**不写"5% 假阳率"**):

```typescript
  'settings.backtest.SIGNAL_BACKTEST_FWER_ALPHA': {
    title: '信号验证多重检验族水平',
    summary: '对信号"已验证"标记做 family-wise(Bonferroni-CI)多重检验校正的族水平(双尾口径;等价单尾假验证率约为该值的一半)。',
    usage: '默认 0.05(沿用现有 95% CI 水平)。一次批跑同时检验多个(信号类型×市场)组合时,按组合数收紧每格的置信下界,防止纯靠运气的组合被标"已验证"。越小越严。',
    valueNotes: [
      '域 [0.0001, 0.05]:越界值钳制到边界,非数字回退默认 0.05。',
      '上限 0.05 保证校正只会比未校正更严,绝不更宽松。',
      '单一组合(family=1)时无多重检验,行为与未校正完全一致。',
    ],
    impact: ['影响 signal_stats 中 ci_low_corrected 与前端"已验证"标识;family_size 字段透出同检组合数。'],
    notes: ['修改后需重新运行 --signal-backtest 才会按新族水平重算校正列。'],
  },
```

`.env.example` `:705`(`# SIGNAL_BACKTEST_HORIZON_BARS=10`)之后追加注释行(仓库惯例,SIGNAL_BACKTEST_* 全为注释样例):

```
# 信号验证多重检验族水平(Inc 1c;双尾,域 [0.0001,0.05],默认 0.05=沿用 95% CI 水平)
# SIGNAL_BACKTEST_FWER_ALPHA=0.05
```

- [ ] **Step 4: 跑测试确认通过(含覆盖门)**

Run: `python -m pytest tests/test_signal_backtest_config.py tests/test_config_registry.py tests/test_system_config_api.py -q`
Expected: 全绿(registry↔locale↔web-metadata 覆盖门自动校验新条目;红则按报错补齐字段——参照 HK 印花税踩坑,examples/docs 不齐会红)。

- [ ] **Step 5: flake8 + commit**

```bash
flake8 src/config.py src/core/config_registry.py tests/test_signal_backtest_config.py
git add -A
git commit -m "feat: 新增 SIGNAL_BACKTEST_FWER_ALPHA 配置(默认 0.05,钳制域 [0.0001,0.05],registry+locale+env.example 三向同步,文案标明双尾口径)"
```

---

### Task 6: 前端 — 类型/mapper/credibility/三组件 + vitest

**Files:**
- Modify: `apps/dsa-web/src/types/kline.ts`(`SignalMarker`:47 后、`BoardEntry`:89 后)
- Modify: `apps/dsa-web/src/api/stocks.ts`(`RawSignalMarker`:40 后、`mapSignalMarker`:74 后、`RawBoardEntry`:87 行内、`mapBoardEntry`:106 后)
- Modify: `apps/dsa-web/src/utils/credibility.ts`(新增 2 个纯函数)
- Modify: `apps/dsa-web/src/components/board/SignalBoardGroup.tsx`(`:59` verified span 后)
- Modify: `apps/dsa-web/src/components/workstation/StockSignalsPanel.tsx`(`:61` verified span 后)
- Modify: `apps/dsa-web/src/components/kline/SignalDrilldownPanel.tsx`(`:81` verified span 后)
- Test: `apps/dsa-web/src/utils/__tests__/credibility.test.ts`、`apps/dsa-web/src/api/__tests__/stocks.board.test.ts`、`apps/dsa-web/src/components/board/__tests__/SignalBoard.test.tsx`(追加)

**Interfaces:**
- Consumes: Task 4 的 wire 字段 `ci_low_corrected`/`family_size`(snake_case,可能缺席=旧后端,mapper `?? null` 容错)。
- Produces: `SignalMarker.ciLowCorrected/familySize`、`BoardEntry.ciLowCorrected/familySize`(camelCase);`unverifiedExcessNote(input) -> string | null`(矛盾消解文案,legacy null 不注解)。

- [ ] **Step 1: 写失败测试**

`apps/dsa-web/src/utils/__tests__/credibility.test.ts` describe 内追加:

```typescript
  it('unverified excess note explains correction, silent for legacy/verified', () => {
    // 矛盾态:raw 超额>0 但 verified=false 且有校正值 → 给出数字解释
    expect(unverifiedExcessNote({
      verified: false, baselineExcess: 0.05, ciLowCorrected: 0.48, familySize: 20,
    })).toBe('20 组同检校正后下界 48%,未超基准');
    // familySize 缺失但有校正值 → 泛化措辞
    expect(unverifiedExcessNote({
      verified: false, baselineExcess: 0.05, ciLowCorrected: 0.48, familySize: null,
    })).toBe('多重检验校正后下界 48%,未超基准');
    // legacy 行(无校正值)→ 不注解,维持升级前展示(M-5:不显示 N=0)
    expect(unverifiedExcessNote({
      verified: false, baselineExcess: 0.05, ciLowCorrected: null, familySize: null,
    })).toBeNull();
    // verified=true / 无超额 / excess 缺失 → 无矛盾,不注解
    expect(unverifiedExcessNote({
      verified: true, baselineExcess: 0.05, ciLowCorrected: 0.52, familySize: 20,
    })).toBeNull();
    expect(unverifiedExcessNote({
      verified: false, baselineExcess: -0.02, ciLowCorrected: 0.4, familySize: 5,
    })).toBeNull();
    expect(unverifiedExcessNote({
      verified: false, baselineExcess: null, ciLowCorrected: 0.4, familySize: 5,
    })).toBeNull();
  });
```

(import 行追加 `unverifiedExcessNote`。)

`apps/dsa-web/src/api/__tests__/stocks.board.test.ts` 追加(仿既有 mapper 测试形状;在含 `ci_low: 0.55` 的 raw entry fixture 上补 `ci_low_corrected: 0.48, family_size: 20`,断言映射;另一 entry 不带两字段断言 `null`):

```typescript
  it('maps ci_low_corrected/family_size to camelCase, null when absent', async () => {
    // 在既有 mock 响应的 entry 上补 ci_low_corrected: 0.48, family_size: 20 后:
    // expect(entry.ciLowCorrected).toBe(0.48); expect(entry.familySize).toBe(20);
    // 旧后端载荷(无两字段)→ expect(entry.ciLowCorrected).toBeNull(); expect(entry.familySize).toBeNull();
  });
```

(实现者按该文件既有 mock-axios 模式补全;两条断言路径必须都有——新字段映射 + 缺席容错。)

`apps/dsa-web/src/components/board/__tests__/SignalBoard.test.tsx` 追加:矛盾行(`verified: false, baselineExcess: 0.05, ciLowCorrected: 0.48, familySize: 20`)渲染出 `data-testid="board-corrected-note"` 且文本含 `校正后下界 48%`;legacy 行(两字段 null)**不渲染**该 testid。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd apps/dsa-web && npx vitest run src/utils/__tests__/credibility.test.ts src/api/__tests__/stocks.board.test.ts src/components/board/__tests__/SignalBoard.test.tsx`
Expected: FAIL — `unverifiedExcessNote` 未导出 / `ciLowCorrected` 类型不存在。

- [ ] **Step 3: 实现**

`apps/dsa-web/src/types/kline.ts`:`SignalMarker.baselineExcess`(`:47`)后追加、`BoardEntry.baselineExcess`(`:89`)后追加:

```typescript
  ciLowCorrected: number | null;   // family-wise 校正后 CI 下界(Inc 1c);null=legacy/无样本
  familySize: number | null;       // 同检格子数 N(Inc 1c);null=legacy
```

`apps/dsa-web/src/api/stocks.ts`:
1. `RawSignalMarker`(`:40` `baseline_excess` 后)与 `RawBoardEntry`(`:87` 行内 `baseline_excess` 后)各追加:

```typescript
  ci_low_corrected?: number | null;
  family_size?: number | null;
```

2. `mapSignalMarker`(`:74` `baselineExcess` 行后)与 `mapBoardEntry`(`:106` `baselineExcess` 行后)各追加:

```typescript
  ciLowCorrected: raw.ci_low_corrected ?? null,
  familySize: raw.family_size ?? null,
```

(mapBoardEntry 中变量名为 `r`。)

`apps/dsa-web/src/utils/credibility.ts` 末尾追加:

```typescript
interface UnverifiedExcessInput {
  verified: boolean;
  baselineExcess: number | null;
  ciLowCorrected: number | null;
  familySize: number | null;
}

/**
 * 矛盾消解说明(Inc 1c):raw 超额为正但 verified=false 时,用校正下界解释原因。
 * legacy 行(ciLowCorrected=null,未重跑)不注解——维持升级前展示,不显示"N=0"。
 * 其余情形(已验证/无超额/数据缺失)返回 null。
 */
export function unverifiedExcessNote(input: UnverifiedExcessInput): string | null {
  if (input.verified) return null;
  if (input.baselineExcess === null || input.baselineExcess <= 0) return null;
  if (input.ciLowCorrected === null) return null;
  const corr = `${Math.round(input.ciLowCorrected * 100)}%`;
  const fam = input.familySize != null ? `${input.familySize} 组同检校正后` : '多重检验校正后';
  return `${fam}下界 ${corr},未超基准`;
}
```

三组件:在 verified span 之后各加一段(testid 前缀随组件:`board-`/`signals-`/`drilldown-`;传参取自行内数据对象 `e`/`m`/`marker`):

`SignalBoardGroup.tsx`(`:59` verified span 后):

```tsx
                {unverifiedExcessNote({ verified: e.verified, baselineExcess: e.baselineExcess, ciLowCorrected: e.ciLowCorrected, familySize: e.familySize }) && (
                  <span data-testid="board-corrected-note" className="ml-1 text-secondary-text">
                    ({unverifiedExcessNote({ verified: e.verified, baselineExcess: e.baselineExcess, ciLowCorrected: e.ciLowCorrected, familySize: e.familySize })})
                  </span>
                )}
```

`StockSignalsPanel.tsx`(`:61` verified span 后)同型,testid=`signals-corrected-note`,数据对象 `m`。
`SignalDrilldownPanel.tsx`(`:81` verified span 后)同型,testid=`drilldown-corrected-note`,数据对象 `marker`。
(import 行各追加 `unverifiedExcessNote`;若重复调用不雅,可在 map 回调内提取 `const note = unverifiedExcessNote({...})` 后 `{note && <span ...>({note})</span>}`——以通过 lint 为准。)

- [ ] **Step 4: 跑测试 + lint + build 确认通过**

Run: `cd apps/dsa-web && npx vitest run && npm run lint && npm run build`
Expected: vitest 全绿(既有组件测试的 fixture 若因 BoardEntry/SignalMarker 类型新增必填字段而 TS 报错,给 fixture 补 `ciLowCorrected: null, familySize: null`——§7.12(a) 前端侧);lint 0 error;build 成功。

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: 前端透出 ciLowCorrected/familySize(类型+mapper 容错旧载荷)并以 unverifiedExcessNote 消解 raw 超额与未验证并列的矛盾展示(legacy 行不注解)"
```

---

### Task 7: 文档 + CHANGELOG + 零回归 grep + 全量门禁

**Files:**
- Modify: `docs/signal-credibility.md`(verified 语义章节追加 Inc 1c 说明)
- Modify: `docs/CHANGELOG.md`(`[Unreleased]` 扁平一条)
- Test: 全量 `./scripts/ci_gate.sh` + web-gate

- [ ] **Step 1: 零回归 grep(§7.12)**

```bash
grep -rn "aggregate_signal_stats\|SignalStat(\|resolve_marker_hit_fields" tests/ --include="*.py" -l
grep -rn "ci_low\|verified\|baseline_excess" apps/dsa-web/src --include="*.test.*" -l
```

逐文件确认:(a)精确 dict/字段集断言已补两 key(Task 3/4/6 已处理的之外若有漏网,补之);(b)stat 桩对象无缺属性 TypeError/AttributeError。结论写进交付说明。

- [ ] **Step 2: 更新文档**

`docs/signal-credibility.md`:在 verified 判定说明处追加一节(实现者按现有文档结构落笔,要点必须含):
- verified 自 Inc 1c 起 = `sample >= min_sample` 且**校正后下界** `ci_low_corrected > baseline`(family-wise Bonferroni-CI;N≤1 时与原判定完全一致);
- family = 单次 `--signal-backtest` 聚合中 `sample >= min_sample` 的 `(signal_type × market)` 格子;跨 run/interval/horizon 未合并校正;
- `family_size`/`ci_low_corrected` 为写时快照,改 `min_sample`/alpha 后需重跑刷新;老行 NULL=legacy,读路径回退 raw `ci_low`(升级前行为);
- alpha 配置 `SIGNAL_BACKTEST_FWER_ALPHA`(双尾口径,等价单尾≈alpha/2;域 [0.0001,0.05]);
- baseline 为每市场共享且按已知常量比较,校正不含 baseline 自身估计误差(诚实边界)。

`docs/CHANGELOG.md` `[Unreleased]` 追加一行(扁平格式,禁 `###` 标题):

```markdown
- [修复] 链路B 信号"已验证"标记增加 family-wise(Bonferroni-CI)多重检验校正:一次批跑同检的 (信号类型×市场) 组合按 family 规模收紧置信下界,防止纯靠运气的组合被标"已验证";新增 ci_low_corrected/family_size 透明字段全栈透出(API+看板+前端注解),老统计行自动回退原判定,单组合场景行为与之前完全一致;新配置 SIGNAL_BACKTEST_FWER_ALPHA(默认 0.05,仅可更严)
```

- [ ] **Step 3: 全量门禁**

```bash
cd /root/chainb-mtc && PATH="/root/AI/WorkSpace/cursor/AI _Trading_System/.venv/bin:$PATH" ./scripts/ci_gate.sh
cd apps/dsa-web && npm run lint && npm run build
```

Expected: ci_gate 全绿(基线 3872 passed,新增 ~25+);web-gate lint 0 error + build 成功。**不得用 `| tail` 截断输出**;失败则修复后重跑,禁止带红交付。

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "docs: signal-credibility 补 Inc 1c 多重检验校正语义(family 口径/写时快照/legacy 回退/alpha 双尾)并记 CHANGELOG"
```

---

## Self-Review 结论(plan 作者已核)

- **Spec 覆盖**:§4.2/4.3→Task 1;§4.4/§4.7(helper)→Task 2;§4.5→Task 3;§4.6 后端 4 层→Task 4;§4.7 config 四件套→Task 5;§4.6 前端 2 层→Task 6;§6 文档/§8 交付→Task 7。§7 测试 13 项映射:#1-5/#10→T1,#7→T2,#6/#11→T3,#8 后端半/#13(b)→T4,#9→T5(+T2 helper),#8 前端半/#13(a)→T6,#12→T7。无缺口。
- **数值 pin 均经真实 venv 手算验证**(bonferroni_z/wilson 边界格子/判别式第 5 位差)。
- **类型一致性**:`bonferroni_z`/`resolve_verified_min_sample`/`ci_low_corrected`/`family_size`/`ciLowCorrected`/`familySize`/`unverifiedExcessNote` 各 task 间签名一致。
- **顺序依赖**:T1→T2→T3→T4 严格顺序;T5 可与 T3/T4 并行(T2 的 getattr 防御解耦);T6 依赖 T4 的 wire 字段名;T7 收尾。
