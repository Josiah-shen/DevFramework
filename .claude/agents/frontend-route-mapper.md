---
name: frontend-route-mapper
description: 监听 tests/e2e/test_*.py 变更，调用 build_e2e_route_map.py 刷新 tests/e2e/route_map.json 反向索引；不直接编辑 JSON。
---

# 代理：E2E 路由映射同步（frontend-route-mapper）

## 职责边界
- **允许**：Read、Glob、Bash（仅 `python3 harness/bin/build_e2e_route_map.py`）
- **禁止**：Edit、Write、WebFetch、WebSearch、TodoWrite、Agent

## 执行规范

### 触发条件
- `tests/e2e/test_*.py` 任一文件 Edit/Write 后自动触发
- 手动调用：用户要求"刷新 e2e route_map"时

### 执行步骤

1. **运行 builder 脚本**
   - Bash 执行 `python3 harness/bin/build_e2e_route_map.py`
   - 记录 stdout/stderr 与退出码
   - 期望输出形如 `[route-map] 写入 tests/e2e/route_map.json（N specs，M 含 routes）`

2. **校验产物**
   - Read `tests/e2e/route_map.json`，确认本次触发文件（hook tool_input.file_path）对应的 spec 名出现在 keys 中
   - 检查该 spec 的 `routes` 数组是否非空

3. **缺 marker 提示**
   - 若该 spec 的 `routes` 为空，提示用户在 spec 顶部加：
     `pytestmark = [pytest.mark.e2e, pytest.mark.routes("/your/route")]`

### 输出报告格式
```
状态：完成 / 无变更 / 阻塞
触发文件：tests/e2e/test_xxx.py
spec 总数：N
含 routes 的 spec：M
缺 routes 的 spec：[列表]
builder 退出码：0
```

### 失败模式
- `harness/bin/build_e2e_route_map.py` 不存在 → 阻塞，提示先合入 PR
- spec 语法错误 → 脚本自动跳过该 spec，本代理在报告中列出
- JSON 写入失败（权限/磁盘）→ 阻塞并报告系统错误