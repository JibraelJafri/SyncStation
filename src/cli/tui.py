import os
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.box import ROUNDED, SIMPLE, DOUBLE_EDGE
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TaskProgressColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.theme import Theme

from InquirerPy import inquirer
from InquirerPy.base.control import Choice
from InquirerPy.separator import Separator

from src.core.models import Playlist, MirroredPlaylist, SyncPlan, SyncPlanItem, PlanAction, ConfidenceTier

# ─── COLOR & THEME DEFINITIONS ──────────────────────────────────────────────
custom_theme = Theme({
    "info": "cyan",
    "warning": "yellow",
    "danger": "bold red",
    "success": "bold green",
    "highlight": "bold magenta",
    "dimmed": "dim white",
    "accent": "bold cyan"
})

console = Console(theme=custom_theme)

from InquirerPy.utils import get_style

INQUIRER_STYLE = get_style({
    "questionmark": "#00d7d7 bold",
    "question": "bold",
    "answermark": "#00ff87 bold",
    "answer": "#00ff87 bold",
    "input": "#00d7d7",
    "checkbox": "#00d7d7",
    "separator": "#6c6c6c",
    "instruction": "#808080 italic",
    "pointer": "#00d7ff bold",
    "validator": "#ff5f5f",
    "marker": "#00ff87 bold",
    "fuzzy_prompt": "#00d7d7",
    "fuzzy_info": "#808080",
    "fuzzy_border": "#00d7d7",
    "fuzzy_match": "#00ff87 bold",
}, style_override=True)

