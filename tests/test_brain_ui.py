"""Tests for the dependency-free terminal brain UI."""

import ast
import io
import sys
import unittest
from pathlib import Path

from talos_core.interfaces.brain_ui import BRAIN_FRAMES, DEFAULT_WIDTH, BrainUI, UIState, main

MODULE_PATH = Path(__file__).resolve().parents[1] / "talos_core" / "interfaces" / "brain_ui.py"


class _TTY(io.StringIO):
    def isatty(self) -> bool:
        return True


class _Clock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class BrainFrameTests(unittest.TestCase):
    def test_module_exposes_three_to_five_distinct_frames(self):
        self.assertGreaterEqual(len(BRAIN_FRAMES), 3)
        self.assertLessEqual(len(BRAIN_FRAMES), 5)
        self.assertEqual(len(set(BRAIN_FRAMES)), len(BRAIN_FRAMES))
        heights = {len(frame) for frame in BRAIN_FRAMES}
        self.assertEqual(len(heights), 1)

    def test_clock_steps_through_the_animation(self):
        out, clock = io.StringIO(), _Clock()
        ui = BrainUI(out, clock, fps=4.0)
        seen = set()
        for tick in range(len(BRAIN_FRAMES)):
            clock.now = tick / 4.0
            seen.add(ui.frame())
        self.assertEqual(len(seen), len(BRAIN_FRAMES))

    def test_status_and_thought_log_are_rendered(self):
        ui = BrainUI(io.StringIO(), clock=lambda: 0.0, width=52)
        text = ui.frame(
            UIState(
                model="qwen3:8b",
                status="listening",
                thoughts=("speech unavailable", "first thought", "second thought"),
            )
        )
        self.assertIn("model: qwen3:8b", text)
        self.assertIn("state: listening", text)
        self.assertIn("speech unavailable", text)
        self.assertIn("second thought", text)
        self.assertTrue(text.splitlines()[-1].startswith(">"))

    def test_log_area_scrolls_to_the_newest_lines(self):
        ui = BrainUI(io.StringIO(), clock=lambda: 0.0, width=48, log_lines=2)
        text = ui.frame(UIState(thoughts=("old", "middle", "newest")))
        self.assertNotIn("old", text)
        self.assertIn("middle", text)
        self.assertIn("newest", text)

    def test_every_line_is_bounded_by_width(self):
        for width in (24, DEFAULT_WIDTH, 120):
            with self.subTest(width=width):
                ui = BrainUI(io.StringIO(), clock=lambda: 0.25, width=width)
                text = ui.frame(
                    UIState(
                        model="a-very-long-model-name-that-needs-truncation",
                        status="thinking very hard",
                        thoughts=("a thought that is much longer than the available width",),
                    )
                )
                for line in text.splitlines():
                    self.assertLessEqual(len(line), width)
                    self.assertEqual(len(line), width)

    def test_invalid_width_and_fps_are_rejected(self):
        with self.assertRaises(ValueError):
            BrainUI(io.StringIO(), clock=lambda: 0.0, width=0)
        with self.assertRaises(ValueError):
            BrainUI(io.StringIO(), clock=lambda: 0.0, fps=0)

    def test_render_writes_one_newline_terminated_frame(self):
        out = io.StringIO()
        ui = BrainUI(out, clock=lambda: 0.0, width=40)
        frame = ui.render()
        self.assertEqual(out.getvalue(), frame + "\n")
        self.assertEqual(frame, ui.frame())


class NonTTYTests(unittest.TestCase):
    def test_non_tty_renders_one_static_frame_without_sleeping(self):
        out, sleeps = io.StringIO(), []
        self.assertFalse(out.isatty())
        self.assertEqual(
            main(stream=out, clock=lambda: 0.0, sleep=sleeps.append, input_stream=io.StringIO(), width=44),
            0,
        )
        self.assertEqual(sleeps, [])
        self.assertEqual(out.getvalue().count("model: "), 1)
        self.assertEqual(out.getvalue().count("state: text mode"), 1)
        self.assertIn("speech unavailable", out.getvalue())

    def test_tty_run_animates_before_prompting(self):
        out, sleeps = _TTY(), []
        ui = BrainUI(
            out,
            clock=lambda: 0.0,
            width=40,
            input_stream=io.StringIO(""),
            sleep=sleeps.append,
        )
        self.assertEqual(ui.run_interactive(), 0)
        self.assertEqual(len(sleeps), len(BRAIN_FRAMES))
        self.assertGreater(sleeps[0], 0)


class InputTests(unittest.TestCase):
    def test_prompt_reads_one_line_and_records_it(self):
        out, stdin = io.StringIO(), io.StringIO("hello talos\n")
        ui = BrainUI(out, clock=lambda: 0.0, input_stream=stdin)
        self.assertEqual(ui.prompt(), "hello talos")
        self.assertEqual(ui.thoughts, ["you: hello talos"])
        self.assertIn(">", out.getvalue().splitlines()[-1])
        self.assertEqual(ui.prompt(), None)
        self.assertFalse(ui._running)

    def test_read_line_strips_line_endings(self):
        ui = BrainUI(io.StringIO(), clock=lambda: 0.0, input_stream=io.StringIO("windows\r\nplain\n"))
        self.assertEqual(ui.read_line(), "windows")
        self.assertEqual(ui.read_line(), "plain")
        self.assertIsNone(ui.read_line())

    def test_commands_update_model_and_stop_the_session(self):
        ui = BrainUI(io.StringIO(), clock=lambda: 0.0)
        self.assertTrue(ui.handle_input("/model qwen3:8b"))
        self.assertEqual(ui.model, "qwen3:8b")
        self.assertTrue(ui.handle_input("/status"))
        self.assertTrue(ui.handle_input("   "))
        self.assertIn("you: (empty)", ui.thoughts)
        self.assertFalse(ui.handle_input("/quit"))
        self.assertEqual(ui.thoughts[-1], "talos: session closed")

    def test_responder_adds_a_reply_line(self):
        ui = BrainUI(io.StringIO(), clock=lambda: 0.0, responder=lambda text: f"heard {text}")
        ui.handle_input("are you there")
        self.assertEqual(ui.thoughts, ["you: are you there", "talos: heard are you there"])

    def test_scripted_session_stops_at_quit(self):
        out = io.StringIO()
        exit_code = main(
            stream=out,
            clock=lambda: 0.0,
            script=["hello", "/quit", "never processed"],
            input_stream=io.StringIO(),
            width=44,
        )
        self.assertEqual(exit_code, 0)
        self.assertIn("you: hello", out.getvalue())
        self.assertIn("talos: session closed", out.getvalue())
        self.assertNotIn("never processed", out.getvalue())


class DependencyTests(unittest.TestCase):
    def test_module_imports_only_the_standard_library(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                imported.add(node.module.split(".")[0])
        allowed = set(sys.stdlib_module_names) | {"__future__"}
        self.assertEqual(imported - allowed, set())
        self.assertNotIn("rich", imported)
        self.assertNotIn("prompt_toolkit", imported)
        self.assertNotIn("prompt_toolkit", source.lower())

    def test_brain_ui_does_not_import_talos_modules(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertFalse(node.module.startswith("talos_core"))


if __name__ == "__main__":
    unittest.main()
