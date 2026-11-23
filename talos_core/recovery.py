"""Polling and reconnect policies adapted from OpenClaw and Hermes. See UPSTREAM.md."""

import random


class PollBackoff:
    """Per-command quiet-poll history. Times and returned delays are in seconds."""

    delays = (5, 10, 30, 60)

    def __init__(self):
        self.history = {}

    def record(self, command, changed, now):
        previous, _ = self.history.get(command, (-1, now))
        index = 0 if changed else min(previous + 1, len(self.delays) - 1)
        self.history[command] = (index, now)
        return self.delays[index]

    def reset(self, command):
        self.history.pop(command, None)

    def prune(self, now):
        for command, (_, seen) in list(self.history.items()):
            if now - seen > 3600:
                self.reset(command)


def stable_connection(opened, now):
    return opened is not None and now - opened >= 5


def reconnect_delay(attempt, *, base=.3, cap=15, jitter=True):
    ceiling = min(cap, base * 2 ** min(32, max(0, int(attempt))))
    return random.random() * ceiling if jitter else ceiling