# ─── TUI COMPONENT MANAGER ───────────────────────────────────────────────────
class TUI:
    @staticmethod
    def print_hero_banner(
        export_file_name: Optional[str] = None,
        playlist_count: int = 0,
        yt_connected: bool = False,
        active_mirrors_count: int = 0
    ):
        console.clear()
        banner_text = Text()
        banner_text.append("⚡ SyncStation ", style="bold cyan")
        banner_text.append("— Spotify → YouTube Music Migration Engine\n", style="bold white")
        
        # Sub-status badges
        export_str = f"Export: {export_file_name} ({playlist_count} playlists)" if export_file_name else "Export: Not Loaded"
        banner_text.append(f"📁 {export_str}   ", style="cyan" if export_file_name else "yellow")
        
        yt_str = f"YouTube Music: Connected ({active_mirrors_count} mirrors)" if yt_connected else "YouTube Music: Disconnected"
        banner_text.append(f"🔑 {yt_str}", style="green" if yt_connected else "red")

        panel = Panel(
            banner_text,
            box=ROUNDED,
            border_style="cyan",
            padding=(0, 2),
            title="[bold white]Terminal Edition[/bold white]",
            title_align="right",
            subtitle="[italic cyan]⚡ Wake up, Samurai. We've got playlists to burn.[/italic cyan]",
            subtitle_align="right"
        )
        console.print(panel)

    @staticmethod
    def select_main_menu_action() -> str:
        choices = [
            Choice("wizard", "🚀  Transfer / Sync Playlists (Interactive Wizard)"),
            Choice("diff", "🔍  Inspect Diffs & Dry-Run (Preview Changes First)"),
            Choice("quick_sync", "⚡  Fast Delta Sync All (0 Duplicates Guarantee)"),
            Choice("list", "📋  Browse Discovered Spotify Playlists"),
            Choice("mirrors", "🪞  View Active Mirrors & Sync Status"),
            Choice("auth", "🔑  YouTube Music Session & Diagnostics"),
            Choice("switch_export", "📂  Switch / Reload Spotify Export File"),
            Choice("resume", "🔄  Resume Interrupted Transfers"),
            Separator(),
            Choice("exit", "🚪  Exit")
        ]

        action = inquirer.select(
            message="Main Menu — Select an action:",
            choices=choices,
            style=INQUIRER_STYLE,
            default="wizard",
            pointer="❯ "
        ).execute()

        return action

    @staticmethod
    def select_playlists_checkbox(playlists: List[Playlist]) -> List[Playlist]:
        if not playlists:
            return []

        choices = [
            Choice(
                value=pl,
                name=f"{pl.name:<38} ({pl.track_count:>4d} tracks)"
            )
            for pl in playlists
        ]

        selected = inquirer.checkbox(
            message="Select playlists (Space to toggle, 'a' all, 'i' invert, Enter to confirm):",
            choices=choices,
            style=INQUIRER_STYLE,
            pointer="❯ ",
            enabled_symbol="◉ ",
            disabled_symbol="◯ ",
            instruction="[Space] toggle • [a] select all • [i] invert"
        ).execute()

        return selected or []

    @staticmethod
    def select_single_playlist(playlists: List[Playlist], message: str = "Select a playlist:") -> Optional[Playlist]:
        if not playlists:
            return None

        choices = [
            Choice(value=pl, name=f"{pl.name:<38} ({pl.track_count:>4d} tracks)")
            for pl in playlists
        ]
        choices.append(Separator())
        choices.append(Choice(value=None, name="↩  Back to Menu"))

        selected = inquirer.select(
            message=message,
            choices=choices,
            style=INQUIRER_STYLE,
            pointer="❯ "
        ).execute()

        return selected

    @staticmethod
    def select_strategy() -> str:
        choices = [
            Choice("UPDATE", "Intelligent Sync / Delta Update (Recommended) — 0 duplicate guarantee"),
            Choice("CREATE_NEW", "Create Fresh Copy — Creates new playlist even if mirror exists"),
            Choice("SKIP_EXISTING", "Skip Existing Playlists — Only migrate playlists not yet on YouTube"),
        ]

        return inquirer.select(
            message="Existing Playlist Strategy:",
            choices=choices,
            style=INQUIRER_STYLE,
            default="UPDATE",
            pointer="❯ "
        ).execute()

    @staticmethod
    def confirm_action(message: str, default: bool = True) -> bool:
        return inquirer.confirm(
            message=message,
            default=default,
            style=INQUIRER_STYLE
        ).execute()

    @staticmethod
    def render_diff_card(
        pl: Playlist,
        plan: Optional[SyncPlan]
    ):
        if plan is None:
            content = Text()
            content.append("ℹ This playlist is not yet mirrored on YouTube Music.\n", style="bold cyan")
            content.append(f"All {pl.track_count} tracks will be transferred.", style="white")
            panel = Panel(content, title=f"🎵 {pl.name}", box=ROUNDED, border_style="cyan")
            console.print(panel)
            return

        in_sync = [it for it in plan.items if it.action == PlanAction.NO_OP]
        additions = [it for it in plan.items if it.action == PlanAction.ADD_TRACK]
        removals = [it for it in plan.items if it.action == PlanAction.REMOVE_TRACK]
        extra_yt = [it for it in plan.items if it.action == PlanAction.SKIP_TRACK and "YouTube Music-only" in it.rationale]

        card = Text()
        card.append(f"Source (Spotify): {plan.source_track_count} tracks  │  ", style="dim")
        card.append(f"Destination (YouTube): {plan.destination_track_count} tracks  │  ", style="dim")
        card.append(f"In Sync: {len(in_sync)} tracks\n", style="bold green")

        if additions:
            card.append(f"\n➕ Tracks to Add / Restore ({len(additions)}):\n", style="bold green")
            for it in additions:
                if "Missing on YouTube Music" in it.rationale:
                    card.append(f"  ↺ [RESTORE] {it.source_track.artist} - {it.source_track.name}\n", style="cyan")
                else:
                    card.append(f"  + [NEW]     {it.source_track.artist} - {it.source_track.name}\n", style="green")

        if removals:
            card.append(f"\n➖ Removed from Spotify ({len(removals)}):\n", style="bold red")
            for it in removals:
                card.append(f"  - [REMOVED] {it.rationale}\n", style="red")

        if extra_yt:
            card.append(f"\n★ YouTube Music-Only Tracks (Preserved) ({len(extra_yt)}):\n", style="bold magenta")
            for it in extra_yt:
                card.append(f"  ★ [YT ONLY] {it.rationale}\n", style="magenta")

        if not additions and not removals:
            card.append("\n✓ 100% In Sync. No changes needed.\n", style="bold green")

        border_color = "green" if (not additions and not removals) else "yellow"
        panel = Panel(card, title=f"🎵 {pl.name}", box=ROUNDED, border_style=border_color)
        console.print(panel)

    @staticmethod
    def render_mirrors_table(mirrors: List[MirroredPlaylist]):
        if not mirrors:
            console.print("[yellow]No active mirrored playlists found in database.[/yellow]")
            return

        table = Table(title=f"Active Mirrored Playlists ({len(mirrors)})", box=ROUNDED, border_style="cyan")
        table.add_column("Spotify Source", style="bold white")
        table.add_column("YouTube Destination", style="cyan")
        table.add_column("Tracks", justify="right", style="magenta")
        table.add_column("Status", justify="center")
        table.add_column("YouTube Playlist ID", style="blue")

        for m in mirrors:
            st = m.last_sync_status.value
            st_style = "[bold green]IN SYNC[/bold green]" if st == "IN_SYNC" else f"[yellow]{st}[/yellow]"
            table.add_row(m.name, m.yt_playlist_name, str(m.spotify_track_count), st_style, m.yt_playlist_id)

        console.print(table)

    @staticmethod
    def render_summary_table(reports: List[Dict[str, Any]]):
        total_transferred = sum(r.get("synced_tracks", 0) for r in reports)
        table = Table(title="Migration Summary", box=ROUNDED, border_style="green")
        table.add_column("Playlist", style="bold white", width=30)
        table.add_column("Status", justify="center", width=14)
        table.add_column("Synced/Total", justify="right", width=14)
        table.add_column("YouTube Music URL", style="cyan", overflow="fold")

        for r in reports:
            st = r["status"]
            st_style = f"[bold green]{st}[/bold green]" if st in ["CREATED", "UPDATED", "IN_SYNC"] else f"[yellow]{st}[/yellow]"
            cnt_str = f"{r['synced_tracks']}/{r['total_tracks']}"
            table.add_row(r["name"], st_style, cnt_str, r.get("yt_url", ""))

        console.print(table)
        console.print(Panel(f"[bold white]Total Playlists:[/bold white] {len(reports)}   │   [bold green]Total Tracks Transferred:[/bold green] {total_transferred} [dim](0 duplicates inserted)[/dim]", box=ROUNDED, border_style="green"))

    @staticmethod
    def get_progress_bar() -> Progress:
        return Progress(
            SpinnerColumn("dots", style="cyan"),
            TextColumn("[bold cyan]{task.description}"),
            BarColumn(bar_width=25, complete_style="green", finished_style="bold green"),
            TaskProgressColumn(),
            TextColumn("[dim]({task.completed}/{task.total})"),
            TextColumn("[dim italic]{task.fields[current_track]}"),
            TimeElapsedColumn(),
            console=console,
            transient=True
        )
