# Playbook：从业务项目重建模板（附录）

## 这篇是什么

**定位**：模板目录丢失、损坏、或想基于某个活跃业务项目**从零重建**一份干净模板时用。

**适用频率**：极低。一个健康的工作流里，模板应该是 git repo 持续维护，日常通过 [sync-upstream.md](sync-upstream.md) 回流改进——你永远不应该"定期重建"。

**触发信号**（满足其一才跑本 playbook）：
- 模板 git repo 丢了、远端不可用、本地副本覆盖了又没 push
- 模板与实际业务项目偏离太大，回流成本高于重建
- 想换一个新的业务项目作为新模板的基础

## 执行流程概览

```
业务项目 (污染状态：含业务代码 / 业务文档 / 历史记录)
   │
   │  1. rsync 带 exclude 批量复制
   │  2. 清空 src/ 业务代码，重建分层骨架
   │  3. 反向映射业务占位符 → 模板占位符
   │  4. 重写模板专属文件 (README / PRODUCT_SENSE / ARCHITECTURE 等)
   │  5. 补齐 verify stubs、.gitkeep
   ▼
干净模板
```

## 前置条件

- 业务项目在本地且 git 状态干净
- 已确认反向映射表（业务占位符 → 模板占位符），例如 `<业务 Java pkg> → com.xptsqas`
- 目标模板路径空闲或你愿意覆盖

## 步骤

### 1. rsync 复制 + 多层排除

使用 **exclude 策略先复制、再二次清理**，**不要**指望一次 rsync exclude 列全（历史踩坑：深层业务目录 glob 匹配不到）。

```bash
SRC=/path/to/business-project
TPL=/path/to/new-template

rsync -a \
  --exclude='.git' --exclude='node_modules' --exclude='target' \
  --exclude='dist' --exclude='.DS_Store' \
  --exclude='.claude/worktrees' --exclude='.claude/settings.local.json' \
  --exclude='.claude/.DS_Store' \
  --exclude='.env' \
  --exclude='harness/exec-plans/*.md' \
  --exclude='harness/tasks/*/' \
  --exclude='harness/memory/*.md' \
  --exclude='harness/trace/*' \
  --exclude='harness/verify/*' \
  --exclude='harness/design-docs/*' \
  --exclude='docs/design-docs/*' \
  --exclude='docs/exec-plans' \
  --exclude='docs/tasks' \
  --exclude='scripts/verify/api_config.json' \
  --exclude='scripts/verify/e2e_config.json' \
  --exclude='tests/测试报告_*.md' \
  --exclude='tests/.pytest_cache' \
  --exclude='harness/bin/__pycache__' \
  --exclude='*.pyc' \
  "$SRC/" "$TPL/"
```

> **业务代码排除用目录级操作**：`--exclude='tests/unit/test_*.py'` 这类 glob 容易漏，改用复制完再 `rm` 更稳。

### 2. 清空 src/ 业务代码，重建分层骨架

```bash
rm -rf "$TPL/src/backend/src"
rm -rf "$TPL/src/frontend/src" "$TPL/src/frontend/public"
rm -f "$TPL/src/frontend/package-lock.json"
rm -f "$TPL/src/database"/*.sql

# Backend 分层骨架（模板占位包名：com.xptsqas）
BASE="$TPL/src/backend/src/main/java/com/xptsqas"
for d in types utils config core/services core/repository api; do
    mkdir -p "$BASE/$d" && touch "$BASE/$d/.gitkeep"
done
mkdir -p "$TPL/src/backend/src/test/java/com/xptsqas" && touch "$TPL/src/backend/src/test/java/com/xptsqas/.gitkeep"
mkdir -p "$TPL/src/backend/src/main/resources" && touch "$TPL/src/backend/src/main/resources/.gitkeep"

# Frontend 分层骨架
for d in types utils config core ui; do
    mkdir -p "$TPL/src/frontend/src/$d" && touch "$TPL/src/frontend/src/$d/.gitkeep"
done
mkdir -p "$TPL/src/frontend/public" && touch "$TPL/src/frontend/public/.gitkeep"
mkdir -p "$TPL/src/database/schema" && touch "$TPL/src/database/schema/.gitkeep"
```

### 3. 清空业务测试，保留框架级文件

```bash
find "$TPL/tests" -name 'test_*.py' -delete
# 保留：conftest.py / pytest.ini / requirements.txt / __init__.py / 目录
```

### 4. harness 空目录补 `.gitkeep`

```bash
for d in exec-plans tasks memory trace verify design-docs; do
    mkdir -p "$TPL/harness/$d"
    [ -z "$(ls -A "$TPL/harness/$d" 2>/dev/null)" ] && touch "$TPL/harness/$d/.gitkeep"
done
mkdir -p "$TPL/docs/design-docs" && touch "$TPL/docs/design-docs/.gitkeep"
```

### 5. 反向映射业务占位符 → 模板占位符

把业务项目里的包名/项目名等反向翻译成模板占位符。最省力的办法是直接**跑一次 sync-upstream.sh**（它已经内置这套映射）：

```bash
# 前提：先把 playbook 文件恢复到模板
cp -r /some/backup/docs/playbooks "$TPL/docs/"

"$TPL/docs/playbooks/sync-upstream.sh" --from "$SRC" --yes
```

