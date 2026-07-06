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
    EXECUTION_MODES,
    OUTPUT_FORMATS,
    PROVIDER_TYPES,
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
    "Run workflow",
    "Quick run",
    "Batch process",
    "Guided setup",
    "Choose guide",
    "Choose mode",
    "Customize UI",
    "Profiles",
    "Models",
    "API keys",
    "Instructions",
    "Doctor",
    "Combine outputs",
    "Install dependencies",
    "Status",
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
            ("Modes", "/mode", "Speed and quality tradeoffs"),
            ("Models", "/models", "Local and cloud status"),
            ("API Keys", "/env", "Configure providers"),
            ("Profiles", "/profiles", "Defaults and locks"),
            ("Doctor", "/doctor", "System readiness"),
            ("Install", "/install", "PDF and image tools"),
            ("Status", "/status", "Current configuration"),
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
        "Choose mode": "/mode",
        "Customize UI": "/ui",
        "Profiles": "/profiles",
        "Models": "/models",
        "API keys": "/env",
        "Instructions": "/instructions",
        "Doctor": "/doctor",
        "Combine outputs": "/combine",
        "Install dependencies": "/install",
        "Status": "/status",
        "Run checks": "/check",
        "Docs": "/docs",
        "Clean local outputs": "/clean",
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
            ["/ui", "Customize hero, density, prompt"],
            ["/doctor", "Check local setup"],
            ["/combine [run-folder]", "Rebuild staged outputs into one document"],
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


def mode_command() -> None:
    store = ConfigStore()
    profile = store.load_default_profile()
    ui.heading("Akshara Vision", "Execution Mode")
    ui.write(
        "Fast favors throughput. Balanced keeps the current defaults. Quality spends more time for harder pages."
    )
    profile.model.execution_mode = ui.choose(
        "Execution mode", EXECUTION_MODES, profile.model.execution_mode
    )
    store.save_profile(profile)
    ui.write(f"Execution mode set to: {profile.model.execution_mode}")


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


def onboard(
    store: Optional[ConfigStore] = None, profile_name: Optional[str] = None
) -> WorkflowProfile:
    store = store or ConfigStore()
    ui.heading("Akshara Vision", "Onboarding")
    ui.write("Press Enter to accept the shown default. Use arrow keys for menus.")
    profile = WorkflowProfile(name=profile_name or "default")
    profile.name = ui.text("Profile name (Enter accepts default)", profile.name)
    profile.workflow = ui.choose("Workflow", WORKFLOWS, profile.workflow)
    profile.document_type = ui.choose("Document type", DOCUMENT_TYPES, profile.document_type)
    profile.source_language = ui.text(
        "Source language (for example: English, Hindi, Kannada)",
        profile.source_language,
    )
    profile.output_language = ui.text(
        "Output language (for example: English, Hindi, Kannada)",
        profile.output_language,
    )
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
        ],
        profile.translation_mode,
    )
    profile.sync_translation_defaults()

    profile.model = choose_model(profile.model)
    profile.model.execution_mode = ui.choose(
        "Execution mode", EXECUTION_MODES, profile.model.execution_mode
    )
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
    model_choices = (
        status.models if status and status.models else _recommended_models(provider_name)
    )
    model_choices = _with_custom_model_choice(model_choices)
    model = ui.choose(
        "Model",
        model_choices,
        current.model if current.model in model_choices else model_choices[0],
    )
    if model == "Custom model id...":
        model = ui.text("Model id", current.model if current.provider == provider_name else "")
    endpoint = current.endpoint or ""
    if provider_name in {"openai-compatible-local", "lm-studio", "jan", "llama-cpp"}:
        endpoint = ui.text("Local endpoint", endpoint or _default_endpoint(provider_name))

    context_window = current.context_window
    generation_limit = current.generation_limit
    if provider_name in {"ollama", "openai-compatible-local", "lm-studio", "jan", "llama-cpp"}:
        default_cw = (
            str(current.context_window) if current.context_window is not None else "16384 (Default)"
        )
        cw_str = ui.text("Context size limit (tokens, e.g. 2048, 8192, 16384)", default_cw)
        if cw_str.strip() and "default" not in cw_str.lower():
            try:
                context_window = int(cw_str)
            except ValueError:
                pass
        else:
            context_window = None
        default_gen = (
            str(current.generation_limit)
            if current.generation_limit is not None
            else "16384 (Default)"
        )
        gen_str = ui.text("Generation limit (output tokens, max 16384)", default_gen)
        if gen_str.strip() and "default" not in gen_str.lower():
            try:
                generation_limit = min(16384, max(1024, int(gen_str)))
            except ValueError:
                pass
        else:
            generation_limit = None

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

        profile.model = choose_model(profile.model)
        profile.model.execution_mode = ui.choose(
            "Execution mode", EXECUTION_MODES, profile.model.execution_mode
        )
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
    profile.sync_translation_defaults()
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
    try:
        result = _run_with_progress(RunRequest(profile=profile, inputs=selection, dry_run=False))
    except Exception as exc:
        ui.section("Error")
        ui.write(f"ERROR: {exc}")
        ui.write("\nRun stopped. No outputs were written or modified.")
        return None
    _finished_screen(result)
    return result


