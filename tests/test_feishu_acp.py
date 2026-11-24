import io
import json
import tempfile
import unittest
from pathlib import Path

from talos_core import acp, feishu


class Response:
    def __init__(self, value):
        self.value = value
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self): return json.dumps(self.value).encode()


class FeishuAcpTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_feishu_doc_and_feishu_drive_with_fake_transport(self):
        feishu.configure(self.state, "app", "secret")
        replies = [
            Response({"code": 0, "tenant_access_token": "token"}),
            Response({"code": 0, "data": {"content": "hello"}}),
            Response({"code": 0, "tenant_access_token": "token"}),
            Response({"code": 0, "data": {"files": []}}),
        ]
        self.assertEqual(feishu.doc(self.state, "doc1", opener=lambda *a, **k: replies.pop(0))["data"]["content"], "hello")
        self.assertEqual(feishu.drive(self.state, opener=lambda *a, **k: replies.pop(0))["data"]["files"], [])

    def test_feishu_requires_credentials(self):
        with self.assertRaises(RuntimeError):
            feishu.token(self.state)

    def test_acp_initialization_and_prompt(self):
        lines = io.StringIO("\n".join([
            json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"sessionId": "s1"}}),
            json.dumps({"jsonrpc": "2.0", "id": 3, "result": {"stopReason": "end_turn"}}),
        ]) + "\n")
        process = type("P", (), {
            "stdin": io.StringIO(), "stdout": lines, "stderr": io.StringIO(),
            "wait": lambda self, timeout=None: 0,
            "kill": lambda self: None,
        })()
        session = acp.ACPSession(["agent"], popen=lambda *a, **k: process)
        self.assertEqual(session.initialize()["protocolVersion"], "2024-11-05")
        self.assertEqual(session.prompt("hi")["stopReason"], "end_turn")
        session.close()

    def test_acp_error_is_reported(self):
        lines = io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"message": "bad"}}) + "\n")
        process = type("P", (), {"stdin": io.StringIO(), "stdout": lines, "stderr": io.StringIO(),
                                 "wait": lambda self, timeout=None: 0, "kill": lambda self: None})()
        session = acp.ACPSession(["agent"], popen=lambda *a, **k: process)
        with self.assertRaises(acp.ACPError):
            session.call("initialize")


if __name__ == "__main__":
    unittest.main()
