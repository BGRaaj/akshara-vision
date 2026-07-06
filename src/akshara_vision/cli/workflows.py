import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable, List, Optional

from akshara_vision.cli.ui import ui
from akshara_vision.core.config import ConfigStore
from akshara_vision.core.constants import (
    DOCUMENT_TYPES,
    OCR_MODES,
    OUTPUT_FORMATS,
    PROVIDER_TYPES,
    WORKFLOWS,
)
from akshara_vision.core.env import env_status, load_env_files
from akshara_vision.core.input_discovery import discover_inputs
from akshara_vision.core.models import ModelSettings, RunRequest, WorkflowProfile
from akshara_vision.core.pipeline import run_pipeline
from akshara_vision.instructions import DEFAULT_PRESET, install_editable_instruction, load_instruction
from akshara_vision.registries.exporters import exporter_registry
from akshara_vision.registries.providers import get_provider, provider_registry

load_env_files()


HOME_ACTIONS = [
    "Run workflow",
    "Quick run",
    "Batch process",
    "Guided setup",
    "Choose guide",
    "Customize UI",
    "Profiles",
    "Models",
    "API keys",
    "Instructions",
    "Doctor",
    "Run checks",
    "Docs",
    "Clean local outputs",
    "Exit",
]


def show_home(interactive: bool = False) -> None:
    _render_home()
    if interactive:
        interactive_session()


def _render_home() -> None:
    store = ConfigStore()
    profile = store.load_default_profile()
    prefs = store.load_ui_preferences()
    ui.hero(variant=prefs["hero"], guide=prefs["guide"])
    ui.section("Board")
    ui.board(
        [
            ("Run", "/run", "Full guided workflow"),
            ("Quick", "/quick", "Use saved defaults"),
            ("Batch", "/batch", "Folders and manifests"),
            ("Setup", "/init", "Create your workflow"),
            ("Guide", "/guide", "Choose assistance level"),
            ("Models", "/models", "Local and cloud status"),
            ("API Keys", "/env", "Configure providers"),
            ("Profiles", "/profiles", "Defaults and locks"),
            ("Doctor", "/doctor", "System readiness"),
            ("Checks", "/check", "Compile and test"),
            ("Customize", "/ui", "Prompt and hero design"),
        ],
        compact=prefs["density"] == "compact",
    )
    ui.section("Default Workflow")
    ui.table(
        [
            ["Profile", profile.name],
            ["Workflow", profile.workflow],
            ["Model", f"{profile.model.provider}:{profile.model.model}"],
            ["Outputs", ", ".join(profile.output_formats)],
            ["Locked", "yes" if profile.locked else "no"],
        ]
    )
    ui.section("Session")
    if prefs["guide"] == "minimal":
        ui.write("/menu  /run  /quick  /doctor  /exit")
    elif prefs["guide"] == "full":
        ui.write("Press Enter for the action picker, or type /help for every command.")
        ui.write("Use /guide to choose how much guidance Akshara Vision shows.")
    else:
        ui.write("Press Enter for options, /help for commands, or /exit to leave.")


def interactive_session() -> None:
    store = ConfigStore()
    while True:
        prefs = store.load_ui_preferences()
        raw = ui.text(ui.prompt_label(prefs["prompt"]), "").strip()
        if not raw:
            raw = _menu_command()
        if not raw:
            continue
        if _dispatch_session_command(raw) is False:
            return


def _menu_command() -> str:
    action = ui.choose("Action", HOME_ACTIONS, "Run workflow")
    return {
        "Run workflow": "/run",
        "Quick run": "/quick",
        "Batch process": "/batch",
        "Guided setup": "/init",
        "Choose guide": "/guide",
        "Customize UI": "/ui",
        "Profiles": "/profiles",
        "Models": "/models",
        "API keys": "/env",
        "Instructions": "/instructions",
        "Doctor": "/doctor",
        "Run checks": "/check",
        "Docs": "/docs",
        "Clean local outputs": "/clean",
        "Exit": "/exit",
    }[action]