def review_run(profile: WorkflowProfile, selection) -> None:
    ui.section("Review")
    rows = [
        ["Workflow", profile.workflow],
        ["Document type", profile.document_type],
        ["Source language", profile.source_language],
        ["Output language", profile.output_language],
        ["Translation", _translation_label(profile)],
        ["Provider", profile.model.provider],
        ["Model", profile.model.model],
        ["Mode", profile.model.execution_mode],
        ["Generation limit", str(profile.model.generation_limit or "auto up to 16384")],
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
    ui.section("Working")
    with ui.progress("Processing") as reporter:

        def progress(_event: str, message: str, advance: int = 1) -> None:
            reporter.update(message, advance=advance)

        return run_pipeline(request, progress=progress)


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
        ui.write(
            "WARNING  Run completed with truncated output. One or more page chunks hit the token context/generation limit."
        )
    else:
        ui.heading("Akshara Vision", "Finished")
        ui.write("SUCCESS  Run completed.")
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
        for issue in issues:
            ui.write(f"  {issue}")
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


def _show_profile(profile: WorkflowProfile) -> None:
    ui.heading("Akshara Vision", f"Profile: {profile.name}")
    ui.table(
        [
            ["Name", profile.name],
            ["Workflow", profile.workflow],
            ["Document type", profile.document_type],
            ["Source language", profile.source_language],
            ["Output language", profile.output_language],
            ["Translation", _translation_label(profile)],
            ["Outputs", ", ".join(profile.output_formats)],
            ["Instruction", profile.instruction_preset],
            ["Output folder", profile.output_dir],
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
    ui.heading("Akshara Vision", f"Modify: {profile.name}")
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
        ],
        "Everything",
    )
    if section in {"Workflow and document", "Everything"}:
        profile.workflow = ui.choose("Workflow", WORKFLOWS, profile.workflow)
        profile.document_type = ui.choose("Document type", DOCUMENT_TYPES, profile.document_type)
    if section in {"Languages and translation", "Everything"}:
        profile.source_language = ui.text(
            "Source language (for example: English, Hindi, Kannada)",
            profile.source_language,
        )
        profile.output_language = ui.text(
            "Output language (for example: English, Hindi, Kannada)",
            profile.output_language,
        )
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
            ],
            profile.translation_mode,
        )
        profile.sync_translation_defaults()
    if section in {"Model and limits", "Everything"}:
        profile.model = choose_model(profile.model)
        profile.model.execution_mode = ui.choose(
            "Execution mode", EXECUTION_MODES, profile.model.execution_mode
        )
    if section in {"Outputs", "Everything"}:
        profile.output_formats = choose_output_formats(profile.output_formats)
    if section in {"Output folder", "Everything"}:
        profile.output_dir = ui.text("Output folder", profile.output_dir)
    if section in {"Lock/default", "Everything"}:
        profile.locked = ui.confirm(
            "Lock this profile as the default quick-run workflow?", profile.locked
        )
    saved = store.save_profile(profile)
    if profile.locked:
        store.set_default_profile(profile.name)
    ui.write(f"Saved profile: {saved}")


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


def model_command(action: str = "status") -> None:
    ui.heading("Akshara Vision", "Models")
    if action == "setup":
        settings = choose_model()
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
    target = run_dir or ui.text("Run folder containing staged outputs")
    if not target:
        ui.write("No folder selected.")
        return
    try:
        result = combine_stage_outputs(Path(target).expanduser())
    except Exception as exc:
        ui.write(f"ERROR: {exc}")
        return
    ui.write(f"Combined output: {result['output_path']}")
    ui.write(f"Language-specific alias: {result['alias_path']}")


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
        return ["gemma4:12b", "qwen3.6:27b", "qwen3.5:4b", "llama3.2-vision:11b"]
    if provider_name in {"openai-compatible-local", "lm-studio", "jan", "llama-cpp"}:
        return ["gemma-4-12b-it", "qwen-3.6-27b-instruct", "qwen-3.5-4b-instruct"]
    if provider_name == "gemini":
        return ["gemini-3.5-flash", "gemini-3.5-pro", "gemini-3.1-flash-lite"]
    if provider_name == "anthropic":
        return ["claude-sonnet-5", "claude-fable-5"]
    if provider_name == "openai":
        return ["gpt-5.5", "gpt-5.4"]
    return ["offline-restoration-preview"]


def _with_custom_model_choice(models: List[str]) -> List[str]:
    cleaned = [model for model in models if model and model != "Custom model id..."]
    return cleaned + ["Custom model id..."]


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
