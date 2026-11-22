import argparse
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from telegram_gateway import ApiError, api_client, main, open_channel, owner_id, pair_owner, poll


class TelegramGatewayTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "telegram.db"
        self.updates = []
        self.sent = []
        self.offsets = []
        self.model = self.enterContext(patch("telegram_gateway.model_text", return_value=json.dumps({"reply": "A local reply."})))
        self.enterContext(patch("telegram_gateway.check_ollama_model"))

    def add(self, n, text, sender=19, chat=19, kind="private", bot=False):
        self.updates.append({"update_id": n, "message": {"chat": {"type": kind, "id": chat},
            "from": {"id": sender, "is_bot": bot}, "text": text}})

    def api(self, method, **args):
        if method == "getUpdates":
            self.offsets.append(args["offset"])
            return self.updates  # Deliberately replay old IDs on every poll.
        self.assertEqual(method, "sendMessage")
        self.sent.append(args)
        return {"message_id": len(self.sent)}

    def run_poll(self, api=None, owner=19, bot=42):
        with closing(open_channel(self.path)) as db:
            poll(db, api or self.api, bot, owner, "fixture")

    def test_owner_and_chat_must_match_and_replay_is_ignored_after_restart(self):
        self.add(50, "group", kind="group")
        self.add(51, "other DM", chat=18, sender=18)
        self.add(52, "mismatched sender", sender=18)
        self.add(53, "bot message", bot=True)
        self.add(54, "real owner")
        self.run_poll()
        self.run_poll()
        self.assertEqual(self.offsets, [0, 55])
        self.assertEqual(self.sent, [{"chat_id": 19, "text": "A local reply."}])
        prompt = self.model.call_args.args[0]
        self.assertEqual(len(prompt), 2)
        self.assertEqual(prompt[-1]["content"], "real owner")

    def test_reply_includes_only_the_selected_excerpt_even_after_restart(self):
        self.add(70, "When was that?")
        message = self.updates[0]["message"]
        message["reply_to_message"] = {"chat": {"id": 19, "type": "private"}, "from": {"id": 42},
                                       "text": "Amber was Tuesday. Violet was Friday."}
        message["quote"] = {"text": "Violet was Friday."}
        self.run_poll()
        turn = json.loads(self.model.call_args.args[0][-1]["content"])
        self.assertEqual(turn, {"quoted_message": {"speaker": "Talos", "excerpt": "Violet was Friday."},
                                "new_request": "When was that?"})
        with closing(open_channel(self.path)) as db:
            self.assertEqual(db.execute("SELECT question FROM turns").fetchone()[0], "When was that?")
        self.assertNotIn(b"Violet was Friday", self.path.read_bytes())

    def test_foreign_parent_and_unsupported_quote_never_supply_new_instructions(self):
        self.add(1, "Explain")
        message = self.updates[0]["message"]
        parent = {"chat": {"id": 19, "type": "private"}, "from": {"id": 19}, "text": "Original wording"}
        message.update(reply_to_message=parent, quote={"text": "/reset"})
        self.run_poll()
        current = json.loads(self.model.call_args.args[0][-1]["content"])
        self.assertEqual(current["quoted_message"]["excerpt"], "Original wording")
        parent["chat"]["id"] = 20
        self.updates[0]["update_id"] = 2
        self.run_poll()
        self.assertEqual(self.model.call_args.args[0][-1]["content"], "Explain")

    def test_ignored_updates_share_one_cursor_commit_and_survive_restart(self):
        for number in range(1, 11):
            self.add(number, "ignored", chat=1000, sender=1000)
        statements = []
        with closing(open_channel(self.path)) as db:
            db.set_trace_callback(statements.append)
            poll(db, self.api, 42, 19, "fixture")
        self.assertEqual(statements.count("COMMIT"), 1)
        self.add(11, "hello")
        self.add(12, "ignored again", kind="group")
        self.run_poll()
        self.run_poll()
        self.assertEqual(self.offsets, [0, 11, 13])
        self.assertEqual(len(self.sent), 1)

    def test_history_correction_restart_and_reset(self):
        for index, words in enumerate(("The notebook was blue.", "Actually green.", "Which colour?"), 1):
            self.add(index, words)
            self.run_poll()
        prompt = self.model.call_args.args[0]
        self.assertEqual([r["content"] for r in prompt if r["role"] == "user"],
                         ["The notebook was blue.", "Actually green.", "Which colour?"])
        self.add(4, "/new")
        self.run_poll()
        self.add(5, "Hello")
        self.run_poll()
        self.assertEqual(len(self.model.call_args.args[0]), 2)

    def test_uncertain_delivery_is_not_repeated_and_rate_limit_can_retry(self):
        self.add(12, "One")
        for error in (ApiError(429, 2), ConnectionResetError()):
            def fail(method, **args):
                if method == "sendMessage":
                    raise error
                return self.api(method, **args)
            with self.assertRaises(type(error)):
                self.run_poll(fail)
        self.run_poll()
        self.assertEqual(self.offsets, [0, 13])
        self.assertEqual(self.sent, [])

    def test_help_remains_available_during_model_outage(self):
        self.model.side_effect = RuntimeError("offline")
        self.add(3, "Hi")
        self.add(4, "/help")
        self.run_poll()
        self.assertIn("unavailable", self.sent[0]["text"])
        self.assertIn("/new", self.sent[1]["text"])
        with closing(open_channel(self.path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM turns").fetchone()[0], 0)

    def test_bot_and_owner_have_separate_context_and_long_unicode_reply_fits(self):
        self.model.return_value = json.dumps({"reply": "😀" * 6000})
        self.add(1, "original context")
        self.run_poll()
        self.updates.clear()
        self.add(2, "second owner", chat=20, sender=20)
        self.run_poll(owner=20)
        self.assertEqual(len(self.model.call_args.args[0]), 2)
        self.updates.clear()
        self.add(1, "another bot")
        self.run_poll(bot=43)
        self.assertEqual(len(self.model.call_args.args[0]), 2)
        self.assertEqual(self.offsets, [0, 2, 0])
        self.assertTrue(all(len(m["text"].encode("utf-16-le")) <= 8000 for m in self.sent))
        self.assertEqual("".join(m["text"] for m in self.sent[:3]), "😀" * 6000)

    def test_rate_limit_on_second_chunk_resumes_without_repeating_first(self):
        self.model.return_value = json.dumps({"reply": "A" * 4000 + "second part"})
        self.add(9, "long answer")
        def limited(method, **args):
            if method == "sendMessage" and self.sent:
                raise ApiError(429, 3)
            return self.api(method, **args)
        with self.assertRaises(ApiError):
            self.run_poll(limited)
        self.run_poll()
        self.assertEqual([m["text"] for m in self.sent], ["A" * 4000, "second part"])
        self.model.assert_called_once()
        with closing(open_channel(self.path)) as db:
            self.assertEqual(db.execute("SELECT status,message_id FROM delivery").fetchone(), ("confirmed", 2))
            self.assertEqual(db.execute("SELECT count(*) FROM outbox").fetchone()[0], 0)

    def test_setup_checks_identity_and_webhook_before_model_without_consuming_messages(self):
        state = self.path.parent / "unused"
        argv = ["telegram_gateway.py", "--owner", "tg:19", "--check", "--state", str(state)]
        with patch.dict(os.environ, TALOS_TELEGRAM_TOKEN="42:fixture"), patch.object(sys, "argv", argv), patch(
            "telegram_gateway.api_client"
        ) as transport, patch("telegram_gateway.check_ollama_model") as check:
            api = transport.return_value
            api.side_effect = [{"id": 42, "is_bot": True}, {"url": ""}]
            main()
            check.assert_called_once()
            self.assertEqual([call.args[0] for call in api.call_args_list], ["getMe", "getWebhookInfo"])
            self.assertFalse(state.exists())
            api.side_effect = [{"id": 42, "is_bot": True}, {"url": "https://example.invalid"}]
            with self.assertRaises(SystemExit) as exit_code:
                main()
            self.assertEqual(exit_code.exception.code, 1)
            self.assertFalse(state.exists())
        for bad in ("tg:-19", "@username", "0", "123_456", "１２３", "9007199254740992"):
            with self.subTest(bad=bad), self.assertRaises(argparse.ArgumentTypeError):
                owner_id(bad)

    def test_http_client_targets_bot_api_and_reports_rate_limit_without_token(self):
        response = io.BytesIO(b'{"ok":false,"error_code":429,"parameters":{"retry_after":12}}')
        response.status = 429
        with patch("telegram_gateway.http.client.HTTPSConnection") as factory:
            conn = factory.return_value
            conn.getresponse.return_value = response
            with self.assertRaises(ApiError) as caught:
                api_client("42:fixture")("getUpdates", offset=5)
            self.assertEqual(caught.exception.delay, 12)
            self.assertNotIn("fixture", str(caught.exception))
            factory.assert_called_once_with("api.telegram.org", timeout=40)
            method, path, body, headers = conn.request.call_args.args
            self.assertEqual((method, path, json.loads(body)), ("POST", "/bot42:fixture/getUpdates", {"offset":5}))
            conn.close.assert_called_once()

    def test_incomplete_or_internal_model_output_never_reaches_chat_history(self):
        self.model.side_effect = ["analysis</think>answer", json.dumps({"reply": "<think>rough draft</think>"})]
        self.add(7, "first question")
        self.add(8, "second question")
        self.run_poll()
        self.assertTrue(all("unavailable" in m["text"] for m in self.sent))
        with closing(open_channel(self.path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM turns").fetchone()[0], 0)


    def test_pairing_accepts_only_matching_private_human_and_survives_restart(self):
        self.add(1, "/pair fixture-code", sender=31, chat=31, kind="group")
        self.add(2, "/pair fixture-code", sender=32, chat=32, bot=True)
        self.add(3, "/pair incorrect", sender=33, chat=33)
        self.add(4, "/pair fixture-code")
        self.add(5, "after pairing")
        with closing(open_channel(self.path)) as db, patch("telegram_gateway.secrets.token_urlsafe", return_value="fixture-code"):
            self.assertEqual(pair_owner(db, self.api, 42), 19)
        with closing(open_channel(self.path)) as db:
            self.assertEqual(pair_owner(db, None, 42), 19)
        self.run_poll()
        self.assertEqual(self.offsets, [0, 5])
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.model.call_args.args[0][-1]["content"], "after pairing")

    def test_expired_pairing_code_has_no_effect(self):
        self.add(1, "/pair fixture-code")
        with closing(open_channel(self.path)) as db, patch("telegram_gateway.secrets.token_urlsafe", return_value="fixture-code"), patch(
            "telegram_gateway.time.monotonic", side_effect=[0, 299, 301, 302]
        ):
            with self.assertRaisesRegex(RuntimeError, "expired"):
                pair_owner(db, self.api, 42)
            self.assertEqual(db.execute("SELECT count(*) FROM pairing").fetchone()[0], 0)


@unittest.skipUnless(os.environ.get("TALOS_TEST_OLLAMA") == "1", "requires an isolated local model")
class TelegramConversationReplay(unittest.TestCase):
    def test_model_uses_the_correction_after_restart(self):
        lines = ["I left a blue notebook in the drawer.",
                 "Correction: the notebook is green and it is on the shelf.",
                 "Which notebook did I leave, and where exactly is it?"]
        replies = []
        with tempfile.TemporaryDirectory() as folder:
            for number, text in enumerate(lines, 1):
                def api(method, **body):
                    if method == "getUpdates":
                        return [{"update_id": number, "message": {"chat": {"id": 8, "type": "private"},
                                "from": {"id": 8}, "text": text}}]
                    replies.append(body["text"])
                    return {"message_id": number}
                with closing(open_channel(Path(folder) / "channel.db")) as db:
                    poll(db, api, 42, 8, "qwen3:4b")
        self.assertEqual(len(replies), 3)
        for reply in replies:
            self.assertNotIn("unavailable", reply)
            self.assertNotIn("</think>", reply)
        self.assertIn("green", replies[-1].casefold())
        self.assertIn("shelf", replies[-1].casefold())
        self.assertNotRegex(replies[-1].casefold(), r"\b(blue|drawer|verified|confirmed)\b")
