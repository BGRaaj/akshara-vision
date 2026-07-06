import re
from pathlib import Path
from typing import Optional

from akshara_vision.core.models import ModelSettings
from akshara_vision.providers.base import ProviderStatus


class MockProvider:
    name = "mock"

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            name=self.name,
            available=True,
            detail="Offline preview provider available for demos and tests.",
            models=["offline-restoration-preview"],
        )

    def restore_text(
        self,
        text: str,
        instruction: str,
        settings: ModelSettings,
        media_path: Optional[Path] = None,
    ) -> tuple[str, dict]:
        del instruction, settings
        usage = {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30, "truncated": False}
        if media_path:
            # Return valid JSON string representing a restored visual text for tests
            return (
                '{"restored_text": "[Mock restored text from multimodal file '
                f'{media_path.name}]", "uncertain": [], "notes": "mocked vision response"}}'
            ), usage
        for marker in ("SOURCE CHUNK\n", "SOURCE TEXT\n"):
            if marker in text:
                text = text.split(marker, 1)[1]
                break
        cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
        cleaned = re.sub(r"-\n(?=[a-zA-Z])", "", cleaned)
        cleaned = re.sub(r"[ \t]+", " ", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip() + "\n", usage