或手动对框架代码里的 4 处包名硬编码做 sed（**只需改这四个地方，其他代码不含业务命名**）：

| 文件 | 字段 |
|------|------|
| `harness/bin/creator.py` | `BACKEND_JAVA_ROOT` |
| `scripts/lint-deps.py` | `_JAVA_GROUP`、`_BACKEND_JAVA_PREFIX` |
| `scripts/verify/checks/style.py` | `_BACKEND_JAVA_ROOT` |
| `harness/split-task-checklist.md` | 示例路径 |

### 6. 重写模板专属文件

这些是模板**不从业务项目同步的本地文件**（上一步 sync-upstream 不会动它们）：

| 文件 | 内容方向 |
|------|----------|
| `README.md` | 改为"框架使用说明"，去掉业务描述 |
| `docs/PRODUCT_SENSE.md` | 改为空占位模板（`<业务描述>` 格式） |
| `docs/ARCHITECTURE.md` | 技术栈表与部署拓扑改占位符化 |
| `docs/dev-startup.md` | 具体凭据改 `${DB_USER}` / `${DB_PASS}` / `${DB_NAME}` 引用 |
| `src/backend/pom.xml` | `groupId` / `artifactId` / `name` / `version` 改 xptsqas 版 |
| `src/frontend/package.json` | `name` 改 `xptsqas-frontend`、`version` 改 `0.1.0` |
| `scripts/verify/api_config.json` | 改最小占位（只留 `/actuator/health`） |
| `scripts/verify/e2e_config.json` | 改空 `scenarios` 数组 |
| `.env.example` | 确保含 `DB_HOST` / `DB_NAME` / `DB_USER` / `DB_PASS` / `DB_PORT` / `BACKEND_PORT` / `FRONTEND_PORT` / `PROJECT_NAME` |

### 7. 修 init.sh 已知 bug（如果是直接从源复制）

```bash
# 若 init.sh 里出现 $VAR） 紧贴中文全角括号，bash set -u 会误解析
grep -nE '\$[A-Z_]+[）]' "$TPL/init.sh"
# 若有匹配：把对应行的 $VAR 改成 ${VAR}
```

### 8. 验证模板完整性

```bash
cd "$TPL"

# 1) 执行自检
python3 harness/bin/executor.py check
# 期望：exit 0，JSON 里 ok: true

# 2) 审计评分
python3 harness/bin/creator.py audit
# 期望：score >= 80（.claude/agents/ 缺失的 20 分可接受）

# 3) init.sh 干跑（用 /tmp 副本避免污染模板）
TEST_DIR=$(mktemp -d)
cp -r "$TPL" "$TEST_DIR/probe"
cd "$TEST_DIR/probe" && ./init.sh demo
grep -rln "xptsqas" --include="*.py" --include="*.md" --include="*.xml" --include="*.json" . | grep -v /playbooks/
# 期望：输出为空（所有 xptsqas 已替换为 demo）
cd "$TPL" && rm -rf "$TEST_DIR"

# 4) 反向映射遗漏
grep -rln "com\.nanjing\|nanjing-carbon" --include="*.py" --include="*.md" --include="*.xml" --include="*.json" . | grep -v /playbooks/
# 期望：输出为空
```

### 9. 初始化 git + 提交

```bash
cd "$TPL"
git init
git add -A
git commit -m "init: bootstrap framework template from <business-slug> (YYYY-MM-DD)"
```

## 完整踩坑清单

| # | 陷阱 | 防范 |
|---|------|------|
| 1 | rsync `--exclude='tests/unit/test_*.py'` 某些版本 glob 不递归 | 复制后再 `find ... -delete` |
| 2 | `.env` 会被 rsync 带上 | `--exclude='.env'` + 事后 `rm` 兜底 |
| 3 | `tests/.pytest_cache/v/cache/nodeids` 含业务测试名 | `--exclude='tests/.pytest_cache'` |
| 4 | `src/frontend/public/` 含业务地理数据 / 静态资源 | 完全 `rm -rf` 后 `.gitkeep` |
| 5 | `src/frontend/package-lock.json` 是业务项目锁 | 删掉，让新项目 `npm install` 重建 |
| 6 | `scripts/verify/*.json` 是项目专属 API/E2E 配置 | exclude + 事后建占位 |
| 7 | `docs/exec-plans/`、`docs/tasks/` 这两个子目录易漏 | rsync exclude 里要显式加 |
| 8 | init.sh `$VAR）` 紧贴中文全角括号触发 `set -u` 误解析 | 改 `${VAR}）`；playbook 脚本内置 grep 防回归 |
| 9 | `harness/bin/__pycache__` | exclude `*.pyc` + `__pycache__` |
| 10 | 业务项目的 `pom.xml` 版本号 `2.0.0` 继承来不合适 | 模板用 `0.1.0` |
| 11 | `harness/exec-plans/*.md`、`tasks/*/`、`memory/*.md` 含历史任务 | exclude 后 `.gitkeep` |
| 12 | 整个 `.claude/` 是运行时状态 | 完全 exclude |

## 判定标准：什么算框架 vs 业务

同 [sync-upstream.md 的判定表](sync-upstream.md#核心模型)。此处不重复。

## 完成后

重建好的模板之后日常维护走 [sync-upstream.md](sync-upstream.md) 即可——重建 playbook 希望永远不用第二次。