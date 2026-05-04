#!/usr/bin/env bash
# codex.sh — Claude harness 调用 Codex CLI 的统一入口。
#
# 设计契约见：
#   - harness/exec-plans/codex-integration.md（影响范围、阶段步骤）
#   - harness/exec-plans/codex-token-tuning.md（reasoning 自动档 + meta 埋点 ADR）
#
# 使用：
#   codex.sh <subcmd> --cd <DIR> [--sandbox <mode>] \
#     [--reasoning-hint structural|simple] [--scope-hint "<paths>"] \
#     [其他 codex 参数...] < stdin
# 子命令：
#   exec   实现 / 解读（默认 sandbox=workspace-write，read-only 解读时显式覆盖）
#   review 代码 review（默认 sandbox=read-only）
#   apply  应用 codex 生成的 patch（默认 sandbox=workspace-write）
#
# 退出码契约：
#   0   成功
#   64  用法错误（缺 --cd、未知子命令、CODEX_BIN 未设、参数缺失）
#   124 超时
#   127 二进制缺失 / CODEX_DISABLE 已设
#   其他 codex 内部错误（原样透传）
#
# 调用方解析 stdout 末行 `[codex.sh] log=<path>` 拿到本次调用日志路径。
# meta 见 `<log_path>.meta.json`。

set -uo pipefail

CODEX_BIN="${CODEX_BIN:-/Applications/Codex.app/Contents/Resources/codex}"
CODEX_TIMEOUT="${CODEX_TIMEOUT:-600}"
CODEX_MODEL="${CODEX_MODEL:-gpt-5.5}"
# CODEX_REASONING 优先级：仅当被显式设置且非空时算 env 覆盖。
# 不再像旧版一样默认填 xhigh —— 默认值由 decide_reasoning() 推导。

# ---- 全局回退开关 ---------------------------------------------------------
if [[ -n "${CODEX_DISABLE:-}" ]]; then
  echo "[codex.sh] codex 不可用：CODEX_DISABLE=${CODEX_DISABLE}" >&2
  exit 127
fi

# ---- 二进制存在性 ---------------------------------------------------------
if [[ ! -x "$CODEX_BIN" ]]; then
  echo "[codex.sh] codex 不可用：CODEX_BIN=$CODEX_BIN 不存在或不可执行" >&2
  exit 127
fi

# 版本提示（不阻断；仅为 trace 留下版本信息，便于事后追溯）
CODEX_VERSION="$("$CODEX_BIN" --version 2>/dev/null | awk '{print $NF}')"
if [[ -z "$CODEX_VERSION" ]]; then
  echo "[codex.sh] WARN unknown_version codex_version=<empty>" >&2
else
  echo "[codex.sh] INFO codex_version=$CODEX_VERSION" >&2
fi

# ---- 子命令解析 -----------------------------------------------------------
if [[ $# -lt 1 ]]; then
  echo "[codex.sh] 用法错误：缺少子命令（exec|review|apply）" >&2
  exit 64
fi

SUBCMD="$1"
shift

case "$SUBCMD" in
  exec|review|apply) ;;
  *)
    echo "[codex.sh] 用法错误：未知子命令 '$SUBCMD'（仅支持 exec|review|apply）" >&2
    exit 64
    ;;
esac

# ---- 默认 sandbox ---------------------------------------------------------
case "$SUBCMD" in
  exec)   DEFAULT_SANDBOX="workspace-write" ;;
  review) DEFAULT_SANDBOX="read-only" ;;
  apply)  DEFAULT_SANDBOX="workspace-write" ;;
esac

# ---- 解析 --cd / --sandbox / reasoning-hint / scope-hint -----------------
# 注意：--reasoning-hint 与 --scope-hint 只供本脚本决策，不透传给 codex；
# 不识别这两个参数也不报错（向后兼容旧调用方）。
CD_DIR=""
SANDBOX=""
REASONING_HINT=""
SCOPE_HINT=""
PASSTHROUGH=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cd)
      [[ $# -lt 2 ]] && { echo "[codex.sh] 用法错误：--cd 缺参数" >&2; exit 64; }
      CD_DIR="$2"
      shift 2
      ;;
    --cd=*)
      CD_DIR="${1#--cd=}"
      shift
      ;;
    --sandbox)
      [[ $# -lt 2 ]] && { echo "[codex.sh] 用法错误：--sandbox 缺参数" >&2; exit 64; }
      SANDBOX="$2"
      shift 2
      ;;
    --sandbox=*)
      SANDBOX="${1#--sandbox=}"
      shift
      ;;
    --reasoning-hint)
      [[ $# -lt 2 ]] && { echo "[codex.sh] 用法错误：--reasoning-hint 缺参数" >&2; exit 64; }
      REASONING_HINT="$2"
      shift 2
      ;;
    --reasoning-hint=*)
      REASONING_HINT="${1#--reasoning-hint=}"
      shift
      ;;
    --scope-hint)
      [[ $# -lt 2 ]] && { echo "[codex.sh] 用法错误：--scope-hint 缺参数" >&2; exit 64; }
      SCOPE_HINT="$2"
      shift 2
      ;;
    --scope-hint=*)
      SCOPE_HINT="${1#--scope-hint=}"
      shift
      ;;
    *)
      PASSTHROUGH+=("$1")
      shift
      ;;
  esac
