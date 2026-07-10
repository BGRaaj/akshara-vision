import copy
import base64
import html
import json
import os
import re
import shlex
import signal
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from akshara_vision.cli.ui import ui
from akshara_vision.core.config import ConfigStore
from akshara_vision.core.constants import (
    DOCUMENT_TYPES,
    EXECUTION_MODES,
    OUTPUT_FORMATS,
    WORKFLOWS,
)
from akshara_vision.core.chat import (
    answer_question,
    answer_general_question,
    delete_chat_session,
    build_chat_bundle,
    chat_session_path,
    chat_sessions_root,
    list_chat_sessions,
    load_chat_metadata,
    load_chat_history,
    load_chat_notes,
    save_chat_history,
    search_sources,
)
from akshara_vision.core.env import env_status, load_env_files
from akshara_vision.core.input_discovery import discover_inputs
from akshara_vision.core.models import (
    ModelSettings,
    RunRequest,
    WorkflowProfile,
    effective_translation_mode,
)
from akshara_vision.core.pipeline import (
    available_layout_backends,
    combine_stage_outputs,
    find_executable,
    _render_pdf_page,
    run_pipeline,
)
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
    "Core - Chat over files",
    "Core - Guided setup",
    "Core - Profiles",
    "Core - Models",
    "Core - Instructions",
    "Extended - Resume run",
    "Extended - Review layout and assets",
    "Extended - Compare before and after",
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
    _render_home(expanded=False)
    if interactive:
        interactive_session()


def apply_saved_ui_theme(clear: bool = False) -> None:
    prefs = ConfigStore().load_ui_preferences()
    ui.set_theme(prefs["theme"])
    ui.apply_terminal_theme(clear=clear)


def _render_home(expanded: bool = True) -> None:
    store = ConfigStore()
    profile = store.load_default_profile()
    prefs = store.load_ui_preferences()
    ui.set_theme(prefs["theme"])
    ui.apply_terminal_theme()
    ui.hero(guide=prefs["guide"])
    ui.section("Start Here")
    ui.board(
        [
            ("Run", "/run", "Full guided workflow"),
            ("Quick", "/quick", "Use saved defaults"),
            ("Chat", "/chat", "Ask questions over files"),
            ("Profiles", "/profiles", "Defaults and locks"),
            ("Models", "/models", "Local and cloud choices"),
        ],
        compact=prefs["density"] == "compact",
    )
    if expanded:
        ui.section("Core Workflows")
        ui.board(
            [
                ("Batch", "/batch", "Folders and manifests"),
                ("Setup", "/init", "Create your workflow"),
                ("Instructions", "/instructions", "Restoration prompt presets"),
            ],
            compact=prefs["density"] == "compact",
        )
        ui.section("Extended Tools")
        ui.board(
            [
                ("Resume", "/resume", "Continue interrupted work"),
                ("Review", "/review", "Inspect layout and assets"),
                ("Compare", "/compare", "Source and output side by side"),
                ("Combine", "/combine", "Assemble staged outputs"),
                ("Export", "/export", "Convert existing outputs"),
                ("Docs", "/docs", "Open project guides"),
                ("Guide", "/guide", "Adjust CLI guidance"),
                ("Modes", "/mode", "Speed and quality tradeoffs"),
                ("Customize", "/ui", "Light/dark terminal theme"),
            ],
            compact=prefs["density"] == "compact",
        )
        ui.section("Suggested Next")
        ui.board(
            [
                ("Review", "/review", "Inspect the latest layout map"),
                ("Resume", "/resume", "Recover interrupted work"),
                ("Combine", "/combine", "Rebuild staged outputs"),
                ("Export", "/export", "Change output format"),
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
        ui.write("/run  /quick  /chat  /exit")
    elif prefs["guide"] == "full":
        ui.write("Press Enter for the action picker, or type /help for every command.")
        ui.write("Open /home for the board and /guide to tune the help level.")
    else:
        ui.write("Press Enter for options, /home for the board, or /help for commands.")
        ui.write("Use /guide to choose how much guidance Akshara Vision shows.")
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
        "Core - Chat over files": "/chat",
        "Core - Guided setup": "/init",
        "Core - Profiles": "/profiles",
        "Core - Models": "/models",
        "Core - Instructions": "/instructions",
        "Extended - Resume run": "/resume",
        "Extended - Review layout and assets": "/review",
        "Extended - Compare before and after": "/compare",
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
        ui.write("Try /run, /quick, /batch, /chat, /doctor, /models, /profiles, or /help.")
        return True
    if command in {"/where", "/scope", "/focus", "/cite", "/remember", "/sources", "/find", "/open"}:
        ui.write(f"{command} is available inside /chat after you attach or open a document source.")
        ui.write("Use /chat to work with document sources, or /help to see the full command list.")
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
        _render_home(expanded=True)
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
    elif command in {"/chat", "/ask"}:
        inputs, flags = _session_args(args)
        chat_command(inputs=inputs or None, recursive=flags["recursive"], question=None)
    elif command in {"/profiles", "/profile", "/p"}:
        profile_command(action=args[0] if args else "menu")
    elif command in {"/models", "/model", "/m"}:
        model_command(action=args[0] if args else "menu")
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
    elif command in {"/review", "/inspect", "/qa"}:
        review_command(args[0] if args else None)
    elif command in {"/compare", "/beforeafter", "/diff"}:
        compare_command(args[0] if args else None)
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
            ["/home", "Show the home board"],
            ["/run [inputs...]", "Guided full workflow"],
            ["/quick [inputs...]", "Run locked/default profile"],
            ["/batch [folder...]", "Recursive batch workflow"],
            ["/chat [inputs...]", "Ask grounded questions over runs or files"],
            ["/init", "Create a default profile"],
            ["/profiles", "List or manage profiles"],
            ["/models", "Manage or check model providers"],
            ["/env", "Show API key and endpoint setup"],
            ["/instructions", "View or edit prompts"],
            ["/guide", "Choose guidance level"],
            ["/mode", "Choose speed versus quality mode"],
            ["/ui", "Customize theme and guidance"],
            ["/doctor", "Check local setup"],
            ["/combine [run-folder]", "Rebuild staged outputs into one document"],
            ["/resume [run-folder]", "Recover completed checkpoints from an interrupted run"],
            ["/review [run-folder]", "Inspect layout, assets, and confidence"],
            ["/compare [run-folder]", "Render source and output side by side"],
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
            ["Chat model", _chat_model(profile).model],
            ["Mode", profile.model.execution_mode],
            ["Output folder", profile.output_dir],
            ["Theme", prefs["theme"]],
            ["Guide", prefs["guide"]],
        ]
    )


def _current_guide_level() -> str:
    guide = str(ConfigStore().load_ui_preferences().get("guide") or "balanced").strip().lower()
    return guide if guide in {"minimal", "balanced", "full"} else "balanced"


def guide_command() -> None:
    store = ConfigStore()
    current = store.load_ui_preferences()
    ui.set_theme(current["theme"])
    ui.heading("Akshara Vision", "Guide")
    ui.table(
        [
            ["minimal", "compact prompts for repeat users"],
            ["balanced", "short hints at important decisions"],
            ["full", "context and tradeoffs before key choices"],
        ]
    )
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
    ui.status("success", f"Guide level set to: {guide}")
    _render_home()


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
    ui.write("You can keep a separate chat model if you prefer lighter conversation over the vision model.")
    if ui.confirm("Set a separate chat model?", False):
        chat_model = choose_model(profile.chat_model)
        if chat_model is None:
            return None
        profile.chat_model = chat_model
    else:
        profile.chat_model = copy.deepcopy(profile.model)
    profile.model.execution_mode = ui.choose(
        "Execution mode", EXECUTION_MODES + ["Back"], profile.model.execution_mode
    )
    if profile.model.execution_mode == "Back":
        return profile
    profile.model.request_timeout_seconds = choose_request_timeout(profile.model.request_timeout_seconds)
    profile.output_formats = choose_output_formats(profile.output_formats)
    profile.layout_backend = choose_layout_backend(profile.layout_backend)
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
            "sarvam",
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


def _prompt_missing_model(
    profile: WorkflowProfile,
    target: str,
    store: Optional[ConfigStore] = None,
    persist: bool = False,
) -> Optional[bool]:
    if not (ui.interactive() and sys.stdin.isatty() and sys.stdout.isatty()):
        return None
    if target == "chat":
        current = _chat_model(profile)
        label = "chat model"
    else:
        current = profile.model
        label = "vision model"
    if not ui.confirm(f"No usable {label} is configured. Add one now?", True):
        return False
    chosen = choose_model(current)
    if chosen is None:
        return False
    if target == "chat":
        profile.chat_model = chosen
    else:
        profile.model = chosen
    if persist and store is not None:
        store.save_profile(profile)
        ui.status("success", f"Saved {label} to profile: {profile.name}")
    return True


def choose_output_formats(
    defaults: Optional[List[str]] = None, back_returns_defaults: bool = True
) -> List[str]:
    defaults = [item for item in (defaults or ["txt"]) if item in OUTPUT_FORMATS] or ["txt"]
    selected = list(dict.fromkeys(defaults))
    while True:
        labels = {
            output_format: (
                f"{'[x]' if output_format in selected else '[ ]'} "
                f"{output_format} - {description}"
            )
            for output_format, description in OUTPUT_FORMATS.items()
        }
        choices = list(labels.values()) + ["Select all", "Clear selection", "Done", "Back"]
        default_choice = "Done" if selected else choices[0]
        choice = ui.choose(
            "Output formats (Enter toggles, choose Done to continue)",
            choices,
            default_choice,
        )
        normalized = str(choice).strip()
        if normalized == "Back":
            return defaults if back_returns_defaults else []
        if normalized == "Done":
            if selected:
                return selected
            ui.status("info", "Select at least one output format, or choose Back.")
            continue
        if normalized == "Select all":
            selected = list(OUTPUT_FORMATS.keys())
            continue
        if normalized == "Clear selection":
            selected = []
            continue
        output_format = _output_format_from_choice(normalized, labels)
        if not output_format:
            continue
        if output_format in selected:
            selected.remove(output_format)
        else:
            selected.append(output_format)


def _output_format_from_choice(choice: str, labels: Dict[str, str]) -> Optional[str]:
    if choice in OUTPUT_FORMATS:
        return choice
    for output_format, label in labels.items():
        if choice == label:
            return output_format
    return None


def choose_request_timeout(current: Optional[int] = None) -> Optional[int]:
    choices = [
        "wait forever",
        "skip after 5 minutes",
        "skip after 10 minutes",
        "skip after 20 minutes",
        "skip after 30 minutes",
        "skip after 60 minutes",
        "custom minutes",
        "Back",
    ]
    default = _request_timeout_label(current)
    selected = ui.choose("Slow page/model request handling", choices, default)
    if selected == "Back":
        return current
    if selected == "wait forever":
        return None
    if selected == "custom minutes":
        while True:
            raw = ui.text("Minutes before skipping one slow page/request", "10").strip()
            try:
                minutes = int(raw)
            except ValueError:
                ui.write("Enter a whole number of minutes.")
                continue
            if minutes <= 0:
                ui.write("Enter a value greater than zero, or choose wait forever.")
                continue
            return minutes * 60
    match = re.search(r"(\d+)", selected)
    return int(match.group(1)) * 60 if match else current


def prompt_runtime_mode(profile: WorkflowProfile) -> Optional[WorkflowProfile]:
    runtime = copy.deepcopy(profile)
    guide = _current_guide_level()
    if guide == "full":
        ui.note(
            "Choose how much extra model effort this run can spend. Fast avoids retries, "
            "balanced allows one informed retry, and quality allows deeper recovery."
        )
        ui.table(
            [
                ["fast", "one pass, no restoration retries"],
                ["balanced", "one informed retry for malformed output"],
                ["quality", "up to three retries and deeper review"],
            ]
        )
    elif guide == "balanced":
        ui.note("Runtime mode controls retry depth: fast 0, balanced 1, quality 3.")
    selected_mode = ui.choose(
        "Execution mode for this run",
        EXECUTION_MODES + ["Back"],
        runtime.model.execution_mode,
    )
    if selected_mode == "Back":
        return None
    runtime.model.execution_mode = selected_mode
    runtime.model.request_timeout_seconds = choose_request_timeout(
        runtime.model.request_timeout_seconds
    )
    return runtime