def _dispatch_session_command(raw: str) -> bool:
    try:
        parts = shlex.split(raw)
    except ValueError as exc:
        ui.write(f"Could not parse command: {exc}")
        return True
    if not parts:
        return True
    command = parts[0].lower()
    args = parts[1:]
    if not command.startswith("/"):
        ui.write("Akshara Vision uses slash commands in the interactive session.")
        ui.write("Try /run, /quick, /batch, /doctor, /models, /profiles, or /help.")
        return True
    if command in {"/exit", "/quit", "/q!"}:
        ui.write("Goodbye.")
        return False
    if command in {"/help", "/?"}:
        _session_help()
    elif command in {"/menu"}:
        selected = _menu_command()
        return _dispatch_session_command(selected)
    elif command in {"/home"}:
        _render_home()
    elif command in {"/status"}:
        _status_panel()
    elif command in {"/guide"}:
        guide_command()
    elif command in {"/ui", "/theme", "/display"}:
        ui_command()
    elif command in {"/init", "/onboard"}:
        onboard()
    elif command in {"/run", "/r"}:
        inputs, flags = _session_args(args)
        run_guided(
            inputs=inputs or None,
            recursive=flags["recursive"],
            dry_run=flags["dry_run"],
        )
    elif command in {"/quick", "/q"}:
        inputs, flags = _session_args(args)
        quick_run(
            inputs=inputs or None,
            recursive=flags["recursive"],
            dry_run=flags["dry_run"],
        )
    elif command in {"/batch", "/b"}:
        inputs, flags = _session_args(args)
        batch_run(inputs=inputs or None, dry_run=flags["dry_run"])
    elif command in {"/profiles", "/profile", "/p"}:
        profile_command(action=args[0] if args else "list")
    elif command in {"/models", "/model", "/m"}:
        model_command(action=args[0] if args else "status")
    elif command in {"/env", "/keys"}:
        env_command()
    elif command in {"/instructions", "/instruct", "/ins"}:
        instruct_command(action=args[0] if args else "view")
    elif command in {"/doctor", "/d"}:
        doctor_command()
    elif command in {"/check", "/checks", "/test", "/t"}:
        check_command()
    elif command in {"/docs"}:
        docs_command()
    elif command in {"/clean"}:
        clean_command(yes=False)
    else:
        ui.write(f"Unknown command: {command}")
        _session_help()
    return True


def _session_args(args: List[str]):
    flags = {"dry_run": False, "recursive": False}
    inputs = []
    for arg in args:
        if arg == "--dry-run":
            flags["dry_run"] = True
        elif arg in {"--recursive", "-R"}:
            flags["recursive"] = True
        else:
            inputs.append(arg)
    return inputs, flags


def _session_help() -> None:
    ui.section("Interactive Commands")
    ui.table(
        [
            ["Command", "Action"],
            ["/menu", "Open the action picker"],
            ["/run [inputs...]", "Guided full workflow"],
            ["/quick [inputs...]", "Run locked/default profile"],
            ["/batch [folder...]", "Recursive batch workflow"],
            ["/init", "Create a default profile"],
            ["/profiles", "List or manage profiles"],
            ["/models", "Check model providers"],
            ["/env", "Show API key and endpoint setup"],
            ["/instructions", "View or edit prompts"],
            ["/guide", "Choose guidance level"],
            ["/ui", "Customize hero, density, prompt"],
            ["/doctor", "Check local setup"],
            ["/check, /test", "Compile and run unit tests"],
            ["/clean", "Remove local generated outputs"],
            ["/exit", "Leave the session"],
        ]
    )


