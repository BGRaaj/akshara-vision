from pathlib import Path
from typing import Dict, List, Optional

from akshara_vision.core.constants import default_config_dir
from akshara_vision.core.models import WorkflowProfile
from akshara_vision.core.toml_compat import dump_toml, load_toml


class ConfigStore:
    """Manage portable profile files and user-editable instructions."""

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = root or default_config_dir()
        self.profiles_dir = self.root / "profiles"
        self.instructions_dir = self.root / "instructions"
        self.settings_path = self.root / "settings.toml"

    def ensure(self) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self.profiles_dir.mkdir(parents=True, exist_ok=True)
            self.instructions_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            fallback = Path.cwd() / ".akshara-vision"
            self.root = fallback
            self.profiles_dir = self.root / "profiles"
            self.instructions_dir = self.root / "instructions"
            self.settings_path = self.root / "settings.toml"
            self.root.mkdir(parents=True, exist_ok=True)
            self.profiles_dir.mkdir(parents=True, exist_ok=True)
            self.instructions_dir.mkdir(parents=True, exist_ok=True)

    def list_profiles(self) -> List[str]:
        self.ensure()
        return sorted(path.stem for path in self.profiles_dir.glob("*.toml"))

    def profile_path(self, name: str) -> Path:
        safe = name.strip().replace("/", "-") or "default"
        return self.profiles_dir / f"{safe}.toml"

    def save_profile(self, profile: WorkflowProfile) -> Path:
        self.ensure()
        path = self.profile_path(profile.name)
        path.write_text(dump_toml(profile.to_dict()), encoding="utf-8")
        if profile.locked:
            self.set_default_profile(profile.name)
        return path

    def load_profile(self, name: str = "default") -> WorkflowProfile:
        self.ensure()
        path = self.profile_path(name)
        if not path.exists() and name != "default":
            return self.load_profile("default")
        if not path.exists():
            profile = WorkflowProfile()
            self.save_profile(profile)
            return profile
        return WorkflowProfile.from_dict(load_toml(path))

    def default_profile_name(self) -> str:
        settings = self.load_settings()
        return str(settings.get("default_profile") or "default")

    def set_default_profile(self, name: str) -> None:
        self.ensure()
        settings = self.load_settings()
        settings["default_profile"] = name
        self.save_settings(settings)

    def load_default_profile(self) -> WorkflowProfile:
        return self.load_profile(self.default_profile_name())

    def load_settings(self) -> Dict[str, object]:
        self.ensure()
        return load_toml(self.settings_path)

    def save_settings(self, settings: Dict[str, object]) -> None:
        self.ensure()
        self.settings_path.write_text(dump_toml(settings), encoding="utf-8")

    def load_ui_preferences(self) -> Dict[str, str]:
        settings = self.load_settings()
        ui_settings = settings.get("ui") if isinstance(settings.get("ui"), dict) else {}
        return {
            "hero": str(ui_settings.get("hero") or "inscription"),
            "guide": str(ui_settings.get("guide") or "balanced"),
            "density": str(ui_settings.get("density") or "comfortable"),
            "prompt": str(ui_settings.get("prompt") or "adaptive"),
        }

    def save_ui_preferences(self, preferences: Dict[str, str]) -> None:
        settings = self.load_settings()
        current = settings.get("ui") if isinstance(settings.get("ui"), dict) else {}
        current.update(preferences)
        settings["ui"] = current
        self.save_settings(settings)
