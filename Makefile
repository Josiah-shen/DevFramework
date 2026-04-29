-include .env
export

DB_HOST      ?= 127.0.0.1
DB_PORT      ?= 3306
DB_USER      ?= root
DB_PASS      ?=
PROJECT_NAME ?= xptsqas
_MYSQL = mysql -h$(DB_HOST) -P$(DB_PORT) -u$(DB_USER) $(if $(DB_PASS),-p$(DB_PASS))
PYTHON3      ?= /Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3

.PHONY: build test test-backend test-frontend test-python-unit test-python-integration test-python-e2e \
        lint-arch lint-deps fix-arch verify verify-api validate \
        harness-audit harness-run python-sanity \
        db-init db-schema db-reset

build:
	@if [ -f src/backend/pom.xml ]; then \
	  echo "=== 后端构建 ===" && mvn -f src/backend/pom.xml package -DskipTests -q; \
	else \
	  echo "⚠️  src/backend/pom.xml 未找到，跳过后端构建"; \
	fi
	@if [ -f src/frontend/package.json ]; then \
	  echo "=== 前端构建 ===" && npm --prefix src/frontend run build --silent; \
	else \
	  echo "⚠️  src/frontend/package.json 未找到，跳过前端构建"; \
	fi

test-backend:
	@if [ -f src/backend/pom.xml ]; then \
	  echo "=== 后端测试 ===" && mvn -f src/backend/pom.xml test -q; \
	else \
	  echo "⚠️  src/backend/pom.xml 未找到，跳过后端测试"; \
	fi

test-frontend:
	@if [ -f src/frontend/package.json ]; then \
	  echo "=== 前端测试 ===" && npm --prefix src/frontend run test --if-present; \
	else \
	  echo "⚠️  src/frontend/package.json 未找到，跳过前端测试"; \
	fi

test-python-unit:
	@if [ -d tests ]; then \
	  echo "=== Python 单元测试 ===" && cd tests && $(PYTHON3) -m pytest unit/ -v; \
	else \
	  echo "⚠️  tests/ 未找到，跳过 Python 单元测试"; \
	fi

test-python-integration:
	@if [ -d tests ]; then \
	  echo "=== Python 集成测试 ===" && cd tests && $(PYTHON3) -m pytest integration/ -v; \
	else \
	  echo "⚠️  tests/ 未找到，跳过 Python 集成测试"; \
	fi

test-python-e2e:
	@if [ -d tests ]; then \
	  echo "=== Python E2E 测试（无头）===" && (cd tests && $(PYTHON3) -m pytest e2e/ -v); \
	  echo "=== Python E2E 测试（有头）===" && (cd tests && $(PYTHON3) -m pytest e2e/ -v --headed); \
	else \
	  echo "⚠️  tests/ 未找到，跳过 Python E2E 测试"; \
	fi

test: python-sanity test-backend test-frontend test-python-unit test-python-integration test-python-e2e

lint-arch: lint-deps

lint-deps:
	python3 scripts/lint-deps.py

fix-arch:
	@if [ -f src/backend/pom.xml ]; then \
	  echo "=== Java 格式化 ===" && mvn -f src/backend/pom.xml spotless:apply -q; \
	else \
	  echo "⚠️  src/backend/pom.xml 未找到，跳过 Java 格式化"; \
	fi
	@if [ -f src/frontend/package.json ]; then \
	  echo "=== 前端 lint fix ===" && npm --prefix src/frontend run lint --if-present -- --fix; \
	else \
	  echo "⚠️  src/frontend/package.json 未找到，跳过前端 lint fix"; \
	fi

verify: python-sanity
	python3 scripts/verify/run.py

verify-api: python-sanity
	python3 scripts/verify/run.py --checks api,e2e

validate:
	python3 scripts/validate.py

python-sanity:
	@python3 -c "import py_compile; files=['scripts/validate.py','scripts/lint-deps.py','scripts/verify/run.py','scripts/verify/checks/style.py','scripts/verify/check-scope.py']; [py_compile.compile(f, doraise=True) for f in files]" \
	|| (echo '[sanity] Python 脚本编译失败' && exit 1)

harness-audit:
	python3 harness/bin/creator.py audit

harness-run:
	python3 harness/bin/executor.py check

db-init:
	@echo "=== 初始化数据库 ===" && $(_MYSQL) < src/database/init.sql

db-schema: db-init
	@echo "=== 应用表结构 ===" && $(_MYSQL) $(PROJECT_NAME) < src/database/schema.sql

db-reset:
	@echo "=== 重建数据库 ===" && $(_MYSQL) -e "DROP DATABASE IF EXISTS $(PROJECT_NAME);" && $(MAKE) db-schema

pytest-install:
	pip install -r tests/requirements.txt && playwright install chromium

pytest-all:
	cd tests && pytest -v

pytest-integration:
	cd tests && pytest integration/ -v

pytest-e2e:
	@echo "=== E2E 第一阶段：无头模式 ==="
	cd tests && pytest e2e/ -v
	@echo "=== E2E 第二阶段：有头模式 ==="
	cd tests && pytest e2e/ -v --headed

pytest-report:
	cd tests && pytest --html=report.html --self-contained-html