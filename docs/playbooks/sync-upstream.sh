#!/usr/bin/env bash
# sync-upstream.sh — 从业务项目向模板回流框架改动
#
# 用法：
#   docs/playbooks/sync-upstream.sh --from <业务项目路径> [--to <模板路径>] [--dry-run] [--yes]
#
# 说明见 docs/playbooks/sync-upstream.md。
set -euo pipefail

# 自拷贝执行：脚本在 MANIFEST 中包含自身，同步时会被覆盖导致 bash 解析错误。
# 启动时拷贝到临时文件再 exec，避免运行中文件被替换。
if [[ -z "${_SYNC_REEXEC:-}" ]]; then
    _tmp="$(mktemp "${TMPDIR:-/tmp}/sync-upstream.XXXXXX")"
    cp "$0" "$_tmp"
    chmod +x "$_tmp"
    export _SYNC_REEXEC=1 _SYNC_ORIG_SCRIPT="$0"
    exec bash "$_tmp" "$@"
fi
trap 'rm -f "${BASH_SOURCE[0]}"' EXIT

# ============================================================
# 反向映射：业务项目里的占位符 → 模板里的占位符
# 新项目占位符（例如 com.foo.bar）加到这里一行就行。
# 格式："<业务侧>|<模板侧>"，不含空格。
# ============================================================
REVERSE_MAPPINGS=(
    "com.nanjing.carbon|com.xptsqas"
    "com/nanjing/carbon|com/xptsqas"
)

# ============================================================
# 框架文件清单：应与模板保持对齐的文件
# 规则：新加了纯框架级文件 → 在这里加路径
#       纯业务/模板专属文件 → 不要加（见 sync-upstream.md 判定标准）
# ============================================================
MANIFEST=(
    # --- Harness 引擎（Python 源码 + 任务拆分清单） ---
    "harness/bin/__init__.py"
    "harness/bin/executor.py"
    "harness/bin/creator.py"
    "harness/bin/rubric.py"
    "harness/bin/state.py"
    "harness/split-task-checklist.md"

    # --- 验证脚本（validate / lint / verify 子检查） ---
    "scripts/validate.py"
    "scripts/lint-deps.py"
    "scripts/verify/run.py"
    "scripts/verify/checks/__init__.py"
    "scripts/verify/checks/api.py"
    "scripts/verify/checks/arch.py"
    "scripts/verify/checks/e2e.py"
    "scripts/verify/checks/style.py"

    # --- 根目录构建与运行时配置 ---
    "docs/DEVELOPMENT.md"
    "Makefile"
    "init.sh"
    "CLAUDE.md"

    # --- 测试框架（pytest 配置与依赖） ---
    "tests/conftest.py"
    "tests/pytest.ini"
    "tests/requirements.txt"
    ".gitignore"

    # --- Claude Code 配置：settings 与 coordinator 角色 ---
    ".claude/settings.json"
    ".claude/roles/coordinator.md"

    # --- Claude Code agent 定义（子代理提示词） ---
    ".claude/agents/critic.md"
    ".claude/agents/docs-updater.md"
    ".claude/agents/e2e-updater.md"
    ".claude/agents/executor-code.md"
    ".claude/agents/executor-lint-rule.md"
    ".claude/agents/executor-research.md"
    ".claude/agents/executor-review.md"
    ".claude/agents/executor-shell.md"
    ".claude/agents/refiner.md"
    ".claude/agents/verifier.md"

    # --- 运维手册（playbooks：同步与重建） ---
    "docs/playbooks/sync-upstream.md"
    "docs/playbooks/sync-upstream.sh"
    "docs/playbooks/rebuild-from-source.md"
)

# ============================================================
# 参数解析
# ============================================================
FROM=""
TO=""
DRY_RUN=false
AUTO_YES=false

usage() {
    cat <<'USAGE'
用法: sync-upstream.sh --from <业务项目路径> [--to <模板路径>] [--dry-run] [--yes]

选项:
  --from <path>   业务项目根目录（含待回流的框架改动）
  --to <path>     模板根目录（默认 = 本脚本所在模板的根）
  --dry-run       只打印 diff，不写入
  --yes           所有变更一律应用，不问（谨慎使用）
  -h, --help      本帮助
USAGE
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --from) FROM="$2"; shift 2 ;;
        --to)   TO="$2"; shift 2 ;;
        --dry-run) DRY_RUN=true; shift ;;
        --yes) AUTO_YES=true; shift ;;
        -h|--help) usage ;;
        *) echo "未知参数: $1" >&2; usage ;;
    esac
done

[[ -z "$FROM" ]] && usage

# 默认 TO = 模板根（脚本位于 docs/playbooks/，上两级）
if [[ -z "$TO" ]]; then
    TO="$(cd "$(dirname "${_SYNC_ORIG_SCRIPT:-$0}")/../.." && pwd)"
fi

[[ ! -d "$FROM" ]] && { echo "❌ --from 路径不存在: $FROM" >&2; exit 2; }
[[ ! -d "$TO" ]] && { echo "❌ --to 路径不存在: $TO" >&2; exit 2; }
[[ ! -f "$TO/CLAUDE.md" ]] && { echo "❌ --to 目录缺少 CLAUDE.md，不像模板根: $TO" >&2; exit 2; }
[[ ! -f "$TO/harness/bin/executor.py" ]] && { echo "❌ --to 缺少 harness/bin/executor.py，不像模板根" >&2; exit 2; }

