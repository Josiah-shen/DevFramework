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

确定性失败（编译错、语法错、模块缺失）会被识别并立即跳过剩余重试，详见 `harness/lib/failure_classifier.py`。扩展规则只需在 `DETERMINISTIC_PATTERNS` 追加 `(pattern, reason)` 元组；新增规则请在 `harness/tests/test_failure_classifier.py` 补正/反向用例各一条。

## E2E 测试运行

### 触发与执行机制

- 自动化 verify（`python3 harness/bin/executor.py verify <slug>`）会读取 exec-plan 的 `## 影响范围`，按 scope 收敛 E2E 执行范围。
- `standard` profile 使用 scope 化路径，只运行能从影响范围推导出的 spec；`full` profile 跑全量 E2E 两轮。
- E2E step 拆成两轮：先无头执行 `pytest tests/e2e/<spec> -v`，通过后再执行有头版本 `pytest tests/e2e/<spec> -v --headed`。
- 两轮执行采用 fail fast：无头阶段失败时直接终止，不再进入有头阶段。
- 本地手工全量入口保持不变：`make pytest-e2e` 仍执行无头 + 有头双轮全量。
- 自动化 verify 的 scope 来自 exec-plan，不会从 git diff 临时猜测范围。
- scope 中可以写目录或文件；目录命中时按该目录下相关文件继续推导。
- 多个 scope 同时命中时取并集，任一全局路径命中都会触发全量降级。
- E2E 未被触发时，其他 validate step 仍按原 profile 执行。

### scope → spec 推导规则

按以下优先级从 scope 推导 E2E spec：

1. scope 直接包含 `tests/e2e/test_*.py` 时，仅运行对应 spec。
2. scope 命中"19 项全量降级清单"路径时，全量运行 `tests/e2e`。
3. 通过 `tests/e2e/route_map.json` 反查 `spec.keywords` / `spec.routes`，运行匹配 spec。
4. 使用 `scripts/validate.py` 中的 `_E2E_SPEC_NAME_HINTS` 做文件名启发式兜底。
5. 仍无法推导时，执行全量降级。

开发者维护时按以下口径判断：

- 明确知道覆盖 spec 时，优先把 spec 路径写入 exec-plan 的 `## 影响范围`。
- 页面路由相关改动优先依赖 `route_map.json`，避免在 validate 中追加临时规则。
- 文件名启发式只作为兜底，不能替代 spec 顶部的 route marker。
- 无法确认影响面的改动应接受全量降级，不要强行缩小 scope。

### 19 项全量降级清单

以下路径改动视为全局影响，scope 化会降级为全量 E2E：

**入口配置 (5)**：影响全局应用配置和构建过程。

- `package.json`
- `package-lock.json`
- `index.html`
- `vite.config.js`
- `.eslintrc.cjs`

**应用入口 (2)**：影响应用启动和顶层挂载。

- `main.js`
- `App.vue`

**路由 + 全局布局 (3)**：影响全局路由解析和页面框架。

- `src/frontend/src/ui/router/`
- `AdminLayout.vue`
- `ScreenLayout.vue`

**HTTP 拦截器与服务汇总 (2)**：影响所有网络请求和服务初始化。

- `http.js`
- `index.js`

**通用工具 (3)**：影响全局工具函数使用结果。

- `logger.js`
- `format.js`
- `url.js`

**公共资源 (1)**：影响全局静态资源加载。

- `public/`

**测试基础设施 (3)**：影响全局 E2E 测试环境。

- `conftest.py`
- `pytest.ini`
- `requirements.txt`

维护全量降级清单时遵循以下原则：

- 只收录会影响多条业务路径、测试运行环境或应用启动链路的路径。
- 单个页面、单个表单、单个业务组件不应直接加入全量降级清单。
- 新增项必须同步说明所属类目，避免清单变成无边界的兜底集合。
- 删除项前先确认已有 spec、route marker 和文件名启发式能覆盖原有风险。

### 路由 marker 自动同步（route_map）

- 每个 E2E spec 顶部声明 `pytestmark = [pytest.mark.e2e, pytest.mark.routes("/screen/dashboard", ...)]`，用 `pytest.mark.routes` 标明覆盖路由。
- `harness/bin/build_e2e_route_map.py` 通过 ast 静态解析所有 spec，并全量重写 `tests/e2e/route_map.json`。
- `.claude/settings.json` 的 `PostToolUse` hook 会在 Edit/Write `tests/e2e/test_*.py` 后自动调用 `build_e2e_route_map` 同步 JSON。
- 需要手工同步时运行：

```bash
python3 harness/bin/build_e2e_route_map.py
```

同步结果需要满足以下预期：

- `route_map.json` 与 spec 中的 route marker 保持同一提交内更新。
- 每个 route marker 只描述该 spec 实际覆盖的页面路径。
- spec 迁移或重命名后重新运行 builder，避免旧 spec key 残留。
- hook 未触发时以手工命令结果为准，不手写 JSON。

### 维护流程

