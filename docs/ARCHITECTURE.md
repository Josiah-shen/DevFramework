# 架构总览

> 本文件描述**框架默认形态**。若项目更换技术栈（例如后端改用 Go、数据库改用 Postgres），请同步更新技术栈表与部署拓扑。分层规则、数据流与依赖规则是框架约束，不随技术栈变化。

## 技术栈（模板默认值，可替换）

| 类别 | 默认技术 | 占位符 |
|------|----------|--------|
| 后端框架 | Spring Boot 3 | `<后端框架>` |
| 后端运行时 | Java 17 | `<运行时>` |
| ORM | MyBatis-Plus | `<ORM>` |
| 数据库 | MySQL 8 | `<数据库>` |
| 前端框架 | Vue 3 + Vite | `<前端框架>` |
| UI 组件库 | Element Plus | `<UI 库>` |
| Web 服务器 | Nginx | `<反向代理>` |
| 构建工具 | Make | Make（保留） |
| 脚本/验证 | Python 3 | Python 3（保留） |

## 部署拓扑

```
用户浏览器
    ↓ HTTP
  <反向代理>
  ├── 静态资源  → 前端构建产物（ui/）
  └── /api/*   → 反向代理 → <后端服务>:<BACKEND_PORT>
                                    ↓
                              <数据库>:<DB_PORT>
```

实际端口与服务名由 `.env` 配置。模板默认：前端 `80` / 后端 `8088` / 数据库 `3306`。

## 分层规则（框架核心约束，不随技术栈变化）

| 层级 | 目录 | 说明 | 允许依赖 |
|------|------|------|----------|
| Layer 0 | `types/` | 纯类型定义 | 无内部依赖 |
| Layer 1 | `utils/` | 工具函数 | Layer 0 |
| Layer 2 | `config/` | 配置 | Layer 0-1 |
| Layer 3 | `core/services/` `core/repository/` | 业务逻辑、数据访问 | Layer 0-2 |
| Layer 4 | `api/` `ui/` | 接口层 | Layer 0-3，彼此不互相引用 |

## 数据流

```
<前端框架> (ui/)  ──HTTP──▶  <反向代理> /api  ──▶  <后端服务> (api/)
                                                        ↓
                                                 core/services/
                                                        ↓
                                                    config/
                                                        ↓
                                                     utils/
                                                        ↓
                                                     types/
                                                        ↓
                                                   <数据库>
```

## 依赖规则

- 高层可依赖低层，低层禁止依赖高层
- Layer 4 各模块（`api`、`cli`、`ui`）彼此不得互相引用
- 跨层引用须通过接口（interface）解耦
- 前端通过反向代理访问后端，不直连后端端口
- 由 `scripts/lint-deps.py` 与 `scripts/verify/checks/arch.py` 自动校验
