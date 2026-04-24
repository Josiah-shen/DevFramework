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