- **新增 spec 时**：在文件顶部添加 `pytest.mark.routes(...)`，列出该 spec 覆盖的路由；hook 会自动同步 `route_map.json`；同时在 `_E2E_SPEC_NAME_HINTS` 字典补一项作为兜底。
- **新增前端页面/组件时**：单页面影响通常由 scope 自动覆盖；若属于全局影响路径，在 `_E2E_FULL_FALLBACK_PATHS` 添加对应路径。
- **修改路由 marker 后**：hook 会自动重建 `route_map.json`；提交前确认 diff 中 spec 与 JSON 同步出现。
- **调整降级范围时**：优先确认改动是否会影响应用启动、路由框架、网络层、公共工具、公共资源或测试环境；只有全局影响才加入全量降级清单。
- **重命名 spec 时**：同步检查 `_E2E_SPEC_NAME_HINTS` 的 key 和 `route_map.json` 中的 spec 记录。
- **删除 spec 时**：删除对应启发式兜底，并确认没有 scope 仍指向旧 spec。
- **新增公共工具时**：先判断是否只服务单一页面；若被多业务复用，再考虑全量降级。
- **提交前自检**：确认 spec、route marker、route_map、启发式兜底四者没有互相脱节。

### 与 Makefile 入口的关系

- `make pytest-e2e` / `make test-python-e2e`：开发者手工运行全量 E2E，保持无头 + 有头双轮全量。
- 自动化 verify：使用 scope 化双轮执行，并保持 fail fast 策略。
- `pytest-e2e-headless` 与 `pytest-e2e-headed` 是 Makefile 中的分阶段入口，供全量双轮流程复用。
- Makefile 手工入口与自动化 verify 互不干扰；开发者需要快速验证影响范围时优先使用 verify，需要完整回归时使用 Makefile 全量入口。
- 调试单个 spec 时可以直接调用 `pytest tests/e2e/<spec> -v`，但提交前仍以 Makefile 或 verify 结果为准。
- CI 或 harness 中需要稳定复现时，优先使用自动化 verify 的 scope 化入口。
- 发布前回归、路由框架调整、测试基础设施调整，应使用 Makefile 全量入口确认。

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
- 测试文件（`*.test.*` / `*.spec.*`）例外：kebab-case 仅校验去掉末尾 `.test` / `.spec` 后的主干，与 vitest/jest 默认命名惯例一致；豁免由 [scripts/verify/checks/style.py](../scripts/verify/checks/style.py) 的 `TEST_SUFFIX_RE` 实现

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

## 聚合查询规范

统计/报表类接口优先在 SQL 层聚合，避免"全量拉取 + Java 侧 groupBy"的反模式（参考 2026-04-23 能耗统计 review：8 个指标各自 `selectEnergyStatsRaw` 全量查询再在 `EnergyStatisticsHelper` 内循环累加，初始化一次页面触发 8× 全表扫描）。

**应在 SQL 层聚合**（满足任一即适用）：

- 原始结果集 > 100 行，或预期随数据增长
- 聚合列（`GROUP BY` 的列、时间维度）已有索引
- 聚合逻辑可用 `SUM / AVG / COUNT / CASE WHEN` 表达
- 同一条业务线需要按多个维度聚合

**可在内存聚合**（需同时满足）：

- 原始结果集 < 100 行，且数据量稳定
- 聚合涉及业务规则切换、单位换算链路复杂，SQL 难以维护

**反模式**：同一张表按不同维度拉全量再在 Java 侧 `stream().collect(groupingBy)`；或并发 N 个接口，每个接口后端再各自全量查询，把 O(行数) 放大为 O(N × 行数)。

**建议做法**：用一条 `GROUP BY` 查询返回聚合结果；多维度用 `GROUPING SETS` 或 `CASE WHEN` 一次返回。对比示例（能耗按机构月份聚合）：

```java
// 反模式：全量取回 Java 侧分组
List<Row> rows = mapper.selectEnergyStatsRaw(params, null);  // 全表扫描
Map<Long, BigDecimal> byOrg = rows.stream()
    .collect(groupingBy(Row::getOrgId,
        mapping(Row::getAmount, reducing(ZERO, BigDecimal::add))));
```

```xml
<!-- 推荐：SQL 预聚合，只返回聚合行 -->
SELECT org_id,
       DATE_FORMAT(data_date, '%Y-%m') AS period,
       SUM(amount * et.coal_equiv_factor) AS kgce
FROM energy_consumption ec
JOIN energy_type et ON ec.energy_type_id = et.id
WHERE data_date BETWEEN #{start} AND #{end}
GROUP BY org_id, period
```

如需多指标合并，优先提供"批量聚合接口"返回一次查询结果，而非前端 `Promise.all` 并发触发多个全量接口。

## 验证前置条件

`make validate` / `make verify` 的 `接口存活` 与 `业务路径` 两项依赖后端在
`127.0.0.1:8088` 监听。后端离线时管道会 fail-fast 并打印启动指引，不会
"静默跳过"。

本地启动后端：

    cd src/backend && mvn spring-boot:run

若只想跑不依赖后端的子集：

    make build && make lint-arch && make test