def _status_panel() -> None:
    store = ConfigStore()
    profile = store.load_default_profile()
    prefs = store.load_ui_preferences()
    ui.section("Status")
    ui.table(
        [
            ["Config", str(store.root)],
            ["Default profile", profile.name],
            ["Workflow", profile.workflow],
            ["Provider", profile.model.provider],
            ["Model", profile.model.model],
            ["Output folder", profile.output_dir],
            ["Hero", prefs["hero"]],
            ["Guide", prefs["guide"]],
            ["Density", prefs["density"]],
            ["Prompt", prefs["prompt"]],
        ]
    )


def guide_command() -> None:
    store = ConfigStore()
    current = store.load_ui_preferences()
    ui.heading("Akshara Vision", "Guide")
    guide = ui.choose(
        "How much guidance should the CLI show?",
        [
            "balanced - concise hints and clean defaults",
            "full - explain choices while onboarding",
            "minimal - compact board for repeat users",
        ],
        f"{current['guide']} - {_guide_label(current['guide'])}",
    ).split(" ", 1)[0]
    store.save_ui_preferences({"guide": guide})
    ui.write(f"Guide level set to: {guide}")


def ui_command() -> None:
    store = ConfigStore()
    current = store.load_ui_preferences()
    ui.heading("Akshara Vision", "Customize")
    hero = ui.choose(
        "Opening hero",
        ["inscription", "classic", "minimal"],
        current["hero"],
    )
    density = ui.choose(
        "Layout density",
        ["comfortable", "compact"],
        current["density"],
    )
    prompt = ui.choose(
        "Prompt label",
        ["adaptive", "full", "short"],
        current["prompt"],
    )
    guide = ui.choose(
        "Guide level",
        ["balanced", "full", "minimal"],
        current["guide"],
    )
    store.save_ui_preferences(
        {
            "hero": hero,
            "density": density,
            "prompt": prompt,
            "guide": guide,
        }
    )
    ui.write("UI preferences saved. Use /home to redraw the board.")


def onboard(store: Optional[ConfigStore] = None, profile_name: Optional[str] = None) -> WorkflowProfile:
    store = store or ConfigStore()
    ui.heading("Akshara Vision", "Onboarding")
    ui.write("Press Enter to accept the shown default. Use arrow keys for menus.")
    profile = WorkflowProfile(name=profile_name or "default")
    profile.name = ui.text("Profile name (Enter accepts default)", profile.name)
    profile.workflow = ui.choose("Workflow", WORKFLOWS, profile.workflow)
    profile.document_type = ui.choose("Document type", DOCUMENT_TYPES, profile.document_type)
    profile.source_language = ui.text("Source language", profile.source_language)
    profile.output_language = ui.text("Output language", profile.output_language)
    profile.translation_mode = ui.choose(
        "Translation mode",
        ["off", "same-language-cleanup", "translate", "bilingual", "transliterate", "metadata-only"],
        profile.translation_mode,
    )
    profile.ocr_mode = ui.choose("OCR/decode mode", OCR_MODES, profile.ocr_mode)
    profile.model = choose_model(profile.model)
    profile.output_formats = choose_output_formats(profile.output_formats)
    profile.instruction_preset = DEFAULT_PRESET
    profile.output_dir = ui.text("Output folder", profile.output_dir)
    profile.locked = ui.confirm("Lock this profile as the default quick-run workflow?", True)
    saved = store.save_profile(profile)
    if profile.locked:
        store.set_default_profile(profile.name)
    ui.write(f"Saved profile: {saved}")
    return profile


def choose_model(current: Optional[ModelSettings] = None) -> ModelSettings:
    current = current or ModelSettings()
    statuses = {name: provider.status() for name, provider in provider_registry().items()}
    choices = []
    for provider_name in PROVIDER_TYPES:
        status = statuses.get(provider_name)
        suffix = "available" if status and status.available else "setup needed"
        choices.append(f"{provider_name} ({suffix})")
    default_label = next(
        (choice for choice in choices if choice.startswith(f"{current.provider} ")),
        choices[0],
    )
    selected_label = ui.choose("Model provider", choices, default_label)
    provider_name = selected_label.split(" ", 1)[0]
    status = statuses.get(provider_name)
    model_choices = status.models if status and status.models else _recommended_models(provider_name)
    model = ui.choose("Model", model_choices, current.model if current.model in model_choices else model_choices[0])
    endpoint = current.endpoint or ""
    if provider_name in {"openai-compatible-local", "lm-studio", "jan", "llama-cpp"}:
        endpoint = ui.text("Local endpoint", endpoint or _default_endpoint(provider_name))
    return ModelSettings(provider=provider_name, model=model, endpoint=endpoint or None)


