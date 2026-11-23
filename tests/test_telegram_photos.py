import base64
import io
import os
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from talos_core.interfaces.telegram_gateway import MAX_PHOTO_BYTES, jpeg_pixels, download_photo, open_channel, poll


class TelegramPhotoTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(self.directory) / "channel.db"
        self.image = (Path(__file__).parent / "fixtures/synthetic-shirt.jpg").read_bytes()
        self.sent = []
        self.files = []
        self.updates = []
        self.download = Mock(return_value=self.image)

    def photo(self, number, *, sender=7, chat=7, kind="private", bot=False):
        self.updates.append({"update_id": number, "message": {"from": {"id": sender, "is_bot": bot},
            "chat": {"id": chat, "type": kind}, "caption": "Which garment is visible?",
            "photo": [{"width": 90, "height": 90, "file_id": "tiny"},
                      {"width": 320, "height": 240, "file_id": "small"},
                      {"width": 800, "height": 600, "file_id": "big", "file_size": 300000}]}})

    def api(self, method, **params):
        if method == "getUpdates":
            return self.updates
        if method == "getFile":
            self.files.append(params["file_id"])
            return {"file_path": "photos/" + params["file_id"] + ".jpg"}
        self.assertEqual(method, "sendMessage")
        self.sent.append(params["text"])
        return {"message_id": len(self.sent)}

    def poll(self):
        with closing(open_channel(self.path)) as db:
            poll(db, self.api, 42, 7, "fixture", download=self.download)

    def test_authorization_precedes_fetch_and_photo_is_never_archived(self):
        self.photo(1, sender=1, chat=1)
        self.photo(2, kind="group")
        self.photo(3, bot=True)
        self.photo(4)
        with patch("talos_core.interfaces.telegram_gateway.describe_image", return_value="A dark shirt.") as vision:
            self.poll()
            self.poll()
        vision.assert_called_once_with("Which garment is visible?", base64.b64encode(self.image).decode())
        self.assertEqual(self.files, ["small"])
        self.assertEqual(self.sent, ["A dark shirt."])
        with closing(open_channel(self.path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM turns").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM outbox").fetchone()[0], 0)
        self.assertNotIn(base64.b64encode(self.image[:120]), self.path.read_bytes())
        self.assertNotIn(b"Which garment", self.path.read_bytes())

    def test_caption_and_current_photo_reach_the_vision_request_together(self):
        self.photo(1)
        calls = []
        def ollama(method, path, **params):
            calls.append((method, path, params))
            if path == "/api/show":
                return {"capabilities": ["vision"]}
            return {"message": {"content": "A shirt."}}
        with patch("talos_core.room.room_vision.check_ollama_model") as check, patch("talos_core.room.room_vision.request_ollama_json", side_effect=ollama):
            self.poll()
        check.assert_called_once()
        self.assertEqual([path for _, path, _ in calls], ["/api/show", "/api/chat"])
        messages = calls[1][2]["payload"]["messages"]
        self.assertEqual(len(messages), 2)
        self.assertIn("Do not identify people", messages[0]["content"])
        self.assertEqual(messages[1], {"role": "user", "content": "Which garment is visible?",
                                       "images": [base64.b64encode(self.image).decode()]})
        self.assertEqual(self.sent, ["A shirt."])

    def test_jpeg_document_uses_bytes_not_its_filename_and_skips_other_files(self):
        self.photo(1)
        message = self.updates[0]["message"]
        del message["photo"]
        message["document"] = {"mime_type": "image/jpeg", "file_name": "../untrusted.jpg", "file_id": "original"}
        self.updates.append({"update_id": 2, "message": {**message, "document": {"mime_type": "application/pdf"}}})
        with patch("talos_core.interfaces.telegram_gateway.describe_image", return_value="A shirt."):
            self.poll()
        self.assertEqual(self.files, ["original"])
        self.assertEqual(self.sent, ["A shirt."])
        self.assertNotIn(b"untrusted.jpg", self.path.read_bytes())

    def test_reply_to_owner_photo_uses_new_question_and_commands_still_work(self):
        self.photo(1)
        parent = self.updates.pop()["message"]
        self.updates = [{"update_id": i, "message": {
            "from": {"id": 7}, "chat": {"id": 7, "type": "private"},
            "reply_to_message": parent, "text": text}}
            for i, text in enumerate(("What color is it?", "/new"), 5)]
        with patch("talos_core.interfaces.telegram_gateway.describe_image", return_value="Dark blue.") as vision:
            self.poll()
        self.assertEqual(vision.call_args.args[0], "What color is it?")
        self.assertEqual(self.files, ["small"])
        self.assertEqual(self.sent, ["Dark blue.", "Started a fresh conversation."])

    def test_reply_to_photo_from_another_chat_never_fetches_the_photo(self):
        self.photo(1, chat=8)
        parent = self.updates.pop()["message"]
        self.updates = [{"update_id": 2, "message": {"from": {"id": 7}, "chat": {"id": 7, "type": "private"},
                         "reply_to_message": parent, "text": "Hello"}}]
        with patch("talos_core.interfaces.telegram_gateway.reply_to", return_value="Hello.") as reply:
            self.poll()
        self.assertEqual(self.files, [])
        self.assertEqual(reply.call_args.args[3], "Hello")

    def test_retries_a_read_and_falls_back_to_smaller_size(self):
        self.photo(1)
        self.download.side_effect = [TimeoutError(), b"x" * (MAX_PHOTO_BYTES + 1), self.image]
        with patch("talos_core.interfaces.telegram_gateway.describe_image", return_value="A shirt."):
            self.poll()
        self.assertEqual(self.files, ["small", "small", "tiny"])
        self.assertEqual(self.sent, ["A shirt."])

    def test_compressed_byte_limit_alone_cannot_admit_an_oversized_frame(self):
        self.assertEqual(jpeg_pixels(self.image), 627 * 627)
        huge = b"\xff\xd8\xff\xc0\x00\x0b\x08\xff\xff\xff\xff\x01\x01\x11\x00\xff\xd9"
        self.photo(1)
        self.download.return_value = huge
        with patch("talos_core.interfaces.telegram_gateway.describe_image") as vision:
            self.poll()
        vision.assert_not_called()
        self.assertIn("too large", self.sent[0])
        with self.assertRaises(ValueError):
            jpeg_pixels(b"\xff\xd8\xff\xe0\xff\xff\xff\xd9")

    def test_invalid_photo_and_oversized_photo_do_not_reach_model(self):
        self.photo(1)
        self.download.return_value = b"not jpeg"
        with patch("talos_core.interfaces.telegram_gateway.describe_image") as vision:
            self.poll()
            self.photo(2)
            self.download.return_value = b"x" * (MAX_PHOTO_BYTES + 1)
            self.poll()
        vision.assert_not_called()
        self.assertIn("resend", self.sent[0])
        self.assertIn("too large", self.sent[1])

    def test_http_reader_bounds_body_and_refuses_redirects_and_absolute_paths(self):
        with patch("talos_core.interfaces.telegram_gateway.http.client.HTTPSConnection") as factory:
            for bad in ("https://example.test/a.jpg", "../a.jpg", "photos/%2e%2e/a.jpg"):
                with self.assertRaises(ValueError):
                    download_photo("42:fixture", bad)
            factory.assert_not_called()
            response = io.BytesIO(b"x" * (MAX_PHOTO_BYTES + 500))
            response.status = 200
            factory.return_value.getresponse.return_value = response
            self.assertEqual(len(download_photo("42:fixture", "photos/file_123.jpg")), MAX_PHOTO_BYTES + 1)
            factory.return_value.request.assert_called_once_with("GET", "/file/bot42:fixture/photos/file_123.jpg")
            response.status = 302
            with self.assertRaises(OSError):
                download_photo("42:fixture", "photos/file_123.jpg")

    @unittest.skipUnless(os.environ.get("TALOS_TEST_VISION") == "1", "requires the local vision model")
    def test_real_local_photo_answer(self):
        self.photo(1)
        self.poll()
        self.assertIn("shirt", self.sent[0].casefold())