def _request_timeout_label(seconds: Optional[int]) -> str:
    if not seconds:
        return "wait forever"
    minutes = max(1, int(seconds) // 60)
    known = {
        5: "skip after 5 minutes",
        10: "skip after 10 minutes",
        20: "skip after 20 minutes",
        30: "skip after 30 minutes",
        60: "skip after 60 minutes",
    }
    return known.get(minutes, "custom minutes")


def _request_timeout_display(seconds: Optional[int]) -> str:
    if not seconds:
        return "wait forever"
    minutes = max(1, int(seconds) // 60)
    return f"skip after {minutes} minute{'s' if minutes != 1 else ''}"


def choose_language_value(label: str, current: str, default_keyword: str) -> str:
    current = str(current or default_keyword).strip() or default_keyword
    default_label = "Auto-detect language" if default_keyword == "auto" else "Same as source language"
    choices = [
        f"{default_label} ({default_keyword})",
        f"Keep current: {current}",
        "Enter language manually",
        "Back",
    ]
    selected = ui.choose(label, choices, choices[1])
    if selected == "Back":
        return current
    if selected.startswith(default_label):
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


def choose_layout_backend(default: str = "native") -> str:
    backend_names = available_layout_backends()
    labels = []
    values = {}
    for name in backend_names:
        if name == "native":
            label = "Native page layout analysis"
        elif name == "off":
            label = "Off - skip layout analysis"
        else:
            label = f"{name} layout backend"
        labels.append(label)
        values[label] = name
    labels.append("Back")
    values["Back"] = default or "native"
    default_backend = default if default in backend_names else "native"
    default_label = next(label for label, value in values.items() if value == default_backend)
    ui.note("Layout analysis stores page blocks, confidence, columns, and figure/text hints.")
    selected = ui.choose("Layout analysis", labels, default_label)
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
        if ui.interactive() and not _model_usable(profile.model):
            prompted = _prompt_missing_model(profile, "restore", store=store, persist=True)
            if prompted is False:
                return None
        else:
            chosen_model = choose_model(profile.model)
            if chosen_model is None:
                return None
            profile.model = chosen_model
        runtime = prompt_runtime_mode(profile)
        if runtime is None:
            return None
        profile = runtime
        profile.output_formats = choose_output_formats(profile.output_formats)
    else:
        if ui.interactive() and not _model_usable(profile.model):
            prompted = _prompt_missing_model(profile, "restore", store=store, persist=True)
            if prompted is False:
                return None
        runtime = prompt_runtime_mode(profile)
        if runtime is None:
            return None
        profile = runtime
    return execute_run(
        profile,
        inputs=inputs,
        recursive=recursive,
        dry_run=dry_run,
        prompt_runtime_controls=False,
    )


def quick_run(
    inputs: Optional[Iterable[str]] = None, recursive: bool = False, dry_run: bool = False
):
    store = ConfigStore()
    profile = store.load_default_profile()
    ui.heading("Akshara Vision", "Quick Run")
    if ui.interactive() and not _model_usable(profile.model):
        prompted = _prompt_missing_model(profile, "restore", store=store, persist=True)
        if prompted is False:
            return None
    return execute_run(
        profile,
        inputs=inputs,
        recursive=recursive,
        dry_run=dry_run,
        prompt_runtime_controls=False,
    )


def batch_run(
    inputs: Optional[Iterable[str]] = None,
    profile_name: Optional[str] = None,
    dry_run: bool = False,
):
    store = ConfigStore()
    profile = store.load_profile(profile_name) if profile_name else store.load_default_profile()
    ui.heading("Akshara Vision", "Batch Run")
    if ui.interactive() and not _model_usable(profile.model):
        prompted = _prompt_missing_model(profile, "restore", store=store, persist=True)
        if prompted is False:
            return None
    return execute_run(
        profile,
        inputs=inputs,
        recursive=True,
        dry_run=dry_run,
        prompt_runtime_controls=False,
    )


def chat_command(
    inputs: Optional[Iterable[str]] = None,
    profile_name: Optional[str] = None,
    recursive: bool = False,
    question: Optional[str] = None,
    system_prompt: Optional[str] = None,
):
    store = ConfigStore()
    profile = store.load_profile(profile_name) if profile_name else store.load_default_profile()
    ui.heading("Akshara Vision", "Document Chat")
    input_values = list(inputs or [])
    session_path: Optional[Path] = None
    if ui.interactive() and not _chat_model_usable(_chat_model(profile)):
        prompted = _prompt_missing_model(profile, "chat", store=store, persist=True)
        if prompted is False:
            ui.status(
                "warning",
                "No usable chat model is configured. Open /models or edit the profile to choose a chat model.",
            )
            return None
    if not input_values and ui.interactive():
        while True:
            chat_mode = ui.choose(
                "Chat mode",
                ["General conversation", "Document chat", "Saved conversations", "Back"],
                "Document chat",
            )
            if chat_mode == "Back":
                ui.status("info", "Chat cancelled.")
                return None
            if chat_mode == "Saved conversations":
                selected_session = _choose_saved_chat_session()
                if selected_session is None:
                    continue
                session_path = selected_session
                input_values = []
                history = load_chat_history(session_path)
                session_notes = load_chat_notes(session_path)
                metadata = load_chat_metadata(session_path)
                title = str(metadata.get("title") or session_path.stem)
                ui.status("info", f"Resumed saved conversation: {title}")
                chat_model = _chat_model(profile)
                if ui.interactive() and not _chat_model_usable(chat_model):
                    prompted = _prompt_missing_model(profile, "chat", store=store, persist=True)
                    if prompted is False:
                        return None
                break
            if chat_mode == "Document chat":
                entered = ui.text("Path(s) to run folders, output files, files, folders, or globs")
                input_values = [item.strip() for item in entered.split(",") if item.strip()]
                break
            input_values = []
            break
    pending_question = question
    if not pending_question and ui.interactive() and input_values and (_can_lazy_chat(input_values) or _has_folder_input(input_values)):
        if _can_lazy_chat(input_values):
            ui.note("For a single image or page-specific PDF question, Akshara can answer without pre-indexing the whole file.")
        if _has_folder_input(input_values):
            ui.note("For folders, describe the file, nested folder, page, or topic so Akshara can focus before indexing.")
        pending_question = ui.text("Ask your first question")
    bundle = None
    if input_values:
        with ui.progress("Indexing chat sources...") as reporter:
            bundle = build_chat_bundle(
                input_values,
                profile=profile,
                recursive=recursive,
                question=pending_question,
            )
            reporter.finish(f"Indexed {len(bundle.sources)} source chunk(s)")
        bundle.profile.model = copy.deepcopy(_chat_model(profile))
        if ui.interactive() and _has_folder_input(input_values):
            _review_chat_sources(bundle)
        _remember_chat_source_pool(bundle)
        if session_path is None:
            session_path = chat_session_path(input_values)
    ui.section("Review")
    if bundle is not None:
        ui.table(
            [
                ["Title", bundle.title],
                ["Provider", _chat_model(profile).provider],
                ["Model", _chat_model(profile).model],
                ["Sources", str(len(bundle.sources))],
            ]
        )
    else:
        ui.table(
            [
                ["Title", "General conversation"],
                ["Provider", _chat_model(profile).provider],
                ["Model", _chat_model(profile).model],
                ["Sources", "0"],
            ]
        )
    if session_path is None and not input_values:
        session_path = _new_general_chat_session(profile)
    history: List[tuple[str, str]] = load_chat_history(session_path)
    session_notes: List[str] = load_chat_notes(session_path)
    citation_source_ids: List[str] = []
    if history:
        ui.status("info", f"Loaded {len(history)} previous chat turn(s).")
    if ui.interactive():
        guide = _current_guide_level()
        if guide == "full":
            ui.section("Chat Controls")
            ui.table(
                [
                    ["/where TERM", "jump to the best matching source by keyword, page, source id, file name, or topic"],
                    ["/cite S1 S2", "pin the next answer to specific sources"],
                    ["/scope TERM", "narrow the active source set by keyword, page, source id, file name, or topic"],
                    ["/remember NOTE", "store a small local preference or fact"],
                ]
            )
            ui.note("Use /scope all to return to every indexed source.")
        elif guide == "balanced":
            ui.note("Tip: /where narrows, /cite pins, and /remember stores a small note. Use /help for the full list.")
    while True:
        if not pending_question:
            pending_question = ui.text("Ask a question about these sources")
        pending_question = str(pending_question or "").strip()
        if not pending_question:
            break
        if pending_question.startswith("/"):
            command_name = pending_question.split(maxsplit=1)[0].lower()
            if command_name == "/help":
                _show_chat_help()
                pending_question = ""
                continue
            if command_name == "/attach":
                attached = _attach_chat_sources(
                    pending_question,
                    profile=profile,
                    recursive=recursive,
                )
                if attached is not None:
                    bundle, attached_inputs = attached
                    input_values = attached_inputs
                    session_path = chat_session_path(input_values)
                    history = load_chat_history(session_path)
                    session_notes = load_chat_notes(session_path)
                    citation_source_ids = []
                    ui.status("success", f"Attached {len(bundle.sources)} source(s).")
                pending_question = ""
                continue
            if bundle is None:
                ui.status("warning", "Chat tools like /where and /cite need a document source. Use /attach to add one first.")
                pending_question = ""
                continue
            result = _handle_chat_tool(
                pending_question,
                bundle,
                history,
                session_path,
                session_notes=session_notes,
                citation_source_ids=citation_source_ids,
            )
            if result is False:
                break
            if isinstance(result, dict):
                if "bundle" in result and result["bundle"] is not None:
                    bundle = result["bundle"]
                if "citation_source_ids" in result:
                    citation_source_ids = list(result["citation_source_ids"] or [])
                if "session_notes" in result:
                    session_notes = list(result["session_notes"] or [])
                if "history" in result:
                    history = list(result["history"] or history)
            pending_question = ""
            continue
        chat_model = _chat_model(profile)
        if ui.interactive() and not _chat_model_usable(chat_model):
            ui.section("Chat Model")
            prompted = _prompt_missing_model(profile, "chat", store=store, persist=True)
            if prompted is False:
                ui.status(
                    "warning",
                    "No usable chat model is configured. Open /models or edit the profile to choose a chat model.",
                )
                return None
            chat_model = _chat_model(profile)
        if bundle is None:
            with ui.progress("Answering...") as reporter:
                answer, usage = answer_general_question(
                    copy.deepcopy(profile),
                    pending_question,
                    system_prompt=system_prompt,
                    history=history,
                    notes=session_notes,
                )
                reporter.finish("Complete")
            selected_sources = []
        else:
            with ui.progress("Answering...") as reporter:
                answer, usage, selected_sources = answer_question(
                    bundle,
                    pending_question,
                    system_prompt=system_prompt,
                    history=history,
                    notes=session_notes,
                    citation_source_ids=citation_source_ids,
                )
                reporter.finish("Complete")
        ui.section("Answer")
        stream_pause = 0.012 if len(answer or "") <= 1200 else 0.006
        ui.stream(answer or "[no answer]", pause=stream_pause)
        if selected_sources:
            ui.section("Sources")
            ui.bullet_list(
                [
                    f"{source.source_id}: {source.label}"
                    for source in selected_sources
                ]
            )
        if isinstance(usage, dict) and (
            usage.get("prompt_tokens") or usage.get("completion_tokens") or usage.get("total_tokens")
        ):
            prompt_tokens = int(usage.get("prompt_tokens") or 0)
            completion_tokens = int(usage.get("completion_tokens") or 0)
            total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))
            if usage.get("truncated"):
                suffix = " (still truncated after retry)" if usage.get("retry_attempted") else " (truncated)"
            else:
                suffix = " (retry recovered clipped answer)" if usage.get("retry_attempted") else ""
            ui.write(
                f"Token usage: input {prompt_tokens}, output {completion_tokens}, total {total_tokens}{suffix}"
            )
        history.append((pending_question, answer))
        save_chat_history(
            session_path,
            history,
            notes=session_notes,
            metadata=_chat_session_metadata(profile, bundle, input_values, session_path),
        )
        if question is not None or not ui.interactive():
            break
        if not ui.confirm("Ask another question?", True):
            break
        if bundle is None and ui.interactive():
            if ui.confirm("Attach a document or folder for the next question?", False):
                entered = ui.text("Path(s) to run folders, output files, files, folders, or globs")
                next_inputs = [item.strip() for item in entered.split(",") if item.strip()]
                if next_inputs:
                    with ui.progress("Indexing chat sources...") as reporter:
                        bundle = build_chat_bundle(
                            next_inputs,
                            profile=profile,
                            recursive=recursive,
                            question=None,
                        )
                        reporter.finish(f"Indexed {len(bundle.sources)} source chunk(s)")
                    bundle.profile.model = copy.deepcopy(_chat_model(profile))
                    _remember_chat_source_pool(bundle)
                    session_path = chat_session_path(next_inputs)
        pending_question = ""
    _next_recommendations(
        _next_steps_for_context("chat", run_dir=Path(input_values[0]) if input_values else None)
    )
    return bundle


def _can_lazy_chat(input_values: Iterable[str]) -> bool:
    values = [str(value).strip() for value in input_values if str(value).strip()]
    if len(values) != 1:
        return False
    suffix = Path(values[0]).expanduser().suffix.lower()
    return suffix in {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}