def choose_output_formats(defaults: Optional[List[str]] = None) -> List[str]:
    defaults = defaults or ["txt"]
    choices = list(OUTPUT_FORMATS.keys())
    selected = ui.checkbox("Output formats", choices, defaults)
    return selected or ["txt"]


def run_guided(
    inputs: Optional[Iterable[str]] = None,
    profile_name: Optional[str] = None,
    recursive: bool = False,
    dry_run: bool = False,
) -> Optional[dict]:
    store = ConfigStore()
    profile = store.load_profile(profile_name) if profile_name else store.load_default_profile()
    ui.heading("Akshara Vision", "Guided Run")
    if not profile.locked:
        profile.workflow = ui.choose("Workflow", WORKFLOWS, profile.workflow)
        profile.document_type = ui.choose("Document type", DOCUMENT_TYPES, profile.document_type)
        profile.ocr_mode = ui.choose("OCR/decode mode", OCR_MODES, profile.ocr_mode)
        profile.model = choose_model(profile.model)
        profile.output_formats = choose_output_formats(profile.output_formats)
    return execute_run(profile, inputs=inputs, recursive=recursive, dry_run=dry_run)


def quick_run(inputs: Optional[Iterable[str]] = None, recursive: bool = False, dry_run: bool = False):
    store = ConfigStore()
    profile = store.load_default_profile()
    ui.heading("Akshara Vision", "Quick Run")
    return execute_run(profile, inputs=inputs, recursive=recursive, dry_run=dry_run)


def batch_run(inputs: Optional[Iterable[str]] = None, profile_name: Optional[str] = None, dry_run: bool = False):
    store = ConfigStore()
    profile = store.load_profile(profile_name) if profile_name else store.load_default_profile()
    ui.heading("Akshara Vision", "Batch Run")
    return execute_run(profile, inputs=inputs, recursive=True, dry_run=dry_run)


def execute_run(
    profile: WorkflowProfile,
    inputs: Optional[Iterable[str]] = None,
    recursive: bool = False,
    dry_run: bool = False,
):
    input_values = list(inputs or [])
    if not input_values:
        entered = ui.text("Input files, folders, globs, or manifest paths")
        input_values = [item.strip() for item in entered.split(",") if item.strip()]
    selection = discover_inputs(input_values, recursive=recursive)
    review_run(profile, selection)
    if dry_run:
        ui.write("Dry run complete. No outputs were written.")
        return None
    if not selection.files:
        ui.write("No supported input files found.")
        return None
    if not ui.confirm("Start this run?", True):
        ui.write("Run cancelled.")
        return None
    result = _run_with_progress(RunRequest(profile=profile, inputs=selection, dry_run=False))
    _finished_screen(result)
    return result


def review_run(profile: WorkflowProfile, selection) -> None:
    ui.section("Review")
    rows = [
        ["Workflow", profile.workflow],
        ["Document type", profile.document_type],
        ["OCR mode", profile.ocr_mode],
        ["Source language", profile.source_language],
        ["Output language", profile.output_language],
        ["Provider", profile.model.provider],
        ["Model", profile.model.model],
        ["Outputs", ", ".join(profile.output_formats)],
        ["Destination", profile.output_dir],
        ["Inputs found", str(selection.supported_count)],
    ]
    ui.table(rows)
    for line in selection.display_files():
        ui.write(f"  {_friendly_path(Path(line))}")
    if selection.missing:
        ui.write(f"Missing: {', '.join(selection.missing)}")
    if selection.unsupported:
        ui.write("Unsupported:")
        for item in selection.unsupported:
            ui.write(f"  {item}")


