# 开发框架模板（xptsqas）

> **这是什么**：一个可直接 clone 的新项目脚手架。内置**分层架构约束**、**自动化验证管道**、以及**Harness 任务引擎**——从 0 到 1 搭建新项目时只需 `./init.sh <项目名>` 即可获得一套可立刻运行、可立刻按流程交付的骨架。
>
> 默认技术栈：**Spring Boot 3 + Vue 3 + MySQL 8**。可整体替换，但分层规则与 Harness 流程不随技术栈变化。

---

## 快速开始

```bash
# 1. 克隆模板到新项目目录
git clone <模板地址> my-new-project && cd my-new-project

# 2. 用项目名初始化（自动替换包名、重命名 Java 目录、改项目名）
./init.sh my-new-project

# 3. 准备环境配置
cp .env.example .env         # 按需改 DB_PASS / 端口等

# 4. 构建 & 自检
make build
python3 harness/bin/executor.py check   # 框架自检（返回 0 即 OK）

# 5. 启动
cd src/backend && mvn spring-boot:run     # 后端
cd src/frontend && npm install && npm run dev   # 前端
```

详细本地启动步骤见 [docs/dev-startup.md](docs/dev-startup.md)。

---

## 技术栈（默认，可替换）

| 类别 | 默认技术 | 可替换为 |
|------|----------|----------|
| 后端 | Spring Boot 3 · Java 17 · MyBatis-Plus | 任何后端栈（Go、Node、Python FastAPI 等） |
| 数据库 | MySQL 8 | Postgres、TiDB 等 |
| 前端 | Vue 3 · Vite · Element Plus · Pinia | React、Svelte 等 |
| 反向代理 | Nginx | Traefik、Caddy 等 |
| 工具链 | Make · Python 3 | 保留（验证脚本依赖） |

替换技术栈时同步更新：`src/backend/pom.xml` 或等价构建配置、`src/frontend/package.json`、`docs/ARCHITECTURE.md` 技术栈表、`Makefile` 相关目标。

---

## 目录结构

```
.
├── CLAUDE.md                   # 给 Claude Code 的项目指令（框架约定摘要）
├── Makefile                    # build / test / lint-arch / fix-arch 等入口
├── init.sh                     # 模板重命名脚本（首次运行）
├── .env.example                # 环境变量模板
├── docs/
│   ├── ARCHITECTURE.md         # 分层规则、技术栈、部署拓扑
│   ├── DEVELOPMENT.md          # 开发 / 测试 / lint 命令
│   ├── PRODUCT_SENSE.md        # 业务上下文（模板存根，按项目改写）
│   └── dev-startup.md          # 本地启动指南
├── harness/
│   ├── bin/                    # Harness 引擎（executor / creator / rubric / state）
│   ├── split-task-checklist.md # 拆分任务规范
│   ├── exec-plans/             # 任务执行计划（运行时产出）
│   ├── tasks/                  # 任务检查点（运行时产出）
│   ├── memory/                 # 审计/批判记录（运行时产出）
│   ├── trace/ / verify/ / design-docs/
├── scripts/
│   ├── validate.py             # 构建/测试/lint/verify 调度器
│   ├── lint-deps.py            # 分层依赖静态检查
│   └── verify/                 # 多维验证（arch/style/api/e2e）
├── src/
│   ├── backend/                # 分层骨架（types/utils/config/core/api），业务为空
│   ├── frontend/               # 分层骨架（types/utils/config/core/ui），业务为空
│   └── database/schema/        # SQL 迁移（模板为空）
└── tests/
    ├── unit/ / integration/ / e2e/   # pytest 补充层
    ├── conftest.py / pytest.ini / requirements.txt
```

---

## 分层规则（框架核心约束）

| 层级 | 目录 | 允许依赖 |
|------|------|----------|
| Layer 0 | `types/` | 无 |
| Layer 1 | `utils/` | Layer 0 |
| Layer 2 | `config/` | Layer 0-1 |
| Layer 3 | `core/services/` `core/repository/` | Layer 0-2 |
| Layer 4 | `api/` `ui/` | Layer 0-3，彼此不互相引用 |

- `make lint-arch`：静态校验依赖方向
- `make fix-arch`：对可修复的违规做格式化修复

---

## Harness 引擎（任务闭环）

所有非简单任务（>3 文件 / 跨层 / 需评审）走统一闭环：

```
check → init <slug> → 填充 exec-plan → ExitPlanMode → approve → 编码 → verify → complete
```

关键命令：

```bash
python3 harness/bin/executor.py check              # 环境自检
python3 harness/bin/creator.py audit               # 给框架健康度打分
python3 harness/bin/executor.py init <slug> "描述"  # 创建任务骨架
python3 harness/bin/executor.py verify <slug>      # 按 exec-plan 范围验证
python3 harness/bin/executor.py complete <slug>    # 归档任务
```

更多说明见 [CLAUDE.md](CLAUDE.md) 与 [harness/split-task-checklist.md](harness/split-task-checklist.md)。

---

## 常用 Make 命令

| 命令 | 作用 |
|------|------|
| `make build` | 构建后端（Maven）+ 前端（Vite） |
| `make test` | 运行单测（Java / Vitest / pytest） |
| `make lint-arch` | 校验分层依赖 + 代码风格 |
| `make fix-arch` | 可修复项自动修复 |
| `make verify` | 全量验证（arch + style + api + e2e） |
| `make db-init` / `db-reset` | MySQL 初始化 / 重置（按 `.env` 配置） |

---

## 质量标准

- **结构化日志**，禁止 `console.log` / `print()`
- **单文件 ≤ 500 行**（由 `scripts/verify/checks/style.py` 强制）
- **命名约定**：PascalCase（类型）、camelCase（函数）、kebab-case（文件名）

---

## init.sh 做了什么

1. 替换所有文本文件中的 `xptsqas` → `<新项目名>`，`com.xptsqas` → `com.<新项目名>`
2. 重命名 Java 包目录：`src/backend/src/main/java/com/xptsqas/` → `.../com/<新项目名>/`
3. 同步 `creator.py` / `lint-deps.py` / `verify/checks/style.py` 中的包名常量
4. 更新 `pom.xml` 的 `groupId` / `artifactId`、`package.json` 的 `name`

脚本幂等：若传入名称与当前一致则直接退出。

---

## 贡献 / 协议

按项目需要在此处补充 License、贡献指南、联系人等信息。
