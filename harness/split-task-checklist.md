# 拆分类任务前置检查清单

> 适用范围：所有将大文件/大类按职责拆分的任务（controller-split、*-impl-split、*-vue-split 等）。
> 目的：把 4 次 failure 中重复出现的痛点（worktree 依赖缺失、残留 diff、范围外历史违规）
> 在任务**开工前**一次性消除，而不是等 verify 失败后再人工诊断。

## 0. 起点：确认当前任务是"拆分类"

满足以下任一条件即属拆分类：
- 目标是"把 > 500 行的文件拆成多个 < 500 行的文件"
- 目标是"把 Controller/Service/Vue 组件按职责分散到子包/子目录"
- exec-plan 的"是否结构性变更"字段为"是"

## 1. 进入 worktree 后立即软链前端依赖

worktree 默认只复制 git 跟踪文件，`src/frontend/node_modules` 为空，`make build` 必挂，
典型症状：`sh: vite: command not found` / `make: *** [build] Error 127`。

**自动路径（推荐）**：首次在 worktree 下跑 `python3 harness/bin/executor.py init <slug> ...`
或 `verify <slug>` 时，executor 会自动为 `src/frontend/node_modules` 与 `src/backend/target`
建立指向主仓的软链（日志前缀 `[worktree-deps]`）。若主仓也没有对应目录会打印告警——
此时需先回主仓执行 `make build` 产出依赖再回 worktree。

**手动兜底**（离线场景或 executor 打印 "跳过自动软链" 时）：

```bash
# 从 worktree 根目录执行（假设 worktree 位于 .claude/worktrees/<slug>）
cd .claude/worktrees/<slug>
ln -s ../../../src/frontend/node_modules src/frontend/node_modules
# 后端目标目录同理（如需）
ln -s ../../../src/backend/target src/backend/target
# 验证
ls src/frontend/node_modules/.bin/vite   # 应命中，不应报 "No such file"
```

如果仍遇到 `sh: vite: command not found`，检查：
1. `pwd` 是否仍在主仓而非 worktree
2. 软链路径中 `../` 的层数是否与 worktree 实际深度匹配
3. 主仓的 `src/frontend/node_modules` 是否真的存在（首次 clone 后需先 `pnpm install`）

## 2. 建立 baseline 快照

在写任何代码前先记录"干净的 worktree 状态"，合并前用它做对照检测意外改动：

```bash
git status --porcelain > /tmp/baseline-<slug>.txt
# 如果 baseline 非空，说明 worktree 已有脏改动，必须先 commit / stash / restore
```

合并前重跑一次：

```bash
git status --porcelain > /tmp/final-<slug>.txt
diff /tmp/baseline-<slug>.txt /tmp/final-<slug>.txt
# 新增条目应全部在 exec-plan 声明范围内
```

## 3. 在 exec-plan 声明**允许修改**的文件集合（必填）

在 `harness/exec-plans/<slug>.md` 的 `## 影响范围` 段用反引号包裹路径列出：

```markdown
## 影响范围
- 受影响文件：
  - `src/backend/src/main/java/com/xptsqas/api/ExampleController.java`（删除）
  - `src/backend/src/main/java/com/xptsqas/api/example/`（新增子包）
  - `src/backend/src/main/java/com/xptsqas/api/support/ControllerTypeUtils.java`
```

要点：
- 精确文件用反引号 + 完整相对路径
- 目录前缀用反引号包裹目录路径（`executor.py verify` 与 `check-scope.py` 均支持前缀匹配；
  `src/.../pv/*.java` 形式也能被解析，提取出 `src/.../pv/` 前缀）
- 未在此列表中的文件：verify 的代码规范检查会降级为 ⚠️，check-scope 会告警

## 4. verify 的预期行为

运行 `python3 harness/bin/executor.py verify <slug>`：

| 违规位置 | 行为 |
|----------|------|
| 声明范围内新增/修改文件有违规 | ❌ 硬失败，阻断管道 |
| 声明范围外历史违规（Dashboard.vue 800 行、basicData.js 命名等） | ⚠️ 输出到 `[pre-existing debt]` 段落，**不阻断** |
| worktree 有未声明的改动（如 `api_config.json`、`e2e_config.json`） | ⚠️ `[boundary/worktree-scope-drift]` 告警到 stderr，warning 阶段不阻断 |

如果 exec-plan 未声明范围，verify 会打印 `⚠️ plan 未声明受影响文件集合，verify 回退为全仓扫描`，
此时**所有**历史违规都会阻断管道——这是回退模式，不是预期模式。

## 5. review 前自检

合并主干前在 worktree 根目录手动跑一次：

```bash
# 1. scope 漂移自检
python3 scripts/verify/check-scope.py --slug <slug> --base main
# stderr 应为空；若有输出则列出的文件需要 git restore 或补进 exec-plan

# 2. 与 baseline 对照
git status --porcelain > /tmp/final-<slug>.txt
diff /tmp/baseline-<slug>.txt /tmp/final-<slug>.txt

# 3. 行数核对（针对 500 行拆分任务）
find <声明的新增目录> -name "*.java" -o -name "*.vue" | xargs wc -l | awk '$1>=500 {print}'
# 应为空
```

## 相关规则

- `style/no-debug-print`（error）— 禁止 console.log / print() 等调试输出
- `boundary/worktree-scope-drift`（warning，两阶段发布中）— 本清单的自动化对应规则
- `style/line-count`（隐式，error）— 单文件 ≤ 500 行

## 相关文件

- `scripts/verify/run.py` — verify 调度器，支持 `--scope` 与 `HARNESS_VERIFY_SCOPE` 环境变量
- `scripts/verify/checks/style.py` — 代码规范检查，按 scope 分流
- `scripts/verify/check-scope.py` — 规则 `boundary/worktree-scope-drift` 实现
- `harness/bin/executor.py` — `verify <slug>` 从 exec-plan 提取 scope 并注入
