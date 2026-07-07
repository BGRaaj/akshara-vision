import copy
import json
import os
import re
import shlex
import signal
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
    EXECUTION_MODES,
    OUTPUT_FORMATS,
    WORKFLOWS,
)
from akshara_vision.core.env import env_status, load_env_files
from akshara_vision.core.input_discovery import discover_inputs
from akshara_vision.core.models import (
    ModelSettings,
    RunRequest,
    WorkflowProfile,
    effective_translation_mode,
)
from akshara_vision.core.pipeline import combine_stage_outputs, find_executable, run_pipeline
from akshara_vision.instructions import (
    DEFAULT_PRESET,
    install_editable_instruction,
    load_instruction,
)
from akshara_vision.registries.exporters import exporter_registry
from akshara_vision.registries.providers import provider_registry

load_env_files()


HOME_ACTIONS = [
    "Core - Run workflow",
    "Core - Quick run",
    "Core - Batch process",
    "Core - Guided setup",
    "Core - Profiles",
    "Core - Models",
    "Core - Instructions",
    "Extended - Resume run",
    "Extended - Combine outputs",
    "Extended - Export existing output",
    "Extended - Docs",
    "Interface - Choose guide",
    "Interface - Choose mode",
    "Interface - Customize UI",
    "Setup - API keys",
    "Setup - Doctor",
    "Setup - Install dependencies",
    "Maintenance - Status",
    "Maintenance - Run checks",
    "Maintenance - Clean local outputs",
    "Exit",
]


def show_home(interactive: bool = False) -> None:
    _render_home()
    if interactive:
        interactive_session()


def apply_saved_ui_theme(clear: bool = False) -> None:
    prefs = ConfigStore().load_ui_preferences()
    ui.set_theme(prefs["theme"])
    ui.apply_terminal_theme(clear=clear)


def _render_home() -> None:
    store = ConfigStore()
    profile = store.load_default_profile()
    prefs = store.load_ui_preferences()
    ui.set_theme(prefs["theme"])
    ui.apply_terminal_theme()
    ui.hero(guide=prefs["guide"])
    ui.section("Core Workflows")
    ui.board(
        [
            ("Run", "/run", "Full guided workflow"),
            ("Quick", "/quick", "Use saved defaults"),
            ("Batch", "/batch", "Folders and manifests"),
            ("Setup", "/init", "Create your workflow"),
            ("Profiles", "/profiles", "Defaults and locks"),
            ("Models", "/models", "Local and cloud choices"),
            ("Instructions", "/instructions", "Restoration prompt presets"),
        ],
        compact=prefs["density"] == "compact",
    )
    ui.section("Extended Tools")
    ui.board(
        [
            ("Resume", "/resume", "Continue interrupted work"),
            ("Combine", "/combine", "Assemble staged outputs"),
            ("Export", "/export", "Convert existing outputs"),
            ("Docs", "/docs", "Open project guides"),
            ("Guide", "/guide", "Adjust CLI guidance"),
            ("Modes", "/mode", "Speed and quality tradeoffs"),
            ("Customize", "/ui", "Light/dark terminal theme"),
        ],
        compact=prefs["density"] == "compact",
    )
    ui.section("Setup And Maintenance")
    ui.board(
        [
            ("API Keys", "/env", "Provider key status"),
            ("Doctor", "/doctor", "System readiness"),
            ("Install", "/install", "PDF/image dependencies"),
            ("Status", "/status", "Current configuration"),
            ("Checks", "/check", "Compile and test"),
            ("Clean", "/clean", "Remove generated artifacts"),
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
    ui.status("info", "Press Ctrl+C at any time to cancel")


def interactive_session() -> None:
    store = ConfigStore()
    while True:
        try:
            prefs = store.load_ui_preferences()
            ui.set_theme(prefs["theme"])
            ui.apply_terminal_theme()
            raw = ui.text(ui.prompt_label(prefs["prompt"]), "").strip()
            if not raw:
                raw = _menu_command()
            if not raw:
                continue
            if _dispatch_session_command(raw) is False:
                return
        except KeyboardInterrupt:
            ui.write("\nNamaskara.")
            return


def _menu_command() -> str:
    action = ui.choose("Action", HOME_ACTIONS, "Core - Run workflow")
    return {
        "Core - Run workflow": "/run",
        "Core - Quick run": "/quick",
        "Core - Batch process": "/batch",
        "Core - Guided setup": "/init",
        "Core - Profiles": "/profiles",
        "Core - Models": "/models",
        "Core - Instructions": "/instructions",
        "Extended - Resume run": "/resume",
        "Extended - Combine outputs": "/combine",
        "Extended - Export existing output": "/export",
        "Extended - Docs": "/docs",
        "Interface - Choose guide": "/guide",
        "Interface - Choose mode": "/mode",
        "Interface - Customize UI": "/ui",
        "Setup - API keys": "/env",
        "Setup - Doctor": "/doctor",
        "Setup - Install dependencies": "/install",
        "Maintenance - Status": "/status",
        "Maintenance - Run checks": "/check",
        "Maintenance - Clean local outputs": "/clean",
        "Exit": "/exit",
    }[action]


def _translation_label(profile: WorkflowProfile) -> str:
    mode = profile.normalized_translation_mode()
    resolved = effective_translation_mode(
        profile.source_language,
        profile.output_language,
        profile.translation_mode,
    )
    if mode == "auto":
        return f"auto -> {resolved}"
    if resolved != mode:
        return f"{mode} -> {resolved}"
    return mode


def _dispatch_session_command(raw: str) -> bool:
    try:
        parts = shlex.split(raw, posix=sys.platform != "win32")
    except ValueError as exc:
        ui.status("error", f"Could not parse command: {exc}")
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
        ui.write("Namaskara.")
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
    elif command in {"/mode", "/speed"}:
        mode_command()
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
        profile_command(action=args[0] if args else "menu")
    elif command in {"/models", "/model", "/m"}:
        model_command(action=args[0] if args else "status")
    elif command in {"/env", "/keys"}:
        env_command()
    elif command in {"/instructions", "/instruct", "/ins"}:
        instruct_command(action=args[0] if args else "view")
    elif command in {"/doctor", "/d"}:
        doctor_command()
    elif command in {"/combine", "/assemble", "/merge"}:
        combine_command(args[0] if args else None)
    elif command in {"/resume", "/recover"}:
        resume_command(args[0] if args else None)
    elif command in {"/export", "/x"}:
        export_command(args[0] if args else None)
    elif command in {"/install", "/setup"}:
        install_command()
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
            ["/mode", "Choose speed versus quality mode"],
            ["/ui", "Customize theme and guidance"],
            ["/doctor", "Check local setup"],
            ["/combine [run-folder]", "Rebuild staged outputs into one document"],
            ["/resume [run-folder]", "Recover completed checkpoints from an interrupted run"],
            ["/export [path]", "Convert a run folder or existing output to another format"],
            ["/install", "Install PDF/image system dependencies"],
            ["/check, /test", "Compile and run unit tests"],
            ["/clean", "Remove local generated outputs"],
            ["/exit", "Leave the session"],
        ]
    )


