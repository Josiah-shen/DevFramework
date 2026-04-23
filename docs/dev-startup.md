# 本地开发环境启动指南

> 所有连接信息以 `.env` 为准（由 `.env.example` 复制而来）。本文中的 `${DB_USER}` / `${DB_PASS}` / `${DB_NAME}` / `${FRONTEND_PORT}` / `${BACKEND_PORT}` 等均是占位符，请按项目实际值替换或 `source .env` 注入。

## 环境依赖

| 组件 | 工具 | 说明 |
|------|------|------|
| 数据库 | MySQL 8+（本机或 Docker） | 端口 `${DB_PORT}`（默认 3306） |
| 后端 | Spring Boot + Maven | 端口 `${BACKEND_PORT}`（默认 8088，被占用时自动递增） |
| 前端 | Vite + Vue 3 | 端口 `${FRONTEND_PORT}`（默认 5173） |

---

## 启动步骤

### 1. 启动 MySQL

```bash
brew services start mysql   # 或 docker compose up -d db
```

**连接信息（从 `.env` 读取）：**
- host: `${DB_HOST}`
- port: `${DB_PORT}`
- user: `${DB_USER}`
- password: `${DB_PASS}`
- database: `${DB_NAME}`

---

### 2. 初始化数据库（**仅首次**）

```bash
mysql -u ${DB_USER} -p${DB_PASS} -e \
  "CREATE DATABASE IF NOT EXISTS ${DB_NAME} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"

# 业务 schema 与种子数据按项目放入 src/database/schema/ 后再导入：
# mysql -u ${DB_USER} -p${DB_PASS} ${DB_NAME} < src/database/schema/<your-schema>.sql
```

> 后续启动**跳过此步**，数据已持久化在本地 MySQL。

---

### 3. 启动后端

```bash
cd src/backend
DB_PASS=${DB_PASS} mvn spring-boot:run
```

启动后访问：`http://localhost:${BACKEND_PORT}/api`。

---

### 4. 启动前端

```bash
cd src/frontend
npm install        # 仅首次
npm run dev
```

启动后访问：`http://localhost:${FRONTEND_PORT}`。

---

## 重启服务

| 目标 | 命令 |
|------|------|
| 后端 | 终端 `Ctrl+C` 后重新 `mvn spring-boot:run` |
| 前端 | 终端 `Ctrl+C` 后重新 `npm run dev` |
| MySQL | `brew services restart mysql` 或 `docker compose restart db` |

---

## 注意事项

- **CORS**：后端 `application.yml` 的允许来源需与 `${FRONTEND_PORT}` 保持一致，或通过 `APP_CORS_ALLOWED_ORIGINS` 环境变量覆盖。
- **生产部署**：使用 `docker-compose.yml`（如提供），不走本指南流程。
- **密码重置 / 其他项目特定运维流程**：请在项目 fork 后按实际情况补充。
