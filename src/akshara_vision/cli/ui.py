import shutil
import time
import textwrap
from typing import Iterable, List, Optional


try:
    from InquirerPy import inquirer  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - dependency fallback
    inquirer = None

try:
    from rich.console import Console  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - dependency fallback
    Console = None

try:
    from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - dependency fallback
    Progress = None


class MonoUI:
    """Black-and-white terminal helpers with dependency-light fallbacks."""

    def __init__(self) -> None:
        self.console = Console(color_system=None) if Console else None

    def width(self) -> int:
        columns = shutil.get_terminal_size((78, 20)).columns
        return min(max(columns - 2, 54), 118)

    def write(self, message: str = "") -> None:
        if self.console:
            self.console.print(message, style="white", markup=False, highlight=False)
        else:
            print(message)

    def heading(self, title: str, subtitle: Optional[str] = None) -> None:
        width = self.width()
        line = "=" * width
        self.write(line)
        self.write(title.upper().center(width))
        if subtitle:
            self.write(subtitle.center(width))
        self.write(line)

    def hero(self, variant: str = "inscription", guide: str = "balanced") -> None:
        width = self.width()
        line = "=" * width
        self.write(line)
        compact_title = variant == "minimal" or width < 68
        if compact_title:
            self.write("AKSHARA VISION".center(width))
        elif variant == "classic":
            for row in _classic_hero():
                self.write(row.center(width))
        else:
            for row in _inscription_hero(width):
                self.write(row.center(width))
        if not compact_title:
            self.write("AKSHARA VISION".center(width))
        self.write("Restore. Read. Preserve.".center(width))
        if guide == "full":
            self.write(
                "Choose a workflow, inspect the plan, then run only when ready.".center(width)
            )
        self.write(line)

    def section(self, title: str) -> None:
        self.write("")
        self.write(title)
        self.write("-" * len(title))

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

    def choose(self, message: str, choices: List[str], default: Optional[str] = None) -> str:
        if not choices:
            raise ValueError("choose requires at least one choice")
        if inquirer:
            return str(
                inquirer.select(
                    message=message, choices=choices, default=default or choices[0]
                ).execute()
            )
        self.write(message)
        for index, choice in enumerate(choices, start=1):
            marker = "default" if choice == default else ""
            self.write(f"  {index}. {choice} {marker}".rstrip())
        raw = input("> ").strip()
        if not raw:
            return default or choices[0]
        if raw.isdigit() and 1 <= int(raw) <= len(choices):
            return choices[int(raw) - 1]
        return raw

    def checkbox(
        self, message: str, choices: List[str], default: Optional[List[str]] = None
    ) -> List[str]:
        default = default or []
        if inquirer:
            return list(
                inquirer.checkbox(message=message, choices=choices, default=default).execute()
            )
        self.write(message)
        self.write("Choose comma-separated numbers, or press Enter for default.")
        for index, choice in enumerate(choices, start=1):
            marker = "default" if choice in default else ""
            self.write(f"  {index}. {choice} {marker}".rstrip())
        raw = input("> ").strip()
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
        if inquirer:
            return str(inquirer.text(message=message, default=default).execute())
        suffix = f" [{default}]" if default else ""
        raw = input(f"{message}{suffix}: ").strip()
        return raw or default

    def confirm(self, message: str, default: bool = True) -> bool:
        if inquirer:
            return bool(inquirer.confirm(message=message, default=default).execute())
        suffix = "Y/n" if default else "y/N"
        raw = input(f"{message} ({suffix}): ").strip().lower()
        if not raw:
            return default
        return raw in {"y", "yes", "true", "1"}

    def progress(self, title: str, total: int = 0):
        return ProgressReporter(self, title)


ui = MonoUI()


class ProgressReporter:
    def __init__(self, ui_instance: MonoUI, title: str) -> None:
        self.ui = ui_instance
        self.title = title
        self._progress = None
        self._task = None
        self._started_at = 0.0

    def __enter__(self):
        self._started_at = time.monotonic()
        if Progress and self.ui.console:
            self._progress = Progress(
                SpinnerColumn(),
                TextColumn("{task.description}"),
                TimeElapsedColumn(),
                console=self.ui.console,
                transient=False,
            )
            self._progress.__enter__()
            self._task = self._progress.add_task(self.title)
        else:
            self.ui.section(self.title)
        return self

    def update(self, message: str, advance: int = 1) -> None:
        del advance
        if self._progress is not None and self._task is not None:
            self._progress.update(self._task, description=message)
        else:
            elapsed = max(time.monotonic() - self._started_at, 0.0)
            self.ui.write(f"[{elapsed:0.1f}s] {message}")

    def finish(self, message: str = "Complete") -> None:
        self.update(message)

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.finish("Complete")
        if self._progress is not None:
            self._progress.__exit__(exc_type, exc, tb)
        return False


def _classic_hero() -> List[str]:
    return [
        "     _    _  __ ____  _   _    _    ____      _    ",
        "    / \\  | |/ // ___|| | | |  / \\  |  _ \\    / \\   ",
        "   / _ \\ | ' / \\___ \\| |_| | / _ \\ | |_) |  / _ \\  ",
        "  / ___ \\| . \\  ___) |  _  |/ ___ \\|  _ <  / ___ \\ ",
        " /_/   \\_\\_|\\_\\|____/|_| |_/_/   \\_\\_| \\_\\/_/   \\_\\",
        "                    V I S I O N",
    ]


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
        "        _        _  __  _____  _   _       _       ____        _        ",
        "       / \\      | |/ / / ___/ | | | |     / \\     |  _ \\      / \\       ",
        "      / _ \\     | ' /  \\___ \\ | |_| |    / _ \\    | |_) |    / _ \\      ",
        "     / ___ \\    | . \\   ___) ||  _  |   / ___ \\   |  _ <    / ___ \\     ",
        "    /_/   \\_\\   |_|\\_\\ |____/ |_| |_|  /_/   \\_\\  |_| \\_\\  /_/   \\_\\    ",
        "                              V I S I O N",
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