def _status_panel() -> None:
    store = ConfigStore()
    profile = store.load_default_profile()
    prefs = store.load_ui_preferences()
    ui.set_theme(prefs["theme"])
    ui.section("Status")
    ui.table(
        [
            ["Config", str(store.root)],
            ["Default profile", profile.name],
            ["Workflow", profile.workflow],
            ["Provider", profile.model.provider],
            ["Model", profile.model.model],
            ["Mode", profile.model.execution_mode],
            ["Output folder", profile.output_dir],
            ["Theme", prefs["theme"]],
            ["Guide", prefs["guide"]],
        ]
    )


def guide_command() -> None:
    store = ConfigStore()
    current = store.load_ui_preferences()
    ui.set_theme(current["theme"])
    ui.heading("Akshara Vision", "Guide")
    guide = ui.choose(
        "How much guidance should the CLI show?",
        [
            "balanced - concise hints and clean defaults",
            "full - explain choices while onboarding",
            "minimal - compact board for repeat users",
            "Back",
        ],
        f"{current['guide']} - {_guide_label(current['guide'])}",
    )
    if guide == "Back":
        return
    guide = guide.split(" ", 1)[0]
    store.save_ui_preferences({"guide": guide})
    ui.write(f"Guide level set to: {guide}")


def mode_command() -> None:
    store = ConfigStore()
    profile = store.load_default_profile()
    ui.heading("Akshara Vision", "Execution Mode")
    ui.write(
        "Fast favors throughput. Balanced keeps the current defaults. Quality spends more time for harder pages."
    )
    profile.model.execution_mode = ui.choose(
        "Execution mode", EXECUTION_MODES + ["Back"], profile.model.execution_mode
    )
    if profile.model.execution_mode == "Back":
        return
    store.save_profile(profile)
    ui.write(f"Execution mode set to: {profile.model.execution_mode}")


def ui_command() -> None:
    store = ConfigStore()
    current = store.load_ui_preferences()
    ui.set_theme(current["theme"])
    ui.apply_terminal_theme()
    ui.heading("Akshara Vision", "Customize")
    theme = ui.choose("Theme", ["dark", "light", "Back"], current["theme"])
    if theme == "Back":
        return
    ui.set_theme(theme)
    ui.apply_terminal_theme(clear=True)
    store.save_ui_preferences(
        {
            "theme": theme,
        }
    )
    ui.status("success", "UI preferences saved.")
    _render_home()


def onboard(
    store: Optional[ConfigStore] = None, profile_name: Optional[str] = None
) -> Optional[WorkflowProfile]:
    store = store or ConfigStore()
    ui.heading("Akshara Vision", "Onboarding")
    ui.write("Press Enter to accept the shown default. Use arrow keys for menus.")
    profile = WorkflowProfile(name=profile_name or "default")
    profile.name = ui.text("Profile name (Enter accepts default)", profile.name)
    profile.workflow = ui.choose("Workflow", WORKFLOWS + ["Back"], profile.workflow)
    if profile.workflow == "Back":
        return profile
    profile.document_type = ui.choose("Document type", DOCUMENT_TYPES + ["Back"], profile.document_type)
    if profile.document_type == "Back":
        return profile
    profile.source_language = choose_language_value("Source language", profile.source_language, "auto")
    profile.output_language = choose_language_value("Output language", profile.output_language, "same")
    profile.language_policy = choose_language_policy(profile.language_policy)
    ui.write("Translation will switch on automatically when the output language differs.")
    profile.translation_mode = ui.choose(
        "Translation mode",
        [
            "auto",
            "off",
            "same-language-cleanup",
            "translate",
            "bilingual",
            "transliterate",
            "metadata-only",
            "Back",
        ],
        profile.translation_mode,
    )
    if profile.translation_mode == "Back":
        return profile
    profile.sync_translation_defaults()

    chosen_model = choose_model(profile.model)
    if chosen_model is None:
        return None
    profile.model = chosen_model
    profile.model.execution_mode = ui.choose(
        "Execution mode", EXECUTION_MODES + ["Back"], profile.model.execution_mode
    )
    if profile.model.execution_mode == "Back":
        return profile
    profile.output_formats = choose_output_formats(profile.output_formats)
    profile.extract_figures = ui.confirm(
        "Enable figure/image markers and page image assets for assembly?", profile.extract_figures
    )
    profile.instruction_preset = DEFAULT_PRESET
    profile.output_dir = choose_output_folder(profile.output_dir)
    profile.locked = ui.confirm("Lock this profile as the default quick-run workflow?", True)
    saved = store.save_profile(profile)
    if profile.locked:
        store.set_default_profile(profile.name)
    ui.write(f"Saved profile: {saved}")
    return profile