def _has_folder_input(input_values: Iterable[str]) -> bool:
    return any(Path(str(value)).expanduser().is_dir() for value in input_values if str(value).strip())


def _review_chat_sources(bundle) -> None:
    ui.section("Indexed Sources")
    rows = [["ID", "Source"]]
    rows.extend([[source.source_id, source.label] for source in bundle.sources[:12]])
    if len(bundle.sources) > 12:
        rows.append(["...", f"and {len(bundle.sources) - 12} more"])
    ui.table(rows)
    if ui.confirm("Use these sources for chat?", True):
        return
    term = ui.text("File name, nested folder, source id, or topic to focus").strip()
    if not term:
        return
    matches = _filter_chat_sources(bundle.sources, term)
    if not matches:
        ui.status("warning", "No matching indexed sources found. Keeping the current source set.")
        return
    bundle.sources = _renumber_cli_sources(matches)
    ui.status("success", f"Focused chat to {len(bundle.sources)} source(s).")


def _attach_chat_sources(
    command: str,
    profile: WorkflowProfile,
    recursive: bool = False,
):
    parts = command.split(maxsplit=1)
    raw_paths = parts[1].strip() if len(parts) > 1 else ""
    if not raw_paths:
        raw_paths = ui.text("Path(s) to run folders, output files, files, folders, or globs")
    input_values = [item.strip() for item in raw_paths.split(",") if item.strip()]
    if not input_values:
        ui.status("info", "Attach cancelled.")
        return None
    with ui.progress("Indexing attached sources...") as reporter:
        bundle = build_chat_bundle(
            input_values,
            profile=profile,
            recursive=recursive,
            question=None,
        )
        reporter.finish(f"Indexed {len(bundle.sources)} source chunk(s)")
    bundle.profile.model = copy.deepcopy(_chat_model(profile))
    if ui.interactive() and _has_folder_input(input_values):
        _review_chat_sources(bundle)
    _remember_chat_source_pool(bundle)
    return bundle, input_values


def _chat_model(profile: WorkflowProfile) -> ModelSettings:
    chat_model = getattr(profile, "chat_model", None)
    if isinstance(chat_model, ModelSettings):
        return chat_model
    return profile.model


def _chat_model_usable(model: ModelSettings) -> bool:
    if not model.provider or not model.model:
        return False
    if model.provider == "mock":
        return False
    provider = provider_registry().get(model.provider)
    if provider is None:
        return False
    try:
        status = provider.status()
    except Exception:
        return False
    return bool(status.available)


def _model_usable(model: ModelSettings) -> bool:
    if not model.provider or not model.model:
        return False
    if model.provider == "mock":
        return False
    provider = provider_registry().get(model.provider)
    if provider is None:
        return False
    try:
        status = provider.status()
    except Exception:
        return False
    return bool(status.available)


def _new_general_chat_session(profile: WorkflowProfile) -> Path:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", f"{profile.name}-chat".lower()).strip("-") or "chat"
    return chat_sessions_root() / f"{slug}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"


def _chat_session_metadata(
    profile: WorkflowProfile,
    bundle,
    input_values: List[str],
    session_path: Optional[Path],
) -> Dict[str, object]:
    model = _chat_model(profile)
    return {
        "title": getattr(bundle, "title", "General conversation"),
        "mode": "document" if bundle is not None else "general",
        "provider": model.provider,
        "model": model.model,
        "inputs": list(input_values),
        "path": str(session_path) if session_path else "",
    }


def _choose_saved_chat_session() -> Optional[Path]:
    sessions = list_chat_sessions()
    if not sessions:
        ui.status("info", "No saved chat sessions found.")
        return None
    choices: List[str] = []
    mapping: Dict[str, Path] = {}
    for session in sessions[:24]:
        metadata = load_chat_metadata(session)
        title = str(metadata.get("title") or session.stem.replace("-", " ").strip().title())
        label = f"{title} | {session.name}"
        choices.append(label)
        mapping[label] = session
    choice = ui.choose("Saved conversation", choices + ["Delete saved conversation", "Back"], choices[0])
    if choice == "Back":
        return None
    if choice == "Delete saved conversation":
        target_label = ui.choose("Delete which saved conversation?", choices, choices[0])
        target = mapping.get(target_label)
        if target and ui.confirm(f"Delete saved conversation '{target.name}'?", False):
            if delete_chat_session(target):
                ui.status("success", f"Deleted saved conversation: {target.name}")
            else:
                ui.status("warning", "Could not delete the selected conversation.")
        return None
    return mapping.get(choice)
def _filter_chat_sources(sources, term: str):
    lowered_terms = [item.lower() for item in re.findall(r"[A-Za-z0-9][A-Za-z0-9._-]{1,}", term)]
    requested_ids = {item.upper() for item in re.findall(r"\bS\d+\b", term, flags=re.I)}
    if not lowered_terms and not requested_ids:
        return []
    matches = []
    for source in sources:
        label = str(source.label).lower()
        path = str(source.metadata.get("path") or source.metadata.get("source") or "").lower()
        source_id = str(source.source_id).upper()
        if source_id in requested_ids or any(item in label or item in path for item in lowered_terms):
            matches.append(source)
    return matches


def _where_chat_sources(sources, term: str):
    requested_ids = [item.upper() for item in re.findall(r"\bS\d+\b", term, flags=re.I)]
    if requested_ids:
        ordered = [source for source in sources if source.source_id.upper() in requested_ids]
        return ordered[:6]
    ranked = search_sources(sources, term, limit=6)
    if ranked:
        return ranked[:6]
    return _filter_chat_sources(sources, term)[:6]


def _renumber_cli_sources(sources):
    cloned = [copy.deepcopy(source) for source in sources]
    for index, source in enumerate(cloned, start=1):
        source.source_id = f"S{index}"
    return cloned


def _remember_chat_source_pool(bundle) -> None:
    if not hasattr(bundle, "_source_pool"):
        setattr(bundle, "_source_pool", [copy.deepcopy(source) for source in bundle.sources])


def _chat_source_pool(bundle):
    pool = getattr(bundle, "_source_pool", None)
    if pool:
        return pool
    _remember_chat_source_pool(bundle)
    return getattr(bundle, "_source_pool", bundle.sources)


def _handle_chat_tool(
    command: str,
    bundle,
    history: List[tuple[str, str]],
    session_path: Optional[Path],
    session_notes: Optional[List[str]] = None,
    citation_source_ids: Optional[List[str]] = None,
) -> object:
    parts = command.split(maxsplit=1)
    name = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""
    if name in {"/exit", "/quit"}:
        return False
    if name == "/sources":
        ui.section("Sources")
        ui.table(
            [["ID", "Label", "Role"]]
            + [
                [
                    source.source_id,
                    source.label,
                    str(source.metadata.get("role_label") or source.metadata.get("role") or ""),
                ]
                for source in bundle.sources[:40]
            ]
        )
        return True
    if name == "/find":
        if not arg:
            ui.status("warning", "Use /find followed by a word or phrase.")
            return True
        matches = search_sources(bundle.sources, arg)
        ui.section("Matches")
        if not matches:
            ui.write("No matching source chunks found.")
            return True
        ui.table([["ID", "Label", "Excerpt"]] + [[s.source_id, s.label, _excerpt_for_query(s.text, arg)] for s in matches])
        return True
    if name == "/where":
        if not arg:
            ui.status("warning", "Use /where followed by a file name, page, source id, or topic.")
            return True
        matches = _where_chat_sources(_chat_source_pool(bundle), arg)
        if not matches:
            ui.status("warning", "No matching source found.")
            return True
        bundle.sources = _renumber_cli_sources(matches)
        ui.status("success", f"Scoped to best match: {bundle.sources[0].label}")
        ui.section("Focused Match")
        ui.table([["ID", "Label", "Excerpt"]] + [[s.source_id, s.label, _excerpt_for_query(s.text, arg)] for s in matches[:3]])
        return {"bundle": bundle, "citation_source_ids": []}
    if name == "/scope":
        if not arg:
            ui.status("warning", "Use /scope followed by a file name, folder term, page, topic, or all.")
            return True
        if arg.strip().lower() in {"all", "*", "reset"}:
            bundle.sources = _renumber_cli_sources(_chat_source_pool(bundle))
            ui.status("success", f"Scope reset to all {len(bundle.sources)} source(s).")
            return {"bundle": bundle, "citation_source_ids": []}
        matches = _filter_chat_sources(_chat_source_pool(bundle), arg)
        if not matches:
            ui.status("warning", "No matching indexed sources found.")
            return True
        bundle.sources = _renumber_cli_sources(matches)
        ui.status("success", f"Scoped chat to {len(bundle.sources)} source(s).")
        return {"bundle": bundle, "citation_source_ids": []}
    if name == "/focus":
        if not arg:
            ui.status("warning", "Use /focus followed by a file name, folder term, topic, or source id.")
            return True
        if arg.strip().lower() in {"all", "*", "reset"}:
            bundle.sources = _renumber_cli_sources(_chat_source_pool(bundle))
            ui.status("success", f"Focus reset to all {len(bundle.sources)} source(s).")
            return {"bundle": bundle, "citation_source_ids": []}
        matches = _filter_chat_sources(_chat_source_pool(bundle), arg)
        if not matches:
            ui.status("warning", "No matching indexed sources found.")
            return True
        bundle.sources = _renumber_cli_sources(matches)
        ui.status("success", f"Focused chat to {len(bundle.sources)} source(s). History is still preserved.")
        return {"bundle": bundle, "citation_source_ids": []}
    if name == "/cite":
        if not arg:
            ui.status("warning", "Use /cite followed by one or more source ids, such as S1 S3.")
            return True
        requested = [item.upper() for item in re.findall(r"\bS\d+\b", arg, flags=re.I)]
        if not requested:
            ui.status("warning", "Use /cite with source ids like S1 or S2.")
            return True
        selected = [source.source_id.upper() for source in bundle.sources if source.source_id.upper() in requested]
        if not selected:
            ui.status("warning", "None of those source ids are available in the current scope.")
            return True
        if citation_source_ids is not None:
            citation_source_ids[:] = selected
        ui.status("success", "Pinned citations to: " + ", ".join(selected))
        return {"citation_source_ids": selected}
    if name == "/remember":
        if not arg:
            ui.status("warning", "Use /remember followed by a short note.")
            return True
        note = arg.strip()
        if session_notes is not None:
            session_notes.append(note)
            del session_notes[:-24]
            save_chat_history(session_path, history, notes=session_notes)
        ui.status("success", "Stored a run-local note for this chat.")
        return {"session_notes": session_notes or []}
    if name == "/sessions":
        sessions = list_chat_sessions()
        if not sessions:
            ui.status("info", "No saved conversations found.")
            return True
        rows = [["Title", "Path"]]
        for session in sessions[:12]:
            metadata = load_chat_metadata(session)
            title = str(metadata.get("title") or session.stem.replace("-", " ").strip().title())
            rows.append([title, str(session)])
        ui.section("Saved Conversations")
        ui.table(rows)
        return True
    if name == "/open":
        source = next((item for item in bundle.sources if item.source_id.lower() == arg.lower()), None)
        if source is None:
            ui.status("warning", "Use /open with a source id such as S1.")
            return True
        ui.section(f"{source.source_id}: {source.label}")
        ui.write(source.text[:3000].strip() or "[missing text]")
        return True
    if name == "/clear":
        history.clear()
        save_chat_history(session_path, history)
        ui.status("success", "Chat history cleared for this run.")
        return True
    if name == "/help":
        _show_chat_help()
        return True
    ui.status("warning", "Unknown chat tool. Try /help.")
    return True


def _show_chat_help() -> None:
    ui.section("Chat Tools")
    ui.table(
        [
            ["/attach PATH", "Attach a run folder, file, folder, or manifest"],
            ["/sessions", "List saved conversations"],
            ["/where TERM", "Jump to the best matching source by keyword, page, source id, file name, or topic"],
            ["/cite S1 S2", "Anchor answers to specific sources"],
            ["/scope TERM", "Narrow the active document target; use /scope all to reset"],
            ["/focus TERM", "Keep only matching sources; use /focus all to reset"],
            ["/remember NOTE", "Store a tiny run-local note"],
            ["/sources", "List indexed source chunks"],
            ["/find TERM", "Search source chunks locally"],
            ["/open S1", "Open a cited source excerpt"],
            ["/clear", "Clear saved chat history for this run"],
            ["/exit", "Leave chat"],
        ]
    )


def _excerpt_for_query(text: str, query: str, limit: int = 160) -> str:
    lower = text.lower()
    terms = [term for term in re.findall(r"[A-Za-z0-9][A-Za-z0-9-]{1,}", query.lower())]
    position = min((lower.find(term) for term in terms if lower.find(term) >= 0), default=0)
    start = max(position - 50, 0)
    excerpt = text[start : start + limit].replace("\n", " ").strip()
    return excerpt + ("..." if len(text) > start + limit else "")


