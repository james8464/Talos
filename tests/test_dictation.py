import unittest

from talos_core.cognition import dictation as d


class AddressTests(unittest.TestCase):
    def test_wake_word_and_direct_address_cues_are_addressed(self):
        cases = {
            "Hey Talos, what's the weather?": d.REASON_WAKE_GREETING,
            "Hi, Talos": d.REASON_WAKE_GREETING,
            "Talos, set a timer.": d.REASON_WAKE_VOCATIVE,
            "Wait, Talos": d.REASON_WAKE_VOCATIVE,
            "Talos stop": d.REASON_WAKE_LEADING,
            "Can you open the window?": d.REASON_REQUEST,
            "Please remind me at noon": d.REASON_REQUEST,
            "Could you check on Talos later?": d.REASON_WAKE_REQUEST,
        }
        for text, reason in cases.items():
            with self.subTest(text=text):
                decision = d.addressed(text)
                self.assertTrue(decision)
                self.assertTrue(decision.accepted)
                self.assertEqual(decision.reason, reason)
                self.assertEqual(decision.source, "text")

    def test_third_person_and_reported_speech_are_not_addressed(self):
        cases = {
            "I told him to call me": d.REASON_THIRD_PERSON,
            "I told Talos to wait": d.REASON_REPORTED_ASSISTANT,
            "She said Talos can wait": d.REASON_THIRD_PERSON,
            "They asked us to move": d.REASON_THIRD_PERSON,
            "Talos is offline again": d.REASON_ABOUT_ASSISTANT,
            "Talos will be late": d.REASON_ABOUT_ASSISTANT,
            "I need Talos to help with this": d.REASON_NAME_MENTION,
            "The meeting is at noon": d.REASON_BACKGROUND,
            "   ": d.REASON_EMPTY,
        }
        for text, reason in cases.items():
            with self.subTest(text=text):
                decision = d.addressed(text)
                self.assertFalse(decision)
                self.assertFalse(decision.accepted)
                self.assertEqual(decision.reason, reason)

    def test_vocative_wins_over_quoted_third_person_content(self):
        cases = {
            "Hey Talos, he said to wait": d.REASON_WAKE_GREETING,
            "Talos, they said it is ready": d.REASON_WAKE_VOCATIVE,
        }
        for text, reason in cases.items():
            with self.subTest(text=text):
                decision = d.addressed(text)
                self.assertTrue(decision)
                self.assertEqual(decision.reason, reason)

    def test_wake_word_with_request_is_not_treated_as_a_mention(self):
        decision = d.addressed("Could you ask Talos to open it?")
        self.assertTrue(decision)
        self.assertEqual(decision.reason, d.REASON_WAKE_REQUEST)
        self.assertEqual(decision.cue, "wake_word")

    def test_addressed_is_case_and_whitespace_insensitive_and_deterministic(self):
        first = d.addressed("   HEY   tAlOs,   stop  ")
        second = d.addressed("hey talos, stop")
        self.assertTrue(first)
        self.assertEqual(first, second)
        self.assertEqual(first.reason, d.REASON_WAKE_GREETING)

    def test_addressed_rejects_non_text(self):
        with self.assertRaises(TypeError):
            d.addressed(None)


class VisionTests(unittest.TestCase):
    def test_single_person_facing_the_mac_is_attentive(self):
        decision = d.vision_attention(1, True)
        self.assertTrue(decision)
        self.assertEqual(decision.reason, "one person facing the Mac")
        self.assertEqual(decision.source, "camera")
        self.assertEqual(decision.cue, "single_facing")

    def test_single_person_away_multiple_people_and_empty_view_are_not_attentive(self):
        cases = [
            (0, True, "no one in view", "empty"),
            (1, False, "one person facing away from the Mac", "single_away"),
            (1, None, "one person facing away from the Mac", "single_away"),
            (2, True, "2 people in view; audience is ambiguous", "crowd"),
            (3, False, "3 people in view; audience is ambiguous", "crowd"),
        ]
        for people, facing, reason, cue in cases:
            with self.subTest(people=people, facing=facing):
                decision = d.vision_attention(people, facing)
                self.assertFalse(decision)
                self.assertEqual(decision.reason, reason)
                self.assertEqual(decision.cue, cue)

    def test_facing_accepts_strings_and_people_accepts_observations(self):
        self.assertTrue(d.vision_attention(1, "toward"))
        self.assertTrue(d.vision_attention(1, "at-mac"))
        self.assertFalse(d.vision_attention(1, "away"))
        self.assertTrue(d.vision_attention(["alex"], True))
        self.assertFalse(d.vision_attention(["alex", "sam"], True))

    def test_invalid_camera_summaries_are_rejected(self):
        with self.assertRaises(ValueError):
            d.vision_attention(-1, True)
        with self.assertRaises(ValueError):
            d.vision_attention(1, "sideways")
        with self.assertRaises(TypeError):
            d.vision_attention(True, True)
        with self.assertRaises(TypeError):
            d.vision_attention("one", True)


