# Playbook：框架回流（Upstream Sync）

## 这篇是什么

**定位**：把**业务项目**里演进过的**框架**（harness 引擎、验证脚本、Makefile 目标、init.sh、CLAUDE.md 等）同步回**模板**，让后续新项目直接继承改进。

**适用频率**：高。每当业务项目里调整了 harness 流程、加了新的 verify check、修了 init.sh bug，就跑一次。

**不适用场景**：
- 新项目初始化 → 直接 clone 模板即可，不需要走这个
- 模板本身丢失 → 走 [rebuild-from-source.md](rebuild-from-source.md)

## 核心模型

```
业务项目 (评估 / 迭代框架的试验田)
   │
   │  提取框架级改动 + 反向映射业务占位符
   ▼
模板 (所有新项目的起点)
```

**方向单向**：只从业务项目**读**，只往模板**写**。脚本不会反向污染业务项目。

**判定"什么算框架"**：

| 类型 | 处理 | 例子 |
|------|------|------|
| 纯框架代码 | 自动同步（在 MANIFEST 里） | `harness/bin/*.py`、`scripts/lint-deps.py`、`scripts/verify/checks/*.py` |
| 框架文档 | 自动同步 | `docs/DEVELOPMENT.md`、`harness/split-task-checklist.md`、`CLAUDE.md` |
| 框架入口 | 自动同步 | `Makefile`、`init.sh`、`.gitignore` |
| Claude 配置 | 自动同步（仅框架侧） | `.claude/agents/*.md`、`.claude/roles/coordinator.md`、`.claude/settings.json` |
| Playbook 自身 | 自动同步 | `docs/playbooks/sync-upstream.{md,sh}`、`docs/playbooks/rebuild-from-source.md` |
| 模板专属 | **不同步** | `README.md`（业务项目的 README 没意义）、`docs/PRODUCT_SENSE.md`、`src/backend/pom.xml`、`src/frontend/package.json` |
| 业务代码 | **不同步** | 任何 `src/**` 下的业务实现、`tests/unit/test_xxx.py` 业务单测 |
| 运行时状态 | **不同步** | `harness/exec-plans/*.md`、`harness/tasks/**`、`.claude/worktrees/**`、`.claude/settings.local.json`、`.DS_Store` |

分歧点判断：**这个文件在下一个新项目里复制过去是否有价值？** 有则同步，无则不同步。

## 前置条件

- 业务项目是 git 仓库且 `git status` 干净（或至少你知道现在处于什么状态）
- 模板也是 git 仓库；所有待同步改动可以放到一个新 commit 里
- 你对"框架 vs 业务"的边界有判断力——脚本只做机械部分，语义决策在你

## 执行步骤

### 1. Preflight：先看业务项目里动了什么

在业务项目里看一下自上次同步以来框架文件的改动：

```bash
cd /path/to/business-project
git log --oneline -- harness/ scripts/ Makefile init.sh CLAUDE.md docs/DEVELOPMENT.md | head -20
```

如果看到的都是业务提交，跳过同步。如果有 "fix harness ...", "add verify check ..." 之类的，继续。

### 2. Dry-run

在**模板目录**下跑：

```bash
cd /path/to/template
docs/playbooks/sync-upstream.sh --from /path/to/business-project --dry-run
```

脚本会：
1. 读 MANIFEST（脚本顶部硬编码的框架文件清单）
2. 把业务项目里的符号反向映射回模板占位符（见脚本 `REVERSE_MAPPINGS`）
3. 对每个文件输出 diff
4. 列出在业务项目里不存在的 manifest 条目（可能是业务项目没同步更早版本）

**读 diff 的时候问自己**：
- 这个改动是"框架的普适改进"还是"业务项目的特定需求"？
- 例：`creator.py` 加了个 `REQUIRED_AGENTS` → 框架改进，同步
- 例：`style.py` 加了对业务特定文件名的白名单 → 业务特定，**跳过**

### 3. 实际同步（带逐文件确认）

```bash
docs/playbooks/sync-upstream.sh --from /path/to/business-project
```

对每个有 diff 的文件，脚本会暂停让你 `y/N/q`。你可以：
- `y` 接受这个文件的改动
- `N`（默认）跳过本文件
- `q` 立刻退出（已经确认的不会被回滚）

> **提醒**：脚本是"整文件替换"，不是行级 patch。如果业务项目里一个文件**混了框架改进和业务特定逻辑**，你要么先在业务项目里把那一次混合提交拆干净再跑，要么手动编辑模板。

### 4. 回归检查（脚本自动跑）

同步完成后脚本自动执行：
- `python3 harness/bin/executor.py check`（模板自检）
- `init.sh` 括号防回归检查（历史踩过坑，见下文）
- 反向映射遗漏检查（模板不应残留业务占位符）

