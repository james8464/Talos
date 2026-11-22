"""Interruptible Ollama generation for the read-only room conversation path."""

import http.client
import json
import os
import re
import socket
import threading

from local_runtime import OLLAMA_HOST, OLLAMA_PORT


class SpeechCancelled(RuntimeError):
    pass


class SpokenStream:
    def __init__(self, emit):
        self.emit = emit
        self.lock = threading.Lock()
        self.cancelled = False
        self.socket = None
        self.emitted = False
        self.complete = False

    def check(self):
        if self.cancelled:
            raise SpeechCancelled("Voice turn interrupted.")

    def finish_generation(self):
        with self.lock:
            self.check()
            self.complete = True

    def cancel(self):
        with self.lock:
            if self.complete or self.cancelled:
                return False
            self.cancelled = True
            if self.socket:
                try:
                    self.socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            return True

    def tokens(self, messages, model):
        schema = {"type": "array", "items": {"type": "string", "maxLength": 500},
                  "minItems": 1, "maxItems": 4}
        payload = {"model": model, "stream": True, "think": False, "format": schema,
                   "keep_alive": -1 if "TALOS_OLLAMA_PORT" in os.environ else "30m",
                   "messages": [*messages, {"role": "system", "content":
                       "Respond with a JSON array. Put each complete spoken sentence in its own string. "
                       "Use at most four sentences and 500 characters total. /no_think"}],
                   "options": {"temperature": 0.2, "num_ctx": 8192, "num_predict": 192}}
        conn = http.client.HTTPConnection(OLLAMA_HOST, int(os.environ.get("TALOS_OLLAMA_PORT", OLLAMA_PORT)), timeout=60)
        try:
            conn.connect()
            with self.lock:
                self.check()
                self.socket = conn.sock
            conn.request("POST", "/api/chat", body=json.dumps(payload), headers={"Content-Type":"application/json"})
            with conn.getresponse() as response:
                if response.status != 200:
                    raise RuntimeError("Local voice generation failed.")
                for _ in range(2048):
                    self.check()
                    line = response.readline(32_769)
                    if not line or len(line) > 32_768:
                        break
                    chunk = json.loads(line)
                    if "error" in chunk:
                        raise RuntimeError("Local voice generation failed.")
                    content = chunk.get("message", {}).get("content", "")
                    if not isinstance(content, str):
                        break
                    yield content
                    if chunk.get("done"):
                        if chunk.get("done_reason") == "length":
                            break
                        return
            raise RuntimeError("Local voice generation was incomplete.")
        finally:
            with self.lock:
                self.socket = None
            conn.close()

    def reply(self, messages, text, model, allowed):
        self.emit({"started": True})
        for attempt in range(2):
            try:
                return self._answer(messages, model, allowed)
            except ValueError:
                self.check()
                if self.emitted:
                    raise
                if attempt == 0:
                    messages = [*messages, {"role": "system", "content":
                        "Answer the speaker using only the facts they reported. Avoid repeating their words "
                        "or claiming any action. Write a short conversational answer."}]
        return "Tell me a little more about that."

    def _answer(self, messages, model, allowed):
        raw = ""
        output = []
        pending = []
        decoder = json.JSONDecoder()
        pos = None
        # The model emits a JSON array. A closed, independently validated string
        # is the playback boundary; arbitrary token fragments never reach speech.
        tokens = self.tokens(messages, model)
        try:
            for token in tokens:
                self.check()
                raw += token
                if len(raw) > 4096:
                    raise ValueError("Spoken response too long.")
                if pos is None:
                    match = re.match(r"^\s*\[", raw)
                    if not match:
                        continue
                    pos = match.end()
                while True:
                    while pos < len(raw) and raw[pos].isspace():
                        pos += 1
                    if pos == len(raw) or raw[pos] == "]":
                        break
                    if len(output) > 0:
                        if raw[pos] != ",":
                            raise ValueError("Invalid spoken response.")
                        start = pos + 1
                        while start < len(raw) and raw[start].isspace():
                            start += 1
                    else:
                        start = pos
                    try:
                        sentence, end = decoder.raw_decode(raw, start)
                    except json.JSONDecodeError:
                        break
                    if not isinstance(sentence, str) or not sentence.strip():
                        raise ValueError("Invalid spoken sentence.")
                    sentence = sentence.strip()
                    candidate = " ".join([*output, sentence])
                    if (len(output) >= 4 or len(candidate) > 500 or "<" in sentence or ">" in sentence
                            or not allowed(sentence) or not allowed(candidate)):
                        raise ValueError("Rejected spoken sentence.")
                    self.check()
                    output.append(sentence)
                    pending.append(sentence)
                    if re.search(r"""[.!?…]["'’”)]*$""", sentence) and not re.search(
                            r"(?:\b(?:mr|mrs|ms|dr|prof|st|vs)|e\.g|i\.e)\.$", sentence, re.I):
                        self.emit({"sentence": " ".join(pending)})
                        self.emitted = True
                        pending.clear()
                    pos = end
            whole = json.loads(raw)
            if not output or not isinstance(whole, list) or not all(isinstance(s, str) for s in whole) or [s.strip() for s in whole] != output:
                raise ValueError("Incomplete spoken response.")
            self.check()
            if pending:
                self.emit({"sentence": " ".join(pending)})
                self.emitted = True
            return " ".join(output)
        finally:
            tokens.close()
