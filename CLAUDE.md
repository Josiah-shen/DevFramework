## 父代理角色
父代理（主会话）在本项目内**即是 coordinator**，职责与约束以 @.claude/roles/coordinator.md 为准：
- 默认只做规划、委派、汇总，不直接 Edit/Write/Bash
- 逃生口：任务满足"一句话可描述 **且** 描述中不含'和'字"时，父代理可直接动手
- 每次新任务先检查 `harness/tasks/{task-slug}/checkpoint.md`，有则从中断处恢复
- 标准流程：理解拆解 → 构造 prompt → 委派等待 → 机械验证（verifier）→ 交叉 review（按需）→ 复现检测（critic）→ 汇总

@.claude/roles/coordinator.md

## 快速链接
- [架构总览](docs/ARCHITECTURE.md) — 分层规则、数据流
- [开发指南](docs/DEVELOPMENT.md) — 构建、测试、lint 命令
- [业务上下文](docs/PRODUCT_SENSE.md) — 项目简介、业务范围
- [Codex 集成](harness/bin/codex.sh) — gpt-5.5 实现 / review / 终验，单点回退开关 `CODEX_DISABLE=1`
  - reasoning 自动档由 `codex.sh` 决策：`xhigh` 仅用于结构性变更或公开接口任务（`api/Controller.java` / `/api/` / `*.controller.ts` / `Mapper.xml` / `schema.sql`），文档/JSON-only scope 用 `medium`，其余默认 `high`；可通过 `CODEX_REASONING=<档位>` 强制覆盖。每次调用同时落 `<log>.meta.json` 含 tokens / http_status / duration / files_touched 字段，供 critic 跨任务统计。
  - **行为变更说明**：旧版 codex.sh 的 `CODEX_REASONING="${CODEX_REASONING:-xhigh}"` 实际未传给 codex（codex 跑 `~/.codex/config.toml` 默认值 `high`）；本次起 `decide_reasoning()` 决策结果通过 `-c model_reasoning_effort=` 真正生效。如需保持手工最高档，调用方显式 `export CODEX_REASONING=xhigh`。
## Harness 引擎
- 子命令总览：
  - `executor.py`：`check` / `status` / `init` / `plan` / `approve` / `verify` / `smoke` / `complete`。
  - `creator.py`：`audit`（评分并写报告）/ `build`（按评分行动，健康档位为 dry-run）/ `fix`（单维度修复）。
- 新任务开始：`python3 harness/bin/executor.py check` — 环境自检；失败返回 2，先跑 `python3 harness/bin/creator.py audit` 看评分再决定是否 `creator build`。
- 非简单任务闭环：`executor init <slug> "描述"` → 填充 `harness/exec-plans/<slug>.md`（必须含 `## 影响范围` 段）→ Claude 主调 ExitPlanMode 请批准 → `approve <slug>` → 编码 → `verify <slug>` → `complete <slug>`。
- 中途查询：`executor status <slug>` 查看当前阶段；`executor plan <slug>` 重新补齐缺失骨架段。
- Verify 范围收敛：`executor verify` 会从 exec-plan 的 `## 影响范围` 抽取路径透传给 `scripts/validate.py --scope`；范围外历史违规降级 warning，仅对范围内改动硬失败。**所有 profile（standard / full）均按 scope 收敛**——升级到 full 后 scope 仍生效（通过 `HARNESS_VERIFY_SCOPE` env 注入到 `make verify`），范围外违规一律降级为 warning。仅当用户显式跑 `make verify`（不经 executor）时保留全仓硬失败的向后兼容。
- 失败自动升级：scope 模式连续 2 次 FAIL 后，第 3 次 verify 自动升级 `--profile full`；stdout 输出 `[verify] auto-escalation: standard → full ...`，仍失败才反馈给编码者。结构性变更（plan 标记「是否结构性变更：是」）或公开接口路径（`api/Controller.java`、`/api/`、`*.controller.ts`、`Mapper.xml`、`schema.sql`）入 scope 时也会立即升级到 full。**升级到 full 后 scope 透传不变**（critic-2026-04-29 R2 修复），结构性变更不会被无关历史违规阻塞。
- 简单任务最低验证：`init <slug> --simple` 任务编码完成后必须跑 `python3 harness/bin/executor.py smoke <slug>`，跑 smoke 档（arch + style）做基线保护。
- 失败早退：`validate.py` 对连续两轮相同失败指纹自动跳过剩余重试，避免浪费时间在确定性问题上。
- 简单任务（单文件小改、不跨层、修改 < 3 个文件）可跳过引擎，直接按 coordinator 流程。
- 拆分类任务请先阅读 `harness/split-task-checklist.md`。
- Worktree 依赖自愈：`executor init` / `verify` 仅把 `src/frontend/node_modules` 软链到主仓（只读依赖共享）；`src/backend/target` 与前端 vite/vitest 缓存（`src/frontend/.vite-cache`、`.vitest-cache`）每个 worktree 独立，支持多 worktree 并行构建。若主仓尚未构建过 `node_modules` 会打印 `[worktree-deps]` 告警，此时请在主仓执行 `pnpm install` / `make build` 后再回 worktree。手动兜底命令见 `harness/split-task-checklist.md` 第 1 节。
## 构建命令
make build      # 构建项目
make test       # 运行测试
make lint-arch  # 运行架构 lint
python3 harness/bin/executor.py smoke <slug>  # 简单任务的 smoke 验证
harness/bin/codex.sh exec --cd <path>      # 调用 Codex 实现
harness/bin/codex.sh review --uncommitted --cd <path>  # 调用 Codex review 未提交变更
## 分层规则
Layer 0: types/                              → 纯类型定义，无内部依赖
Layer 1: utils/                              → 工具函数，仅依赖 Layer 0
Layer 2: config/                             → 配置，依赖 Layer 0-1
Layer 3: core/services/ core/repository/     → 业务逻辑、数据访问，依赖 Layer 0-2
Layer 4: api/ ui/                            → 接口层，依赖 Layer 0-3，彼此不互相引用
## 质量标准
- 结构化日志，禁止 console.log / print()
- 单文件不超过 500 行
- PascalCase（类型）、camelCase（函数）、kebab-case（文件名）
- 测试文件（`*.test.*` / `*.spec.*`）例外：kebab-case 仅校验去掉末尾 `.test` / `.spec` 后的主干（vitest/jest 命名惯例）
- Codex 报告必须含 `codex_model` / `codex_reasoning` 元信息；`harness/trace/` 下 review 文件 frontmatter 必须含 `reviewer` 字段（`codex` / `claude`），缺省视为 `claude`，否则 critic 跨任务统计会丢条目