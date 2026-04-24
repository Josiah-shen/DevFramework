#!/usr/bin/env bash
# 用法: ./init.sh <新项目名>
# 将框架中所有 "xptsqas" 占位符替换为新项目名，完成后可直接启动。
set -euo pipefail

cleanup_on_error() {
    local exit_code=$?
    if [ $exit_code -ne 0 ]; then
        echo ""
        echo "⚠️  初始化中途失败（exit=$exit_code），正在清理 .bak 残留..."
        find . "${EXCLUDE_PATHS[@]}" -name "*.bak" -delete 2>/dev/null || true
        echo "⚠️  请运行 git status / git checkout . 检查并回滚部分替换结果。"
    fi
}

NEW_NAME=${1:?"用法: ./init.sh <项目名>  例: ./init.sh myproject"}
OLD_NAME="xptsqas"

if [[ "$NEW_NAME" == "$OLD_NAME" ]]; then
    echo "新项目名与当前名称相同，无需初始化。"
    exit 0
fi

if ! [[ "$NEW_NAME" =~ ^[a-z][a-z0-9]*$ ]]; then
    echo "❌ 项目名只能包含小写字母和数字，且必须以字母开头（不含连字符）"
    exit 1
fi

# 幂等性与工作区检查
if [ ! -d "src/backend/src/main/java/com/xptsqas" ]; then
    echo "❌ 未找到 com/xptsqas 包目录；此仓库可能已初始化过，或不是模板仓库。"
    exit 1
fi

if command -v git >/dev/null 2>&1 && git rev-parse --git-dir >/dev/null 2>&1; then
    if ! git diff --quiet || ! git diff --cached --quiet; then
        echo "❌ 工作区有未提交改动；请先 commit 或 stash，失败时才能用 git checkout . 回滚。"
        exit 1
    fi
fi

OLD_PKG="com.xptsqas"
NEW_PKG="com.${NEW_NAME}"
OLD_PKG_PATH="com/xptsqas"
NEW_PKG_PATH="com/${NEW_NAME}"

# PascalCase: xptsqas → Xptsqas, myproject → Myproject
OLD_PASCAL="Xptsqas"
NEW_PASCAL="$(echo "${NEW_NAME:0:1}" | tr '[:lower:]' '[:upper:]')${NEW_NAME:1}"

echo "🔄 初始化项目: $OLD_NAME → $NEW_NAME"
echo "   Java 包:    $OLD_PKG → $NEW_PKG"
echo "   类名前缀:   $OLD_PASCAL → $NEW_PASCAL"

# 需要替换文本的文件类型
FILE_PATTERNS=(
    "*.xml" "*.yml" "*.yaml" "*.java" "*.js" "*.vue"
    "*.json" "*.sql" "*.md" "*.py" "*.sh"
    "Makefile" ".env.example" "Dockerfile" "nginx.conf"
)

EXCLUDE_PATHS=(
    -not -path './.git/*'
    -not -path '*/node_modules/*'
    -not -path '*/target/*'
    -not -path '*/.claude/worktrees/*'
)

trap cleanup_on_error EXIT

_find_files() {
    local args=()
    for p in "${FILE_PATTERNS[@]}"; do
        args+=(-o -name "$p")
    done
    find . \
        "${EXCLUDE_PATHS[@]}" \
        -type f \
        \( "${args[@]:1}" \) \
        -print0
}

# 三轮 sed：精确匹配，避免误替换
echo "  → 替换 Java 包名（com.xptsqas → ${NEW_PKG}）"
_find_files | xargs -0 sed -i.bak "s|${OLD_PKG}|${NEW_PKG}|g"

echo "  → 替换 Java 包路径（com/xptsqas → ${NEW_PKG_PATH}）"
_find_files | xargs -0 sed -i.bak "s|${OLD_PKG_PATH}|${NEW_PKG_PATH}|g"

echo "  → 替换 PascalCase 类名前缀（${OLD_PASCAL} → ${NEW_PASCAL}）"
_find_files | xargs -0 sed -i.bak "s|${OLD_PASCAL}|${NEW_PASCAL}|g"

echo "  → 替换项目名（${OLD_NAME} → ${NEW_NAME}）"
_find_files | xargs -0 sed -i.bak "s|${OLD_NAME}|${NEW_NAME}|g"

find . "${EXCLUDE_PATHS[@]}" -name "*.bak" -delete

# 重命名 Java 包目录
OLD_JAVA_DIR="src/backend/src/main/java/com/xptsqas"
NEW_JAVA_DIR="src/backend/src/main/java/com/${NEW_NAME}"
OLD_TEST_DIR="src/backend/src/test/java/com/xptsqas"
NEW_TEST_DIR="src/backend/src/test/java/com/${NEW_NAME}"

if [ -d "$OLD_JAVA_DIR" ]; then
    mv "$OLD_JAVA_DIR" "$NEW_JAVA_DIR"
    echo "  → 重命名包目录: $OLD_JAVA_DIR → $NEW_JAVA_DIR"
fi
if [ -d "$OLD_TEST_DIR" ]; then
    mv "$OLD_TEST_DIR" "$NEW_TEST_DIR"
    echo "  → 重命名测试目录: $OLD_TEST_DIR → $NEW_TEST_DIR"
fi

# 重命名主类文件
OLD_APP="${NEW_JAVA_DIR}/${OLD_PASCAL}Application.java"
NEW_APP="${NEW_JAVA_DIR}/${NEW_PASCAL}Application.java"
OLD_TEST_FILE="${NEW_TEST_DIR}/${OLD_PASCAL}ApplicationTests.java"
NEW_TEST_FILE="${NEW_TEST_DIR}/${NEW_PASCAL}ApplicationTests.java"

[ -f "$OLD_APP" ] && mv "$OLD_APP" "$NEW_APP" && echo "  → 重命名主类: $OLD_APP → $NEW_APP"
[ -f "$OLD_TEST_FILE" ] && mv "$OLD_TEST_FILE" "$NEW_TEST_FILE"

echo ""
echo "✅ 初始化完成: $NEW_NAME"
echo ""
echo "下一步："
echo "  1. cp .env.example .env  # 按需修改 DB_PASS 等配置"
echo "  2. docker compose up -d  # 一键启动全栈"
echo "  3. 访问 http://localhost:\${FRONTEND_PORT:-80}"