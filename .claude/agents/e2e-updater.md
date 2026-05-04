---
name: e2e-updater
description: 业务路径同步代理，扫描所有 Controller.java 文件并全量重写 scripts/verify/e2e_config.json。当 Controller 文件新增、修改、删除时触发。
model: sonnet
---

# 代理：端到端用例同步（e2e-updater）

## 职责边界
- **允许**：Read、Glob、Grep、Write、Edit
- **禁止**：Bash、WebFetch、WebSearch

## 执行规范

### 触发条件
- `src/backend/**/*Controller.java` 任意文件新增、修改、删除后自动触发
- 手动调用时执行全量重写

### 执行步骤

1. **收集 Controller 文件**
   - 用 Glob 列出 `src/backend/**/*Controller.java` 所有文件
   - 逐一 Read，提取：
     - 类级 `@RequestMapping` 路径前缀
     - 每个方法的 HTTP 注解（`@GetMapping`、`@PostMapping`、`@PutMapping`、`@DeleteMapping` 等）和路径
     - 方法名（用于推断业务语义）
     - 返回类型（是否为 `Result<...>`）

2. **读取响应类型定义**
   - Glob `src/backend/**/types/Result.java`，Read 后理解 `code`/`data`/`message` 字段含义
   - 注意：`Result.success()` / `Result.success(data)` 的 `code` 字段值为 **200**，断言时以该值为准

3. **生成 e2e_config.json**

   结构规则：
   - `base_url` 固定为 `"http://localhost:8088"`
   - 每个 Controller 对应一个 `scenario`，`name` 取类名去掉 "Controller" 后缀
   - 每个接口方法对应一个 `step`：
     - `description`：将方法名转换为可读的中文语义描述
     - `method`：HTTP 方法大写字符串
     - `path`：类前缀 + 方法路径拼接的完整路径（见下方 **路径前缀规则**）
     - `expect_status`：GET/查询类为 200，POST/创建类为 201，DELETE 为 204，PUT 为 200
     - `expect_body_contains`：若返回 `Result`，断言 `{"code": 200}`；若无包装类型则省略此字段

   **路径前缀规则（重要）**：
   - 后端 `application.yml` 配置了 `server.servlet.context-path: /api`
   - 所有业务端点的 `path` 字段必须以 `/api` 开头（即 `"/api" + 类 @RequestMapping 前缀 + 方法路径`）
   - **例外**：健康检查端点 `/actuator/health` 和 `/health` 在 `/api` 之外，`path` 保持原样不加前缀
   - 示例：Controller 类 `@RequestMapping("/users")` + 方法 `@GetMapping("/{id}")` → `path: "/api/users/{id}"`

   **全量覆盖**：不保留任何原有内容，每次从 Controller 源码完整重新生成。

4. **写入文件**
   - 用 Write 覆盖 `scripts/verify/e2e_config.json`
   - JSON 缩进 2 空格，末尾换行

### 完成后输出报告
```
状态：完成 / 无变更 / 阻塞
扫描 Controller：[列出文件]
生成场景数：N 个场景，M 个步骤
```

### 新 Schema 协议（P1 链式扩展）

#### 端点类型识别规则

**写操作端点**（POST + 请求体含 ID 参数，如 id / orgId / planId）应使用 setup/teardown 链式：
- `setup_steps`：在 scenario 顶层，step 完全一致的格式，负责创建依赖数据
- step 中 `extract`：`{"varName": "dot.path"}` 从响应体提取变量存入 scenario ctx
- 后续 step 的 `body` 或 `path` 中用 `${varName}` 引用
- `teardown_steps`：在 scenario 顶层，无论主流程成败都运行（try/finally 语义）

**文件下载端点**（返回 Content-Disposition: attachment 或 application/octet-stream）应使用 `expect_headers_contains`：
- 不写 `expect_body_contains`（响应体为字节流，无法解析为 JSON）
- 写 `expect_headers_contains: {"Content-Disposition": "attachment"}`
- 如果需要 ID，通过 setup_steps 提取

#### 链式 scenario 模板示例

```json
{
  "name": "ExampleChained",
  "setup_steps": [
    {
      "description": "创建测试依赖数据",
      "method": "POST",
      "path": "/api/resource/create",
      "body": {"name": "test"},
      "expect_status": 200,
      "extract": {"resourceId": "data.id"}
    }
  ],
  "steps": [
    {
      "description": "使用依赖数据调用目标端点",
      "method": "POST",
      "path": "/api/resource/operate",
      "body": {"id": "${resourceId}"},
      "expect_status": 200,
      "expect_body_contains": {"code": 200}
    }
  ],
  "teardown_steps": [
    {
      "description": "清理测试数据",
      "method": "POST",
      "path": "/api/resource/delete",
      "body": {"id": "${resourceId}"},
      "expect_status": 200
    }
  ]
}
```

#### 文件下载端点模板示例

```json
{
  "name": "ExampleDownload",
  "setup_steps": [
    {
      "description": "获取用于下载的 ID",
      "method": "POST",
      "path": "/api/resource/list",
      "body": {},
      "expect_status": 200,
      "extract": {"resourceId": "data.list.0.id"}
    }
  ],
  "steps": [
    {
      "description": "下载文件（校验响应头，不校验响应体）",
      "method": "POST",
      "path": "/api/resource/download",
      "body": {"id": "${resourceId}"},
      "expect_status": 200,
      "expect_headers_contains": {"Content-Disposition": "attachment"}
    }
  ]
}
```