def review_command(run_dir: Optional[str] = None):
    ui.heading("Akshara Vision", "Review Layout")
    if not run_dir:
        run_dir = ui.text("Path to Akshara run folder")
    if not run_dir:
        ui.status("info", "Review cancelled.")
        return None
    path = Path(run_dir).expanduser()
    manifest_path = path / "run_manifest.json"
    if not manifest_path.exists():
        ui.status("error", f"No run_manifest.json found in {path}")
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        ui.status("error", f"Could not read manifest: {exc}")
        return None

    metadata = manifest.get("metadata") if isinstance(manifest.get("metadata"), dict) else {}
    structure = (
        metadata.get("document_structure")
        if isinstance(metadata.get("document_structure"), dict)
        else {}
    )
    layout_profile = (
        structure.get("layout_profile") if isinstance(structure.get("layout_profile"), dict) else {}
    )
    layout_tree = structure.get("layout_tree") if isinstance(structure.get("layout_tree"), list) else []
    assets = _manifest_assets(metadata)
    low_confidence = _low_confidence_layout_blocks(layout_tree)
    table_blocks, chart_blocks = _special_layout_blocks(layout_tree)

    ui.section("Summary")
    ui.table(
        [
            ["Run folder", str(path)],
            ["Title", str(metadata.get("title") or "Untitled")],
            ["Document type", str(metadata.get("document_type") or manifest.get("document_type") or "")],
            ["Dominant flow", str(layout_profile.get("dominant_flow") or "unknown")],
            ["Columns", str(layout_profile.get("column_count_estimate") or "unknown")],
            ["Layout nodes", str(len(layout_tree))],
            ["Table blocks", str(len(table_blocks))],
            ["Chart blocks", str(len(chart_blocks))],
            ["Assets", str(len(assets))],
            ["Low-confidence blocks", str(len(low_confidence))],
        ]
    )
    notes = layout_profile.get("notes") if isinstance(layout_profile.get("notes"), list) else []
    if notes:
        ui.section("Layout Notes")
        ui.bullet_list([str(note) for note in notes])
    preview = _native_layout_previews(layout_tree)
    if preview:
        ui.section("Layout Preview")
        for block in preview[:3]:
            ui.write(block)
            ui.write("")
    if low_confidence:
        ui.section("Low-Confidence Blocks")
        ui.table([["Source", "Role", "Zone", "Confidence"]] + low_confidence[:12])
    if table_blocks:
        ui.section("Tabular Blocks")
        ui.table([["Source", "Role", "Zone", "Confidence"]] + table_blocks[:12])
    if chart_blocks:
        ui.section("Chart Blocks")
        ui.table([["Source", "Role", "Zone", "Confidence"]] + chart_blocks[:12])
    if assets:
        ui.section("Figure Assets")
        ui.table([["Label", "Zone", "Size", "Path"]] + _asset_review_rows(assets[:16]))
        ui.note("If an asset crop is wrong, delete that image file; later exports will skip it.")

    report_path = path / "layout_review.md"
    report_path.write_text(
        _layout_review_markdown(path, metadata, layout_profile, layout_tree, assets, low_confidence),
        encoding="utf-8",
    )
    ui.status("success", f"Saved review: {report_path}")
    _next_recommendations(_next_steps_for_context("review", run_dir=path))
    return report_path


def compare_command(run_dir: Optional[str] = None):
    ui.heading("Akshara Vision", "Compare")
    if not run_dir:
        run_dir = ui.text("Path to Akshara run folder")
    if not run_dir:
        ui.status("info", "Compare cancelled.")
        return None
    path = Path(run_dir).expanduser()
    path = _resolve_compare_run_dir(path)
    if path is None:
        ui.status(
            "error",
            "Point compare at a run folder or a compiled output file inside a run folder.",
        )
        return None
    manifest_path = path / "run_manifest.json"
    if not manifest_path.exists():
        ui.status("error", f"No run_manifest.json found in {path}")
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        ui.status("error", f"Could not read manifest: {exc}")
        return None
    metadata = manifest.get("metadata") if isinstance(manifest.get("metadata"), dict) else {}
    with ui.progress("Preparing comparison previews...") as reporter:
        comparisons = _build_compare_index(path, metadata)
        reporter.finish(f"Prepared {len(comparisons)} comparison preview(s)")
    if not comparisons:
        ui.status("warning", "No comparable source/output pairs were found.")
        return None
    report_path = path / "compare_review.html"
    report_path.write_text(
        _compare_review_html(path, metadata, comparisons),
        encoding="utf-8",
    )
    ui.status("success", f"Saved compare report: {report_path}")
    _next_recommendations(_next_steps_for_context("compare", run_dir=path))
    return report_path


def _build_compare_index(run_dir: Path, metadata: Dict[str, object]) -> List[Dict[str, object]]:
    return _compare_views(run_dir, metadata)


def _resolve_compare_run_dir(path: Path) -> Optional[Path]:
    candidates = [path]
    if path.is_file():
        candidates.insert(0, path.parent)
    candidates.extend(path.parents)
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.is_dir() and (candidate / "run_manifest.json").exists():
            return candidate
    return None


def _manifest_assets(metadata: Dict[str, object]) -> List[Dict[str, object]]:
    assets = metadata.get("assets") if isinstance(metadata.get("assets"), list) else []
    if assets:
        return [asset for asset in assets if isinstance(asset, dict)]
    collected: List[Dict[str, object]] = []
    restoration = metadata.get("restoration") if isinstance(metadata.get("restoration"), list) else []
    for record in restoration:
        if not isinstance(record, dict):
            continue
        chunks = record.get("chunks") if isinstance(record.get("chunks"), list) else []
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            for asset in chunk.get("assets") if isinstance(chunk.get("assets"), list) else []:
                if isinstance(asset, dict):
                    collected.append(asset)
    return collected


def _low_confidence_layout_blocks(layout_tree: List[object]) -> List[List[str]]:
    rows: List[List[str]] = []
    for node in layout_tree:
        if not isinstance(node, dict):
            continue
        native = node.get("native_layout") if isinstance(node.get("native_layout"), dict) else {}
        blocks = native.get("blocks") if isinstance(native.get("blocks"), list) else []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            try:
                confidence = float(block.get("confidence") or 1.0)
            except (TypeError, ValueError):
                confidence = 1.0
            if confidence >= 0.55:
                continue
            rows.append(
                [
                    str(node.get("source") or ""),
                    str(block.get("role") or "block"),
                    str(block.get("page_zone") or ""),
                    f"{confidence:.2f}",
                ]
            )
    return rows


def _special_layout_blocks(layout_tree: List[object]) -> tuple[List[List[str]], List[List[str]]]:
    tables: List[List[str]] = []
    charts: List[List[str]] = []
    for node in layout_tree:
        if not isinstance(node, dict):
            continue
        native = node.get("native_layout") if isinstance(node.get("native_layout"), dict) else {}
        blocks = native.get("blocks") if isinstance(native.get("blocks"), list) else []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            role = str(block.get("role") or "")
            confidence = float(block.get("confidence") or 0.0)
            row = [
                str(node.get("source") or ""),
                role,
                str(block.get("page_zone") or ""),
                f"{confidence:.2f}",
            ]
            if role == "table-region":
                tables.append(row)
            elif role == "chart-region":
                charts.append(row)
    return tables, charts


def _asset_review_rows(assets: List[Dict[str, object]]) -> List[List[str]]:
    rows = []
    for asset in assets:
        layout = asset.get("layout") if isinstance(asset.get("layout"), dict) else {}
        rows.append(
            [
                str(asset.get("label") or asset.get("kind") or "figure"),
                str(layout.get("page_zone") or ""),
                f"{asset.get('width') or '?'}x{asset.get('height') or '?'}",
                str(asset.get("path") or ""),
            ]
        )
    return rows


def _native_layout_previews(layout_tree: List[object]) -> List[str]:
    previews: List[str] = []
    for node in layout_tree:
        if not isinstance(node, dict):
            continue
        native = node.get("native_layout") if isinstance(node.get("native_layout"), dict) else {}
        blocks = native.get("blocks") if isinstance(native.get("blocks"), list) else []
        if not blocks:
            continue
        title = f"{node.get('source') or 'page'} | {node.get('role_label') or node.get('role') or 'body'}"
        preview = _render_native_layout_block_map(blocks)
        if preview:
            previews.append(f"{title}\n{preview}")
    return previews


def _render_native_layout_block_map(blocks: List[Dict[str, object]]) -> str:
    width = 38
    height = 14
    canvas = [[" " for _ in range(width)] for _ in range(height)]
    labels = []
    for index, block in enumerate(blocks[:10], start=1):
        bbox = block.get("relative_bbox") if isinstance(block.get("relative_bbox"), list) else None
        if not bbox or len(bbox) != 4:
            continue
        left = max(0, min(width - 1, int(float(bbox[0]) * width)))
        top = max(0, min(height - 1, int(float(bbox[1]) * height)))
        right = max(left + 1, min(width, int(float(bbox[2]) * width)))
        bottom = max(top + 1, min(height, int(float(bbox[3]) * height)))
        mark = _block_mark(block.get("role"))
        for y in range(top, bottom):
            for x in range(left, right):
                canvas[y][x] = mark
        labels.append(
            f"{index}. {block.get('role') or 'block'} @{block.get('page_zone') or 'zone'} "
            f"conf {float(block.get('confidence') or 0.0):.2f}"
        )
    lines = ["+" + "-" * width + "+"]
    for row in canvas:
        lines.append("|" + "".join(row) + "|")
    lines.append("+" + "-" * width + "+")
    if labels:
        lines.append("Blocks:")
        lines.extend(f"  - {label}" for label in labels[:10])
    return "\n".join(lines)


def _block_mark(role: object) -> str:
    role_text = str(role or "").lower()
    if "chart" in role_text:
        return "C"
    if "table" in role_text:
        return "B"
    if "figure" in role_text:
        return "F"
    if "header" in role_text or "footer" in role_text:
        return "H"
    if "page-number" in role_text or "small-mark" in role_text:
        return "N"
    return "T"


def _layout_review_markdown(
    run_dir: Path,
    metadata: Dict[str, object],
    layout_profile: Dict[str, object],
    layout_tree: List[object],
    assets: List[Dict[str, object]],
    low_confidence: List[List[str]],
) -> str:
    lines = [
        "# Layout Review",
        "",
        f"- Run folder: `{run_dir}`",
        f"- Title: {metadata.get('title') or 'Untitled'}",
        f"- Document type: {metadata.get('document_type') or ''}",
        f"- Dominant flow: {layout_profile.get('dominant_flow') or 'unknown'}",
        f"- Column estimate: {layout_profile.get('column_count_estimate') or 'unknown'}",
        f"- Layout nodes: {len(layout_tree)}",
        f"- Table blocks: {len(_special_layout_blocks(layout_tree)[0])}",
        f"- Chart blocks: {len(_special_layout_blocks(layout_tree)[1])}",
        f"- Assets: {len(assets)}",
        f"- Low-confidence blocks: {len(low_confidence)}",
        "",
    ]
    notes = layout_profile.get("notes") if isinstance(layout_profile.get("notes"), list) else []
    if notes:
        lines.extend(["## Notes", ""])
        lines.extend(f"- {note}" for note in notes)
        lines.append("")
    if low_confidence:
        lines.extend(["## Low-Confidence Blocks", "", "| Source | Role | Zone | Confidence |", "| --- | --- | --- | --- |"])
        lines.extend(f"| {row[0]} | {row[1]} | {row[2]} | {row[3]} |" for row in low_confidence[:60])
        lines.append("")
    if assets:
        lines.extend(["## Assets", "", "| Label | Zone | Size | Path |", "| --- | --- | --- | --- |"])
        for row in _asset_review_rows(assets):
            lines.append(f"| {row[0]} | {row[1]} | {row[2]} | `{row[3]}` |")
        lines.append("")
    lines.append("Reviewer note: deleting a wrong asset image is safe; exporters skip missing assets.")
    return "\n".join(lines).strip() + "\n"


