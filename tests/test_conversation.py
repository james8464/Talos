import unittest

from talos_core.cognition.conversation import MAX_HISTORY_CHARS, MAX_MESSAGE_CHARS, RECENT_MESSAGES, limit_history


class ConversationHistoryTests(unittest.TestCase):
    def setUp(self):
        self.instructions = {"role": "system", "content": "You are Talos."}

    def test_retains_latest_messages_and_system_instructions(self):
        conversation = [
            {"role": role, "content": str(turn)}
            for turn in range(RECENT_MESSAGES)
            for role in ("user", "assistant")
        ]
        messages = [self.instructions, *conversation]

        self.assertEqual(
            limit_history(messages), [self.instructions, *conversation[-RECENT_MESSAGES:]]
        )
        self.assertEqual(len(messages), 1 + RECENT_MESSAGES * 2)

    def test_drops_leading_assistant_reply_after_trimming(self):
        conversation = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": str(i)}
            for i in range(RECENT_MESSAGES + 1)
        ]

        self.assertEqual(limit_history([self.instructions, *conversation])[1:], conversation[2:])

    def test_counts_recent_text_against_total_limit(self):
        original = "x" * (MAX_HISTORY_CHARS * 2) + " latest question"
        messages = [self.instructions, *[
            {"role": role, "content": original}
            for role in ("user", "assistant", "user", "assistant", "user")
        ]]

        retained = limit_history(messages)[1:]

        self.assertLessEqual(sum(len(message["content"]) for message in retained), MAX_HISTORY_CHARS)
        self.assertEqual(retained[-1]["content"], original[-MAX_MESSAGE_CHARS:])
        self.assertEqual(messages[-1]["content"], original)
