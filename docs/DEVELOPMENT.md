# 开发指南

## 构建命令

```bash
make build                      # 后端 Maven 打包 + 前端 npm build（自动跳过缺失部分）
mvn -f src/backend/pom.xml package -DskipTests   # 仅后端
npm --prefix src/frontend run build              # 仅前端
```

## 测试命令

```bash
make test                       # 后端 + 前端 + Python 单元/集成/E2E 全量
mvn -f src/backend/pom.xml test                  # 仅后端
npm --prefix src/frontend run test               # 仅前端
make pytest-all                 # 所有 Python 测试
make pytest-integration         # Python 集成测试
make pytest-e2e                 # Python E2E（无头 + 有头两阶段）
make pytest-report              # 生成 HTML 测试报告
make pytest-install             # 安装 pytest 依赖 + Playwright chromium
```

## Lint 与格式化

```bash
make lint-arch                  # 架构依赖 lint（= lint-deps）
make lint-deps                  # 层级依赖检查（scripts/lint-deps.py）
make fix-arch                   # Java spotless:apply + 前端 eslint --fix
```

## 验证与 Harness

```bash
make verify                     # 运行 scripts/verify/run.py 端到端验证
make validate                   # 运行 scripts/validate.py 统一验证管道
python scripts/validate.py      # 等价调用
make harness-audit              # harness creator 评分
make harness-run                # harness executor check 自检
```

## 数据库命令

```bash
make db-init                    # 初始化数据库（执行 init.sql）
make db-schema                  # db-init + 应用 schema.sql
make db-reset                   # DROP 后重建
```

## 代码质量规则

- 结构化日志，禁止 `console.log` / `print()`
- 单文件不超过 500 行
- 命名规范：PascalCase（类型）、camelCase（函数）、kebab-case（文件名）

## 拆分类任务

将大文件按职责拆分的任务（controller-split、*-impl-split、*-vue-split 等）开工前请阅读
[harness/split-task-checklist.md](../harness/split-task-checklist.md)，其中列出 worktree
依赖软链、baseline 快照、exec-plan 范围声明、verify 预期、review 自检五项。

## 目录约定

- `scripts/lint-deps.*` — 层级依赖检查
- `scripts/verify/checks/style.py` — 代码质量规则（文件行数、命名、调试输出检查），支持 `--scope` 分流范围外历史违规
- `scripts/verify/check-scope.py` — 规则 `boundary/worktree-scope-drift`（warning 阶段），对比 exec-plan 声明与实际 diff
- `scripts/verify/` — 端到端功能验证
- `harness/tasks/` — 任务状态和检查点
- `harness/trace/` — 执行轨迹和失败记录
- `harness/memory/` — 经验教训存储