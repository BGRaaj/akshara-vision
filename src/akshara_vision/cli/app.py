import argparse
import sys
from typing import List, Optional

try:
    import typer
except ModuleNotFoundError:  # pragma: no cover - dependency fallback
    typer = None

from akshara_vision.cli.workflows import (
    apply_saved_ui_theme,
    batch_run,
    chat_command,
    check_command,
    clean_command,
    combine_command,
    docs_command,
    env_command,
    doctor_command,
    export_command,
    guide_command,
    interactive_session,
    instruct_command,
    install_command,
    mode_command,
    model_command,
    onboard,
    profile_command,
    quick_run,
    review_command,
    resume_command,
    run_guided,
    show_home,
    ui_command,
)


if typer:
    app = typer.Typer(
        add_completion=False,
        invoke_without_command=True,
        no_args_is_help=False,
        pretty_exceptions_enable=False,
        help="Akshara Vision: restore, read, and preserve archival books.",
    )

    @app.callback()
    def root(ctx: typer.Context):
        apply_saved_ui_theme()
        if ctx.invoked_subcommand is None:
            show_home(interactive=_interactive_allowed())

    def _input_argument():
        return typer.Argument(None, help="Path(s) to input files, folders, globs, or manifests.")

    def _profile_option():
        return typer.Option(None, "--profile", "-p", help="Profile name.")

    def _recursive_option():
        return typer.Option(False, "--recursive", "-R", help="Discover files recursively.")

    def _dry_run_option():
        return typer.Option(False, "--dry-run", help="Preview the run without writing outputs.")

    @app.command("init")
    @app.command("i")
    def init_command():
        onboard()

    @app.command("install")
    @app.command("setup")
    def install():
        install_command()

    @app.command("run")
    @app.command("r")
    def run_command(
        inputs: Optional[List[str]] = _input_argument(),
        profile: Optional[str] = _profile_option(),
        recursive: bool = _recursive_option(),
        dry_run: bool = _dry_run_option(),
    ):
        run_guided(inputs=inputs, profile_name=profile, recursive=recursive, dry_run=dry_run)

    @app.command("quick")
    @app.command("q")
    def quick_command(
        inputs: Optional[List[str]] = _input_argument(),
        recursive: bool = _recursive_option(),
        dry_run: bool = _dry_run_option(),
    ):
        quick_run(inputs=inputs, recursive=recursive, dry_run=dry_run)

    @app.command("batch")
    @app.command("b")
    def batch_command(
        inputs: Optional[List[str]] = _input_argument(),
        profile: Optional[str] = _profile_option(),
        dry_run: bool = _dry_run_option(),
    ):
        batch_run(inputs=inputs, profile_name=profile, dry_run=dry_run)

    @app.command("chat")
    @app.command("ask")
    def chat(
        inputs: Optional[List[str]] = _input_argument(),
        profile: Optional[str] = _profile_option(),
        recursive: bool = _recursive_option(),
        question: Optional[str] = typer.Option(
            None, "--question", help="Ask a single question non-interactively."
        ),
        system_prompt: Optional[str] = typer.Option(
            None, "--prompt", help="Override the default grounded chat instruction."
        ),
    ):
        chat_command(
            inputs=inputs,
            profile_name=profile,
            recursive=recursive,
            question=question,
            system_prompt=system_prompt,
        )

    @app.command("process")
    def process_command(
        inputs: Optional[List[str]] = _input_argument(),
        profile: Optional[str] = _profile_option(),
        recursive: bool = _recursive_option(),
        dry_run: bool = _dry_run_option(),
    ):
        quick_run(inputs=inputs, recursive=recursive, dry_run=dry_run)

    @app.command("profile")
    @app.command("p")
    def profiles_command(
        action: str = typer.Argument(
            "menu",
            help="menu, list, show, create, modify, use, lock, duplicate, delete, edit, export, import",
        ),
        name: str = typer.Option("default", "--name", "-n", help="Profile name."),
        source: Optional[str] = typer.Option(
            None, "--source", "-s", help="Profile file to import."
        ),
        lock: bool = typer.Option(False, "--lock", help="Lock as default."),
    ):
        profile_command(action=action, name=name, source=source, lock=lock)

    @app.command("model")
    @app.command("m")
    def models_command(action: str = typer.Argument("status", help="status or setup")):
        model_command(action=action)

    @app.command("env")
    @app.command("keys")
    def env():
        env_command()

    @app.command("instruct")
    @app.command("ins")
    def instructions_command(
        action: str = typer.Argument("view", help="view, edit, or reset"),
        preset: str = typer.Option(
            "book_restoration_default", "--preset", help="Instruction preset."
        ),
    ):
        instruct_command(action=action, preset=preset)

    @app.command("doctor")
    @app.command("d")
    def doctor():
        doctor_command()

    @app.command("combine")
    @app.command("assemble")
    @app.command("merge")
    def combine(
        run_dir: Optional[str] = typer.Argument(None, help="Path to Akshara run folder with staged outputs."),
    ):
        combine_command(run_dir=run_dir)

    @app.command("resume")
    @app.command("recover")
    def resume(
        run_dir: Optional[str] = typer.Argument(None, help="Path to interrupted Akshara run folder."),
    ):
        resume_command(run_dir=run_dir)

    @app.command("review")
    @app.command("inspect")
    @app.command("qa")
    def review(
        run_dir: Optional[str] = typer.Argument(None, help="Path to Akshara run folder."),
    ):
        review_command(run_dir=run_dir)

    @app.command("check")
    @app.command("test")
    @app.command("t")
    def check():
        raise typer.Exit(check_command())

    @app.command("export")
    @app.command("x")
    def export(
        run_dir: str = typer.Argument(..., help="Existing run folder or compiled output file."),
        formats: Optional[List[str]] = typer.Option(None, "--format", "-f", help="Output format."),
    ):
        export_command(run_dir=run_dir, formats=formats)

    @app.command("docs")
    def docs():
        docs_command()

    @app.command("home")
    def home():
        show_home(interactive=_interactive_allowed())

    @app.command("shell")
    @app.command("s")
    def shell():
        show_home(interactive=False)
        interactive_session()

    @app.command("clean")
    @app.command("c")
    def clean(yes: bool = typer.Option(False, "--yes", "-y", help="Remove without confirmation.")):
        clean_command(yes=yes)

    @app.command("guide")
    @app.command("g")
    def guide():
        guide_command()

    @app.command("mode")
    @app.command("speed")
    def mode():
        mode_command()

    @app.command("ui")
    @app.command("theme")
    def customize_ui():
        ui_command()


