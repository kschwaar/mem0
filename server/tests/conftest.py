import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parents[1]

_NATIVE_HTTP_CHILD = r'''
import json
import sys
from types import SimpleNamespace

payload = json.loads(sys.stdin.read())
import server_state
server_state.initialize_state = lambda _config: None
import main
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

class FakeMemory:
    def __init__(self, rows, fail_get):
        self.rows = rows
        self.fail_get = fail_get

    def get(self, memory_id):
        if self.fail_get:
            raise OperationalError("SELECT memory", {}, RuntimeError("offline store"))
        return next((row for row in self.rows if row["id"] == memory_id), None)

    def get_all(self, filters=None, **_params):
        filters = filters or {}
        return {"results": [row for row in self.rows if all(row.get(key) == value for key, value in filters.items())]}

    def search(self, query, filters=None, **_params):
        filters = filters or {}
        query = query.casefold()
        return {
            "results": [
                row for row in self.rows
                if query in row["memory"].casefold()
                and all(row.get(key) == value for key, value in filters.items())
            ]
        }

    def delete(self, memory_id):
        self.rows[:] = [row for row in self.rows if row["id"] != memory_id]

store = FakeMemory(payload["memories"], payload["fail_get"])
main.get_memory_instance = lambda: store
main.app.dependency_overrides[main.verify_auth] = lambda: SimpleNamespace(role="admin")
main.app.dependency_overrides[main.require_admin] = lambda: SimpleNamespace(role="admin")
responses = []
with TestClient(main.app) as client:
    for request in payload["requests"]:
        response = client.request(
            request["method"], request["path"],
            json=request.get("json"), headers=request.get("headers"),
        )
        responses.append({
            "status": response.status_code,
            "body": response.json(),
            "request_id_header": response.headers.get("x-request-id"),
            "allow_header": response.headers.get("allow"),
        })
print(json.dumps(responses))
'''


@pytest.fixture
def native_http():
    def run(requests, memories=None, fail_get=False):
        with tempfile.TemporaryDirectory(prefix="native-http-home-") as home:
            env = {
                "AUTH_DISABLED": "true",
                "JWT_SECRET": "native-http-test-secret",
                "ADMIN_API_KEY": "",
                "OPENAI_API_KEY": "",
                "ANTHROPIC_API_KEY": "",
                "GOOGLE_API_KEY": "",
                "MEM0_TELEMETRY": "false",
                "MEM0_VECTOR_STORE_PROVIDER": "pgvector",
                "PYTHON_DOTENV_DISABLED": "true",
                "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": str(SERVER_DIR),
                "HOME": home,
                "XDG_CONFIG_HOME": str(Path(home) / ".config"),
                "XDG_CACHE_HOME": str(Path(home) / ".cache"),
            }
            result = subprocess.run(
                [sys.executable, "-c", _NATIVE_HTTP_CHILD],
                input=json.dumps({
                    "requests": requests,
                    "memories": memories or [],
                    "fail_get": fail_get,
                }),
                text=True,
                capture_output=True,
                cwd=SERVER_DIR,
                env=env,
                timeout=20,
                check=False,
            )

        captured = f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        assert result.returncode == 0, f"isolated API process exited {result.returncode}; {captured}"
        try:
            return json.loads(result.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError) as error:
            pytest.fail(f"isolated API process did not emit response JSON ({error}); {captured}")

    return run
