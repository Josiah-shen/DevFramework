---
date: 2026-04-24
task: 验证脚手架模板能否被新项目直接使用（只读检查）
phase: import-check
command: python3 -c "from harness.lib.failure_classifier import is_deterministic_failure; print(is_deterministic_failure('error: cannot find symbol'))"
exit_code: 1
---

## 错误输出

```
Traceback (most recent call last):
  File "<string>", line 1, in <module>
ModuleNotFoundError: No module named 'harness.lib'
```

## 失败位置
`harness/lib/` 目录在仓库中不存在。`harness/` 下实际只有：
- `bin/`
- `design-docs/`
- `exec-plans/`
- `memory/`
- `tasks/`
- `trace/`
- `verify/`
- `split-task-checklist.md`

无论从项目根目录还是任意位置调用，`harness.lib.failure_classifier` 都无法 import。CLAUDE.md 中提到 “`validate.py` 对连续两轮相同失败指纹自动跳过剩余重试”，看起来该功能依赖一个尚未落地的 `harness/lib/failure_classifier` 模块，文档与代码不同步。
