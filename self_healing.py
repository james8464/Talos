import os
import sys
import json
import subprocess
import shutil
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional
from datetime import datetime
from contextlib import closing
import sqlite3
import torch
import numpy as np

try:
    from clip import load as load_clip, available_models
    HAS_CLIP = True
except ImportError:
    HAS_CLIP = False
    print("Warning: openai-clip not installed. Self-healing clustering disabled.")

from agent_memory import open_memory
import cognitive_engine
import section_patches
import self_diagnosis

# Configuration
LOG_FILE = Path("state/self_healing.log")
PATCH_DIR = Path("state/self-healing-patches")
PATCH_DIR.mkdir(parents=True, exist_ok=True)

class SelfHealer:
    """
    The Self-Healing loop: monitors logs for errors, identifies the failing
    component, generates a candidate fix, and verifies it in a sandbox.
    """
    def __init__(self, state_path: Path):
        self.state = state_path
        if HAS_CLIP:
            self.model, self.preprocess = load_clip("ViT-B/32", device="cpu")
        self.processed_incidents = set()

    def monitor_logs(self) -> List[Dict[str, Any]]:
        """Scan logs for unhandled exceptions or critical failures."""
        incidents = []
        # In a real system, we'd tail the logs. Here we scan the most recent entries.
        # For this implementation, we'll check the Talos activity log.
        activity_log = self.state / "Talos activity.md"
        if not activity_log.exists():
            return []

        content = activity_log.read_text(encoding="utf-8")
        # Look for "failed" or "Error" markers in the logs
        for line in content.splitlines():
            if "failed" in line.lower() or "error" in line.lower():
                if line not in self.processed_incidents:
                    incidents.append({"log": line, "timestamp": datetime.now().isoformat()})
        return incidents

    def analyze_incident(self, incident: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Diagnose the root cause and identify the target file."""
        log_text = incident["log"]
        # Simple heuristic: look for file paths or function names in the log
        # In a full implementation, we'd use a LLM to analyze the traceback.
        import re
        paths = re.findall(r"(/Users/james/Developer/Talos/\S+)", log_text)
        if paths:
            target_file = paths[0]
            return {"target": target_file, "issue": log_text}
        return None

    def generate_fix(self, analysis: Dict[str, Any]) -> Optional[str]:
        """
        Generates a candidate patch.
        In this implementation, we simulate the 'Thinking' phase where a
        subagent would write the code.
        """
        # This would call a specialized 'coder' agent.
        # For the prototype, we simulate the creation of a .diff file.
        target = analysis["target"]
        # Simulate a simple fix (e.g., replacing a known bug pattern)
        return f"--- a/{target}\n+++ b/{target}\n@@ -1,1 +1,1 @@\n-buggy_code()\n+fixed_code()"

    def verify_and_apply(self, patch: str, target: str) -> bool:
        """Verify patch in sandbox and apply if successful."""
        # Use the existing section_patches mechanism
        res = section_patches.verify_patch_in_sandbox(Path("/Users/james/Developer/Talos"), patch)
        if res["ok"]:
            apply_res = section_patches.apply_verified_patch(
                Path("/Users/james/Developer/Talos"), self.state, patch
            )
            return apply_res["applied"]
        return False

    def run_cycle(self):
        """The main self-healing loop."""
        incidents = self.monitor_logs()
        for inc in incidents:
            analysis = self.analyze_incident(inc)
            if analysis:
                patch = self.generate_fix(analysis)
                if patch:
                    if self.verify_and_apply(patch, analysis["target"]):
                        self.processed_incidents.add(inc["log"])
                        with open(LOG_FILE, "a") as f:
                            f.write(f"[{datetime.now().isoformat()}] Healed: {analysis['target']}\n")

def heal_talos(state: Path):
    healer = SelfHealer(state)
    healer.run_cycle()