def choose_model(current: Optional[ModelSettings] = None) -> Optional[ModelSettings]:
    current = current or ModelSettings()
    with ui.progress("Analyzing available model providers...") as reporter:
        statuses = {name: provider.status() for name, provider in provider_registry().items()}
        reporter.finish("Finished analyzing providers.")
    source = ui.choose("Model source", ["local", "cloud", "Back"], _provider_source(current.provider))
    if source == "Back":
        return current
    if source == "cloud":
        provider_names = [
            "openai",
            "anthropic",
            "gemini",
            "openrouter",
            "groq",
            "mistral",
            "together",
            "fireworks",
            "perplexity",
            "deepseek",
            "xai",
            "cerebras",
            "custom-openai-compatible",
        ]
        default_provider = current.provider if current.provider in provider_names else "openai"
    else:
        provider_names = ["ollama", "openai-compatible-local", "lm-studio", "jan", "llama-cpp"]
        default_provider = current.provider if current.provider in provider_names else "ollama"
    choices = []
    for provider_name in provider_names:
        status = statuses.get(provider_name)
        if status and status.available:
            suffix = "available"
        elif source == "cloud":
            suffix = "api key needed"
        else:
            suffix = "setup needed"
        choices.append(f"{provider_name} ({suffix})")
    default_label = next(
        (choice for choice in choices if choice.startswith(f"{default_provider} ")),
        choices[0],
    )
    selected_label = ui.choose("Model provider", choices + ["Back"], default_label)
    if selected_label == "Back":
        return None
    provider_name = selected_label.split(" ", 1)[0]
    status = statuses.get(provider_name)
    model_choices = status.models if status and status.models else _recommended_models(provider_name)
    manual_choice = "Enter exact model name manually"
    if model_choices:
        model_choices = list(dict.fromkeys(model_choices + [manual_choice, "Back"]))
        model = ui.choose(
            "Model",
            model_choices,
            current.model if current.model in model_choices else model_choices[0],
        )
    else:
        model = manual_choice
    if model == "Back":
        return None
    if model == manual_choice:
        hint = _model_name_hint(provider_name)
        ui.write(hint)
        default_model = "" if current.model == "offline-restoration-preview" else current.model
        model = ui.text("Exact model id accepted by this provider", default_model)
        if not model:
            model = current.model or "offline-restoration-preview"
    endpoint = current.endpoint or ""
    if provider_name in {
        "openai-compatible-local",
        "lm-studio",
        "jan",
        "llama-cpp",
        "custom-openai-compatible",
    }:
        endpoint_label = "Local endpoint" if source == "local" else "Cloud endpoint"
        endpoint = ui.text(endpoint_label, endpoint or _default_endpoint(provider_name))

    context_window = current.context_window
    generation_limit = current.generation_limit
    if provider_name in {"ollama", "openai-compatible-local", "lm-studio", "jan", "llama-cpp"}:
        context_window = choose_token_limit(
            "Context window",
            current.context_window,
            suggestions=[8192, 16384, 32768, 65536],
            minimum=2048,
        )
        generation_limit = choose_token_limit(
            "Output token limit",
            current.generation_limit,
            suggestions=[4096, 8192, 16384, 32768],
            minimum=1024,
        )

    return ModelSettings(
        provider=provider_name,
        model=model,
        endpoint=endpoint or None,
        execution_mode=current.execution_mode,
        context_window=context_window,
        generation_limit=generation_limit,
    )


def choose_output_formats(defaults: Optional[List[str]] = None) -> List[str]:
    defaults = defaults or ["txt"]
    choices = list(OUTPUT_FORMATS.keys()) + ["Back"]
    selected = ui.checkbox("Output formats", choices, defaults)
    if "Back" in selected:
        return defaults
    selected = [item for item in selected if item in OUTPUT_FORMATS]
    return selected or ["txt"]


def choose_language_value(label: str, current: str, default_keyword: str) -> str:
    current = str(current or default_keyword).strip() or default_keyword
    choices = [
        f"Use {default_keyword}",
        f"Keep current: {current}",
        "Enter language manually",
        "Back",
    ]
    selected = ui.choose(label, choices, choices[1])
    if selected == "Back":
        return current
    if selected.startswith("Use "):
        return default_keyword
    if selected.startswith("Keep current"):
        return current
    ui.note("Enter a language name, for example English, Kannada, Sanskrit, Hindi, Tamil, or auto/same.")
    entered = ui.text(f"{label} manual value", current)
    return entered.strip() or current


def choose_token_limit(
    label: str,
    current: Optional[int],
    suggestions: List[int],
    minimum: int,
) -> Optional[int]:
    current_label = f"Keep current: {current}" if current is not None else "Use backend default"
    choices = [current_label, "Use backend default"]
    choices.extend(f"{value:,} tokens" for value in suggestions)
    choices.extend(["Enter manually", "Back"])
    selected = ui.choose(label, choices, current_label)
    if selected == "Back":
        return current
    if selected == "Use backend default":
        return None
    if selected.startswith("Keep current"):
        return current
    if selected == "Enter manually":
        ui.note(f"Enter a whole number of tokens. Minimum accepted value: {minimum}.")
        raw = ui.text(f"{label} manual token value", str(current or suggestions[1]))
        try:
            return max(minimum, int(raw.replace(",", "").strip()))
        except ValueError:
            ui.status("warning", "Invalid number. Keeping the previous value.")
            return current
    try:
        return max(minimum, int(selected.split(" ", 1)[0].replace(",", "")))
    except ValueError:
        return current


def choose_language_policy(default: str = "preserve-detected") -> str:
    choices = [
        "Keep all readable languages and scripts",
        "Only extract the declared source language",
        "Back",
    ]
    values = {
        choices[0]: "preserve-detected",
        choices[1]: "strict-source",
        choices[2]: default,
    }
    default_label = choices[1] if default == "strict-source" else choices[0]
    ui.note("Choose how Akshara should handle mixed-language pages.")
    selected = ui.choose("Language handling mode", choices, default_label)
    return values[selected]


def choose_output_folder(default: str = "akshara-output") -> str:
    while True:
        entered = ui.text("Path to output folder", default)
        validated = _validate_output_dir(entered)
        if validated is not None:
            return str(validated)
        ui.write("Enter a folder path, not a file path. The parent folder must already exist.")


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
        profile.workflow = ui.choose("Workflow", WORKFLOWS + ["Back"], profile.workflow)
        if profile.workflow == "Back":
            return None
        profile.document_type = ui.choose("Document type", DOCUMENT_TYPES + ["Back"], profile.document_type)
        if profile.document_type == "Back":
            return None

        chosen_model = choose_model(profile.model)
        if chosen_model is None:
            return None
        profile.model = chosen_model
        profile.model.execution_mode = ui.choose(
            "Execution mode", EXECUTION_MODES + ["Back"], profile.model.execution_mode
        )
        if profile.model.execution_mode == "Back":
            return None
        profile.output_formats = choose_output_formats(profile.output_formats)
    return execute_run(profile, inputs=inputs, recursive=recursive, dry_run=dry_run)


def quick_run(
    inputs: Optional[Iterable[str]] = None, recursive: bool = False, dry_run: bool = False
):
    store = ConfigStore()
    profile = store.load_default_profile()
    ui.heading("Akshara Vision", "Quick Run")
    return execute_run(profile, inputs=inputs, recursive=recursive, dry_run=dry_run)