class AttendingTests(unittest.TestCase):
    def test_explicit_address_wins_and_camera_cannot_override_it(self):
        decision = d.attending("Hey Talos", speaker="other", people=3, facing=False)
        self.assertTrue(decision)
        self.assertEqual(
            decision.reason,
            "explicit address: greeting followed by the wake word; "
            "camera ignored: 3 people in view; audience is ambiguous",
        )

    def test_explicit_address_is_attended_even_with_an_empty_view(self):
        decision = d.attending("Talos, are you there?", people=0, facing=True)
        self.assertTrue(decision)
        self.assertEqual(
            decision.reason,
            "explicit address: vocative wake word; camera ignored: no one in view",
        )

    def test_other_person_speech_is_not_attended(self):
        decision = d.attending("I told him to wait outside", speaker="other")
        self.assertFalse(decision)
        self.assertEqual(
            decision.reason,
            "speaker is other; third-person reference; speech is about someone else",
        )

    def test_background_speech_from_the_primary_user_is_not_attended(self):
        decision = d.attending("I told him to wait outside")
        self.assertFalse(decision)
        self.assertEqual(
            decision.reason,
            "no direct address; third-person reference; speech is about someone else",
        )

    def test_camera_can_confirm_an_unaddressed_utterance(self):
        decision = d.attending("what time is it", people=1, facing="toward")
        self.assertTrue(decision)
        self.assertEqual(decision.reason, "no direct address; camera: one person facing the Mac")
        self.assertEqual(decision.source, "camera")

    def test_camera_gates_out_crowds_and_people_facing_away(self):
        crowd = d.attending("what time is it", people=["alex", "sam"], facing=True)
        self.assertFalse(crowd)
        self.assertEqual(
            crowd.reason,
            "no direct address; camera: 2 people in view; audience is ambiguous",
        )
        away = d.attending("what time is it", people=1, facing=False)
        self.assertFalse(away)
        self.assertEqual(
            away.reason,
            "no direct address; camera: one person facing away from the Mac",
        )

    def test_camera_cannot_override_third_person_speech(self):
        decision = d.attending("I told him to wait outside", people=1, facing=True)
        self.assertFalse(decision)
        self.assertEqual(
            decision.reason,
            "no direct address; third-person reference; speech is about someone else; "
            "camera does not override the text",
        )
        mention = d.attending("I need Talos to help with this", people=1, facing=True)
        self.assertFalse(mention)
        self.assertEqual(
            mention.reason,
            "no direct address; Talos is mentioned but not addressed; "
            "camera does not override the text",
        )

    def test_without_address_or_camera_the_topic_is_not_attended(self):
        decision = d.attending("the meeting is at noon")
        self.assertFalse(decision)
        self.assertEqual(
            decision.reason,
            "no direct address; no wake word or direct-address cue; likely background speech",
        )