def _compare_views(run_dir: Path, metadata: Dict[str, object]) -> List[Dict[str, object]]:
    items_root = run_dir / "items"
    sources_root = run_dir / "sources"
    restoration = metadata.get("restoration") if isinstance(metadata.get("restoration"), list) else []
    manifest_comparisons = _compare_views_from_manifest(run_dir, items_root, sources_root, restoration)
    if manifest_comparisons:
        return manifest_comparisons
    if not items_root.exists():
        return []
    output_files = _preferred_compare_outputs(items_root)
    comparisons: List[Dict[str, object]] = []
    for index, output_path in enumerate(output_files, start=1):
        item_dir = output_path.parent
        source_path = _match_source_path(items_root, sources_root, item_dir)
        cache_key = item_dir.name
        record = restoration[index - 1] if index - 1 < len(restoration) else {}
        assets = _compare_assets(record)
        layout_tree = _compare_layout_nodes(record, index, metadata)
        page_number = _compare_page_number(item_dir, record, index)
        media_path = _record_media_path(record)
        source_html = _source_preview_html(
            source_path,
            run_dir,
            page_number=page_number,
            media_path=media_path,
            cache_key=cache_key,
        )
        layout_html = _compare_layout_overlay_html(
            source_path,
            layout_tree,
            run_dir,
            page_number=page_number,
            media_path=media_path,
            cache_key=cache_key,
        )
        if layout_html:
            source_html = layout_html
        comparisons.append(
            {
                "label": str(item_dir.relative_to(items_root)).replace("\\", "/"),
                "source_path": source_path,
                "source_html": source_html,
                "output_path": output_path,
                "output_html": _output_preview_html(
                    output_path, run_dir, page_number=page_number, cache_key=cache_key,
                    native_layout=record.get("native_layout") if isinstance(record, dict) else None
                ),
                "layout_html": layout_html,
                "assets_html": _compare_assets_html(assets, run_dir),
            }
        )
    return comparisons


def _compare_views_from_manifest(
    run_dir: Path,
    items_root: Path,
    sources_root: Path,
    restoration: List[object],
) -> List[Dict[str, object]]:
    comparisons: List[Dict[str, object]] = []
    for source_index, record in enumerate(restoration, start=1):
        if not isinstance(record, dict):
            continue
        chunks = record.get("chunks") if isinstance(record.get("chunks"), list) else []
        if not chunks:
            continue
        label = str(record.get("label") or record.get("source") or f"source-{source_index}")
        item_dir = _record_item_dir(items_root, source_index, label)
        source_path = _source_path_for_record(record, run_dir, sources_root, item_dir)
        cache_key = item_dir.name if item_dir is not None else label
        multi_page = len(chunks) > 1 or (source_path is not None and source_path.suffix.lower() == ".pdf")
        for fallback_page, chunk in enumerate(chunks, start=1):
            if not isinstance(chunk, dict):
                continue
            page_number = _chunk_page_number(chunk, fallback_page)
            if not multi_page and not _chunk_has_compare_material(chunk):
                continue
            chunk_label = f"{label} · page {page_number}"
            if multi_page:
                chunk_label += f" / {len(chunks)}"
            layout_tree = [_compare_layout_node_from_chunk(chunk, page_number)]
            media_path = _record_media_path(record) or str(chunk.get("media_path") or "").strip()
            source_html = _source_preview_html(
                source_path,
                run_dir,
                page_number=page_number,
                media_path=media_path,
                cache_key=cache_key,
            )
            layout_html = _compare_layout_overlay_html(
                source_path,
                layout_tree,
                run_dir,
                page_number=page_number,
                media_path=media_path,
                cache_key=cache_key,
            )
            if layout_html:
                source_html = layout_html
            comparisons.append(
                {
                    "label": chunk_label,
                    "source_path": source_path,
                    "source_html": source_html,
                    "output_path": None,
                    "output_html": _chunk_output_preview_html(chunk, chunk.get("native_layout")),
                    "layout_html": layout_html,
                    "assets_html": _compare_assets_html(_chunk_assets(chunk), run_dir),
                }
            )
    return comparisons


def _record_item_dir(items_root: Path, source_index: int, label: str) -> Optional[Path]:
    if not items_root.exists():
        return None
    prefix = f"{source_index:04d}-"
    direct = sorted(path for path in items_root.rglob(f"{prefix}*") if path.is_dir())
    if direct:
        return direct[0]
    label_stem = Path(str(label).replace("\\", "/")).stem.lower()
    if label_stem:
        matching = sorted(
            path for path in items_root.rglob("*") if path.is_dir() and label_stem in path.name.lower()
        )
        if matching:
            return matching[0]
    return None


def _source_path_for_record(
    record: Dict[str, object],
    run_dir: Path,
    sources_root: Path,
    item_dir: Optional[Path],
) -> Optional[Path]:
    source = str(record.get("source") or "").strip()
    if source:
        candidate = Path(source).expanduser()
        if candidate.exists():
            return candidate
    if item_dir is not None:
        matched = _match_source_path(run_dir / "items", sources_root, item_dir)
        if matched is not None:
            return matched
    label = str(record.get("label") or source or "").strip()
    label_stem = Path(label.replace("\\", "/")).stem.lower()
    candidates = sorted(path for path in sources_root.rglob("*") if path.is_file()) if sources_root.exists() else []
    if label_stem:
        for candidate in candidates:
            if label_stem in candidate.stem.lower():
                return candidate
    return candidates[0] if len(candidates) == 1 else None


def _chunk_page_number(chunk: Dict[str, object], fallback: int) -> int:
    for key in ("page_number", "page", "index"):
        value = chunk.get(key)
        if value not in (None, "", []):
            try:
                return int(str(value).strip())
            except (TypeError, ValueError):
                continue
    return fallback


def _chunk_has_compare_material(chunk: Dict[str, object]) -> bool:
    return bool(
        str(chunk.get("restored_text") or "").strip()
        or chunk.get("assets")
        or chunk.get("native_layout")
    )


def _compare_layout_node_from_chunk(chunk: Dict[str, object], page_number: int) -> Dict[str, object]:
    native = chunk.get("native_layout") if isinstance(chunk.get("native_layout"), dict) else {}
    return {
        "reading_order": page_number,
        "page_number": page_number,
        "role": str(chunk.get("role") or chunk.get("status") or "body"),
        "native_layout": native,
    }


def _chunk_assets(chunk: Dict[str, object]) -> List[Dict[str, object]]:
    assets = chunk.get("assets") if isinstance(chunk.get("assets"), list) else []
    return [asset for asset in assets if isinstance(asset, dict)]


def _csv_to_html_table(csv_text: str) -> str:
    """Parse a CSV block and return an HTML table string."""
    import csv as _csv
    import io
    rows = list(_csv.reader(io.StringIO(csv_text.strip())))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return ""
    html_parts = ['<table class="extracted-table">']
    html_parts.append("<thead><tr>")
    for cell in rows[0]:
        html_parts.append(f"<th>{html.escape(cell.strip())}</th>")
    html_parts.append("</tr></thead><tbody>")
    for row in rows[1:]:
        html_parts.append("<tr>")
        for cell in row:
            html_parts.append(f"<td>{html.escape(cell.strip())}</td>")
        html_parts.append("</tr>")
    html_parts.append("</tbody></table>")
    return "".join(html_parts)


def _table_text_to_html(text: str) -> str:
    """Convert a table text (CSV fenced block or Markdown pipe table) to HTML."""
    # Check for ```csv ... ``` fence
    m = re.search(r"```csv\s*\n(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if m:
        return _csv_to_html_table(m.group(1))
    # Fallback: pipe-delimited Markdown table
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    rows = []
    for line in lines:
        if "|" not in line:
            continue
        if line.startswith("|") and line.endswith("|"):
            cells = [c.strip() for c in line[1:-1].split("|")]
        else:
            cells = [c.strip() for c in line.split("|")]
        if all(set(c) <= {'-', ':', ' '} for c in cells):
            continue
        rows.append(cells)
    if not rows:
        return ""
    html_parts = ['<table class="extracted-table">']
    html_parts.append("<thead><tr>")
    for cell in rows[0]:
        html_parts.append(f"<th>{html.escape(cell)}</th>")
    html_parts.append("</tr></thead><tbody>")
    for row in rows[1:]:
        html_parts.append("<tr>")
        for cell in row:
            html_parts.append(f"<td>{html.escape(cell)}</td>")
        html_parts.append("</tr>")
    html_parts.append("</tbody></table>")
    return "".join(html_parts)


def _per_block_output_html(native_layout: Dict[str, object]) -> str:
    blocks = native_layout.get("blocks") if isinstance(native_layout, dict) else None
    if not isinstance(blocks, list) or not blocks:
        return ""
    has_any_text = any(str(b.get("extracted_text") or "").strip() for b in blocks if isinstance(b, dict))
    if not has_any_text:
        return ""

    role_colors = {
        "title-region": "#3559d1", "text-region": "#2a8f5b",
        "table-region": "#d98312", "chart-region": "#9a54d6",
        "figure-region": "#c03b3b", "running-header-or-footer": "#5f6c7b",
        "caption-region": "#7a6955", "list-region": "#1a8a7a",
        "equation-region": "#5346a0", "page-number-region": "#8f6d18",
        "separator-region": "#999999",
    }
    role_labels = {
        "title-region": "Title", "text-region": "Paragraph",
        "table-region": "Table", "chart-region": "Chart",
        "figure-region": "Image", "running-header-or-footer": "Header/Footer",
        "caption-region": "Caption", "list-region": "List",
        "equation-region": "Equation", "page-number-region": "Page No.",
        "separator-region": "Separator",
    }

    parts = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        text = str(b.get("extracted_text") or "").strip()
        if not text:
            continue
        order = b.get("order", 0)
        role = str(b.get("role") or "text-region")
        color = role_colors.get(role, "#314e86")
        label = role_labels.get(role, "Block")

        content_html = ""
        if role == "table-region":
            table_html = _table_text_to_html(text)
            if table_html:
                content_html = table_html
        if not content_html:
            content_html = f'<pre class="block-text">{html.escape(text)}</pre>'

        parts.append(
            f'<div class="block-card">'
            f'<div class="block-card-tag" style="color:{color};">{order}. {html.escape(label)}</div>'
            f'{content_html}'
            f'</div>'
        )
    if not parts:
        return ""
    return '<div class="blocks-output">' + "".join(parts) + '</div>'


def _chunk_output_preview_html(chunk: Dict[str, object], native_layout: Optional[Dict[str, object]] = None) -> str:
    if native_layout:
        per_block = _per_block_output_html(native_layout)
        if per_block:
            return per_block
    text = str(
        chunk.get("translated_text")
        or chunk.get("final_text")
        or chunk.get("restored_text")
        or ""
    ).strip()
    if not text:
        reason = str(chunk.get("failure_reason") or chunk.get("status") or "blank page").strip()
        return f'<p class="missing">{html.escape(reason or "blank page")}</p>'
    role = str(chunk.get("role") or chunk.get("status") or "body").replace("-", " ").title()
    status = str(chunk.get("status") or "").strip()
    meta = " · ".join(part for part in [role, status] if part)
    return (
        '<article class="page-output">'
        f'<div class="page-output-meta">{html.escape(meta)}</div>'
        f'<pre>{html.escape(text)}</pre>'
        "</article>"
    )


def _preferred_compare_outputs(items_root: Path) -> List[Path]:
    grouped: Dict[Path, List[Path]] = {}
    for pattern in (
        "final__*.html",
        "translated__*.html",
        "restored__*.html",
        "final__*.md",
        "translated__*.md",
        "restored__*.md",
        "akshara_output.md",
        "akshara_output.txt",
        "akshara_output.json",
        "final__*.txt",
        "translated__*.txt",
        "restored__*.txt",
        "final__*.json",
        "translated__*.json",
        "restored__*.json",
        "akshara_output.html",
        "akshara_output.pdf",
    ):
        for path in items_root.rglob(pattern):
            if path.is_file():
                grouped.setdefault(path.parent, []).append(path)
    return [
        _best_compare_output(paths)
        for _folder, paths in sorted(grouped.items(), key=lambda item: str(item[0]))
        if paths
    ]


def _best_compare_output(paths: List[Path]) -> Path:
    def score(path: Path) -> tuple[int, str]:
        name = path.name.lower()
        suffix = path.suffix.lower()
        stage_score = 0
        if name.startswith("final__") or name == "akshara_output.html":
            stage_score = 0
        elif name.startswith("translated__"):
            stage_score = 1
        elif name.startswith("restored__"):
            stage_score = 2
        else:
            stage_score = 3
        suffix_score = {
            ".html": 0,
            ".htm": 0,
            ".xhtml": 0,
            ".md": 1,
            ".txt": 2,
            ".pdf": 3,
            ".json": 4,
        }.get(suffix, 9)
        return stage_score * 10 + suffix_score, name

    return sorted(paths, key=score)[0]


def _match_source_path(items_root: Path, sources_root: Path, item_dir: Path) -> Optional[Path]:
    if not sources_root.exists():
        return None
    try:
        relative_dir = item_dir.relative_to(items_root)
    except ValueError:
        return None
    candidate_dir = sources_root / relative_dir.parent
    if not candidate_dir.exists():
        candidate_dir = sources_root
    stem = item_dir.name
    prefix = stem.split("-", 1)[0]
    candidates = []
    for pattern in (f"{stem}*", f"{prefix}*"):
        candidates.extend(path for path in candidate_dir.glob(pattern) if path.is_file())
    if candidates:
        return sorted(candidates)[0]
    return None


def _compare_assets(record: object) -> List[Dict[str, object]]:
    if not isinstance(record, dict):
        return []
    chunks = record.get("chunks") if isinstance(record.get("chunks"), list) else []
    assets: List[Dict[str, object]] = []
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        for asset in chunk.get("assets") or []:
            if isinstance(asset, dict):
                assets.append(asset)
    return assets


def _record_media_path(record: object) -> str:
    if not isinstance(record, dict):
        return ""
    media_path = str(record.get("media_path") or record.get("image_path") or "").strip()
    if media_path:
        return media_path
    chunks = record.get("chunks") if isinstance(record.get("chunks"), list) else []
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        chunk_media = str(chunk.get("media_path") or chunk.get("image_path") or "").strip()
        if chunk_media:
            return chunk_media
    return ""


def _compare_layout_nodes(
    record: object, index: int, metadata: Dict[str, object]
) -> List[Dict[str, object]]:
    if isinstance(record, dict):
        existing = record.get("layout_tree")
        if isinstance(existing, list):
            return [node for node in existing if isinstance(node, dict)]
        chunks = record.get("chunks")
        if isinstance(chunks, list):
            nodes = []
            for order, chunk in enumerate(chunks, start=1):
                if not isinstance(chunk, dict):
                    continue
                native = chunk.get("native_layout") if isinstance(chunk.get("native_layout"), dict) else {}
                if not native:
                    continue
                nodes.append(
                    {
                        "reading_order": order,
                        "page_number": int(chunk.get("index") or chunk.get("page_number") or order),
                        "role": str(chunk.get("role") or chunk.get("status") or "body"),
                        "native_layout": native,
                    }
                )
            if nodes:
                return nodes
    structure = metadata.get("document_structure") if isinstance(metadata.get("document_structure"), dict) else {}
    tree = structure.get("layout_tree") if isinstance(structure.get("layout_tree"), list) else []
    nodes = []
    for node in tree:
        if not isinstance(node, dict):
            continue
        try:
            reading_order = int(node.get("reading_order") or 0)
        except (TypeError, ValueError):
            reading_order = 0
        if reading_order == index:
            nodes.append(node)
    return nodes


def _source_preview_html(
    source_path: Optional[Path],
    run_dir: Path,
    page_number: Optional[int] = None,
    media_path: Optional[str] = None,
    cache_key: Optional[str] = None,
) -> str:
    if source_path is None:
        return '<p class="missing">Source file not found.</p>'
    suffix = source_path.suffix.lower()
    preview_path = _compare_visual_preview_path(
        source_path,
        run_dir,
        media_path=media_path,
        page_number=page_number,
        cache_key=cache_key,
    )
    if preview_path is not None:
        try:
            rel = preview_path.resolve().relative_to(run_dir.resolve())
        except Exception:
            rel = preview_path.name
        return f'<figure class="preview-image"><img src="{html.escape(str(rel), quote=True)}" alt="" /></figure>'
    try:
        rel = source_path.resolve().relative_to(run_dir.resolve())
    except Exception:
        rel = source_path.name
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}:
        return (
            f'<figure class="preview-image"><img src="{html.escape(str(rel), quote=True)}" '
            f'alt="" /></figure>'
        )
    if suffix == ".pdf":
        if page_number and (
            preview := _cached_pdf_page_preview_html(
                source_path, run_dir, page_number, media_path, cache_key=cache_key
            )
        ):
            return preview
        return (
            f'<object data="{html.escape(str(rel), quote=True)}" type="application/pdf" '
            f'class="preview-pdf"><p class="missing">{html.escape(source_path.name)}</p></object>'
        )
    if suffix in {".html", ".htm", ".xhtml"}:
        return (
            f'<iframe src="{html.escape(str(rel), quote=True)}" '
            f'class="preview-frame" title="{html.escape(source_path.name, quote=True)}"></iframe>'
        )
    if suffix in {".txt", ".md", ".html", ".hocr", ".xml", ".json", ".jsonl", ".yaml", ".yml"}:
        try:
            content = source_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return f'<p class="missing">{html.escape(source_path.name)}</p>'
        if suffix == ".html":
            content = re.sub(r"<[^>]+>", "", content)
        return f"<pre>{html.escape(content)}</pre>"
    return f'<p class="missing">{html.escape(source_path.name)}</p>'