echo "🔄 框架回流 (upstream sync)"
echo "   源 (业务项目): $FROM"
echo "   目标 (模板):   $TO"
if $DRY_RUN; then echo "   模式:         DRY-RUN（不写入）"; fi
echo ""

apply_reverse() {
    local content="$1"
    local src dst
    for mapping in "${REVERSE_MAPPINGS[@]}"; do
        src="${mapping%%|*}"
        dst="${mapping##*|}"
        content="${content//${src}/${dst}}"
    done
    printf '%s' "$content"
}

CHANGED=()
IDENTICAL=()
MISSING_IN_SOURCE=()
USER_SKIPPED=()

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

for path in "${MANIFEST[@]}"; do
    src_file="$FROM/$path"
    dst_file="$TO/$path"

    if [[ ! -f "$src_file" ]]; then
        MISSING_IN_SOURCE+=("$path")
        continue
    fi

    normalized="$TMPDIR/$(echo "$path" | tr '/' '_')"
    apply_reverse "$(cat "$src_file")" > "$normalized"

    if [[ -f "$dst_file" ]] && diff -q "$normalized" "$dst_file" >/dev/null 2>&1; then
        IDENTICAL+=("$path")
        continue
    fi

    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    if [[ ! -f "$dst_file" ]]; then
        echo "🆕 新文件: $path"
        head -40 "$normalized" | sed 's/^/  + /'
        echo "  ... (共 $(wc -l < "$normalized") 行)"
    else
        echo "📝 变更: $path"
        diff -u "$dst_file" "$normalized" | head -100 || true
    fi

    if $DRY_RUN; then
        CHANGED+=("$path (dry-run)")
        continue
    fi

    if $AUTO_YES; then
        answer="y"
    else
        echo ""
        printf "应用到模板? [y/N/q(退出)] "
        read -r answer
    fi

    case "$answer" in
        y|Y)
            mkdir -p "$(dirname "$dst_file")"
            cp "$normalized" "$dst_file"
            CHANGED+=("$path")
            echo "  ✅ 已更新"
            ;;
        q|Q)
            echo "🛑 用户退出"
            break
            ;;
        *)
            USER_SKIPPED+=("$path")
            echo "  ⏭  跳过"
            ;;
    esac
done

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "汇总:"
echo "  ✅ 已更新:     ${#CHANGED[@]:-0}"
echo "  ⬌  无差异:     ${#IDENTICAL[@]:-0}"
echo "  ⏭  用户跳过:   ${#USER_SKIPPED[@]:-0}"
echo "  ⚠️  源缺失:     ${#MISSING_IN_SOURCE[@]:-0}"
if [[ ${#MISSING_IN_SOURCE[@]:-0} -gt 0 ]]; then
    for p in "${MISSING_IN_SOURCE[@]}"; do
        echo "       - $p"
    done
fi

if $DRY_RUN; then
    echo ""
    echo "(dry-run，未写入任何文件)"
    exit 0
fi

if [[ ${#CHANGED[@]} -eq 0 ]]; then
    exit 0
fi

echo ""
echo "🔍 回归检查..."

check_failed=0

# 1) executor check
if python3 "$TO/harness/bin/executor.py" check >/dev/null 2>&1; then
    echo "  ✅ executor check"
else
    echo "  ❌ executor check 失败，请进入 $TO 排查"
    check_failed=1
fi

# 2) init.sh 全角括号防回归（历史已踩过坑）
if grep -nE '\$[A-Z_]+[）]' "$TO/init.sh" >/dev/null 2>&1; then
    echo "  ❌ init.sh 检出 \$VAR） 模式（全角括号紧贴变量），bash set -u 下会误解析"
    grep -nE '\$[A-Z_]+[）]' "$TO/init.sh" | sed 's/^/       /'
    check_failed=1
else
    echo "  ✅ init.sh 括号检查"
fi

# 3) 反向映射是否有遗漏（业务符号不应出现在模板）
leaked=0
for mapping in "${REVERSE_MAPPINGS[@]}"; do
    src="${mapping%%|*}"
    if grep -rln --fixed-strings "$src" \
        --include='*.py' --include='*.md' --include='*.xml' \
        --include='*.json' --include='*.sh' --include='Makefile' \
        "$TO" 2>/dev/null | grep -v '/playbooks/' | head -5 > "$TMPDIR/leak.txt"; then
        if [[ -s "$TMPDIR/leak.txt" ]]; then
            echo "  ❌ 模板残留业务符号 '$src':"
            sed 's/^/       /' "$TMPDIR/leak.txt"
            leaked=1
        fi
    fi
done
[[ $leaked -eq 0 ]] && echo "  ✅ 反向映射检查"
[[ $leaked -eq 1 ]] && check_failed=1

if [[ $check_failed -eq 1 ]]; then
    echo ""
    echo "⚠️  有检查失败，请先修复再提交"
    exit 1
fi

echo ""
echo "✅ 回流完成，建议:"
echo "   cd $TO"
echo "   git diff                 # 复核变更"
echo "   git add -A && git commit -m 'sync: upstream from <business-slug>'"
