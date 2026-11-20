"""Exercise the Zalo PC reader's boundaries without reading personal conversations."""
from _paths import PACK
import asyncio
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("pc_history_tested", PACK / "plugins/zalo-extras/pc_history.py")
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)


class PCReaderTests(unittest.IsolatedAsyncioTestCase):
    def test_invalid_request_never_starts_windows(self):
        for args in ({}, {"expected_chat": "\n"}, {"expected_chat": "Hao", "max_pages": True},
                     {"expected_chat": "Hao", "max_pages": 0}, {"expected_chat": "Hao", "max_pages": 13}):
            with self.assertRaises(ValueError):
                reader.validate_request(args)

    def test_chat_name_is_data_even_with_shell_characters(self):
        name = 'Hao & $(Get-Process) " quoted'
        self.assertEqual(reader.validate_request({"expected_chat": name}), (name, 1))
        self.assertNotIn(name, reader.SCRIPT)
        self.assertIn("ConvertFrom-Json", reader.SCRIPT)
        self.assertNotIn("SendKeys", reader.SCRIPT)
        self.assertNotIn("SetForegroundWindow", reader.SCRIPT)

    async def test_unavailable_platform_returns_actionable_error(self):
        with patch.object(reader, "check", return_value="Interactive Windows required"):
            result = json.loads(await reader.read_history({"expected_chat": "Hao"}, SimpleNamespace()))
        self.assertFalse(result["ok"])
        self.assertIn("Windows", result["error"])

    async def run_fake_capture(self, payload):
        with tempfile.TemporaryDirectory() as folder:
            context = SimpleNamespace(vault_root=folder)
            class Process:
                returncode = 0
                async def communicate(self):
                    return json.dumps(payload).encode(), b""
            async def launch(*args, **kwargs):
                import base64
                request = json.loads(base64.b64decode(kwargs["env"]["JAVIS_ZALO_PC_REQUEST"]))
                directory = Path(request["directory"])
                if payload.get("ok"):
                    screenshot = directory / "page.png"
                    screenshot.write_bytes(b"test evidence")
                    payload["pages"] = [{"page": 1, "screenshot": str(screenshot), "text": "12 boxes"}]
                return Process()
            with patch.object(reader, "check", return_value=None), patch.object(
                reader.asyncio, "create_subprocess_exec", side_effect=launch
            ):
                result = json.loads(await reader.read_history({"expected_chat": "Hao"}, context))
            files = list(Path(folder).rglob("*.png"))
            return result, len(files)

    async def test_wrong_conversation_returns_no_content_or_saved_screenshot(self):
        result, count = await self.run_fake_capture({"ok": False, "error": "Conversation header mismatch"})
        self.assertFalse(result["ok"])
        self.assertNotIn("pages", result)
        self.assertEqual(count, 0)

    async def test_visible_pages_never_claim_full_coverage_or_verified_account(self):
        result, count = await self.run_fake_capture({"ok": True, "stop_reason": "no_visible_change"})
        self.assertFalse(result["complete"])
        self.assertFalse(result["account_verified"])
        self.assertEqual(count, 1)
        self.assertTrue(result["pages"][0]["screenshot"].startswith("attachments/zalo-pc/"))
        self.assertIn("not proof", result["limitations"])

    def test_registration_uses_full_permission_for_desktop_scrolling(self):
        tools = []
        reader.register(SimpleNamespace(register_tool=lambda **kw: tools.append(kw)))
        self.assertEqual(tools[0]["name"], "zalo_pc_read_history")
        self.assertEqual(tools[0]["min_mode"], "full")
        self.assertIn("expected_chat", tools[0]["schema"]["required"])

    async def test_missing_brain_never_launches_capture(self):
        with patch.object(reader, "check", return_value=None), patch.object(
            reader.asyncio, "create_subprocess_exec"
        ) as launch:
            result = json.loads(await reader.read_history({"expected_chat": "Hao"}, SimpleNamespace()))
        self.assertFalse(result["ok"])
        launch.assert_not_called()

    async def test_start_failure_removes_evidence_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(reader, "check", return_value=None), patch.object(
                reader.asyncio, "create_subprocess_exec", side_effect=OSError("blocked")
            ):
                result = json.loads(await reader.read_history(
                    {"expected_chat": "Hao"}, SimpleNamespace(vault_root=folder)))
            self.assertFalse(result["ok"])
            self.assertEqual(list((Path(folder) / "attachments/zalo-pc").iterdir()), [])

    async def test_invalid_capture_responses_are_cleaned_up(self):
        for payload in (None, [], {"ok": True}, {"ok": True, "pages": [None]},
                        {"ok": True, "pages": [{"text": "private"}]}):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as folder:
                class Process:
                    returncode = 0
                    async def communicate(self):
                        return json.dumps(payload).encode(), b""
                async def launch(*args, **kwargs):
                    return Process()
                with patch.object(reader, "check", return_value=None), patch.object(
                    reader.asyncio, "create_subprocess_exec", side_effect=launch
                ):
                    result = json.loads(await reader.read_history(
                        {"expected_chat": "Hao"}, SimpleNamespace(vault_root=folder)))
                self.assertFalse(result["ok"])
                self.assertNotIn("pages", result)
                self.assertEqual(list((Path(folder) / "attachments/zalo-pc").iterdir()), [])

    async def test_nonzero_exit_cannot_claim_success(self):
        with tempfile.TemporaryDirectory() as folder:
            class Process:
                returncode = 1
                async def communicate(self):
                    return b'{"ok":true,"pages":[{"text":"private"}]}', b""
            async def launch(*args, **kwargs):
                return Process()
            with patch.object(reader, "check", return_value=None), patch.object(
                reader.asyncio, "create_subprocess_exec", side_effect=launch
            ):
                result = json.loads(await reader.read_history(
                    {"expected_chat": "Hao"}, SimpleNamespace(vault_root=folder)))
            self.assertFalse(result["ok"])
            self.assertNotIn("pages", result)


if __name__ == "__main__":
    unittest.main()