def _output_preview_html(
    output_path: Path,
    run_dir: Path,
    page_number: Optional[int] = None,
    cache_key: Optional[str] = None,
    native_layout: Optional[Dict[str, object]] = None,
) -> str:
    if native_layout:
        per_block = _per_block_output_html(native_layout)
        if per_block:
            return per_block
    try:
        rel = output_path.resolve().relative_to(run_dir.resolve())
    except Exception:
        rel = output_path.name
    suffix = output_path.suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}:
        return (
            f'<figure class="preview-image"><img src="{html.escape(str(rel), quote=True)}" '
            f'alt="" /></figure>'
        )
    if suffix == ".pdf":
        if page_number and (
            preview := _cached_pdf_page_preview_html(
                output_path, run_dir, page_number, cache_key=cache_key
            )
        ):
            return preview
        return (
            f'<object data="{html.escape(str(rel), quote=True)}" type="application/pdf" '
            f'class="preview-pdf"><p class="missing">{html.escape(output_path.name)}</p></object>'
        )
    if suffix in {".html", ".htm", ".xhtml"}:
        return (
            f'<iframe src="{html.escape(str(rel), quote=True)}" '
            f'class="preview-frame" title="{html.escape(output_path.name, quote=True)}"></iframe>'
        )
    try:
        content = output_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return f'<p class="missing">{html.escape(output_path.name)}</p>'
    return f"<pre>{html.escape(content)}</pre>"


def _compare_assets_html(assets: List[Dict[str, object]], run_dir: Path) -> str:
    if not assets:
        return ""
    parts = ['<div class="asset-grid">']
    for asset in assets[:12]:
        source = _asset_source_from_metadata(asset, run_dir)
        if source and source.exists():
            try:
                rel = source.resolve().relative_to(run_dir.resolve())
            except Exception:
                rel = source.name
            parts.append(
                f'<figure class="preview-image"><img src="{html.escape(str(rel), quote=True)}" alt="" /></figure>'
            )
    parts.append("</div>")
    return "\n".join(parts)


def _compare_layout_overlay_html(
    source_path: Optional[Path],
    layout_tree: List[object],
    run_dir: Path,
    page_number: Optional[int] = None,
    media_path: Optional[str] = None,
    cache_key: Optional[str] = None,
) -> str:
    if source_path is None:
        return ""
    blocks = _compare_layout_blocks(layout_tree)
    if not blocks:
        return ""
    preview_path = _compare_visual_preview_path(
        source_path,
        run_dir,
        media_path=media_path,
        page_number=page_number,
        cache_key=cache_key,
    )
    if preview_path is None:
        image_src = _compare_image_src(
            source_path,
            run_dir,
            page_number=page_number,
            media_path=media_path,
            cache_key=cache_key,
        )
    else:
        try:
            image_src = html.escape(str(preview_path.resolve().relative_to(run_dir.resolve())), quote=True)
        except Exception:
            image_src = html.escape(preview_path.name, quote=True)
    if not image_src:
        return ""
    overlay = []
    for block in blocks:
        left, top, width, height = block["box"]
        color = block["color"]
        num = html.escape(block["label"])
        overlay.append(
            f'<div class="overlay-block" style="left:{left}%;top:{top}%;width:{width}%;height:{height}%;'
            f'border-color:{color};background:{color}08;">'
            f'<span class="block-num" style="background:{color};">{num}</span></div>'
        )

    # Build color legend
    seen_roles = {}
    for block in blocks:
        r = block.get("role", "text-region")
        if r not in seen_roles:
            seen_roles[r] = (block["color"], block.get("short_name", "Block"))
    legend_items = "".join(
        f'<span class="legend-item"><span class="legend-dot" style="background:{c};"></span>{html.escape(name)}</span>'
        for c, name in seen_roles.values()
    )
    legend_html = f'<div class="layout-legend">{legend_items}<span class="legend-count">{len(blocks)} blocks detected</span></div>'

    return (
        '<figure class="overlay-figure">'
        f'<img src="{image_src}" alt="" />'
        '<div class="overlay-layer">'
        + "".join(overlay)
        + "</div></figure>"
        + legend_html
    )


def _compare_image_src(
    source_path: Path,
    run_dir: Path,
    page_number: Optional[int] = None,
    media_path: Optional[str] = None,
    cache_key: Optional[str] = None,
) -> str:
    if media_path:
        preview = _compare_visual_preview_path(
            source_path,
            run_dir,
            media_path=media_path,
            page_number=page_number,
            cache_key=cache_key,
        )
        if preview is not None:
            try:
                rel = preview.resolve().relative_to(run_dir.resolve())
            except Exception:
                rel = preview.name
            return html.escape(str(rel), quote=True)
    suffix = source_path.suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}:
        try:
            rel = source_path.resolve().relative_to(run_dir.resolve())
        except Exception:
            rel = source_path.name
        return html.escape(str(rel), quote=True)
    if suffix == ".pdf" and page_number:
        preview = _cached_pdf_page_preview_html(
            source_path, run_dir, page_number, media_path, cache_key=cache_key
        )
        if preview:
            match = re.search(r'src="([^"]+)"', preview)
            if match:
                return match.group(1)
    return ""


def _cached_pdf_page_preview_html(
    path: Path,
    run_dir: Path,
    page_number: int,
    media_path: Optional[str] = None,
    cache_key: Optional[str] = None,
) -> str:
    if media_path:
        media = Path(media_path).expanduser()
        if media.exists():
            try:
                rel = media.resolve().relative_to(run_dir.resolve())
            except Exception:
                rel = media.name
            return f'<figure class="preview-image"><img src="{html.escape(str(rel), quote=True)}" alt="" /></figure>'
    cache_root = run_dir / "stages" / "rendered_pages"
    if cache_root.exists():
        candidates = []
        if cache_key:
            direct = cache_root / cache_key / f"page-{page_number:04d}.png"
            if direct.exists():
                candidates.append(direct)
        if not candidates:
            page_slug = f"page-{page_number:04d}.png"
            stem = _slugify(path.stem)
            label = _slugify(path.name)
            candidates = sorted(
                candidate
                for candidate in cache_root.rglob(page_slug)
                if candidate.is_file()
                and (
                    stem in candidate.parent.name.lower()
                    or label in candidate.parent.name.lower()
                    or candidate.parent.name.lower().startswith("page")
                )
            )
        if candidates:
            candidate = candidates[0]
            try:
                rel = candidate.resolve().relative_to(run_dir.resolve())
            except Exception:
                rel = candidate.name
            return f'<figure class="preview-image"><img src="{html.escape(str(rel), quote=True)}" alt="" /></figure>'
    preview = _pdf_page_preview_html(path, page_number)
    return preview


def _compare_visual_preview_path(
    source_path: Optional[Path],
    run_dir: Path,
    media_path: Optional[str] = None,
    page_number: Optional[int] = None,
    cache_key: Optional[str] = None,
) -> Optional[Path]:
    image_suffixes = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
    compare_root = run_dir / "stages" / "compare_previews"
    def safe_key(value: object) -> str:
        text = re.sub(r"[^a-zA-Z0-9]+", "-", str(value or "").strip().lower())
        text = re.sub(r"-+", "-", text).strip("-")
        return text or "item"

    if media_path:
        media = Path(media_path).expanduser()
        if media.exists() and media.suffix.lower() in image_suffixes:
            try:
                media.resolve().relative_to(run_dir.resolve())
                return media.resolve()
            except Exception:
                target_dir = compare_root / safe_key(cache_key or (source_path.stem if source_path else media.stem))
                target_dir.mkdir(parents=True, exist_ok=True)
                target = target_dir / media.name
                if not target.exists():
                    try:
                        shutil.copy2(media, target)
                    except OSError:
                        return None
                return target
    if source_path is None:
        return None
    if source_path.suffix.lower() in image_suffixes and source_path.exists():
        try:
            if source_path.is_relative_to(run_dir):
                return source_path.resolve()
        except AttributeError:
            try:
                source_path.resolve().relative_to(run_dir.resolve())
                return source_path.resolve()
            except Exception:
                pass
        safe_key_value = safe_key(cache_key or source_path.stem)
        target_dir = compare_root / safe_key_value
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / source_path.name
        if not target.exists():
            try:
                shutil.copy2(source_path, target)
            except OSError:
                return None
        return target
    if source_path.suffix.lower() == ".pdf" and page_number is not None:
        cache_root = run_dir / "stages" / "rendered_pages"
        if cache_root.exists():
            if cache_key:
                direct = cache_root / cache_key / f"page-{page_number:04d}.png"
                if direct.exists():
                    return direct
            matches = sorted(cache_root.rglob(f"page-{page_number:04d}.png"))
            if matches:
                return matches[0]
    return None


