import shutil
import sys
import time
import textwrap
from typing import Iterable, List, Optional


try:
    from InquirerPy import inquirer  # type: ignore
    from InquirerPy.utils import InquirerPyStyle  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - dependency fallback
    inquirer = None
    InquirerPyStyle = None

try:
    from rich.console import Console  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - dependency fallback
    Console = None

try:
    from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn, BarColumn  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - dependency fallback
    Progress = None


class MonoUI:
    """Monochrome terminal helpers with orange accent branding and fallbacks."""

    def __init__(self) -> None:
        self.console = Console() if Console else None
        self.theme = "dark"

    def set_theme(self, theme: str) -> None:
        self.theme = "light" if str(theme).strip().lower() == "light" else "dark"

    def style(self) -> str:
        if self.theme == "light":
            return "#3a2417 on #f4ecd8"
        return "white"

    def accent_style(self) -> str:
        if self.theme == "light":
            return "#c45a00 bold"
        return "#ff8c00 bold"

    def prompt_style(self):
        if not InquirerPyStyle:
            return None
        if self.theme == "light":
            return InquirerPyStyle(
                {
                    "questionmark": "#f4ecd8 bg:#c45a00 bold",
                    "question": "#3a2417 bg:#f4ecd8 bold",
                    "answer": "#c45a00 bg:#f4ecd8 bold",
                    "input": "#c45a00 bg:#f4ecd8 bold",
                    "pointer": "#f4ecd8 bg:#c45a00 bold",
                    "highlighted": "#f4ecd8 bg:#c45a00 bold",
                    "selected": "#c45a00 bg:#f4ecd8 bold",
                    "separator": "#3a2417 bg:#f4ecd8",
                    "instruction": "#8a4b1f bg:#f4ecd8",
                }
            )
        return InquirerPyStyle(
            {
                "questionmark": "black bg:#ff8c00 bold",
                "question": "white bold",
                "answer": "#ff8c00 bold",
                "input": "#ff8c00 bold",
                "pointer": "black bg:#ff8c00 bold",
                "highlighted": "black bg:#ff8c00 bold",
                "selected": "#ff8c00 bold",
                "separator": "white",
                "instruction": "#ffb347",
            }
        )

    def apply_terminal_theme(self, clear: bool = False) -> None:
        if not self.interactive():
            return
        if self.theme == "light":
            sequence = (
                "\033]10;#3a2417\007"
                "\033]11;#f4ecd8\007"
                "\033[48;2;244;236;216m\033[38;2;58;36;23m"
            )
        else:
            sequence = "\033]10;#ffffff\007\033]11;#000000\007\033[40m\033[37m"
        if clear:
            sequence += "\033[2J\033[H"
        sys.stdout.write(sequence)
        sys.stdout.flush()

    def width(self) -> int:
        columns = shutil.get_terminal_size((78, 20)).columns
        return min(max(columns - 2, 54), 118)

    def write(self, message: str = "") -> None:
        if self.console and self.theme == "light":
            message = self._fill_background(message)
        if self.console:
            self.console.print(message, style=self.style(), markup=False, highlight=False)
        else:
            print(message)

    def _fill_background(self, message: str) -> str:
        width = shutil.get_terminal_size((78, 20)).columns
        if not message:
            return " " * width
        lines = str(message).splitlines() or [""]
        return "\n".join(line.ljust(width) for line in lines)

    def heading(self, title: str, subtitle: Optional[str] = None) -> None:
        width = self.width()
        line = "=" * width
        self._print_colored(line, self.accent_style())
        self._print_colored(title.upper().center(width), self.accent_style())
        if subtitle:
            self._print_colored(subtitle.center(width), self.accent_style())
        self._print_colored(line, self.accent_style())

    def hero(self, guide: str = "balanced") -> None:
        width = self.width()
        line = "=" * width
        self._print_colored(line, self.accent_style())
        for row in _inscription_hero(width):
            self._print_colored(row.center(width), self.accent_style())
        self._print_colored("Restore. Read. Preserve.".center(width), self.accent_style())
        self._print_colored(line, self.accent_style())
        self.write("Use /help for every command and /menu to open the action picker.".center(width))
        if guide == "full":
            self.write("Use /guide to choose how much guidance Akshara Vision shows.".center(width))

    def status(self, level: str, message: str) -> None:
        """Minimal monochrome status marker."""
        marker = {
            "success": "[*]",
            "error": "[!]",
            "warning": "[!]",
            "info": "[-]",
        }.get(level.lower(), "[-]")
        self.write(f"{marker} {message}")

    def bullet_list(self, items: Iterable[str]) -> None:
        for item in items:
            self.write(f"  - {item}")

    def safe_input(self, prompt: str = "") -> str:
        try:
            return input(prompt).strip()
        except EOFError:
            raise KeyboardInterrupt()

    def section(self, title: str) -> None:
        self.write("")
        self._print_colored(title, self.accent_style())
        self._print_colored("-" * len(title), self.accent_style())

    def note(self, message: str) -> None:
        self.write(f"  {message}")

    def table(self, rows: Iterable[Iterable[str]]) -> None:
        materialized = [list(row) for row in rows]
        if not materialized:
            return
        column_count = len(materialized[0])
        available = max(self.width() - (2 * (column_count - 1)), column_count * 8)
        natural = [
            max(len(str(row[index])) for row in materialized) for index in range(column_count)
        ]
        if sum(natural) <= available:
            widths = natural
        elif column_count == 2:
            widths = [
                min(max(natural[0], 14), 24),
                max(24, available - min(max(natural[0], 14), 24)),
            ]
        elif column_count == 3:
            widths = [min(max(natural[0], 10), 24), min(max(natural[1], 8), 14)]
            widths.append(max(20, available - widths[0] - widths[1]))
        else:
            base = max(8, available // column_count)
            widths = [base] * column_count
        for row in materialized:
            wrapped = [
                textwrap.wrap(str(cell), width=max(widths[index], 8), replace_whitespace=False)
                or [""]
                for index, cell in enumerate(row)
            ]
            height = max(len(cell_lines) for cell_lines in wrapped)
            for line_index in range(height):
                parts = []
                for index, cell_lines in enumerate(wrapped):
                    value = cell_lines[line_index] if line_index < len(cell_lines) else ""
                    parts.append(value.ljust(widths[index]))
                self.write("  ".join(parts).rstrip())

    def board(self, cards: List[tuple], compact: bool = False) -> None:
        width = self.width()
        if compact or width < 74:
            for label, command, detail in cards:
                self.write(f"{command.ljust(12)} {label} - {detail}")
            return
        columns = 2 if width < 100 else 3
        gutter = 2
        card_width = (width - gutter * (columns - 1)) // columns
        rows = [cards[index : index + columns] for index in range(0, len(cards), columns)]
        for row in rows:
            rendered = [_render_card(card, card_width) for card in row]
            height = max(len(card) for card in rendered)
            for line_index in range(height):
                parts = []
                for card in rendered:
                    parts.append((card[line_index] if line_index < len(card) else " " * card_width))
                self.write((" " * gutter).join(parts).rstrip())

    def prompt_label(self, preference: str = "adaptive") -> str:
        if preference == "short":
            return "akv"
        if preference == "full":
            return "akshara"
        return "akv" if self.width() < 72 else "akshara"

    def interactive(self) -> bool:
        return sys.stdin.isatty() and sys.stdout.isatty()

    def choose(self, message: str, choices: List[str], default: Optional[str] = None) -> str:
        if not choices:
            raise ValueError("choose requires at least one choice")
        if inquirer and self.interactive():
            return str(
                inquirer.select(
                    message=message,
                    choices=choices,
                    default=default or choices[0],
                    qmark="◆",
                    style=self.prompt_style(),
                ).execute()
            )
        self.write(message)
        for index, choice in enumerate(choices, start=1):
            marker = "default" if choice == default else ""
            self.write(f"  {index}. {choice} {marker}".rstrip())
        raw = self.safe_input("› ")
        if not raw:
            return default or choices[0]
        if raw.isdigit() and 1 <= int(raw) <= len(choices):
            return choices[int(raw) - 1]
        return raw

    def checkbox(
        self, message: str, choices: List[str], default: Optional[List[str]] = None
    ) -> List[str]:
        default = default or []
        if inquirer and self.interactive():
            result = inquirer.checkbox(
                message=message,
                choices=choices,
                default=default,
                qmark="◆",
                style=self.prompt_style(),
            ).execute()
            if isinstance(result, (list, tuple, set)):
                normalized = []
                for item in result:
                    value = getattr(item, "value", item)
                    if value is None:
                        continue
                    normalized.append(str(value))
                return normalized
            if result is None:
                return []
            return [str(getattr(result, "value", result))]
        self.write(message)
        self.write("Choose comma-separated numbers, or press Enter for default.")
        for index, choice in enumerate(choices, start=1):
            marker = "default" if choice in default else ""
            self.write(f"  {index}. {choice} {marker}".rstrip())
        raw = self.safe_input("› ")
        if not raw:
            return default or [choices[0]]
        selected = []
        for part in raw.split(","):
            part = part.strip()
            if part.isdigit() and 1 <= int(part) <= len(choices):
                selected.append(choices[int(part) - 1])
            elif part in choices:
                selected.append(part)
        return selected or default or [choices[0]]

    def text(self, message: str, default: str = "") -> str:
        if inquirer and self.interactive():
            return str(
                inquirer.text(
                    message=message,
                    default=default,
                    qmark="◆",
                    style=self.prompt_style(),
                ).execute()
            )
        suffix = f" [{default}]" if default else ""
        raw = self.safe_input(f"{message}{suffix}: ")
        return raw or default

    def confirm(self, message: str, default: bool = True) -> bool:
        if inquirer and self.interactive():
            return bool(
                inquirer.confirm(
                    message=message,
                    default=default,
                    qmark="◆",
                    style=self.prompt_style(),
                ).execute()
            )
        suffix = "Y/n" if default else "y/N"
        if not self.interactive():
            self.write(f"{message} ({suffix}): {'yes' if default else 'no'}")
            return default
        raw = self.safe_input(f"{message} ({suffix}): ").lower()
        if not raw:
            return default
        return raw in {"y", "yes", "true", "1"}

    def progress(self, title: str, total: int = 0):
        return ProgressReporter(self, title, total)

    def controls_hint(self) -> str:
        width = self.width()
        if width < 76:
            return "Ctrl+C safe stop | /help after task"
        return "Ctrl+C safe stop | active request finishes cleanly | /help after task"

    def _print_colored(self, message: str, style: str) -> None:
        if self.console:
            if self.theme == "light":
                message = self._fill_background(message)
            self.console.print(message, style=style, markup=False, highlight=False)
        else:
            print(message)


ui = MonoUI()


class ProgressReporter:
    def __init__(self, ui_instance: MonoUI, title: str, total: int = 0) -> None:
        self.ui = ui_instance
        self.title = title
        self.total = total
        self._progress = None
        self._task = None
        self._started_at = 0.0
        self._task_progress = 0

    def __enter__(self):
        self._started_at = time.monotonic()
        if Progress and self.ui.console:
            columns = [SpinnerColumn(), TextColumn("{task.description}")]
            if self.total > 0:
                columns.append(BarColumn())
            columns.append(TimeElapsedColumn())
            columns.append(TextColumn("  " + self.ui.controls_hint()))
            self._progress = Progress(
                *columns,
                console=self.ui.console,
                transient=False,
            )
            self._progress.__enter__()
            self._task = self._progress.add_task(self.title, total=self.total or None)
        else:
            self.ui.section(self.title)
            self.ui.note(self.ui.controls_hint())
        return self

    def update(self, message: str, advance: int = 0) -> None:
        if self._progress is not None and self._task is not None:
            if self.total > 0:
                self._progress.update(self._task, description=message, advance=advance)
            else:
                self._progress.update(self._task, description=message)
        else:
            elapsed = max(time.monotonic() - self._started_at, 0.0)
            if self.total > 0:
                self._task_progress = min(self._task_progress + max(advance, 0), self.total)
                self.ui.write(f"[{elapsed:0.1f}s] {message} (+{advance})")
            else:
                self.ui.write(f"[{elapsed:0.1f}s] {message}")

    def log(self, message: str) -> None:
        if self._progress is not None:
            self._progress.console.print(message, style=self.ui.style(), highlight=False)
        else:
            self.ui.write(message)

    def finish(self, message: str = "Complete") -> None:
        self.update(message, advance=0)

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.finish("Complete")
        if self._progress is not None:
            self._progress.__exit__(exc_type, exc, tb)
        return False


def _inscription_hero(width: int) -> List[str]:
    if width < 86:
        return [
            "     _    _  __ ____  _   _    _    ____      _    ",
            "    / \\  | |/ // ___|| | | |  / \\  |  _ \\    / \\   ",
            "   / _ \\ | ' / \\___ \\| |_| | / _ \\ | |_) |  / _ \\  ",
            "  / ___ \\| . \\  ___) |  _  |/ ___ \\|  _ <  / ___ \\ ",
            " /_/   \\_\\_|\\_\\|____/|_| |_/_/   \\_\\_| \\_\\/_/   \\_\\",
            "                    V I S I O N",
        ]
    return [
            "     _    _  __ ____  _   _    _    ____      _    ",
            "    / \\  | |/ // ___|| | | |  / \\  |  _ \\    / \\   ",
            "   / _ \\ | ' / \\___ \\| |_| | / _ \\ | |_) |  / _ \\  ",
            "  / ___ \\| . \\  ___) |  _  |/ ___ \\|  _ <  / ___ \\ ",
            " /_/   \\_\\_|\\_\\|____/|_| |_/_/   \\_\\_| \\_\\/_/   \\_\\",
            "                    V I S I O N",
        ]


def _render_card(card: tuple, width: int) -> List[str]:
    label, command, detail = card
    inner = max(width - 4, 16)
    detail_lines = textwrap.wrap(str(detail), width=inner) or [""]
    lines = [
        "+" + "-" * (width - 2) + "+",
        "| " + str(label)[:inner].ljust(inner) + " |",
        "| " + str(command)[:inner].ljust(inner) + " |",
    ]
    for detail_line in detail_lines[:2]:
        lines.append("| " + detail_line.ljust(inner) + " |")
    lines.append("+" + "-" * (width - 2) + "+")
    return lines