done

if [[ -z "$CD_DIR" ]]; then
  echo "[codex.sh] 用法错误：必须显式 --cd <DIR>" >&2
  exit 64
fi

if [[ ! -d "$CD_DIR" ]]; then
  echo "[codex.sh] 用法错误：--cd $CD_DIR 不是目录" >&2
  exit 64
fi

[[ -z "$SANDBOX" ]] && SANDBOX="$DEFAULT_SANDBOX"

# ---- reasoning 决策（ADR-1，优先级从高到低） ------------------------------
# 1. CODEX_REASONING 显式且非空 → env
# 2. --reasoning-hint structural → xhigh / auto:structural
# 3. --scope-hint 含公开接口路径 → xhigh / auto:public-api
# 4. --reasoning-hint simple 或 scope 仅含 *.md / *.json → medium / auto:docs
# 5. 默认 → high / auto:default
decide_reasoning() {
  local env_val="${CODEX_REASONING:-}"
  if [[ -n "$env_val" ]]; then
    REASONING="$env_val"
    REASONING_SOURCE="env"
    return
  fi

  if [[ "$REASONING_HINT" == "structural" ]]; then
    REASONING="xhigh"
    REASONING_SOURCE="auto:structural"
    return
  fi

  # 公开接口路径模式（与 ADR-1 列出的五类对齐）
  if [[ -n "$SCOPE_HINT" ]]; then
    # api/Controller.java | /api/ | *.controller.ts | Mapper.xml | schema.sql
    if echo "$SCOPE_HINT" | grep -E -q \
      -e 'api/[A-Za-z0-9_]+Controller\.java' \
      -e '/api/' \
      -e '\.controller\.ts(\b|$)' \
      -e 'Mapper\.xml(\b|$)' \
      -e 'schema\.sql(\b|$)'; then
      REASONING="xhigh"
      REASONING_SOURCE="auto:public-api"
      return
    fi
  fi

  if [[ "$REASONING_HINT" == "simple" ]]; then
    REASONING="medium"
    REASONING_SOURCE="auto:docs"
    return
  fi

  if [[ -n "$SCOPE_HINT" ]]; then
    # scope 仅含 *.md 或 *.json（按 whitespace 切分逐项检查）
    local only_docs=1
    local item
    for item in $SCOPE_HINT; do
      case "$item" in
        *.md|*.json) ;;
        *) only_docs=0; break ;;
      esac
    done
    if [[ "$only_docs" == "1" ]]; then
      REASONING="medium"
      REASONING_SOURCE="auto:docs"
      return
    fi
  fi

  REASONING="high"
  REASONING_SOURCE="auto:default"
}

REASONING=""
REASONING_SOURCE=""
decide_reasoning
echo "[codex.sh] INFO reasoning=$REASONING source=$REASONING_SOURCE" >&2

# ---- 日志目录与文件 -------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LOG_DIR="$REPO_ROOT/harness/trace/codex"
if ! mkdir -p "$LOG_DIR" 2>/dev/null; then
  echo "[codex.sh] 日志目录创建失败：$LOG_DIR" >&2
  exit 64
fi

TS="$(date +%Y%m%d-%H%M%S)"
LOG_FILE="$LOG_DIR/${TS}-$$.log"
META_FILE="${LOG_FILE}.meta.json"

# ---- 毫秒时间戳工具（跨平台兼容） -----------------------------------------
# macOS 原生 date 不支持 %N，优先用 python3；降级到秒级 * 1000。
now_ms() {
  python3 -c 'import time; print(int(time.time()*1000))' 2>/dev/null || echo $(( $(date +%s) * 1000 ))
}