def _run_with_progress(request: RunRequest):
    total = _progress_total(request)
    ui.section("Working")
    with ui.progress("Processing", total=total) as reporter:
        def progress(_event: str, message: str) -> None:
            reporter.update(message)

        return run_pipeline(request, progress=progress)


def _progress_total(request: RunRequest) -> int:
    files = max(request.inputs.supported_count, 1)
    return 6 + (files * 3) + len(request.profile.output_formats)


def _finished_screen(result) -> None:
    exports = result["exports"]
    run_dir = Path(result["run_dir"])
    ui.heading("Akshara Vision", "Finished")
    ui.write("SUCCESS  Run completed.")
    ui.section("Output")
    ui.table(
        [
            ["Run folder", str(run_dir)],
            ["Manifest", str(run_dir / "run_manifest.json")],
            ["Exports", str(len(exports))],
        ]
    )
    if exports:
        ui.section("Files")
        ui.table(
            [["Format", "State", "Path"]]
            + [
                [
                    export.format,
                    "ready" if export.available else "needs setup",
                    str(export.path),
                ]
                for export in exports
            ]
        )
    ui.section("Next")
    ui.table(
        [
            ["Review text", f"{run_dir}/akshara_output.txt"],
            ["Re-export", f"akv export {run_dir}"],
            ["Clean later", "akv clean"],
        ]
    )


def profile_command(
    action: str = "list",
    name: str = "default",
    source: Optional[str] = None,
    lock: bool = False,
) -> None:
    store = ConfigStore()
    if action == "create":
        onboard(store, profile_name=name)
        return
    if action == "list":
        profiles = store.list_profiles()
        ui.heading("Akshara Vision", "Profiles")
        if not profiles:
            ui.write("No profiles yet. Run `akshara init`.")
            return
        default_name = store.default_profile_name()
        for profile in profiles:
            marker = "default" if profile == default_name else ""
            ui.write(f"- {profile} {marker}".rstrip())
        return
    if action == "show":
        profile = store.load_profile(name)
        ui.heading("Akshara Vision", f"Profile: {profile.name}")
        ui.table([[key, str(value)] for key, value in profile.to_dict().items() if key != "model"])
        ui.table([["model.provider", profile.model.provider], ["model.model", profile.model.model]])
        return
    if action in {"use", "lock"}:
        profile = store.load_profile(name)
        profile.locked = action == "lock" or lock or profile.locked
        store.save_profile(profile)
        store.set_default_profile(profile.name)
        ui.write(f"Default profile set to: {profile.name}")
        return
    if action == "import" and source:
        imported = WorkflowProfile.from_dict(_load_profile_dict(Path(source)))
        store.save_profile(imported)
        ui.write(f"Imported profile: {imported.name}")
        return
    if action == "export":
        ui.write(str(store.profile_path(name)))
        return
    if action == "edit":
        _open_editor(store.profile_path(name))
        return
    ui.write(f"Unknown profile action: {action}")


def model_command(action: str = "status") -> None:
    ui.heading("Akshara Vision", "Models")
    if action == "setup":
        settings = choose_model()
        store = ConfigStore()
        profile = store.load_default_profile()
        profile.model = settings
        ui.table([["Provider", settings.provider], ["Model", settings.model], ["Endpoint", settings.endpoint or ""]])
        if ui.confirm("Save this model to the default profile?", True):
            store.save_profile(profile)
            ui.write(f"Saved model to profile: {profile.name}")
        return
    ui.table(provider_status_rows())


