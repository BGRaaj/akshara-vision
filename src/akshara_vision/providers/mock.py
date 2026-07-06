import re

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

    def restore_text(self, text: str, instruction: str, settings: ModelSettings) -> str:
        del instruction, settings
        if "SOURCE TEXT\n" in text:
            text = text.split("SOURCE TEXT\n", 1)[1]
        cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
        cleaned = re.sub(r"-\n(?=[a-zA-Z])", "", cleaned)
        cleaned = re.sub(r"[ \t]+", " ", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip() + "\n"