# ---- 超时命令探测 ---------------------------------------------------------
TIMEOUT_CMD=""
if command -v gtimeout >/dev/null 2>&1; then
  TIMEOUT_CMD="gtimeout"
elif command -v timeout >/dev/null 2>&1; then
  TIMEOUT_CMD="timeout"
fi

# ---- 收集 stdin（供日志记录与透传） ---------------------------------------
STDIN_BUF=""
if [[ ! -t 0 ]]; then
  STDIN_BUF="$(cat)"
fi

# ---- 调用 codex -----------------------------------------------------------
START_MS="$(now_ms)"

STDOUT_TMP="$(mktemp)"
STDERR_TMP="$(mktemp)"
trap 'rm -f "$STDOUT_TMP" "$STDERR_TMP"' EXIT

# review 子命令不接受 --cd / --sandbox（codex CLI 限制），改为子 shell cd 切换工作目录；
# exec / apply 维持原有 --cd / --sandbox 透传行为。
SUBCMD_OPTS=()
if [[ "$SUBCMD" != "review" ]]; then
  SUBCMD_OPTS=(--cd "$CD_DIR" --sandbox "$SANDBOX")
fi

run_codex() {
  # 把 decide_reasoning() 决策结果通过 -c model_reasoning_effort=$REASONING 传给 codex；
  # 放在 SUBCMD_OPTS / PASSTHROUGH 之前，PASSTHROUGH 中后到的 -c 覆盖优先（codex 后到优先）。
  if [[ -n "$TIMEOUT_CMD" ]]; then
    if [[ -n "$STDIN_BUF" ]]; then
      "$TIMEOUT_CMD" "$CODEX_TIMEOUT" "$CODEX_BIN" "$SUBCMD" \
        -c "model_reasoning_effort=$REASONING" \
        ${SUBCMD_OPTS[@]+"${SUBCMD_OPTS[@]}"} \
        ${PASSTHROUGH[@]+"${PASSTHROUGH[@]}"} \
        <<< "$STDIN_BUF" \
        >"$STDOUT_TMP" 2>"$STDERR_TMP"
    else
      "$TIMEOUT_CMD" "$CODEX_TIMEOUT" "$CODEX_BIN" "$SUBCMD" \
        -c "model_reasoning_effort=$REASONING" \
        ${SUBCMD_OPTS[@]+"${SUBCMD_OPTS[@]}"} \
        ${PASSTHROUGH[@]+"${PASSTHROUGH[@]}"} \
        </dev/null \
        >"$STDOUT_TMP" 2>"$STDERR_TMP"
    fi
  else
    if [[ -n "$STDIN_BUF" ]]; then
      "$CODEX_BIN" "$SUBCMD" \
        -c "model_reasoning_effort=$REASONING" \
        ${SUBCMD_OPTS[@]+"${SUBCMD_OPTS[@]}"} \
        ${PASSTHROUGH[@]+"${PASSTHROUGH[@]}"} \
        <<< "$STDIN_BUF" \
        >"$STDOUT_TMP" 2>"$STDERR_TMP"
    else
      "$CODEX_BIN" "$SUBCMD" \
        -c "model_reasoning_effort=$REASONING" \
        ${SUBCMD_OPTS[@]+"${SUBCMD_OPTS[@]}"} \
        ${PASSTHROUGH[@]+"${PASSTHROUGH[@]}"} \
        </dev/null \
        >"$STDOUT_TMP" 2>"$STDERR_TMP"
    fi
  fi
}

if [[ "$SUBCMD" == "review" ]]; then
  ( cd "$CD_DIR" && run_codex )
else
  run_codex
fi
EXIT_CODE=$?

END_MS="$(now_ms)"
DURATION_MS=$((END_MS - START_MS))

# ---- 写日志（行式键值，便于 grep） ---------------------------------------
{
  echo "ts=${TS}"
  echo "subcmd=${SUBCMD}"
  echo "cwd=${CD_DIR}"
  echo "sandbox=${SANDBOX}"
  echo "model=${CODEX_MODEL}"
  echo "reasoning=${REASONING}"
  echo "reasoning_source=${REASONING_SOURCE}"
  echo "exit=${EXIT_CODE}"
  echo "duration_ms=${DURATION_MS}"
  echo "passthrough=${PASSTHROUGH[*]:-}"
  echo "---- stdin ----"
  echo "${STDIN_BUF}"
  echo "---- stdout ----"
  cat "$STDOUT_TMP"
  echo "---- stderr ----"
  cat "$STDERR_TMP"
} >"$LOG_FILE"

