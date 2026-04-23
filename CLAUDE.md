## 快速链接
- [架构总览](docs/ARCHITECTURE.md) — 分层规则、数据流
- [开发指南](docs/DEVELOPMENT.md) — 构建、测试、lint 命令
- [业务上下文](docs/PRODUCT_SENSE.md) — 项目简介、业务范围
## Harness 引擎
- 子命令总览：
  - `executor.py`：`check` / `status` / `init` / `plan` / `approve` / `verify` / `complete`。
  - `creator.py`：`audit`（评分并写报告）/ `build`（按评分行动，健康档位为 dry-run）/ `fix`（单维度修复）。
- 新任务开始：`python3 harness/bin/executor.py check` — 环境自检；失败返回 2，先跑 `python3 harness/bin/creator.py audit` 看评分再决定是否 `creator build`。
- 非简单任务闭环：`executor init <slug> "描述"` → 填充 `harness/exec-plans/<slug>.md`（必须含 `## 影响范围` 段）→ Claude 主调 ExitPlanMode 请批准 → `approve <slug>` → 编码 → `verify <slug>` → `complete <slug>`。
- 中途查询：`executor status <slug>` 查看当前阶段；`executor plan <slug>` 重新补齐缺失骨架段。
- Verify 范围收敛：`executor verify` 会从 exec-plan 的 `## 影响范围` 抽取路径透传给 `scripts/validate.py --scope`；范围外历史违规降级 warning，仅对范围内改动硬失败。
- 失败早退：`validate.py` 对连续两轮相同失败指纹自动跳过剩余重试，避免浪费时间在确定性问题上。
- 简单任务（单文件小改、不跨层、修改 < 3 个文件）可跳过引擎，直接按 coordinator 流程。
- 拆分类任务请先阅读 `harness/split-task-checklist.md`。
- Worktree 依赖自愈：`executor init` / `verify` 会自动把 `src/frontend/node_modules`、`src/backend/target` 软链到主仓；若主仓尚未构建过对应目录会打印 `[worktree-deps]` 告警，此时请在主仓执行 `make build` 后再回 worktree。手动兜底命令见 `harness/split-task-checklist.md` 第 1 节。
## 构建命令
make build      # 构建项目
make test       # 运行测试
make lint-arch  # 运行架构 lint
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