def env_command() -> None:
    load_env_files()
    ui.heading("Akshara Vision", "API Keys")
    ui.write("Akshara reads secrets from your shell or a local .env file.")
    ui.write("Values are never printed, saved to profiles, or written to manifests.")
    ui.section("Status")
    ui.table([["Variable", "State"]] + [[key, value] for key, value in env_status().items()])
    ui.section("Local Models")
    ui.table(
        [
            ["Runtime", "Endpoint"],
            ["LM Studio", "http://localhost:1234/v1"],
            ["Jan", "http://localhost:1337/v1"],
            ["llama.cpp", "http://localhost:8080/v1"],
            ["Ollama", "detected through ollama list"],
        ]
    )
    ui.section("Setup")
    ui.write("Copy .env.example to .env, fill values, then run `akv doctor`.")
    ui.write("For local-only use, API keys can stay empty.")


def instruct_command(action: str = "view", preset: str = DEFAULT_PRESET) -> None:
    store = ConfigStore()
    if action == "edit":
        path = install_editable_instruction(preset, store)
        ui.write(f"Editable instruction: {path}")
        _open_editor(path)
        return
    if action == "reset":
        path = store.instructions_dir / f"{preset}.txt"
        if path.exists():
            path.unlink()
        ui.write(f"Reset instruction preset: {preset}")
        return
    ui.heading("Akshara Vision", f"Instruction: {preset}")
    ui.write(load_instruction(preset, store))


def doctor_command() -> None:
    ui.heading("Akshara Vision", "Doctor")
    tools = [
        ("tesseract", "OCR engine"),
        ("pdftotext", "PDF embedded text extraction"),
        ("ocrmypdf", "Searchable PDF OCR"),
        ("pdftoppm", "PDF page rendering"),
        ("magick", "Image preprocessing"),
        ("ollama", "Local model runtime"),
    ]
    rows = [["Check", "State", "Purpose"]]
    for command, purpose in tools:
        rows.append([command, "found" if shutil.which(command) else "missing", purpose])
    for env_name, purpose in [
        ("OPENAI_API_KEY", "OpenAI cloud models"),
        ("ANTHROPIC_API_KEY", "Anthropic cloud models"),
        ("GEMINI_API_KEY", "Gemini cloud models"),
    ]:
        rows.append([env_name, "set" if os.environ.get(env_name) else "not set", purpose])
    ui.table(rows)
    ui.section("Providers")
    ui.table(provider_status_rows())


def check_command() -> int:
    ui.heading("Akshara Vision", "Check")
    env = os.environ.copy()
    env.setdefault("PYTHONPYCACHEPREFIX", str(Path(tempfile.gettempdir()) / "akshara-vision-pycache"))
    if Path("src").exists():
        existing_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = f"src{os.pathsep}{existing_pythonpath}" if existing_pythonpath else "src"
    commands = [
        [sys.executable, "-m", "compileall", "-q", "src", "tests"],
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
    ]
    failed = 0
    with ui.progress("Checks", total=len(commands)) as reporter:
        for command in commands:
            label = " ".join(command[1:])
            reporter.update(label)
            result = subprocess.run(command, check=False, env=env)
            if result.returncode != 0:
                failed = result.returncode
                break
    if failed:
        ui.write("FAILED  Checks did not pass.")
        return failed
    ui.write("SUCCESS  Compile and unit tests passed.")
    return 0


def provider_status_rows() -> List[List[str]]:
    rows = [["Provider", "State", "Models / setup"]]
    for name, provider in provider_registry().items():
        status = provider.status()
        models_or_setup = ", ".join(status.models[:3]) if status.models else _short_detail(status.detail)
        rows.append(
            [
                name,
                "available" if status.available else "setup needed",
                models_or_setup,
            ]
        )
    return rows


def export_command(run_dir: str, formats: Optional[List[str]] = None) -> None:
    path = Path(run_dir).expanduser()
    text_path = path / "akshara_output.txt"
    raw_path = path / "raw_ocr.txt"
    source_text = text_path if text_path.exists() else raw_path
    if not source_text.exists():
        ui.write("Could not find akshara_output.txt or raw_ocr.txt in that run folder.")
        return
    selected = formats or choose_output_formats(["txt"])
    registry = exporter_registry()
    text = source_text.read_text(encoding="utf-8")
    for output_format in selected:
        exporter = registry.get(output_format)
        if exporter:
            result = exporter.export(text, path / "akshara_output", {"title": path.name})
            ui.write(f"{result.format}: {result.path}")