def main(argv: Optional[List[str]] = None) -> None:
    try:
        if typer:
            app(prog_name="akshara", args=argv, standalone_mode=argv is None)
            return
        _fallback_main(argv if argv is not None else sys.argv[1:])
    except (KeyboardInterrupt, EOFError):
        print("\nNamaskara.")
        sys.exit(1)


def _interactive_allowed() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _fallback_main(argv: List[str]) -> None:
    apply_saved_ui_theme()
    parser = argparse.ArgumentParser(
        prog="akshara",
        description="Akshara Vision: restore, read, and preserve archival books.",
    )
    parser.add_argument("command", nargs="?", default="home")
    parser.add_argument("inputs", nargs="*")
    parser.add_argument("--profile", "-p", default=None)
    parser.add_argument("--recursive", "-R", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--question", default=None)
    parser.add_argument("--prompt", default=None)
    parser.add_argument("--name", "-n", default="default")
    parser.add_argument("--source", "-s", default=None)
    parser.add_argument("--lock", action="store_true")
    parser.add_argument("--yes", "-y", action="store_true")
    parser.add_argument("--format", "-f", action="append", default=None)
    args = parser.parse_args(argv)

    command = args.command
    if command in {"home", ""}:
        show_home(interactive=_interactive_allowed())
    elif command in {"init", "i"}:
        onboard()
    elif command in {"install", "setup"}:
        install_command()
    elif command in {"run", "r"}:
        run_guided(
            args.inputs, profile_name=args.profile, recursive=args.recursive, dry_run=args.dry_run
        )
    elif command in {"quick", "q", "process"}:
        quick_run(args.inputs, recursive=args.recursive, dry_run=args.dry_run)
    elif command in {"batch", "b"}:
        batch_run(args.inputs, profile_name=args.profile, dry_run=args.dry_run)
    elif command in {"chat", "ask"}:
        chat_command(
            args.inputs,
            profile_name=args.profile,
            recursive=args.recursive,
            question=args.question,
            system_prompt=args.prompt,
        )
    elif command in {"profile", "p"}:
        action = args.inputs[0] if args.inputs else "menu"
        profile_command(action=action, name=args.name, source=args.source, lock=args.lock)
    elif command in {"model", "m"}:
        action = args.inputs[0] if args.inputs else "status"
        model_command(action=action)
    elif command in {"env", "keys"}:
        env_command()
    elif command in {"instruct", "ins"}:
        action = args.inputs[0] if args.inputs else "view"
        instruct_command(action=action)
    elif command in {"doctor", "d"}:
        doctor_command()
    elif command in {"combine", "assemble", "merge"}:
        combine_command(args.inputs[0] if args.inputs else None)
    elif command in {"resume", "recover"}:
        resume_command(args.inputs[0] if args.inputs else None)
    elif command in {"review", "inspect", "qa"}:
        review_command(args.inputs[0] if args.inputs else None)
    elif command in {"check", "test", "t"}:
        raise SystemExit(check_command())
    elif command in {"export", "x"}:
        if not args.inputs:
            parser.error("export requires a run directory or output file")
        export_command(args.inputs[0], formats=args.format)
    elif command == "docs":
        docs_command()
    elif command in {"shell", "s"}:
        show_home(interactive=False)
        interactive_session()
    elif command in {"clean", "c"}:
        clean_command(yes=args.yes)
    elif command in {"guide", "g"}:
        guide_command()
    elif command in {"mode", "speed"}:
        mode_command()
    elif command in {"ui", "theme"}:
        ui_command()
    else:
        parser.error(f"unknown command: {command}")


if __name__ == "__main__":
    main()