def batch_run(
    inputs: Optional[Iterable[str]] = None,
    profile_name: Optional[str] = None,
    dry_run: bool = False,
):
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
    profile = copy.deepcopy(profile)
    profile.sync_translation_defaults()
    input_values = list(inputs or [])
    if not input_values:
        entered = ui.text("Path(s) to input files, folders, globs, or manifests")
        input_values = [item.strip() for item in entered.split(",") if item.strip()]
    with ui.progress("Scanning input paths...") as reporter:
        selection = discover_inputs(input_values, recursive=recursive)
        reporter.finish(f"Found {selection.supported_count} supported input(s)")
    if ui.interactive():
        ui.section("Destination")
        profile.output_dir = choose_output_folder(profile.output_dir)
        profile.language_policy = choose_language_policy(profile.language_policy)
        profile.extract_figures = ui.confirm(
            "Enable figure/image markers and page image assets for this run?", profile.extract_figures
        )
    review_run(profile, selection)
    if dry_run:
        ui.write("Dry run complete. No outputs were written.")
        return None
    if not selection.files:
        ui.status("error", "No supported input files found.")
        return None
    if not ui.confirm("Start this run?", True):
        ui.status("info", "Run cancelled.")
        return None
    previous_handler = signal.getsignal(signal.SIGINT)

    def _interrupt_handler(signum, frame):
        del signum, frame
        ui.status("warning", "Safe interruption requested. Preserving completed outputs...")
        ui.status("info", "If a model request is active, Akshara will stop as soon as Python regains control.")
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGINT, _interrupt_handler)
        pid = os.getpid()
        if sys.platform == "win32":
            ui.status("info", f"Press Ctrl+C to interrupt safely and preserve progress. (PID: {pid})")
            ui.write(f"  Fallback force-kill: Stop-Process -Id {pid} -Force")
        else:
            ui.status("info", f"Press Ctrl+C to interrupt safely and preserve progress. (PID: {pid})")
            ui.write(f"  Fallback force-kill: kill -9 {pid}")
        result = _run_with_progress(RunRequest(profile=profile, inputs=selection, dry_run=False))
    except KeyboardInterrupt:
        ui.section("Interrupted")
        latest = _latest_run_folder(Path(profile.output_dir).expanduser())
        if latest:
            ui.write(f"Latest run folder: {latest}")
            ui.write(f"Recover completed output with: akv resume {latest}")
        else:
            ui.status("warning", "Run interrupted before a recoverable folder was found.")
        return None
    except Exception as exc:
        ui.section("Error")
        ui.status("error", f"{exc}")
        ui.write("\nRun stopped. No outputs were written or modified.")
        return None
    finally:
        signal.signal(signal.SIGINT, previous_handler)
    _finished_screen(result)
    return result