def _pdf_page_preview_html(path: Path, page_number: int) -> str:
    pdftoppm_exe = find_executable("pdftoppm")
    if not pdftoppm_exe:
        return ""
    try:
        with tempfile.TemporaryDirectory(prefix="akshara-compare-pdf-") as temp_root:
            rendered = _render_pdf_page(pdftoppm_exe, path, Path(temp_root), page_number, 180)
            if not rendered or not rendered.exists():
                return ""
            data = rendered.read_bytes()
    except Exception:
        return ""
    mime = "image/png"
    encoded = base64.b64encode(data).decode("ascii")
    return f'<figure class="preview-image"><img src="data:{mime};base64,{encoded}" alt="" /></figure>'


def _compare_page_number(item_dir: Path, record: object, index: int) -> int:
    if isinstance(record, dict):
        chunks = record.get("chunks") if isinstance(record.get("chunks"), list) else []
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            for key in ("page_number", "page", "index"):
                value = chunk.get(key)
                if value not in (None, "", []):
                    try:
                        return int(str(value).strip())
                    except (TypeError, ValueError):
                        continue
    match = re.search(r"(?:page[-_ ]?)?(\d{1,5})", item_dir.name, flags=re.IGNORECASE)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            pass
    return index


def _compare_layout_blocks(layout_tree: List[object]) -> List[Dict[str, object]]:
    blocks: List[Dict[str, object]] = []
    for node in layout_tree:
        if not isinstance(node, dict):
            continue
        native = node.get("native_layout") if isinstance(node.get("native_layout"), dict) else {}
        source = native.get("blocks") if isinstance(native.get("blocks"), list) else []
        page = node.get("page_number") or node.get("reading_order") or node.get("index") or 0
        for block in source:
            if not isinstance(block, dict):
                continue
            bbox = block.get("relative_bbox")
            if not (isinstance(bbox, list) and len(bbox) == 4):
                continue
            role = str(block.get("role") or "text-region")
            short_label = {
                "title-region": "Title",
                "text-region": "Text",
                "table-region": "Table",
                "chart-region": "Chart",
                "figure-region": "Figure",
                "running-header-or-footer": "Header/Footer",
                "small-mark-or-page-number": "Pg No.",
                "caption-region": "Caption",
                "list-region": "List",
                "equation-region": "Equation",
                "page-number-region": "Pg No.",
                "separator-region": "Separator",
            }.get(role, "Block")
            order = block.get("order", 0)
            label = str(order)
            color = {
                "title-region": "#3559d1",
                "text-region": "#2a8f5b",
                "table-region": "#d98312",
                "chart-region": "#9a54d6",
                "figure-region": "#c03b3b",
                "running-header-or-footer": "#5f6c7b",
                "small-mark-or-page-number": "#8f6d18",
                "caption-region": "#7a6955",
                "list-region": "#1a8a7a",
                "equation-region": "#5346a0",
                "page-number-region": "#8f6d18",
                "separator-region": "#999999",
            }.get(role, "#314e86")
            blocks.append(
                {
                    "box": [
                        max(0.0, min(float(bbox[0]) * 100.0, 100.0)),
                        max(0.0, min(float(bbox[1]) * 100.0, 100.0)),
                        max(0.0, min((float(bbox[2]) - float(bbox[0])) * 100.0, 100.0)),
                        max(0.0, min((float(bbox[3]) - float(bbox[1])) * 100.0, 100.0)),
                    ],
                    "label": label,
                    "color": color,
                    "role": role,
                    "short_name": short_label,
                    "extracted_text": str(block.get("extracted_text") or ""),
                }
            )
    return blocks[:120]


def _asset_source_from_metadata(asset: Dict[str, object], run_dir: Path) -> Optional[Path]:
    path = str(asset.get("path") or "").strip()
    if not path:
        return None
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = run_dir / candidate
    return candidate


def _compare_review_html(run_dir: Path, metadata: Dict[str, object], comparisons: List[Dict[str, object]]) -> str:
    title = html.escape(str(metadata.get("title") or run_dir.name))
    cards = []
    for item in comparisons:
        source_html = str(item["source_html"])
        layout_html = str(item["layout_html"])
        if layout_html and layout_html == source_html:
            layout_html = ""
        cards.append(
            f'''
            <section class="compare-card">
              <header>
                <h2>{html.escape(str(item["label"]))}</h2>
              </header>
              <div class="compare-grid">
                <div class="panel">
                  <h3>Source</h3>
                  {source_html}
                  {layout_html}
                </div>
                <div class="panel">
                  <h3>Output</h3>
                  {item["output_html"]}
                  {item["assets_html"]}
                </div>
              </div>
            </section>
            '''
        )
    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>Compare - {title}</title>\n"
        "<style>"
        "@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');"
        "*{box-sizing:border-box;}"
        "body{margin:0;padding:2rem;background:#0f1117;color:#e8e0d0;font-family:'Inter',system-ui,sans-serif;line-height:1.6;min-height:100vh;}"
        "main{max-width:1400px;margin:0 auto;}"
        "h1{font-size:1.8rem;font-weight:700;text-align:center;margin:0 0 0.5rem;letter-spacing:-0.02em;color:#f0e8d8;}"
        ".summary{text-align:center;margin:0 0 2rem;font-size:0.82rem;color:#888;}"
        ".compare-card{background:#1a1d27;border:1px solid #2a2d3a;margin:0 0 1.5rem;padding:1.25rem;border-radius:10px;}"
        ".compare-card header h2{margin:0 0 1rem;font-size:1rem;font-weight:600;color:#c8bfa8;letter-spacing:0.01em;}"
        ".compare-grid{display:grid;grid-template-columns:minmax(320px,1fr) minmax(320px,1fr);gap:1.25rem;align-items:start;}"
        ".panel{border:1px solid #2a2d3a;padding:1rem;background:#141720;min-width:0;height:clamp(480px,76vh,920px);overflow:auto;border-radius:6px;}"
        ".panel h3{margin:0 0 0.75rem;font-size:0.72rem;font-weight:700;text-transform:uppercase;letter-spacing:0.1em;color:#666;}"
        ".panel pre{white-space:pre-wrap;word-break:break-word;margin:0;font-size:0.9rem;font-family:'JetBrains Mono',monospace;color:#d4cbb8;line-height:1.6;}"
        ".page-output{border-left:3px solid #c77a24;padding-left:1rem;background:#1c1a14;border-radius:0 4px 4px 0;padding:0.75rem 1rem;}"
        ".page-output-meta{font-size:0.72rem;text-transform:uppercase;letter-spacing:0.1em;color:#7a6a4a;margin:0 0 0.5rem;font-weight:600;}"
        ".preview-image img{max-width:100%;height:auto;display:block;}"
        ".preview-image{margin:0;}"
        ".overlay-figure{position:relative;margin:0;display:block;width:100%;}"
        ".overlay-figure img{display:block;width:100%;height:auto;}"
        ".overlay-layer{position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none;}"
        ".overlay-block{position:absolute;border:1.5px solid;border-radius:2px;box-sizing:border-box;}"
        ".overlay-block .block-num{position:absolute;left:2px;top:2px;display:flex;align-items:center;justify-content:center;min-width:15px;height:15px;padding:0 3px;font-size:8px;font-weight:700;color:#fff;border-radius:8px;line-height:1;font-family:'Inter',sans-serif;box-shadow:0 1px 3px rgba(0,0,0,.5);}"
        ".preview-frame,.preview-pdf{width:100%;min-height:520px;border:1px solid #2a2d3a;background:#141720;}"
        ".asset-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:0.75rem;margin-top:0.75rem;}"
        ".missing{opacity:0.5;font-style:italic;color:#888;}"
        ".layout-legend{display:flex;flex-wrap:wrap;gap:0.5rem;align-items:center;padding:0.5rem 0;font-size:0.72rem;font-family:'Inter',sans-serif;border-top:1px solid #2a2d3a;margin-top:0.5rem;}"
        ".legend-item{display:inline-flex;align-items:center;gap:0.25rem;color:#999;}"
        ".legend-dot{width:8px;height:8px;border-radius:50%;display:inline-block;flex-shrink:0;}"
        ".legend-count{margin-left:auto;font-weight:600;color:#666;font-size:0.7rem;}"
        ".blocks-output{display:flex;flex-direction:column;}"
        ".block-card{padding:0.65rem 0.8rem;border-bottom:1px solid #22252f;transition:background 0.15s;}"
        ".block-card:hover{background:#1e2130;}"
        ".block-card:first-child{border-top:none;}"
        ".block-card-tag{font-size:0.68rem;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:0.3rem;font-family:'Inter',sans-serif;}"
        ".block-text{white-space:pre-wrap;word-break:break-word;margin:0;font-size:0.9rem;line-height:1.6;color:#d4cbb8;}"
        ".extracted-table{width:100%;border-collapse:collapse;font-size:0.82rem;margin:0.25rem 0;}"
        ".extracted-table th{background:#252836;color:#c8bfa8;font-weight:600;padding:0.4rem 0.6rem;text-align:left;border:1px solid #2a2d3a;font-family:'Inter',sans-serif;font-size:0.75rem;text-transform:uppercase;letter-spacing:0.04em;}"
        ".extracted-table td{border:1px solid #2a2d3a;padding:0.35rem 0.6rem;color:#d4cbb8;vertical-align:top;}"
        ".extracted-table tr:nth-child(even) td{background:#191c26;}"
        ".extracted-table tr:hover td{background:#1e2130;}"
        "@media (max-width:900px){.compare-grid{grid-template-columns:1fr}.panel{height:auto;max-height:none}}"
        "@media print{body{background:#fff;color:#000} .compare-card{break-inside:avoid;background:#fff;border:1px solid #ccc;} .panel{height:auto;max-height:none;overflow:visible;background:#fff;} .block-text,.panel pre{color:#000;} .extracted-table th{background:#f5f5f5;color:#000;} .extracted-table td{color:#000;border-color:#ccc;}}"
        "</style>"
        "</head><body><main>"
        f"<h1>{title}</h1>"
        f'<p class="summary">Run folder: {html.escape(str(run_dir))} | Items: {len(comparisons)}</p>'
        + "".join(cards)
        + "</main></body></html>"
    )


def execute_run(
    profile: WorkflowProfile,
    inputs: Optional[Iterable[str]] = None,
    recursive: bool = False,
    dry_run: bool = False,
    prompt_runtime_controls: bool = True,
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
        profile.layout_backend = choose_layout_backend(profile.layout_backend)
        if prompt_runtime_controls:
            profile.model.request_timeout_seconds = choose_request_timeout(
                profile.model.request_timeout_seconds
            )
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
        ui.section("Interrupting")
        ui.status("warning", "Interrupt received. Preserving completed outputs...")
        ui.write("Waiting for the active model request to finish cleanly.")
        ui.write("Already written pages, chunks, and assets stay on disk.")
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
        ui.status("warning", "Safe interruption received. Completed outputs were preserved.")
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
        ["Chat provider", _chat_model(profile).provider],
        ["Chat model", _chat_model(profile).model],
        ["Mode", profile.model.execution_mode],
        ["Mode behavior", _mode_behavior(profile.model.execution_mode)],
        ["Slow request policy", _request_timeout_display(profile.model.request_timeout_seconds)],
        ["Layout analysis", profile.layout_backend],
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
    # Long document runs advance by heterogeneous units: PDF rendering, model
    # calls, retries, figure verification, translation chunks, and exports.
    # A numeric bar can imply false precision, especially for large PDFs, so
    # keep the live progress as a clean spinner and log durable usage events.
    with ui.progress("Processing") as reporter:

        def progress(event: str, message: str, advance: int = 1) -> None:
            reporter.update(message, advance=advance)
            if event in {"usage", "interrupt", "retry"}:
                reporter.log(message)

        return run_pipeline(request, progress=progress)


def _mode_behavior(mode: str) -> str:
    return {
        "fast": "300 DPI, shorter prompt, no restoration retries",
        "balanced": "400 DPI, default prompt, one informed retry",
        "quality": "500 DPI, careful prompt, up to three retries",
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
    _next_recommendations(_next_steps_for_context("run", run_dir=run_dir, exports=exports, issues=issues))


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
            ["Layout analysis", profile.layout_backend],
            ["Locked", "yes" if profile.locked else "no"],
            ["Vision provider", profile.model.provider],
            ["Vision model", profile.model.model],
            ["Chat provider", profile.chat_model.provider],
            ["Chat model", profile.chat_model.model],
            ["Endpoint", profile.model.endpoint or ""],
            ["Mode", profile.model.execution_mode],
            ["Context", str(profile.model.context_window or "auto")],
            ["Generation limit", str(profile.model.generation_limit or "auto")],
            ["Slow request policy", _request_timeout_display(profile.model.request_timeout_seconds)],
        ]
    )
    ui.note("Vision and chat models are configured separately under Model and limits.")


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
            ui.note("Vision and chat models can be set independently in this section.")
            chosen_model = choose_model(profile.model)
            if chosen_model is None:
                continue
            profile.model = chosen_model
            if ui.confirm("Use a separate model for chat?", False):
                chat_model = choose_model(profile.chat_model)
                if chat_model is not None:
                    profile.chat_model = chat_model
            else:
                profile.chat_model = copy.deepcopy(profile.model)
            selected_mode = ui.choose(
                "Execution mode", EXECUTION_MODES + ["Back"], profile.model.execution_mode
            )
            if selected_mode != "Back":
                profile.model.execution_mode = selected_mode
            profile.model.request_timeout_seconds = choose_request_timeout(
                profile.model.request_timeout_seconds
            )
        if section in {"Outputs", "Everything"}:
            profile.output_formats = choose_output_formats(profile.output_formats)
            profile.layout_backend = choose_layout_backend(profile.layout_backend)
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