# ---- 写 meta.json（ADR-2 字段集） ---------------------------------------
# 用 python3 序列化以确保 JSON 合法（转义、null、整数类型）。
# 所有 shell 变量通过 sys.argv 传入，避免 heredoc 字符串插值的转义陷阱。
python3 - \
  "$STDOUT_TMP" "$STDERR_TMP" "$META_FILE" \
  "$TS" "$SUBCMD" "$CODEX_MODEL" "$REASONING" "$REASONING_SOURCE" \
  "$EXIT_CODE" "$DURATION_MS" <<'PYEOF' || echo "[codex.sh] WARN meta_write_failed log=$LOG_FILE" >&2
import json
import re
import sys

(_, stdout_path, stderr_path, meta_path,
 ts, subcmd, model, reasoning, reasoning_source,
 exit_code_str, duration_ms_str) = sys.argv

with open(stdout_path, "r", errors="replace") as f:
    stdout_buf = f.read()
with open(stderr_path, "r", errors="replace") as f:
    stderr_buf = f.read()

exit_code = int(exit_code_str)
duration_ms = int(duration_ms_str)

# tokens_used: 抓 'tokens used\n([0-9,]+)'，去逗号后转 int；取最后一次匹配
tokens_used = None
m = re.findall(r"tokens used\s*\n\s*([0-9,]+)", stderr_buf)
if m:
    try:
        tokens_used = int(m[-1].replace(",", ""))
    except ValueError:
        tokens_used = None

# http_status_terminal: 抓最后一次 'last status: (\d+)'；
# 抓不到且 exit_code=0 记 200；抓不到且 exit_code!=0 记 null
http_status = None
status_matches = re.findall(r"last status:\s*(\d+)", stderr_buf)
if status_matches:
    try:
        http_status = int(status_matches[-1])
    except ValueError:
        http_status = None
elif exit_code == 0:
    http_status = 200

# files_touched: exec/apply 统计 stdout+stderr 中唯一 'diff --git a/<path>' 文件数；review 记 null
if subcmd == "review":
    files_touched = None
else:
    files = set()
    for blob in (stdout_buf, stderr_buf):
        for line in blob.splitlines():
            if line.startswith("diff --git "):
                parts = line.split()
                if len(parts) >= 3 and parts[2].startswith("a/"):
                    files.add(parts[2][2:])
                else:
                    files.add(line)
    files_touched = len(files)

# internal_verify_calls: 统计本次 codex log 中违规跑 verify/e2e 的命令次数（合并 stdout+stderr）。
# 命中以下任一模式即 +1：
#   - 'make verify'         （含 make verify / make verify-* 等所有以 'make verify' 开头）
#   - 'executor verify'     （含 'python3 harness/bin/executor.py verify ...'）
#   - 'pytest tests/e2e'    （含 'pytest tests/e2e/test_xxx.py' 等）
# 用于 critic 跨任务统计 codex 是否违反 .claude/agents/codex-implementer.md 的"硬约束"。
# 期望值为 0；> 0 提示需要回看日志并加强 prompt 约束。
internal_verify_calls = 0
_verify_patterns = (
    re.compile(r"\bmake\s+verify\b"),
    re.compile(r"\bexecutor(?:\.py)?\s+verify\b"),
    re.compile(r"\bpytest\s+[^\n]*tests/e2e\b"),
)
for blob in (stdout_buf, stderr_buf):
    for pat in _verify_patterns:
        internal_verify_calls += len(pat.findall(blob))

meta = {
    "ts": ts,
    "subcmd": subcmd,
    "model": model,
    "reasoning": reasoning,
    "reasoning_source": reasoning_source,
    "exit_code": exit_code,
    "http_status_terminal": http_status,
    "retry_count": 0,
    "duration_ms": duration_ms,
    "tokens_used": tokens_used,
    "files_touched": files_touched,
    "internal_verify_calls": internal_verify_calls,
}

with open(meta_path, "w") as f:
    json.dump(meta, f, ensure_ascii=False, indent=2)
PYEOF

# ---- 透传 stdout / stderr 给调用方 ---------------------------------------
cat "$STDOUT_TMP"
cat "$STDERR_TMP" >&2

# ---- 末行追加日志路径，供 sub-agent 解析 ---------------------------------
echo "[codex.sh] log=${LOG_FILE}"

# ---- 退出码透传 ----------------------------------------------------------
exit "$EXIT_CODE"