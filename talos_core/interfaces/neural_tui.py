import os
import sys
import json
import asyncio
from pathlib import Path
from typing import List, Tuple, Optional
from rich.live import Live
from rich.panel import Panel
from rich.layout import Layout
from rich.console import Console
from rich.text import Text
from rich.table import Table
from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.input import create_input

from talos_core.memory.agent_memory import open_memory
from contextlib import closing
from talos_core.cognition import cognitive_engine
import agent

# Configuration
DB_PATH = Path("/Users/james/Developer/Talos/state/agent.sqlite3")
SENSING_COLORS = {"INFO": "cyan", "RECALL": "gold1", "THINKING": "magenta", "ACTION": "green", "SENSING": "blue"}

class NeuralTUI:
    def __init__(self):
        self.console = Console()
        self.session = PromptSession()
        self.synapse_log: List[Tuple[str, str]] = []
        self.focus_node = "Core"
        self.running = True
        self.current_input = ""

    def log(self, category: str, message: str):
        self.synapse_log.append((category, message))
        if len(self.synapse_log) > 20:
            self.synapse_log.pop(0)

    def render_brain(self) -> Panel:
        t = asyncio.get_event_loop().time()

        # Pulse and rotation states
        pulse_val = (t % 1.0)
        pulse = " ( ) " if int(t * 2) % 2 == 0 else " (   ) "

        # Cognitive Load Simulation - we can vary this based on the last log entry
        # If the last log was 'THINKING' or 'RECALL', load is higher.
        current_load = 0.3
        if self.synapse_log and self.synapse_log[-1][0] in ("THINKING", "RECALL"):
            current_load = 0.7 + (0.2 * (t % 1.0))

        load_bar = "█" * int(current_load * 10) + "░" * (10 - int(current_load * 10))

        chars = ["*", "o", ".", "•", "°", "✧", "✦"]
        orbit_idx = int(t * 3) % len(chars)
        char = chars[orbit_idx]

        # Multi-layered Brain Art with "Energy Rings"
        # The rings expand and contract slightly
        ring_offset = " " if int(t) % 2 == 0 else ""

        brain_art = f"""
               .---.
            .:' {char}   ':.
          .:'     {ring_offset}     ':.
          .:       {pulse}       ':.
          .:    {load_bar}    ':
           ':.       -|-       .:'
             ':. {char}   .:'
               ':.         .:'
                  '---'
        """
        return Panel(
            Text(brain_t_art := brain_art, style="bold cyan"),
            title="[bold cyan]TALOS NEURAL CORE[/]",
            border_style="cyan",
            subtitle=f"Cognitive Load: {load_bar} | Focus: {self.focus_node}"
        )

    def render_synapse_log(self) -> Panel:
        table = Table.grid(expand=True)
        for cat, msg in self.synapse_log:
            color = SENSING_COLORS.get(cat, "white")
            pulse_line = "───╼" if cat == "ACTION" else "┈╼"

            # Add a subtle "glow" effect by padding the category
            cat_text = Text(f" {cat} ", style=f"bold {color}")
            table.add_row(
                cat_text,
                Text(f" {pulse_line} {msg}", style="white")
            )

        return Panel(
            table,
            title="[bold magenta]SYNAPSE LOG[/]",
            border_style="magenta"
        )


    def make_layout(self) -> Layout:
        layout = Layout()
        layout.split_column(
            Layout(name="top", size=16),
            Layout(name="bottom")
        )
        layout["top"].split_row(
            Layout(name="brain", ratio=1),
            Layout(name="log", ratio=2)
        )
        return layout

    async def run_interactive(self):
        layout = self.make_layout()

        with Live(layout, refresh_per_second=4, screen=True) as live:
            while self.running:
                # Update TUI components
                layout["brain"].update(self.render_brain())
                layout["log"].update(self.render_synapse_log())

                # In a real async TUI, we'd use a non-blocking input mechanism.
                # For this implementation, we integrate with the agent's loop.
                await asyncio.sleep(0.1)

    async def process_command(self, cmd: str):
        if cmd == "/quit":
            self.running = False
            return

        self.log("SENSING", f"Input: {cmd}")

        # Visual "Thinking" sequence
        for i in range(3):
            self.log("THINKING", f"Analyzing semantic layers... { '•' * (i+1) }")
            await asyncio.sleep(0.3)

        with closing(open_memory(DB_PATH)) as db:
            if cmd.startswith("/kb "):
                query = cmd[4:].strip()
                self.log("RECALL", f"Querying unified knowledge: {query}")
                # Simulate the "Search" animation
                for step in ["Scanning Index", "Fetching Vault", "Splicing Results"]:
                    self.log("INFO", f" {step}...")
                    await asyncio.sleep(0.2)

                res = cognitive_engine.query_knowledge(db, query, state=DB_PATH.parent)
                self.log("INFO", f"Crystallized {len(res['results'])} nodes from memory")
            elif cmd == "/brief":
                self.log("THINKING", "Assembling situational brief...")
                self.log("ACTION", "Reading jarvis-brief.json")
                await asyncio.sleep(0.5)
                self.log("INFO", "Briefing complete.")
            else:
                self.log("THINKING", f"Processing intent: {cmd}")
                self.log("ACTION", "Synchronizing focal point with Atlas")
                await asyncio.sleep(0.4)

async def main():
    tui = NeuralTUI()
    # Run the TUI live display in the background
    tui_task = asyncio.create_task(tui.run_interactive())

    while tui.running:
        try:
            # The prompt is rendered below the Live display
            cmd = await tui.session.prompt_async("Talos ➔ ")
            await tui.process_command(cmd)
        except (EOFError, KeyboardInterrupt):
            break

    tui.running = False
    await tui_task

if __name__ == "__main__":
    asyncio.run(main())