class StateMachineTests(unittest.TestCase):
    def assert_targets(self, event, expected):
        self.assertEqual([transition.target for transition in event.transitions], expected)

    def test_state_names_and_initial_state(self):
        self.assertEqual(
            set(d.STATES),
            {"idle", "listening", "user_speaking", "agent_speaking", "interrupted", "thinking"},
        )
        machine = d.DictationMachine(at=0)
        self.assertEqual(machine.state, d.IDLE)
        self.assertEqual(len(machine.history), 1)
        self.assertEqual(machine.history[0].source, None)
        self.assertEqual(machine.history[0].target, d.IDLE)
        self.assertEqual(machine.history[0].reason, "initialized")

    def test_happy_path_covers_every_primary_transition(self):
        machine = d.DictationMachine(at=0)
        start = machine.start(1)
        self.assertTrue(start)
        self.assertEqual(start.reason, "dictation started; listening")
        self.assertEqual(machine.state, d.LISTENING)

        speaking = machine.speech_started(2)
        self.assertTrue(speaking)
        self.assertEqual(speaking.reason, "user speech detected")
        self.assertEqual(machine.state, d.USER_SPEAKING)

        thinking = machine.speech_ended(3)
        self.assertTrue(thinking)
        self.assertEqual(thinking.reason, "user finished speaking; thinking")
        self.assertEqual(machine.state, d.THINKING)

        response = machine.response_started(4)
        self.assertTrue(response)
        self.assertEqual(response.reason, "agent began speaking")
        self.assertEqual(machine.state, d.AGENT_SPEAKING)

        finished = machine.response_finished(5)
        self.assertTrue(finished)
        self.assertEqual(finished.reason, "agent finished speaking; listening")
        self.assertEqual(machine.state, d.LISTENING)

        self.assertEqual(
            [transition.target for transition in machine.history],
            [
                d.IDLE,
                d.LISTENING,
                d.USER_SPEAKING,
                d.THINKING,
                d.AGENT_SPEAKING,
                d.LISTENING,
            ],
        )
        self.assertEqual(
            [transition.at for transition in machine.history],
            [0.0, 1.0, 2.0, 3.0, 4.0, 5.0],
        )

    def test_barge_in_stops_playback_and_returns_to_listening(self):
        machine = d.DictationMachine(at=0)
        machine.start(1)
        machine.speech_started(2)
        machine.speech_ended(3)
        machine.response_started(4)

        event = machine.speech_started(5, text="Hey Talos, stop talking")

        self.assertTrue(event)
        self.assertTrue(event.barged_in)
        self.assertEqual(event.state, d.LISTENING)
        self.assertEqual(machine.state, d.LISTENING)
        self.assert_targets(event, [d.INTERRUPTED, d.LISTENING])
        self.assertEqual(event.transitions[0].reason, "barge-in: user speech stopped playback")
        self.assertEqual(event.transitions[1].reason, "barge-in: playback stopped; listening resumed")
        self.assertEqual(event.reason, "barge-in: playback stopped and listening resumed")

    def test_barge_in_accepts_raw_speech_without_a_transcript(self):
        machine = d.DictationMachine(at=0)
        machine.response_started(1)
        event = machine.speech_started(2)
        self.assertTrue(event.barged_in)
        self.assertEqual(machine.state, d.LISTENING)

    def test_background_speech_does_not_barge_in(self):
        machine = d.DictationMachine(at=0)
        machine.start(1)
        machine.speech_started(2)
        machine.speech_ended(3)
        machine.response_started(4)
        before = list(machine.history)

        event = machine.speech_started(5, text="I told him to wait", speaker="other")

        self.assertFalse(event)
        self.assertFalse(event.barged_in)
        self.assertEqual(machine.state, d.AGENT_SPEAKING)
        self.assertEqual(machine.history, before)
        self.assertIn("ignored speech during agent playback", event.reason)
        self.assertIn(d.REASON_THIRD_PERSON, event.reason)

    def test_user_speech_supersedes_thinking(self):
        machine = d.DictationMachine(at=0)
        machine.start(1)
        machine.speech_started(2)
        machine.speech_ended(3)

        event = machine.speech_started(4, text="wait, Talos")

        self.assertTrue(event)
        self.assertEqual(machine.state, d.USER_SPEAKING)
        self.assertEqual(event.reason, "user speech superseded thinking")
        self.assert_targets(event, [d.USER_SPEAKING])

    def test_explicit_interrupt_and_resume(self):
        machine = d.DictationMachine(at=0)
        machine.response_started(1)

        interrupted = machine.interrupt(2, reason="user pressed stop")
        self.assertTrue(interrupted)
        self.assertEqual(machine.state, d.INTERRUPTED)
        self.assertEqual(interrupted.reason, "user pressed stop")

        resumed = machine.resume(3)
        self.assertTrue(resumed)
        self.assertEqual(machine.state, d.LISTENING)
        self.assertEqual(resumed.reason, "listening resumed")

    def test_thinking_can_be_interrupted(self):
        machine = d.DictationMachine(at=0)
        machine.start(1)
        machine.speech_started(2)
        machine.speech_ended(3)

        event = machine.interrupt(4)

        self.assertTrue(event)
        self.assertEqual(machine.state, d.INTERRUPTED)
        self.assertEqual(event.reason, "thinking interrupted")

    def test_speech_after_explicit_interrupt_resumes_then_records_user_speech(self):
        machine = d.DictationMachine(at=0)
        machine.response_started(1)
        machine.interrupt(2)

        event = machine.speech_started(3, text="Talos")

        self.assertTrue(event)
        self.assertEqual(machine.state, d.USER_SPEAKING)
        self.assert_targets(event, [d.LISTENING, d.USER_SPEAKING])

    def test_stop_returns_to_idle_from_any_state(self):
        for prepare, timestamp in [
            (lambda m: None, 1),
            (lambda m: m.start(1), 2),
            (lambda m: (m.start(1), m.speech_started(2)), 3),
            (lambda m: (m.start(1), m.speech_started(2), m.speech_ended(3)), 4),
            (lambda m: m.response_started(1), 2),
            (lambda m: (m.response_started(1), m.interrupt(2)), 3),
        ]:
            with self.subTest(timestamp=timestamp):
                machine = d.DictationMachine(at=0)
                prepare(machine)
                starting_state = machine.state
                event = machine.stop(timestamp)
                self.assertTrue(event)
                self.assertEqual(machine.state, d.IDLE)
                expected = "already idle" if starting_state == d.IDLE else "dictation stopped"
                self.assertEqual(event.reason, expected)

    def test_agent_can_speak_unprompted_from_idle_or_listening(self):
        idle = d.DictationMachine(at=0)
        event = idle.response_started(1)
        self.assertTrue(event)
        self.assertEqual(idle.state, d.AGENT_SPEAKING)
        self.assertEqual(event.reason, "agent began speaking")

        listening = d.DictationMachine(at=0)
        listening.start(1)
        event = listening.response_started(2)
        self.assertTrue(event)
        self.assertEqual(listening.state, d.AGENT_SPEAKING)

    def test_invalid_events_are_rejected_without_changing_state(self):
        idle = d.DictationMachine(at=0)
        self.assertFalse(idle.speech_ended(1))
        self.assertFalse(idle.response_finished(1))
        self.assertFalse(idle.resume(1))
        self.assertFalse(idle.interrupt(1))
        self.assertEqual(idle.state, d.IDLE)

        speaking = d.DictationMachine(at=0)
        speaking.start(1)
        already = speaking.start(2)
        self.assertTrue(already)
        self.assertEqual(already.reason, "already listening")
        self.assertEqual(speaking.state, d.LISTENING)
        self.assertEqual(len(speaking.history), 2)

    def test_continued_speech_stays_in_user_speaking(self):
        machine = d.DictationMachine(at=0)
        machine.start(1)
        machine.speech_started(2)
        history = list(machine.history)

        event = machine.speech_started(3, text="and another thing")

        self.assertTrue(event)
        self.assertEqual(machine.state, d.USER_SPEAKING)
        self.assertEqual(event.reason, "continued user speech")
        self.assertEqual(machine.history, history)

    def test_timestamps_must_be_finite_numbers_and_move_forward(self):
        machine = d.DictationMachine(at=0)
        machine.start(5)

        with self.assertRaises(ValueError):
            machine.speech_started(4)
        with self.assertRaises(TypeError):
            machine.speech_started("6")
        with self.assertRaises(TypeError):
            machine.speech_started(True)
        with self.assertRaises(ValueError):
            d.DictationMachine(at=float("nan"))
        with self.assertRaises(TypeError):
            d.DictationMachine(at="0")

    def test_equal_timestamps_are_allowed_for_a_single_burst(self):
        machine = d.DictationMachine(at=0)
        machine.start(1)
        event = machine.speech_started(1, text="Talos")
        self.assertTrue(event)
        self.assertEqual(machine.state, d.USER_SPEAKING)
        self.assertEqual([transition.at for transition in event.transitions], [1.0])

    def test_replay_is_deterministic(self):
        def run():
            machine = d.DictationMachine(at=0)
            machine.start(1)
            machine.speech_started(2, text="Hey Talos", people=2, facing=True)
            machine.speech_ended(3)
            machine.response_started(4)
            machine.speech_started(5, text="I told him to wait", speaker="other")
            machine.speech_started(6, text="Talos, stop")
            machine.resume(7)
            return machine

        first, second = run(), run()
        self.assertEqual(first.state, second.state)
        self.assertEqual(first.history, second.history)
        self.assertEqual(first.updated_at, second.updated_at)