任一失败都会退出并提示具体位置。

### 5. 人工复核 + 提交

```bash
cd /path/to/template
git diff                            # 完整 diff 过一遍
git add -A
git commit -m "sync: upstream from <business-slug> (<date>)"
git push
```

commit message 建议格式：`sync: upstream from <项目名> (YYYY-MM-DD)`，方便以后回溯。

## MANIFEST 管理

MANIFEST 在 `sync-upstream.sh` 顶部硬编码：

```bash
MANIFEST=(
    "harness/bin/executor.py"
    "harness/bin/creator.py"
    ...
)
```

**什么时候要改 MANIFEST**：
- 业务项目加了一个纯框架级新文件（例如 `scripts/verify/checks/security.py`） → 加进来
- 模板发现某个条目其实是模板专属（不应跨项目同步） → 删掉
- **不要加**：任何带业务命名的文件（`ServiceXxx.java`、`useXxxStore.ts`）

## 反向映射管理

反向映射表负责把业务项目的符号翻译回模板占位符：

```bash
REVERSE_MAPPINGS=(
    "com.xptsqas|com.xptsqas"
    "com/xptsqas|com/xptsqas"
)
```

**什么时候加映射**：
- 业务项目里的 Java 包名、数据库名、项目名等在框架文件中出现过 → 加一行
- 映射**单向**：左是业务侧、右是模板侧
- 增加的映射应**字符串级精确**（避免误改：`foo` → `bar` 可能把 `foobar` 改成 `barbar`），如果容易冲突，用更长的上下文字符串

## 已知陷阱

### 1. init.sh 全角括号触发 `set -u`

**现象**：init.sh 里 `$VAR）`（变量名后直接紧贴中文 `）`），bash 在某些版本会把 `）` 当作标识符延续，`set -u` 下报 `VAR）: unbound variable`。

**防范**：脚本里 grep 检查 `\$[A-Z_]+[）]`；写代码时一律用 `${VAR}` 带花括号。

### 2. 业务项目跑完 harness 任务后 tests/ 有 `.pytest_cache/`

**现象**：`.pytest_cache/v/cache/nodeids` 里包含业务测试名。rsync 过来会泄漏业务词汇。

**防范**：MANIFEST 不覆盖整个 `tests/` 目录，只同步 `conftest.py` / `pytest.ini` / `requirements.txt`。

### 3. 业务项目在 `scripts/verify/` 下放了项目专属 JSON

**现象**：`scripts/verify/api_config.json`、`scripts/verify/e2e_config.json` 里全是业务端点。

**防范**：MANIFEST 不包含这两个 JSON（只同步 `*.py`）。模板自己维护占位版本。

### 4. 两个 repo 的 `.env` 互窜

**现象**：`.env.example` 会同步，但 `.env` 实际凭据不应窜。

**防范**：脚本 MANIFEST 只含 `.env.example` 不含 `.env`；模板 `.gitignore` 忽略 `.env`。

### 5. 业务项目 MANIFEST 文件缺失

**现象**：脚本汇总出现 `⚠️ 源缺失: N` 条。

**诊断**：
- 业务项目比模板老，框架文件还没拉通 → 不是问题，等业务项目升级再回流
- 业务项目重构过 → 可能 MANIFEST 路径过时，需要更新

## 边界情况

### Q: 业务项目里 harness 的某个文件我改了**一行**，但其他地方没动。脚本会让我整文件替换模板的该文件吗？

A: 是的，脚本是整文件替换。如果你不希望模板里某些独立的改进被冲掉，有两个选择：
1. 业务项目和模板的对应文件保持"除业务占位符外完全相同"——正常维护节奏下这是常态
2. 对这个文件在 sync 时选 `N`，然后手工把这一行改动 patch 到模板

### Q: 业务项目里加了一个 `.claude/agents/executor-code.md`。应该回流吗？

A: 自 2026-04 起 MANIFEST 已覆盖 `.claude/agents/*.md`、`.claude/roles/coordinator.md`、`.claude/settings.json`——稳定的 agent 定义、coordinator 角色、项目级 hooks 都会随 sync 一并回流。需要新增 agent 时在 `.claude/agents/` 加文件、在 MANIFEST 对应加一行即可。

注意：`.claude/` 默认在 `.gitignore` 里。模板侧要让这些文件入 git，需要在 `.gitignore` 开白名单（`!.claude/agents/`、`!.claude/roles/`、`!.claude/settings.json`），或在首次 commit 时 `git add -f` 强制加入。`.claude/worktrees/`、`.claude/settings.local.json`、`.DS_Store` 永远不回流。

### Q: 如果同时有多个业务项目都在迭代框架怎么办？

A: 逐个 sync，每次 commit。脚本不支持多源合并——那是 human judgment 活，按顺序跑不会出错。