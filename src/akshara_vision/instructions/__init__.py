from importlib import resources
from pathlib import Path

from akshara_vision.core.config import ConfigStore

DEFAULT_PRESET = "book_restoration_default"


def load_instruction(name: str = DEFAULT_PRESET, store: ConfigStore = None) -> str:
    store = store or ConfigStore()
    user_path = store.instructions_dir / f"{name}.txt"
    if user_path.exists():
        return user_path.read_text(encoding="utf-8")
    return resources.files(__package__).joinpath(f"{name}.txt").read_text(encoding="utf-8")


def install_editable_instruction(name: str = DEFAULT_PRESET, store: ConfigStore = None) -> Path:
    store = store or ConfigStore()
    store.ensure()
    target = store.instructions_dir / f"{name}.txt"
    if not target.exists():
        target.write_text(load_instruction(name, store), encoding="utf-8")
    return target

