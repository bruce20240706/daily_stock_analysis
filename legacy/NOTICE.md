# 归档目录说明（NOTICE）

`legacy/quantpick/` 是项目早期的独立骨架（quantpick），**已归档、不参与当前主系统的开发与运行**，仅作设计参考保留。

- 当前主系统的协作规则真源是仓库根目录的 [`AGENTS.md`](../AGENTS.md)（`CLAUDE.md` 为其软链）。本目录下的 `quantpick/CLAUDE.md`、`quantpick/docs/` 等仅反映早期 quantpick 的约定，**与当前系统无关，请勿据此理解当前开发规范**。
- 本目录已从测试收集中排除（`setup.cfg` 的 `norecursedirs` 含 `legacy`），不纳入 CI 用例。
- 请勿在本目录下继续开发或补全 `TODO`；如需新增能力，请在主系统目录（`src/`、`data_provider/`、`api/`、`apps/` 等）中进行。

> 注：本目录仍受版本控制（保留历史与参考价值）。如需彻底移除，应由 maintainer 评估后通过 `git rm` 显式处理，而非加入 `.gitignore`（对已跟踪文件无效）。
