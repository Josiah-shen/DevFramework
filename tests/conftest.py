import os
import json
import pytest
import urllib.request
import urllib.error

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASS = os.getenv("DB_PASS", "123456")
DB_NAME = os.getenv("DB_NAME", "xptsqas")
BASE_URL = f"http://localhost:{os.getenv('BACKEND_PORT', '8088')}"
FRONTEND_URL = f"http://localhost:{os.getenv('FRONTEND_PORT', '5173')}"


def _backend_available() -> bool:
    try:
        req = urllib.request.Request(
            f"{BASE_URL}/api/basic-data/regions/list",
            data=b"{}",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=3):
            return True
    except Exception:
        return False


def _db_available() -> bool:
    try:
        import pymysql
        conn = pymysql.connect(
            host=DB_HOST, port=DB_PORT, user=DB_USER,
            password=DB_PASS, database=DB_NAME, connect_timeout=3,
        )
        conn.close()
        return True
    except Exception:
        return False


# 在 session 开始时做一次性检测，未启动则终止整个测试流程
def pytest_sessionstart(session):
    _BACKEND_UP = _backend_available()
    _DB_UP = _db_available()

    issues = []
    if not _DB_UP:
        issues.append(
            f"  ✗ 数据库不可达  → 请先执行: make db-schema  (MySQL {DB_HOST}:{DB_PORT}/{DB_NAME})"
        )
    if not _BACKEND_UP:
        issues.append(
            f"  ✗ 后端服务未启动 → 请先执行: make docker-up 或手动启动后端  ({BASE_URL})"
        )

    if issues:
        msg = "\n\n【测试前置条件未满足，终止测试流程】\n" + "\n".join(issues) + "\n"
        pytest.exit(msg, returncode=3)


def _identity_decorator(func):
    return func


requires_backend = _identity_decorator  # 检测已在 session 层完成，此处仅保留装饰器兼容性
requires_db = _identity_decorator


@pytest.fixture(scope="session")
def db_conn():
    import pymysql
    conn = pymysql.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER,
        password=DB_PASS, database=DB_NAME,
        charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
    )
    yield conn
    conn.close()


class _HttpClient:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def request(self, method: str, path: str, body=None, timeout: int = 10):
        url = self.base + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            url, data=data, method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                try:
                    return resp.status, json.loads(raw.decode())
                except (UnicodeDecodeError, json.JSONDecodeError):
                    return resp.status, {}
        except urllib.error.HTTPError as e:
            return e.code, {}

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, body=body, **kw)


@pytest.fixture
def api_client():
    return _HttpClient(BASE_URL)


def pytest_addoption(parser):
    parser.addoption("--headed", action="store_true", default=False, help="以有头模式运行浏览器")


@pytest.fixture(scope="session")
def browser(request):
    from playwright.sync_api import sync_playwright
    headed = request.config.getoption("--headed")
    with sync_playwright() as p:
        b = p.chromium.launch(headless=not headed)
        yield b
        b.close()


@pytest.fixture
def page(browser):
    pg = browser.new_page()
    pg.set_default_timeout(10000)
    yield pg
    pg.close()


class AuthHelper:
    """认证辅助（预留）。当前 API 全部公开，待 JWT 实现后在此注入 token。"""
    def __init__(self):
        self.token = None

    def login(self, _username: str = "admin", _password: str = "admin"):
        # TODO: 实现后接入 /api/auth/login
        self.token = None
        return self.token

    def headers(self):
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        return {}


@pytest.fixture
def auth() -> AuthHelper:
    return AuthHelper()