def _latest_run_folder(output_root: Path) -> Optional[Path]:
    if not output_root.exists():
        return None
    candidates = [path for path in output_root.iterdir() if path.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def review_run(profile: WorkflowProfile, selection) -> None:
    ui.section("Review")
    rows = [
        ["Workflow", profile.workflow],
        ["Document type", profile.document_type],
        ["Source language", profile.source_language],
        ["Output language", profile.output_language],
        ["Language handling", profile.language_policy],
        ["Translation", _translation_label(profile)],
        ["Provider", profile.model.provider],
        ["Model", profile.model.model],
        ["Mode", profile.model.execution_mode],
        ["Mode behavior", _mode_behavior(profile.model.execution_mode)],
        ["Figure/image assets", "on" if profile.extract_figures else "off"],
        ["Generation limit", str(profile.model.generation_limit or "auto")],
        ["Outputs", ", ".join(profile.output_formats)],
        ["Destination", profile.output_dir],
        ["Inputs found", str(selection.supported_count)],
    ]
    ui.table(rows)
    ui.bullet_list([_friendly_path(Path(line)) for line in selection.display_files()])
    if selection.missing:
        ui.write(f"Missing: {', '.join(selection.missing)}")
    if selection.unsupported:
        ui.write("Unsupported:")
        ui.bullet_list(selection.unsupported)


def _run_with_progress(request: RunRequest):
    ui.section("Working")
    with ui.progress("Processing") as reporter:

        def progress(event: str, message: str, advance: int = 1) -> None:
            reporter.update(message, advance=advance)
            if event in {"usage", "interrupt"}:
                reporter.log(message)

        return run_pipeline(request, progress=progress)


def _mode_behavior(mode: str) -> str:
    return {
        "fast": "200 DPI, shorter prompt, heuristic figure crops",
        "balanced": "300 DPI, default prompt, verifies first figure crop",
        "quality": "400 DPI, more careful prompt, verifies figure crops",
    }.get(mode, "standard settings")


def _finished_screen(result) -> None:
    exports = result["exports"]
    run_dir = Path(result["run_dir"])

    metadata = {}
    manifest_path = run_dir / "run_manifest.json"
    if manifest_path.exists():
        import json

        try:
            metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass

    usage = metadata.get("metadata", {}).get("usage") or metadata.get("usage") or {}
    truncated = usage.get("truncated", False)
    run_metadata = metadata.get("metadata", {}) if isinstance(metadata.get("metadata"), dict) else {}
    translation = run_metadata.get("translation") if isinstance(run_metadata.get("translation"), dict) else {}
    restoration = run_metadata.get("restoration") if isinstance(run_metadata.get("restoration"), list) else []
    issues = []

    if truncated:
        issues.append("model context or output limit reached")
    if isinstance(translation, dict) and translation.get("failure_reason"):
        issues.append(str(translation.get("failure_reason")))
    for item in restoration:
        if isinstance(item, dict) and item.get("failure_reason"):
            issues.append(str(item.get("failure_reason")))
    issues = list(dict.fromkeys(issue for issue in issues if issue))

    if truncated:
        ui.heading("Akshara Vision", "Finished (Truncated)")
        ui.status("warning", "Run completed with truncated output. One or more page chunks hit the token context/generation limit.")
    else:
        ui.heading("Akshara Vision", "Finished")
        ui.status("success", "Run completed.")
    ui.section("Output")

    rows = [
        ["Run folder", str(run_dir)],
        ["Manifest", str(manifest_path)],
        ["Exports", str(len(exports))],
    ]
    if isinstance(translation, dict):
        rows.append(
            [
                "Translation",
                f"{translation.get('mode', 'skipped')} -> {translation.get('resolved_mode', 'off')}",
            ]
        )
        rows.append(["Output language", str(translation.get("output_language", ""))])
    if issues:
        rows.append(["Warnings", str(len(issues))])

    if usage and (usage.get("prompt_tokens") or usage.get("completion_tokens")):
        rows.append(["Input tokens", str(usage.get("prompt_tokens", 0))])
        rows.append(["Output tokens", str(usage.get("completion_tokens", 0))])
        rows.append(["Total tokens", str(usage.get("total_tokens", 0))])

    ui.table(rows)
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
    if issues:
        ui.section("Issues")
        ui.bullet_list(issues)
    ui.section("Next")
    ui.table(
        [
            ["Review text", f"{run_dir}/akshara_output.txt"],
            ["Re-export", f"akv export {run_dir}"],
            ["Clean later", "akv clean"],
        ]
    )


def profile_command(
    action: str = "menu",
    name: str = "default",
    source: Optional[str] = None,
    lock: bool = False,
) -> None:
    store = ConfigStore()
    action = (action or "menu").lower()
    if action in {"menu", "manage"}:
        if not sys.stdin.isatty():
            _list_profiles(store)
            return
        _profile_menu(store)
        return
    if action in {"create", "new", "add"}:
        profile_name = name
        if profile_name == "default" and sys.stdin.isatty():
            profile_name = ui.text("New profile name", "default")
        onboard(store, profile_name=profile_name)
        return
    if action in {"list", "ls"}:
        _list_profiles(store)
        return
    if action == "show":
        if not _require_profile(store, name):
            return
        _show_profile(store.load_profile(name))
        return
    if action in {"use", "switch", "default", "lock"}:
        if not _require_profile(store, name):
            return
        profile = store.load_profile(name)
        profile.locked = action == "lock" or lock or profile.locked
        store.save_profile(profile)
        store.set_default_profile(profile.name)
        ui.write(f"Default profile set to: {profile.name}")
        return
    if action in {"modify", "update"}:
        if not _require_profile(store, name):
            return
        _edit_profile_interactive(store, name)
        return
    if action == "delete":
        _delete_profile(store, name)
        return
    if action in {"copy", "duplicate", "clone"}:
        if not _require_profile(store, name):
            return
        _duplicate_profile(store, name)
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
    ui.write(
        "Try: akv p list, akv p create --name NAME, akv p modify --name NAME, akv p delete --name NAME"
    )


def _profile_menu(store: ConfigStore) -> None:
    while True:
        profiles = store.list_profiles()
        ui.heading("Akshara Vision", "Profiles")
        _list_profiles(store, show_heading=False)
        action = ui.choose(
            "Profile action",
            [
                "Create new profile",
                "Modify guided profile",
                "Show profile details",
                "Use as default",
                "Lock default workflow",
                "Duplicate profile",
                "Delete profile",
                "Import profile",
                "Export profile path",
                "Open TOML in editor",
                "Back",
            ],
            "Create new profile" if not profiles else "Modify guided profile",
        )
        if action == "Back":
            return
        if action == "Create new profile":
            name = ui.text("New profile name", "default" if not profiles else "book-restoration")
            onboard(store, profile_name=name)
        elif action == "Modify guided profile":
            selected = _choose_profile(store)
            if selected:
                _edit_profile_interactive(store, selected)
        elif action == "Show profile details":
            selected = _choose_profile(store)
            if selected:
                _show_profile(store.load_profile(selected))
        elif action == "Use as default":
            selected = _choose_profile(store)
            if selected:
                profile_command("use", name=selected)
        elif action == "Lock default workflow":
            selected = _choose_profile(store)
            if selected:
                profile_command("lock", name=selected)
        elif action == "Duplicate profile":
            selected = _choose_profile(store)
            if selected:
                _duplicate_profile(store, selected)
        elif action == "Delete profile":
            selected = _choose_profile(store)
            if selected:
                _delete_profile(store, selected)
        elif action == "Import profile":
            source = ui.text("Profile TOML path")
            if source:
                profile_command("import", source=source)
        elif action == "Export profile path":
            selected = _choose_profile(store)
            if selected:
                profile_command("export", name=selected)
        elif action == "Open TOML in editor":
            selected = _choose_profile(store)
            if selected:
                _open_editor(store.profile_path(selected))


def _require_profile(store: ConfigStore, name: str) -> bool:
    if store.profile_exists(name):
        return True
    ui.write(f"Profile not found: {name}")
    ui.write("Run `akv p list` to see profiles, or `akv p create --name NAME` to create one.")
    return False


def _list_profiles(store: ConfigStore, show_heading: bool = True) -> None:
    profiles = store.list_profiles()
    if show_heading:
        ui.heading("Akshara Vision", "Profiles")
    if not profiles:
        ui.write("No profiles yet. Choose Create new profile or run `akv i`.")
        return
    default_name = store.default_profile_name()
    rows = [["Name", "Default", "Locked", "Translation", "Provider", "Model", "Outputs"]]
    for profile_name in profiles:
        profile = store.load_profile(profile_name)
        rows.append(
            [
                profile.name,
                "yes" if profile.name == default_name else "",
                "yes" if profile.locked else "",
                _translation_label(profile),
                profile.model.provider,
                profile.model.model,
                ", ".join(profile.output_formats),
            ]
        )
    ui.table(rows)


def _choose_profile(store: ConfigStore) -> Optional[str]:
    profiles = store.list_profiles()
    if not profiles:
        ui.write("No profiles yet.")
        return None
    default_name = store.default_profile_name()
    choices = [f"{name} {'(default)' if name == default_name else ''}".strip() for name in profiles]
    selected = ui.choose("Profile", choices, choices[0])
    return selected.split(" ", 1)[0]


def _show_profile(profile: WorkflowProfile, show_heading: bool = True) -> None:
    if show_heading:
        ui.heading("Akshara Vision", f"Profile: {profile.name}")
    ui.table(
        [
            ["Name", profile.name],
            ["Workflow", profile.workflow],
            ["Document type", profile.document_type],
            ["Source language", profile.source_language],
            ["Output language", profile.output_language],
            ["Language handling", profile.language_policy],
            ["Translation", _translation_label(profile)],
            ["Outputs", ", ".join(profile.output_formats)],
            ["Instruction", profile.instruction_preset],
            ["Output folder", profile.output_dir],
            ["Figure/image assets", "on" if profile.extract_figures else "off"],
            ["Locked", "yes" if profile.locked else "no"],
            ["Provider", profile.model.provider],
            ["Model", profile.model.model],
            ["Endpoint", profile.model.endpoint or ""],
            ["Mode", profile.model.execution_mode],
            ["Context", str(profile.model.context_window or "auto")],
            ["Generation limit", str(profile.model.generation_limit or "auto")],
        ]
    )


def _edit_profile_interactive(store: ConfigStore, name: str) -> None:
    profile = store.load_profile(name)
    while True:
        ui.heading("Akshara Vision", f"Modify: {profile.name}")
        _show_profile(profile, show_heading=False)
        section = ui.choose(
            "What should change?",
            [
                "Workflow and document",
                "Languages and translation",
                "Model and limits",
                "Outputs",
                "Output folder",
                "Lock/default",
                "Everything",
                "Back",
            ],
            "Everything",
        )
        if section == "Back":
            return
        if section in {"Workflow and document", "Everything"}:
            profile.workflow = ui.choose("Workflow", WORKFLOWS + ["Back"], profile.workflow)
            if profile.workflow == "Back":
                profile.workflow = store.load_profile(name).workflow
            profile.document_type = ui.choose(
                "Document type", DOCUMENT_TYPES + ["Back"], profile.document_type
            )
            if profile.document_type == "Back":
                profile.document_type = store.load_profile(name).document_type
        if section in {"Languages and translation", "Everything"}:
            profile.source_language = choose_language_value(
                "Source language", profile.source_language, "auto"
            )
            profile.output_language = choose_language_value(
                "Output language", profile.output_language, "same"
            )
            profile.language_policy = choose_language_policy(profile.language_policy)
            ui.write("Translation turns on automatically when output language differs from source.")
            mode_choices = [
                "auto",
                "off",
                "same-language-cleanup",
                "translate",
                "bilingual",
                "transliterate",
                "metadata-only",
                "Back",
            ]
            selected_mode = ui.choose("Translation mode", mode_choices, profile.translation_mode)
            if selected_mode != "Back":
                profile.translation_mode = selected_mode
            profile.sync_translation_defaults()
        if section in {"Model and limits", "Everything"}:
            chosen_model = choose_model(profile.model)
            if chosen_model is None:
                continue
            profile.model = chosen_model
            selected_mode = ui.choose(
                "Execution mode", EXECUTION_MODES + ["Back"], profile.model.execution_mode
            )
            if selected_mode != "Back":
                profile.model.execution_mode = selected_mode
        if section in {"Outputs", "Everything"}:
            profile.output_formats = choose_output_formats(profile.output_formats)
            profile.extract_figures = ui.confirm(
                "Enable figure/image markers and page image assets for assembly?",
                profile.extract_figures,
            )
        if section in {"Output folder", "Everything"}:
            profile.output_dir = choose_output_folder(profile.output_dir)
        if section in {"Lock/default", "Everything"}:
            profile.locked = ui.confirm(
                "Lock this profile as the default quick-run workflow?", profile.locked
            )
        saved = store.save_profile(profile)
        if profile.locked:
            store.set_default_profile(profile.name)
        ui.status("success", f"Saved profile: {saved}")
        ui.write("Choose another section or Back.")


def _delete_profile(store: ConfigStore, name: str) -> None:
    profiles = store.list_profiles()
    if name not in profiles:
        ui.write(f"Profile not found: {name}")
        return
    if len(profiles) == 1:
        ui.write("Cannot delete the last profile. Create another profile first.")
        return
    if not ui.confirm(f"Delete profile '{name}'?", False):
        ui.write("Delete cancelled.")
        return
    deleted = store.delete_profile(name)
    ui.write(f"Deleted profile: {name}" if deleted else f"Profile not found: {name}")


def _duplicate_profile(store: ConfigStore, name: str) -> None:
    source = store.load_profile(name)
    target_name = ui.text("New profile name", f"{source.name}-copy")
    if not target_name:
        ui.write("Duplicate cancelled.")
        return
    if store.profile_exists(target_name) and not ui.confirm(
        f"Overwrite profile '{target_name}'?", False
    ):
        ui.write("Duplicate cancelled.")
        return
    duplicate = WorkflowProfile.from_dict(source.to_dict())
    duplicate.name = target_name
    duplicate.locked = False
    saved = store.save_profile(duplicate)
    ui.write(f"Duplicated profile: {saved}")


def _provider_source(provider_name: str) -> str:
    if provider_name not in {"ollama", "openai-compatible-local", "lm-studio", "jan", "llama-cpp", "mock"}:
        return "cloud"
    return "local"


def _model_name_hint(provider_name: str) -> str:
    hints = {
        "openrouter": "Use the exact OpenRouter model slug, for example provider/model-name.",
        "custom-openai-compatible": "Use the exact model id accepted by the endpoint's /chat/completions API.",
        "ollama": "Use the exact `ollama list` name, for example gemma4:12b-it-q4_K_M.",
    }
    return hints.get(
        provider_name,
        "Use the exact model id shown in the provider dashboard or returned by its /models endpoint.",
    )


def _validate_output_dir(value: str) -> Optional[Path]:
    path = Path(value).expanduser()
    if not str(value).strip():
        return None
    if path.exists():
        return path if path.is_dir() else None
    parent = path.parent
    if not parent.exists() or not parent.is_dir():
        return None
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return path


def model_command(action: str = "status") -> None:
    ui.heading("Akshara Vision", "Models")
    if action == "setup":
        settings = choose_model()
        if settings is None:
            ui.status("info", "Model setup cancelled.")
            return
        store = ConfigStore()
        profile = store.load_default_profile()
        profile.model = settings
        ui.table(
            [
                ["Provider", settings.provider],
                ["Model", settings.model],
                ["Endpoint", settings.endpoint or ""],
            ]
        )
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
    ui.section("Cloud Models")
    ui.table(
        [
            ["Provider", "Setup"],
            ["OpenAI / Anthropic / Gemini", "native provider key"],
            ["OpenRouter / Groq / Mistral", "OpenAI-compatible model listing when available"],
            ["Together / Fireworks / Perplexity", "OpenAI-compatible model listing when available"],
            ["DeepSeek / xAI / Cerebras", "OpenAI-compatible model listing when available"],
            ["Custom", "AKSHARA_CUSTOM_API_KEY + AKSHARA_CUSTOM_OPENAI_COMPATIBLE_BASE_URL"],
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
        ("pdftoppm", "PDF page rendering for multimodal vision"),
        ("ollama", "Local model runtime"),
    ]
    rows = [["Check", "State", "Purpose"]]
    for command, purpose in tools:
        rows.append([command, "found" if find_executable(command) else "missing", purpose])
    for env_name, purpose in [
        ("OPENAI_API_KEY", "OpenAI cloud models"),
        ("ANTHROPIC_API_KEY", "Anthropic cloud models"),
        ("GEMINI_API_KEY", "Gemini cloud models"),
    ]:
        rows.append([env_name, "set" if os.environ.get(env_name) else "not set", purpose])
    ui.table(rows)
    ui.section("Providers")
    ui.table(provider_status_rows())


def combine_command(run_dir: Optional[str] = None) -> None:
    ui.heading("Akshara Vision", "Combine Outputs")
    target = run_dir or ui.text("Path to Akshara run folder containing staged outputs")
    if not target:
        ui.write("No folder selected.")
        return
    try:
        with ui.progress("Combining staged outputs...") as reporter:
            result = combine_stage_outputs(Path(target).expanduser())
            reporter.finish("Combined staged outputs")
    except Exception as exc:
        ui.write(f"ERROR: {exc}")
        return
    ui.write(f"Combined output: {result['output_path']}")
    ui.write(f"Language-specific alias: {result['alias_path']}")
    exports = result.get("exports") or []
    if exports:
        ui.write("Rebuilt exports:")
        for item in exports:
            state = "available" if item.available else "setup note"
            ui.write(f"  {item.format}: {item.path} ({state})")
    _next_recommendations(
        [
            ["Review output", str(result["output_path"])],
            ["Export another format", f"akv export {result['run_dir']}"],
            ["Run checks", "akv check"],
        ]
    )


def resume_command(run_dir: Optional[str] = None) -> None:
    ui.heading("Akshara Vision", "Resume / Recover")
    target = run_dir or ui.text("Path to interrupted Akshara run folder")
    if not target:
        ui.status("error", "No folder selected.")
        return
    run_path = Path(target).expanduser()
    state_path = run_path / "run_state.json"
    if not state_path.exists():
        ui.status("error", f"No run_state.json found in {run_path}")
        return

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        state = {}

    status = state.get("status", "unknown")
    completed = state.get("completed_inputs") if isinstance(state.get("completed_inputs"), list) else []
    total_inputs = state.get("total_inputs", len(completed))
    input_files = state.get("input_files", [])

    ui.status("info", f"State: {status}")
    ui.status("info", f"Completed inputs: {len(completed)}/{total_inputs}")

    if status == "running":
        state["status"] = "interrupted"
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        status = "interrupted"

    if status == "complete" or len(completed) >= total_inputs:
        ui.write("Recovering completed checkpoints into final outputs.")
        combine_command(str(run_path))
        return

    if input_files:
        ui.status("info", "Found original input path(s).")
        if ui.confirm("Resume this run in the same run folder?", True):
            profile_dict = state.get("profile", {})
            profile = WorkflowProfile.from_dict(profile_dict)
            profile.output_dir = str(run_path.parent)
            selection = discover_inputs([str(path) for path in input_files], recursive=True)
            if not selection.files:
                ui.status("warning", "Original inputs were not found. Combining completed checkpoints instead.")
                combine_command(str(run_path))
                return
            result = _run_with_progress(
                RunRequest(
                    profile=profile,
                    inputs=selection,
                    dry_run=False,
                    resume_run_dir=str(run_path),
                )
            )
            _finished_screen(result)
            return

    ui.write("Combining already completed checkpoints into final outputs.")
    combine_command(str(run_path))


def check_command() -> int:
    ui.heading("Akshara Vision", "Check")
    env = os.environ.copy()
    env.setdefault(
        "PYTHONPYCACHEPREFIX", str(Path(tempfile.gettempdir()) / "akshara-vision-pycache")
    )
    if Path("src").exists():
        existing_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            f"src{os.pathsep}{existing_pythonpath}" if existing_pythonpath else "src"
        )
    commands = [
        ("Compilation", [sys.executable, "-m", "compileall", "-q", "src", "tests"]),
        ("Unit Tests", [sys.executable, "-m", "unittest", "discover", "-s", "tests"]),
    ]
    failed = 0
    failed_label = ""
    failed_output = ""
    with ui.progress("Checks", total=len(commands)) as reporter:
        for label, command in commands:
            reporter.update(label, advance=0)
            result = subprocess.run(
                command,
                check=False,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if result.returncode != 0:
                failed = result.returncode
                failed_label = label
                failed_output = (result.stdout or "") + "\n" + (result.stderr or "")
                break
            reporter.update(label, advance=1)
    if failed:
        ui.section(f"Failure in {failed_label}")
        ui.write(failed_output.strip())
        ui.status("error", "Checks did not pass.")
        _next_recommendations([["Inspect failure", "Read the output above"], ["Retry", "akv check"]])
        return failed
    ui.status("success", "Compile and unit tests passed.")
    _next_recommendations([["Run workflow", "akv run"], ["Review setup", "akv doctor"]])
    return 0


def provider_status_rows() -> List[List[str]]:
    rows = [["Provider", "State", "Models / setup"]]
    for name, provider in provider_registry().items():
        status = provider.status()
        models_or_setup = (
            ", ".join(status.models[:3]) if status.models else _short_detail(status.detail)
        )
        rows.append(
            [
                name,
                "available" if status.available else "setup needed",
                models_or_setup,
            ]
        )
    return rows


def export_command(run_dir: Optional[str] = None, formats: Optional[List[str]] = None) -> None:
    if not run_dir:
        run_dir = ui.text("Path to run folder or compiled output file")
    if not run_dir:
        ui.status("info", "Export cancelled.")
        return
    path = Path(run_dir).expanduser()
    source_text, destination, metadata = _export_source(path)
    if source_text is None:
        ui.write("Could not find readable text in that run folder or output file.")
        return
    selected = formats or choose_output_formats(["txt"])
    registry = exporter_registry()
    with ui.progress("Exporting formats...", total=len(selected)) as reporter:
        for output_format in selected:
            exporter = registry.get(output_format)
            if exporter:
                reporter.update(f"Writing {output_format}", advance=0)
                result = exporter.export(source_text, destination, metadata)
                reporter.update(f"Wrote {output_format}", advance=1)
                ui.write(f"{result.format}: {result.path}")
    _next_recommendations([["Combine run", f"akv combine {path}"], ["Run doctor", "akv doctor"]])


def _next_recommendations(rows: List[List[str]]) -> None:
    ui.section("Next")
    ui.table([["Action", "Command / path"]] + rows)


def _export_source(path: Path) -> tuple[Optional[str], Path, dict]:
    if path.is_dir():
        text_path = path / "akshara_output.txt"
        raw_path = path / "raw_ocr.txt"
        source_path = text_path if text_path.exists() else raw_path
        if not source_path.exists():
            return None, path / "akshara_output", {"title": path.name}
        return (
            source_path.read_text(encoding="utf-8", errors="replace"),
            path / "akshara_output",
            _run_metadata_for_export(path),
        )

    if not path.exists() or not path.is_file():
        return None, path.with_name(path.stem + "_converted"), {"title": path.stem}
    text = _read_compiled_output_file(path)
    if text is None:
        return None, path.with_name(path.stem + "_converted"), {"title": path.stem}
    return text, path.with_name(path.stem + "_converted"), {"title": path.stem}


def _read_compiled_output_file(path: Path) -> Optional[str]:
    suffix = path.suffix.lower()
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if suffix in {".txt", ".md", ".hocr", ".xml", ".html"}:
        if suffix == ".html":
            return re.sub(r"<[^>]+>", "", raw)
        return raw
    if suffix == ".json":
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return raw
        if isinstance(data, dict):
            return str(data.get("text") or data.get("restored_text") or data)
        return str(data)
    if suffix == ".jsonl":
        lines = []
        for line in raw.splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                lines.append(line)
                continue
            lines.append(str(item.get("text") if isinstance(item, dict) else item))
        return "\n\n".join(part for part in lines if part.strip())
    if suffix in {".yaml", ".yml"}:
        return raw
    return None


def _run_metadata_for_export(path: Path) -> dict:
    manifest_path = path / "run_manifest.json"
    if not manifest_path.exists():
        return {"title": path.name}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"title": path.name}
    metadata = manifest.get("metadata") if isinstance(manifest, dict) else None
    return metadata if isinstance(metadata, dict) else {"title": path.name}


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
        return ["gemma4:12b", "qwen3.6:27b", "qwen3.5:4b", "llama3.2-vision:11b"]
    if provider_name in {"openai-compatible-local", "lm-studio", "jan", "llama-cpp"}:
        return ["gemma-4-12b-it", "qwen-3.6-27b-instruct", "qwen-3.5-4b-instruct"]
    if provider_name == "gemini":
        return ["gemini-3.5-flash", "gemini-3.5-pro", "gemini-3.1-flash-lite"]
    if provider_name == "anthropic":
        return ["claude-sonnet-5", "claude-fable-5"]
    if provider_name == "openai":
        return ["gpt-5.5", "gpt-5.4"]
    return []


def _default_endpoint(provider_name: str) -> str:
    return {
        "openai-compatible-local": "http://localhost:1234/v1",
        "lm-studio": "http://localhost:1234/v1",
        "jan": "http://localhost:1337/v1",
        "llama-cpp": "http://localhost:8080/v1",
        "custom-openai-compatible": "https://api.example.com/v1",
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
        if sys.platform == "win32":
            editor = "notepad"
        elif sys.platform == "darwin":
            editor = "open -t"
        else:
            editor = "xdg-open"
        ui.status("info", f"Using default editor '{editor}'. Set EDITOR env var to override.")
    subprocess.run(shlex.split(editor, posix=sys.platform != "win32") + [str(path)], check=False)


def _load_profile_dict(path: Path):
    from akshara_vision.core.toml_compat import load_toml

    return load_toml(path)


def _friendly_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return path.name


def install_command() -> None:
    ui.heading("Akshara Vision", "Install Dependencies")
    if find_executable("pdftoppm"):
        ui.write("SUCCESS: System dependencies are already installed and configured.")
        return

    ui.write("Detecting operating system and package managers...")

    import platform

    system = platform.system().lower()

    if system == "darwin":
        ui.write("Detected macOS.")
        if shutil.which("brew"):
            ui.write("Found Homebrew. Installing poppler (pdftoppm)...")
            try:
                subprocess.run(["brew", "install", "poppler"], check=True)
                ui.write(
                    "SUCCESS: Homebrew completed. Run `akv doctor` to confirm pdftoppm is on PATH."
                )
            except subprocess.CalledProcessError as exc:
                ui.write(f"FAILED: Homebrew installation failed: {exc}")
        else:
            ui.write("Homebrew not found. Please install Homebrew (https://brew.sh/) and run:")
            ui.write("  brew install poppler")

    elif system == "linux":
        ui.write("Detected Linux.")
        if shutil.which("apt-get"):
            ui.write("Found apt package manager. Installing poppler-utils...")
            ui.write("Note: This may require sudo permissions.")
            try:
                subprocess.run(["sudo", "apt-get", "update"], check=True)
                subprocess.run(["sudo", "apt-get", "install", "-y", "poppler-utils"], check=True)
                ui.write("SUCCESS: apt completed. Run `akv doctor` to confirm pdftoppm is on PATH.")
            except subprocess.CalledProcessError as exc:
                ui.write(f"FAILED: apt installation failed: {exc}")
        elif shutil.which("pacman"):
            ui.write("Found pacman package manager. Installing poppler...")
            try:
                subprocess.run(["sudo", "pacman", "-S", "--noconfirm", "poppler"], check=True)
                ui.write(
                    "SUCCESS: pacman completed. Run `akv doctor` to confirm pdftoppm is on PATH."
                )
            except subprocess.CalledProcessError as exc:
                ui.write(f"FAILED: pacman installation failed: {exc}")
        else:
            ui.write(
                "Could not identify a supported package manager. Please install poppler-utils manually."
            )

    elif system == "windows":
        ui.write("Detected Windows.")
        if shutil.which("scoop"):
            ui.write("Found Scoop. Installing poppler...")
            try:
                subprocess.run(["scoop", "install", "poppler"], check=True)
                ui.write("SUCCESS: Scoop completed. Restart PowerShell, then run `akv doctor`.")
            except subprocess.CalledProcessError as exc:
                ui.write(f"FAILED: scoop installation failed: {exc}")
        elif shutil.which("winget"):
            ui.write("Found winget. Attempting to install Poppler...")
            try:
                subprocess.run(
                    ["winget", "install", "--id", "oschwartz10612.Poppler", "--silent"], check=True
                )
                ui.write(
                    "SUCCESS: Poppler installer completed. Restart PowerShell, then run `akv doctor`."
                )
            except subprocess.CalledProcessError as exc:
                if exc.returncode == 2316632107:
                    ui.write("SUCCESS: System dependencies are already installed.")
                    ui.write(
                        "Note: If the application cannot find Poppler, please add its installation 'bin' folder to your environment PATH."
                    )
                else:
                    ui.write(f"winget Poppler installation returned/failed: {exc}")
                    ui.write("\nTo install Poppler manually:")
                    ui.write("1. Install via scoop: `scoop install poppler`")
                    ui.write(
                        "2. Or download Poppler binaries from: https://github.com/oschwartz10612/poppler-windows/releases"
                    )
                    ui.write("3. Extract and add the 'bin' folder to your environment PATH.")
        elif shutil.which("choco"):
            ui.write("Found Chocolatey. Installing poppler...")
            try:
                subprocess.run(["choco", "install", "poppler", "-y"], check=True)
                ui.write(
                    "SUCCESS: Chocolatey completed. Restart PowerShell, then run `akv doctor`."
                )
            except subprocess.CalledProcessError as exc:
                ui.write(f"FAILED: Chocolatey installation failed: {exc}")
        else:
            ui.write("Please install Poppler manually:")
            ui.write("- Recommended: install Scoop, then run `scoop install poppler`")
            ui.write("- Poppler: https://github.com/oschwartz10612/poppler-windows/releases")
            ui.write("Make sure it is added to your environment PATH.")