def model_command(action: str = "menu") -> None:
    ui.heading("Akshara Vision", "Models")
    store = ConfigStore()
    if action in {"setup", "restore", "chat"} or action == "menu":
        if action == "restore":
            target = "Model (vision)"
        elif action == "chat":
            target = "Model (chat)"
        else:
            target = ui.choose("Model target", ["Model (vision)", "Model (chat)", "List providers", "Back"], "Model (vision)")
        if target == "Back":
            ui.status("info", "Model setup cancelled.")
            return
        if target == "List providers":
            action = "status"
        else:
            settings = choose_model()
            if settings is None:
                ui.status("info", "Model setup cancelled.")
                return
            profile = store.load_default_profile()
            if target == "Model (chat)":
                profile.chat_model = settings
            else:
                profile.model = settings
            ui.table(
                [
                    ["Target", target],
                    ["Provider", settings.provider],
                    ["Model", settings.model],
                    ["Endpoint", settings.endpoint or ""],
                ]
            )
            if ui.confirm(f"Save this {target.lower()} to the default profile?", True):
                store.save_profile(profile)
                ui.write(f"Saved model to profile: {profile.name}")
            return
    with ui.progress("Loading model provider status...") as reporter:
        rows = provider_status_rows()
        reporter.finish("Model provider status ready")
    ui.table(rows)


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
            ["Sarvam", "SARVAM_API_KEY"],
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
    html_pdf_renderers = (
        "chromium",
        "chromium-browser",
        "google-chrome",
        "google-chrome-stable",
        "brave-browser",
    )
    rows.append(
        [
            "Chromium/Chrome",
            "found" if any(find_executable(command) for command in html_pdf_renderers) else "missing",
            "HTML-backed PDF export",
        ]
    )
    for env_name, purpose in [
        ("SARVAM_API_KEY", "Sarvam cloud models"),
        ("OPENAI_API_KEY", "OpenAI cloud models"),
        ("ANTHROPIC_API_KEY", "Anthropic cloud models"),
        ("GEMINI_API_KEY", "Gemini cloud models"),
    ]:
        rows.append([env_name, "set" if os.environ.get(env_name) else "not set", purpose])
    
    import importlib.util
    for module_name, name_label in [
        ("doctr", "doctr ML Layout"),
        ("paddleocr", "paddleocr ML Layout"),
        ("layoutparser", "layoutparser ML Layout"),
    ]:
        is_found = importlib.util.find_spec(module_name) is not None
        rows.append([name_label, "available" if is_found else "missing (optional)", "ML layout analysis adapter"])
        
    ui.table(rows)
    if any(importlib.util.find_spec(mod) is None for mod in ["doctr", "paddleocr", "layoutparser"]):
        ui.write("Note: Optional ML layout adapters are available for harder documents. Install via: python -m pip install -e \".[layout]\"")
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
        _next_steps_for_context("combine", run_dir=Path(result["run_dir"]), exports=exports)
    )


def resume_command(run_dir: Optional[str] = None) -> None:
    ui.heading("Akshara Vision", "Resume / Recover")
    target = run_dir or ui.text("Path to interrupted Akshara run folder")
    if not target:
        ui.status("error", "No folder selected.")
        return
    run_path = Path(target).expanduser()
    run_path = _resolve_run_folder(run_path)
    if run_path is None:
        ui.status("error", f"No run_state.json found in {Path(target).expanduser()}")
        return
    state_path = run_path / "run_state.json"

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        state = {}

    status = state.get("status", "unknown")
    completed = state.get("completed_inputs") if isinstance(state.get("completed_inputs"), list) else []
    total_inputs = state.get("total_inputs", len(completed))
    input_paths = state.get("input_paths") if isinstance(state.get("input_paths"), list) else []
    input_files = state.get("input_files", [])
    profile_dict = state.get("profile", {}) if isinstance(state.get("profile"), dict) else {}
    profile = WorkflowProfile.from_dict(profile_dict)
    profile.output_dir = str(run_path.parent)

    ui.status("info", f"State: {status}")
    ui.status("info", f"Completed inputs: {len(completed)}/{total_inputs}")
    if ui.interactive() and not _model_usable(profile.model):
        prompted = _prompt_missing_model(profile, "restore", store=ConfigStore(), persist=True)
        if prompted is False:
            return None

    if status == "running":
        state["status"] = "interrupted"
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        status = "interrupted"

    if status == "complete" or len(completed) >= total_inputs:
        ui.write("Recovering completed checkpoints into final outputs.")
        combine_command(str(run_path))
        return

    if input_paths or input_files:
        ui.status("info", "Found original input path(s).")
        if ui.confirm("Resume this run in the same run folder?", True):
            profile_dict = state.get("profile", {})
            profile = WorkflowProfile.from_dict(profile_dict)
            profile.output_dir = str(run_path.parent)
            if ui.interactive():
                runtime = prompt_runtime_mode(profile)
                if runtime is None:
                    return
                profile = runtime
            selection = discover_inputs(
                [str(path) for path in (input_paths or input_files)], recursive=True
            )
            if not selection.files:
                sources_dir = run_path / "sources"
                if sources_dir.exists():
                    ui.status("warning", "Original inputs were not found. Trying copied sources instead.")
                    selection = discover_inputs([str(sources_dir)], recursive=True)
            if not selection.files:
                ui.status(
                    "warning",
                    "Input files were not found. Combining completed checkpoints instead.",
                )
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


def _resolve_run_folder(path: Path) -> Optional[Path]:
    if (path / "run_state.json").exists():
        return path
    candidates = [child for child in path.iterdir() if child.is_dir() and (child / "run_state.json").exists()] if path.exists() and path.is_dir() else []
    if len(candidates) == 1:
        ui.status("info", f"Using interrupted run folder: {candidates[0]}")
        return candidates[0]
    if len(candidates) > 1:
        ui.write("Multiple interrupted runs found below that folder. Please point at one run folder directly.")
    return None


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
        _next_recommendations(
            _next_steps_for_context("check", issues=[failed_label])
        )
        return failed
    ui.status("success", "Compile and unit tests passed.")
    _next_recommendations(_next_steps_for_context("check"))
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
    selected = [item for item in (formats or []) if item in OUTPUT_FORMATS]
    if formats and not selected:
        ui.status("error", "No valid output formats were selected.")
        return
    if not selected:
        selected = choose_output_formats(
            _default_export_formats(path, metadata), back_returns_defaults=False
        )
    if not selected:
        ui.status("info", "Export cancelled.")
        return
    export_metadata = dict(metadata)
    export_metadata.pop("_default_output_formats", None)
    registry = exporter_registry()
    skipped: List[str] = []
    with ui.progress("Exporting formats...", total=len(selected)) as reporter:
        for output_format in selected:
            exporter = registry.get(output_format)
            if not exporter:
                skipped.append(f"{output_format}: unsupported format")
                reporter.update(f"Skipped {output_format}", advance=1)
                ui.write(f"SKIPPED {output_format}: unsupported format")
                continue
            reporter.update(f"Writing {output_format}", advance=0)
            try:
                result = exporter.export(source_text, destination, export_metadata)
            except Exception as exc:
                reason = str(exc).strip() or "export failed"
                skipped.append(f"{output_format}: {reason}")
                reporter.update(f"Skipped {output_format}", advance=1)
                ui.write(f"SKIPPED {output_format}: {reason}")
                continue
            reporter.update(f"Wrote {output_format}", advance=1)
            ui.write(f"{result.format}: {result.path}")
    if skipped:
        ui.section("Skipped")
        for item in skipped:
            ui.write(item)
    _next_recommendations([["Combine run", f"akv combine {path}"], ["Run doctor", "akv doctor"]])


def _next_recommendations(rows: List[List[str]]) -> None:
    ui.section("Next")
    ui.table([["Action", "Command / path"]] + rows)


def _next_steps_for_context(
    context: str,
    run_dir: Optional[Path] = None,
    exports: Optional[List[object]] = None,
    issues: Optional[List[str]] = None,
) -> List[List[str]]:
    rows: List[List[str]] = []
    context = context.lower().strip()
    run_path = str(run_dir) if run_dir else ""
    issue_count = len([item for item in (issues or []) if str(item).strip()])
    export_count = len(exports or [])
    if context == "combine":
        rows.append(["Review output", f"{run_path}/akshara_output.txt" if run_path else "akv review"])
        rows.append(["Compare source/output", f"akv compare {run_path}" if run_path else "akv compare"])
        rows.append(["Export another format", f"akv export {run_path}" if run_path else "akv export"])
        return rows[:3]
    if context == "review":
        rows.append(["Compare source/output", f"akv compare {run_path}" if run_path else "akv compare"])
        rows.append(["Combine outputs", f"akv combine {run_path}" if run_path else "akv combine"])
        rows.append(["Export another format", f"akv export {run_path}" if run_path else "akv export"])
        return rows[:3]
    if context == "compare":
        rows.append(["Review layout", f"akv review {run_path}" if run_path else "akv review"])
        rows.append(["Combine outputs", f"akv combine {run_path}" if run_path else "akv combine"])
        rows.append(["Export another format", f"akv export {run_path}" if run_path else "akv export"])
        return rows[:3]
    if context == "chat":
        rows.append(["Run workflow", "akv run"])
        if run_path:
            rows.append(["Combine outputs", f"akv combine {run_path}"])
        rows.append(["Docs", "akv docs"])
        return rows[:3]
    if context == "check":
        if issue_count:
            rows.append(["Inspect failure", "Read the output above"])
            rows.append(["Run doctor", "akv doctor"])
            rows.append(["Retry checks", "akv check"])
        else:
            rows.append(["Run workflow", "akv run"])
            rows.append(["Review setup", "akv doctor"])
            rows.append(["Open home", "akv home"])
        return rows[:3]
    if context == "run":
        if issue_count:
            rows.append(["Review warnings", f"akv review {run_path}" if run_path else "akv review"])
            rows.append(["Resume later", f"akv resume {run_path}" if run_path else "akv resume"])
        else:
            rows.append(["Compare source/output", f"akv compare {run_path}" if run_path else "akv compare"])
            rows.append(["Review output", f"{run_path}/akshara_output.txt" if run_path else "akv review"])
            rows.append(["Export another format", f"akv export {run_path}" if run_path else "akv export"])
        return rows[:3]
    rows.append(["Run workflow", "akv run"])
    rows.append(["Review output", f"akv review {run_path}" if run_path else "akv review"])
    rows.append(["Docs", "akv docs"])
    return rows[:3]


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
    result = dict(metadata) if isinstance(metadata, dict) else {"title": path.name}
    result["run_dir"] = str(path)
    profile = manifest.get("profile") if isinstance(manifest, dict) else None
    if isinstance(profile, dict):
        result["_default_output_formats"] = profile.get("output_formats")
    return result


def _default_export_formats(path: Path, metadata: dict) -> List[str]:
    if path.is_file():
        return ["md"]
    formats = metadata.get("_default_output_formats")
    if isinstance(formats, list):
        selected = [str(item) for item in formats if str(item) in OUTPUT_FORMATS]
        return selected or ["txt"]
    if isinstance(formats, str):
        selected = [item.strip() for item in formats.split(",") if item.strip() in OUTPUT_FORMATS]
        return selected or ["txt"]
    return ["txt"]


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
    if provider_name == "sarvam":
        return ["sarvam-vision", "sarvam-105b", "sarvam-30b", "mayura", "sarvam-translate"]
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
        "SARVAM_API_KEY is not set.": "set SARVAM_API_KEY",
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
