"""Respan LLM service for Pipecat AI framework - designed for fine-grained task decomposition."""

import http.client
import json
import logging
import os
import sys
from dataclasses import dataclass

from pipecat.services.openai.base_llm import BaseOpenAILLMService
from pipecat.services.openai.llm import OpenAILLMService

logger = logging.getLogger(__name__)


@dataclass
class RespanLLMSettings(BaseOpenAILLMService.Settings):
    """Settings for RespanLLMService."""

    # Respan API configuration
    respan_key: str = ""
    respan_api_key_env: str = "RESPAN_API_KEY"
    max_tokens: int = 4096
    temperature: float = 0.7


class RespanLLMService(OpenAILLMService):
    """Respan LLM service that provides external API language model capabilities.

    Designed for fine-grained task decomposition by using free API models
    that excel at breaking down complex problems into smallest possible components.
    """

    Settings = RespanLLMSettings
    _settings: Settings

    # Use models known for strong reasoning and task decomposition
    # GLM-4.7-Flash and Gemma-4 models are excellent for this
    _respan_models = [
        "zai/glm-4.7-flash",
        "google_gemini_ai/gemma-4-26b-a4b-it",
        "zai/glm-4.5-flash",
        "google_gemini_ai/gemma-4-31b-it",
    ]

    def __init__(
        self,
        *,
        model: str | None = None,
        settings: Settings | None = None,
        **kwargs,
    ):
        """Initialize RespanLLMService.

        Args:
            model: Override the default respan model (defaults to best decomposition model)
            settings: Settings object containing configuration
            **kwargs: Additional arguments passed to parent class
        """
        default_settings = self.Settings(
            max_tokens=4096,
            temperature=0.7,
            respan_key="",
        )

        if settings is not None:
            default_settings = default_settings.model_copy(update=settings.model_dump(exclude_unset=True))

        self._settings = default_settings

        # Set the best model for task decomposition by default
        if model is None:
            self._settings.model = self._respan_models[0]
        else:
            self._settings.model = model

        # Get API key from environment or file
        self._api_key = (
            os.environ.get(self._settings.respan_api_key_env) or
            self._settings.respan_key
        )

        if not self._api_key:
            print("Respan API key not found. Set RESSPAN_API_KEY env var or respan_key in settings.", file=sys.stderr)

        super().__init__(
            model=self._settings.model,
            settings=self._settings,
            **kwargs,
        )

    async def process_frame(self, frame, direction):
        """Process frames using Respan API with task decomposition focus."""
        if direction.value > 0:
            # Frame is coming downstream, process it
            await super().process_frame(frame, direction)
        elif isinstance(frame, self.FrameType):
            # Frame is coming upstream, send to LLM
            await self._send_to_respan(frame, direction)

    async def _send_to_respan(self, frame, direction):
        """Send messages to Respan API with task decomposition focus."""
        if not self._api_key:
            logger.error("Cannot send to Respan: no API key configured")
            return

        messages = frame.context.get_messages() if frame.context else []

        # Add system instruction for task decomposition
        system_content = "You are a highly structured AI assistant that excels at breaking down complex tasks into the smallest possible components. Always decompose tasks before executing them, and use atomic subtasks when possible."

        messages = [
            {"role": "system", "content": system_content},
            *messages
        ]

        connection = None
        try:
            connection = http.client.HTTPSConnection(
                "api.respan.ai",
                timeout=60
            )

            payload = {
                "model": self._settings.model,
                "max_tokens": self._settings.max_tokens,
                "temperature": self._settings.temperature,
                "stream": False,
                "messages": messages,
            }

            connection.request(
                "POST",
                "/api/chat/completions",
                body=json.dumps(payload).encode(),
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json"
                }
            )

            response = connection.getresponse()
            data = response.read(65537)

            if response.status != 200:
                raise RuntimeError(f"Respan API error: HTTP {response.status}")

            result = json.loads(data)
            content = result["choices"][0]["message"]["content"]

            # Push response frames
            await self._add_text_frame(content)

        except Exception as e:
            logger.error(f"Respan API request failed: {e}")
            # Fallback to base class behavior
            await super().process_frame(frame, direction)
        finally:
            if connection is not None:
                connection.close()