def docs_command() -> None:
    ui.heading("Akshara Vision", "Docs")
    for path in [
        "README.md",
        "docs/onboarding.md",
        "docs/cli-design.md",
        "docs/inputs-outputs.md",
        "docs/models.md",
        "docs/profiles.md",
        "docs/instructions.md",
        "docs/privacy.md",
        "SECURITY.md",
        "CONTRIBUTING.md",
    ]:
        ui.write(path)


def _guide_label(level: str) -> str:
    return {
        "minimal": "compact board for repeat users",
        "full": "explain choices while onboarding",
        "balanced": "concise hints and clean defaults",
    }.get(level, "concise hints and clean defaults")


def clean_command(yes: bool = False) -> None:
    ui.heading("Akshara Vision", "Clean")
    targets = [
        Path("akshara-output"),
        Path(".akshara-vision"),
        Path("build"),
        Path("dist"),
        Path(".pytest_cache"),
        Path(".ruff_cache"),
        Path("src/akshara_vision.egg-info"),
    ]
    targets.extend(Path(".").glob("**/__pycache__"))
    existing = [path for path in targets if path.exists()]
    if not existing:
        ui.write("No generated local artifacts found.")
        return
    ui.write("These generated local artifacts will be removed:")
    for path in existing:
        ui.write(f"  {path}")
    if not yes and not ui.confirm("Remove these files?", False):
        ui.write("Clean cancelled.")
        return
    for path in existing:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    ui.write("Clean complete.")


def _recommended_models(provider_name: str) -> List[str]:
    if provider_name == "ollama":
        return ["gemma3:12b", "qwen2.5:14b", "llama3.1:8b", "mistral-small"]
    if provider_name in {"openai-compatible-local", "lm-studio", "jan", "llama-cpp"}:
        return ["gemma-3-12b-it", "qwen2.5-14b-instruct", "llama-3.1-8b-instruct"]
    if provider_name == "gemini":
        return ["gemini-2.5-pro", "gemini-2.5-flash"]
    if provider_name == "anthropic":
        return ["claude-3-5-sonnet-latest", "claude-3-5-haiku-latest"]
    if provider_name == "openai":
        return ["gpt-4.1", "gpt-4.1-mini"]
    return ["offline-restoration-preview"]


def _default_endpoint(provider_name: str) -> str:
    return {
        "openai-compatible-local": "http://localhost:1234/v1",
        "lm-studio": "http://localhost:1234/v1",
        "jan": "http://localhost:1337/v1",
        "llama-cpp": "http://localhost:8080/v1",
    }.get(provider_name, "http://localhost:1234/v1")


def _short_detail(detail: str) -> str:
    replacements = {
        "ollama command not found.": "install ollama",
        "OPENAI_API_KEY is not set.": "set OPENAI_API_KEY",
        "ANTHROPIC_API_KEY is not set.": "set ANTHROPIC_API_KEY",
        "GEMINI_API_KEY is not set.": "set GEMINI_API_KEY",
    }
    if detail in replacements:
        return replacements[detail]
    if "localhost:1234" in detail:
        return "start LM Studio or set endpoint"
    if "localhost:1337" in detail:
        return "start Jan or set endpoint"
    if "localhost:8080" in detail:
        return "start llama.cpp server"
    return detail[:42]


def _open_editor(path: Path) -> None:
    editor = os.environ.get("EDITOR")
    if not editor:
        ui.write(f"Set EDITOR to edit in place. File: {path}")
        return
    subprocess.run([editor, str(path)], check=False)


def _load_profile_dict(path: Path):
    from akshara_vision.core.toml_compat import load_toml

    return load_toml(path)


def _friendly_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return path.name
