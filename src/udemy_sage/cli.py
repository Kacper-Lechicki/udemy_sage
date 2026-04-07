"""Interactive CLI wizard with progress tracking and cost summary."""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import questionary
from rich.columns import Columns
from rich.console import Console, Group
from rich.progress import (
    BarColumn,
    Progress,
    ProgressColumn,
    SpinnerColumn,
    Task,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Column as TableColumn
from rich.text import Text

from udemy_sage.ai_client import (
    AIError,
    build_shared_client,
    estimate_cost,
    generate_note,
)
from udemy_sage.config import (
    CONFIGURABLE_FIELDS,
    DEFAULT_MODELS,
    ERROR_LOG,
    PROVIDERS,
    is_configured,
    load_config,
    log_error,
    save_config,
    validate_config,
)
from udemy_sage.fetcher import FetchError, build_course
from udemy_sage.parser import Course
from udemy_sage.renderer import render_course

console = Console()

MAX_PARALLEL_NOTES = 5


@dataclass(frozen=True)
class ParsedCli:
    """CLI flags after parsing."""

    reconfig_key: str | None
    ollama_base_url: str | None


def _is_valid_udemy_https_url(url: str) -> bool:
    """True if URL uses https and host is udemy.com (including subdomains)."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    return host == "udemy.com" or host.endswith(".udemy.com")


def _validate_udemy_url_text(val: str) -> bool | str:
    """questionary validator for Udemy course URLs."""
    if not val.strip():
        return "URL is required"
    if not val.startswith("https://"):
        return "URL must start with https://"
    if not _is_valid_udemy_https_url(val):
        return "Must be a Udemy URL (host udemy.com)"
    return True


def _parse_cli_args() -> ParsedCli:
    """Parse argv; exit on bad --reconfig, else return flags."""
    parser = argparse.ArgumentParser(
        prog="udemy-sage",
        description=(
            "Download Udemy subtitles and generate Obsidian notes using AI."
        ),
        epilog=(
            "Config file: ~/.udemy-sage/config.json (private permissions).\n"
            "Examples:\n"
            "  udemy-sage\n"
            "  udemy-sage --reconfigure\n"
            "  udemy-sage --reconfigure model\n"
            "  udemy-sage --ollama-base-url http://192.168.1.10:11434"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--reconfigure",
        "--reconfig",
        dest="reconfig",
        nargs="?",
        const="__all__",
        default=None,
        metavar="FIELD",
        help=(
            "Run first-time setup again, or change one field: "
            + ", ".join(CONFIGURABLE_FIELDS)
        ),
    )
    parser.add_argument(
        "--ollama-base-url",
        dest="ollama_base_url",
        default=None,
        metavar="URL",
        help=(
            "When using Ollama, override the API base URL for this run "
            "(default: value from config or http://localhost:11434)."
        ),
    )
    args = parser.parse_args()
    key = args.reconfig
    if key is None:
        return ParsedCli(
            reconfig_key=None,
            ollama_base_url=args.ollama_base_url,
        )
    if key == "__all__":
        return ParsedCli(
            reconfig_key="__all__",
            ollama_base_url=args.ollama_base_url,
        )
    if key not in CONFIGURABLE_FIELDS:
        valid = ", ".join(CONFIGURABLE_FIELDS)
        console.print(
            f"[red]Unknown field {key!r}. Valid: {valid}[/red]",
        )
        sys.exit(1)
    return ParsedCli(reconfig_key=key, ollama_base_url=args.ollama_base_url)


def _save_config_if_valid(config: dict) -> None:
    errors = validate_config(config)
    if errors:
        for msg in errors:
            console.print(f"[red]{msg}[/red]")
        sys.exit(1)
    save_config(config)


def main() -> None:
    """Entry point for the CLI."""
    try:
        parsed = _parse_cli_args()
        _run(parsed.reconfig_key, parsed.ollama_base_url)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
        sys.exit(1)


def _ollama_base_url_for_run(
    config: dict, cli_override: str | None
) -> str | None:
    """Ollama base URL: CLI override, then config, else None (SDK default)."""
    if cli_override and str(cli_override).strip():
        return str(cli_override).strip()
    raw = config.get("ollama_base_url")
    if raw and str(raw).strip():
        return str(raw).strip()
    return None


# pylint: disable-next=too-many-branches,too-many-locals,too-many-statements
def _run(
    reconfig_key: str | None,
    ollama_base_url_cli: str | None = None,
) -> None:
    console.print(
        "[bold]udemy-sage[/bold] — Udemy course → Obsidian notes\n",
    )

    config = load_config()
    if not is_configured():
        config = _wizard()
        _save_config_if_valid(config)
        console.print("[green]Configuration saved.[/green]\n")
    elif reconfig_key == "__all__":
        config = _wizard()
        _save_config_if_valid(config)
        console.print("[green]Configuration saved.[/green]\n")
    elif reconfig_key:
        config = _reconfigure_field(config, reconfig_key)
        _save_config_if_valid(config)
        console.print(f"[green]Updated {reconfig_key}.[/green]\n")

    url = questionary.text(
        "Udemy course URL:",
        validate=_validate_udemy_url_text,
    ).ask()
    if not url:
        console.print("[yellow]Cancelled.[/yellow]")
        return

    provider = config["provider"]
    model = config.get("model", DEFAULT_MODELS.get(provider, ""))
    api_key = config.get("api_key", "")
    vault_path = Path(config["vault_path"]).expanduser()
    cookies_browser = config.get("cookies_browser", "chrome")

    if provider != "ollama" and not str(api_key).strip():
        console.print(
            "[red]API key is missing. Set it with: "
            "udemy-sage --reconfigure=api_key[/red]",
        )
        sys.exit(1)

    vault_resolved = vault_path.resolve()
    if not vault_resolved.is_dir():
        console.print(
            "[red]Vault path does not exist or is not a directory: "
            f"{vault_resolved}[/red]",
        )
        sys.exit(1)

    # Fetch course and subtitles
    console.print(
        f"\n[cyan]Fetching course structure ({cookies_browser} cookies)..."
        f"[/cyan]",
    )
    try:
        course = build_course(url, cookies_browser)
        lessons_with_subs = sum(
            1
            for s in course.sections
            for lesson in s.lessons
            if lesson.transcript
        )
        console.print(
            f"[green]Found {course.total_lessons} lessons "
            f"in {len(course.sections)} sections "
            f"({lessons_with_subs} with subtitles).[/green]\n"
        )

        # Cost estimate
        total_chars = sum(
            len(lesson.transcript)
            for s in course.sections
            for lesson in s.lessons
        )
        estimated_tokens = total_chars // 4
        estimated_cost = estimate_cost(provider, estimated_tokens)

        console.print(f"[bold]Estimated tokens:[/bold] ~{estimated_tokens:,}")
        if estimated_cost > 0:
            console.print(
                f"[bold]Estimated cost:[/bold] ~${estimated_cost:.4f}",
            )
        else:
            console.print("[bold]Estimated cost:[/bold] $0.00 (local model)")

        proceed = questionary.confirm(
            "Proceed with note generation?",
            default=True,
        ).ask()
        if not proceed:
            console.print("[yellow]Cancelled.[/yellow]")
            return

        # Generate notes
        try:
            ollama_base = _ollama_base_url_for_run(config, ollama_base_url_cli)
            total_tokens, ai_failures = _generate_all_notes(
                course,
                provider,
                model,
                api_key,
                ollama_base_url=ollama_base,
            )
        except AIError as e:
            console.print(f"[red]AI setup error: {e}[/red]")
            sys.exit(1)

        # Render to vault
        try:
            skipped = render_course(course, vault_resolved)
        except OSError as e:
            console.print(
                "[red]Could not save notes to vault "
                f"({vault_resolved}): {e}[/red]",
            )
            sys.exit(1)
        if skipped:
            n = len(skipped)
            console.print(f"[yellow]Skipped {n} existing notes.[/yellow]")

    except FetchError as e:
        console.print(f"[red]Fetch error: {e}[/red]")
        sys.exit(1)

    # Cost summary
    _print_cost_summary(provider, total_tokens, ai_failures)
    done_path = f"{vault_resolved}/resources/udemy/{course.slug}/"
    console.print(
        f"\n[bold green]Done! Notes saved to {done_path}[/bold green]",
    )


class _LessonTitleAndBarColumn(ProgressColumn):
    """Title row, then progress bar, percentage, and elapsed time."""

    def __init__(self, rich_console: Console) -> None:
        self._console = rich_console
        self._pct = TextColumn("[progress.percentage]{task.percentage:>3.0f}%")
        self._elapsed = TimeElapsedColumn()
        super().__init__(table_column=TableColumn(ratio=1, no_wrap=False))

    def render(self, task: Task) -> Group:
        title = Text.from_markup(task.description or "", overflow="fold")
        title.no_wrap = False
        bar_w = max(14, self._console.width - 22)
        bar_render = BarColumn(bar_width=bar_w).render(task)
        metrics = Columns(
            [bar_render, self._pct.render(task), self._elapsed.render(task)],
            padding=(0, 1),
        )
        return Group(title, metrics)


# pylint: disable-next=too-many-locals
def _generate_all_notes(
    course: Course,
    provider: str,
    model: str,
    api_key: str,
    *,
    ollama_base_url: str | None = None,
) -> tuple[int, int]:
    """Generate AI notes; returns (total tokens, failed lesson count)."""
    lessons = [(s, lesson) for s in course.sections for lesson in s.lessons]

    shared_client = build_shared_client(provider, api_key, None)

    gen_base_url = ollama_base_url if provider == "ollama" else None

    def run_lesson(section, lesson) -> tuple[int, str | None]:
        try:
            note_content, tokens = generate_note(
                provider=provider,
                model=model,
                api_key=api_key,
                transcript=lesson.transcript,
                lesson_title=lesson.title,
                base_url=gen_base_url,
                client=shared_client,
            )
            lesson.transcript = note_content
            return tokens, None
        except AIError as e:
            lesson_id = f"{section.dirname}/{lesson.filename}"
            log_error(lesson_id, str(e))
            return 0, f"{lesson.title} — {e}"

    work: list[tuple] = []
    for section, lesson in lessons:
        if lesson.transcript:
            work.append((section, lesson))

    max_workers = min(MAX_PARALLEL_NOTES, max(1, len(work)))
    total_tokens = 0
    ai_failures = 0

    with Progress(
        SpinnerColumn(),
        _LessonTitleAndBarColumn(console),
        console=console,
        expand=True,
    ) as progress:
        task = progress.add_task("Generating notes...", total=len(lessons))

        for section, lesson in lessons:
            if not lesson.transcript:
                progress.advance(task)

        if not work:
            return 0, 0

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_to_pair = {
                pool.submit(run_lesson, section, lesson): (section, lesson)
                for section, lesson in work
            }
            for future in as_completed(future_to_pair):
                section, lesson = future_to_pair[future]
                desc = f"[cyan]{section.title}[/cyan] → {lesson.title}"
                progress.update(task, description=desc)
                tokens, err = future.result()
                if err:
                    ai_failures += 1
                    console.print(f"  [red]Error: {err}[/red]")
                else:
                    total_tokens += tokens
                progress.advance(task)

    return total_tokens, ai_failures


def _print_cost_summary(
    provider: str, total_tokens: int, ai_failures: int
) -> None:
    """Display token usage and estimated cost."""
    cost = estimate_cost(provider, total_tokens)
    console.print(f"\n[bold]Token usage:[/bold] {total_tokens:,}")
    if cost > 0:
        console.print(f"[bold]Estimated cost:[/bold] ${cost:.4f}")
    else:
        console.print("[bold]Estimated cost:[/bold] $0.00 (local model)")
    if ai_failures > 0:
        msg = (
            f"[yellow]{ai_failures} lesson(s) failed "
            f"(see {ERROR_LOG} for details).[/yellow]"
        )
        console.print(msg)


def _reconfigure_field(config: dict, key: str) -> dict:
    """Prompt the user to update a single config field."""
    if key == "provider":
        val = questionary.select("AI provider:", choices=list(PROVIDERS)).ask()
        if val:
            config["provider"] = val
            config["model"] = DEFAULT_MODELS.get(val, "")
    elif key == "model":
        prov = config.get("provider", "")
        default = config.get("model", DEFAULT_MODELS.get(prov, ""))
        val = questionary.text("Model name:", default=default).ask()
        if val:
            config["model"] = val
    elif key == "api_key":
        prov = config.get("provider", "AI").title()
        val = questionary.password(f"{prov} API key:").ask()
        if val:
            config["api_key"] = val
    elif key == "vault_path":
        val = questionary.path(
            "Obsidian vault path:",
            only_directories=True,
            default=config.get("vault_path", ""),
        ).ask()
        if val:
            config["vault_path"] = val
    elif key == "cookies_browser":
        val = questionary.select(
            "Browser for Udemy cookies:",
            choices=["chrome", "firefox", "safari", "edge", "brave", "opera"],
            default=config.get("cookies_browser", "chrome"),
        ).ask()
        if val:
            config["cookies_browser"] = val
    elif key == "ollama_base_url":
        default = config.get("ollama_base_url") or "http://localhost:11434"
        val = questionary.text("Ollama base URL:", default=default).ask()
        if val is not None:
            config["ollama_base_url"] = val
    return config


def _wizard() -> dict:
    """Interactive first-run configuration wizard."""
    console.print("[bold]First-run setup[/bold]\n")

    provider = questionary.select(
        "AI provider:",
        choices=list(PROVIDERS),
    ).ask()
    if not provider:
        sys.exit(1)

    model = questionary.text(
        "Model name:",
        default=DEFAULT_MODELS.get(provider, ""),
    ).ask()

    api_key = ""
    if provider != "ollama":
        prompt = f"{provider.title()} API key:"
        api_key = questionary.password(prompt).ask() or ""

    vault_path = questionary.path(
        "Obsidian vault path:",
        only_directories=True,
    ).ask()
    if not vault_path:
        sys.exit(1)

    cookies_browser = questionary.select(
        "Browser for Udemy cookies:",
        choices=["chrome", "firefox", "safari", "edge", "brave", "opera"],
        default="chrome",
    ).ask()

    ollama_base_url = ""
    if provider == "ollama":
        ollama_base_url = (
            questionary.text(
                "Ollama base URL:",
                default="http://localhost:11434",
            ).ask()
            or "http://localhost:11434"
        )

    return {
        "provider": provider,
        "model": model,
        "api_key": api_key,
        "vault_path": vault_path,
        "cookies_browser": cookies_browser,
        "ollama_base_url": ollama_base_url,
    }