class MachineAttentionGatingTests(unittest.TestCase):
    def test_camera_allows_unaddressed_speech_from_the_primary_user(self):
        machine = d.DictationMachine(at=0)
        machine.start(1)
        event = machine.speech_started(2, text="what time is it", people=1, facing=True)
        self.assertTrue(event)
        self.assertEqual(machine.state, d.USER_SPEAKING)
        self.assertIsNotNone(event.attention)
        self.assertTrue(event.attention)
        self.assertIn("camera", event.attention.reason)

    def test_camera_crowd_keeps_unaddressed_speech_out(self):
        machine = d.DictationMachine(at=0)
        machine.start(1)
        event = machine.speech_started(2, text="what time is it", people=2, facing=True)
        self.assertFalse(event)
        self.assertEqual(machine.state, d.LISTENING)
        self.assertIn("audience is ambiguous", event.reason)

    def test_single_viewer_does_not_make_third_person_speech_barge_in(self):
        machine = d.DictationMachine(at=0)
        machine.start(1)
        machine.speech_started(2)
        machine.speech_ended(3)
        machine.response_started(4)
        event = machine.speech_started(
            5, text="I told him to wait", speaker="user", people=1, facing=True
        )
        self.assertFalse(event)
        self.assertFalse(event.barged_in)
        self.assertEqual(machine.state, d.AGENT_SPEAKING)
        self.assertIn("camera does not override the text", event.reason)

    def test_explicit_address_overrides_camera_in_the_machine(self):
        machine = d.DictationMachine(at=0)
        machine.start(1)
        event = machine.speech_started(2, text="Hey Talos", people=4, facing=False)
        self.assertTrue(event)
        self.assertEqual(machine.state, d.USER_SPEAKING)

    def test_other_person_speech_is_ignored_from_idle_and_listening(self):
        idle = d.DictationMachine(at=0)
        event = idle.speech_started(1, text="what time is it", speaker="other", people=1, facing=True)
        self.assertFalse(event)
        self.assertEqual(idle.state, d.IDLE)

        listening = d.DictationMachine(at=0)
        listening.start(1)
        event = listening.speech_started(2, text="I told him to wait", speaker="other")
        self.assertFalse(event)
        self.assertEqual(listening.state, d.LISTENING)

    def test_background_speech_does_not_cancel_thinking(self):
        machine = d.DictationMachine(at=0)
        machine.start(1)
        machine.speech_started(2)
        machine.speech_ended(3)
        event = machine.speech_started(4, text="The meeting is at noon")
        self.assertFalse(event)
        self.assertEqual(machine.state, d.THINKING)

    def test_directed_speech_from_idle_starts_listening_then_speaking(self):
        machine = d.DictationMachine(at=0)
        event = machine.speech_started(1, text="Hey Talos")
        self.assertTrue(event)
        self.assertEqual(machine.state, d.USER_SPEAKING)
        self.assertEqual(
            [transition.target for transition in event.transitions],
            [d.LISTENING, d.USER_SPEAKING],
        )


if __name__ == "__main__":
    unittest.main()
