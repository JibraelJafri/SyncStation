import os
import sys

# Configure UTF-8 stream encoding for Windows console safety
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

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
    def clear_screen():
        """Clears the console screen for crisp, non-stacking TUI transitions."""
        console.clear()

    @staticmethod
    def print_hero_banner(
        export_file_name: Optional[str] = None,
        playlist_count: int = 0,
        yt_connected: bool = False,
        deezer_connected: bool = False,
        active_destination: str = "youtube",
        active_mirrors_count: int = 0
    ):
        banner_text = Text()
        banner_text.append("⚡ SyncStation ", style="bold cyan")
        banner_text.append("— Multi-Platform Playlist Migration & Sync Engine\n", style="bold white")
        
        # Sub-status badges
        export_str = f"Export: {export_file_name} ({playlist_count} playlists)" if export_file_name else "Export: Not Loaded"
        banner_text.append(f"📁 {export_str}   ", style="cyan" if export_file_name else "yellow")
        
        yt_str = "YouTube Music: Connected" if yt_connected else "YouTube Music: Disconnected"
        banner_text.append(f"🔑 {yt_str}   ", style="green" if yt_connected else "red")

        dz_str = "Deezer: Connected" if deezer_connected else "Deezer: Disconnected"
        banner_text.append(f"🎶 {dz_str}   ", style="green" if deezer_connected else "dim white")

        target_display = "YouTube Music" if active_destination.lower() == "youtube" else "Deezer"
        banner_text.append(f"🎯 Target: {target_display}   ", style="bold magenta")
        banner_text.append(f"🪞 Synced: {active_mirrors_count} playlists", style="bold cyan")

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
            Choice("wizard", "🚀  Transfer Playlists (Spotify → Destination Wizard)"),
            Choice("quick_sync", "⚡  Sync Playlists (Delta Update Synced Playlists)"),
            Choice("diff", "🔍  Preview Changes (Dry-Run / Diff Inspector)"),
            Choice("list", "📋  Browse Discovered Spotify Playlists"),
            Choice("mirrors", "🪞  View Synced Playlists & Status"),
            Choice("audit", "🎯  Match Quality & Audit Explorer"),
            Choice("switch_dest", "🔀  Switch Target Destination (YouTube Music / Deezer)"),
            Choice("auth", "🔑  Platform Connections & Credentials"),
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
    def select_destination_choice(current_dest: str = "youtube") -> str:
        choices = [
            Choice("youtube", "🔴  YouTube Music (Active)" if current_dest == "youtube" else "🔴  YouTube Music"),
            Choice("deezer", "🟣  Deezer (Active)" if current_dest == "deezer" else "🟣  Deezer"),
        ]
        return inquirer.select(
            message="Select Target Music Destination:",
            choices=choices,
            style=INQUIRER_STYLE,
            default=current_dest,
            pointer="❯ "
        ).execute()

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
        plan: Optional[SyncPlan],
        destination_name: str = "YouTube Music"
    ):
        if plan is None:
            content = Text()
            content.append(f"ℹ This playlist is not yet mirrored on {destination_name}.\n", style="bold cyan")
            content.append(f"All {pl.track_count} tracks will be transferred.", style="white")
            panel = Panel(content, title=f"🎵 {pl.name}", box=ROUNDED, border_style="cyan")
            console.print(panel)
            return

        in_sync = [it for it in plan.items if it.action == PlanAction.NO_OP]
        additions = [it for it in plan.items if it.action == PlanAction.ADD_TRACK]
        removals = [it for it in plan.items if it.action == PlanAction.REMOVE_TRACK]
        extra_dest = [it for it in plan.items if it.action == PlanAction.SKIP_TRACK]

        card = Text()
        card.append(f"Source (Spotify): {plan.source_track_count} tracks  │  ", style="dim")
        card.append(f"Destination ({destination_name}): {plan.destination_track_count} tracks  │  ", style="dim")
        card.append(f"In Sync: {len(in_sync)} tracks\n", style="bold green")

        if additions:
            card.append(f"\n➕ Tracks to Add / Restore ({len(additions)}):\n", style="bold green")
            for it in additions:
                if "Missing on" in (it.rationale or ""):
                    card.append(f"  ↺ [RESTORE] {it.source_track.artist} - {it.source_track.name}\n", style="cyan")
                else:
                    card.append(f"  + [ADD]     {it.source_track.artist} - {it.source_track.name}\n", style="green")

        if removals:
            duplicates = [it for it in removals if "Duplicate on" in (it.rationale or "")]
            other_removals = [it for it in removals if "Duplicate on" not in (it.rationale or "")]

            if duplicates:
                card.append(f"\n🗑 Duplicate Tracks on {destination_name} to Prune ({len(duplicates)}):\n", style="bold red")
                for it in duplicates:
                    card.append(f"  - [DUPLICATE] {it.rationale}\n", style="red")

            if other_removals:
                card.append(f"\n➖ Tracks to Remove for 1:1 Mirror ({len(other_removals)}):\n", style="bold red")
                for it in other_removals:
                    card.append(f"  - [REMOVE]    {it.rationale}\n", style="red")

        if extra_dest:
            card.append(f"\n★ {destination_name}-Only Tracks (Preserved) ({len(extra_dest)}):\n", style="bold magenta")
            for it in extra_dest:
                card.append(f"  ★ [{destination_name[:3].upper()} ONLY] {it.rationale}\n", style="magenta")

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
        table.add_column("Dest", justify="center", width=10)
        table.add_column("Destination Playlist", style="cyan")
        table.add_column("Tracks", justify="right", style="magenta")
        table.add_column("Status", justify="center")
        table.add_column("Destination ID", style="blue")

        for m in mirrors:
            dest_badge = "[magenta]Deezer[/magenta]" if getattr(m, "destination", "youtube") == "deezer" else "[red]YouTube[/red]"
            st = m.last_sync_status.value
            if st == "IN_SYNC":
                st_style = "[bold green]IN SYNC[/bold green]"
            elif st == "UNREACHABLE":
                st_style = "[bold red]UNREACHABLE[/bold red]"
            elif st in ["CHANGES_DETECTED", "ATTENTION_NEEDED"]:
                st_style = f"[bold yellow]{st}[/bold yellow]"
            else:
                st_style = f"[yellow]{st}[/yellow]"
            table.add_row(m.name, dest_badge, m.yt_playlist_name, str(m.spotify_track_count), st_style, m.yt_playlist_id)

        console.print(table)

    @staticmethod
    def render_summary_table(reports: List[Dict[str, Any]]):
        total_transferred = sum(r.get("delta_transferred", r.get("synced_tracks", 0)) for r in reports)
        total_omitted = sum(r.get("unmatched_tracks", max(0, r.get("total_tracks", 0) - r.get("synced_tracks", 0))) for r in reports)

        table = Table(title="Migration Summary", box=ROUNDED, border_style="cyan", expand=True)
        table.add_column("Playlist", style="bold white", ratio=2)
        table.add_column("Status", justify="center", width=18)
        table.add_column("Synced/Total", justify="right", width=18)
        table.add_column("Destination URL", style="cyan", ratio=3, overflow="fold")

        for r in reports:
            st = str(r.get("status", ""))
            synced = r.get("synced_tracks", 0)
            total = r.get("total_tracks", 0)
            delta = r.get("delta_transferred")

            if st in ["COMPLETED", "COMPLETE_SUCCESS"]:
                st_style = "[bold green]COMPLETED[/bold green]"
            elif st in ["PARTIAL_SUCCESS", "COMPLETED_WITH_WARNINGS"] or (synced < total and st in ["CREATED", "UPDATED"]):
                st_style = f"[bold yellow]PARTIAL ({synced}/{total})[/bold yellow]"
            elif st == "IN_SYNC":
                st_style = "[bold green]IN SYNC[/bold green]"
            elif st == "FAILED":
                st_style = "[bold red]FAILED[/bold red]"
            elif st == "CANCELLED":
                st_style = "[bold yellow]CANCELLED[/bold yellow]"
            elif st == "SKIPPED":
                st_style = "[dim yellow]SKIPPED[/dim yellow]"
            else:
                st_style = f"[bold green]{st}[/bold green]" if st in ["CREATED", "UPDATED"] else f"[yellow]{st}[/yellow]"

            if delta is not None and delta > 0 and delta != synced:
                cnt_str = f"{synced}/{total} [bold green](+{delta})[/bold green]"
            else:
                cnt_str = f"{synced}/{total}"
            table.add_row(r["name"], st_style, cnt_str, r.get("yt_url", ""))

        console.print(table)

        summary_parts = [
            f"[bold white]Total Playlists:[/bold white] {len(reports)}",
            f"[bold green]Total Tracks Transferred:[/bold green] {total_transferred} [dim](0 duplicates)[/dim]"
        ]
        if total_omitted > 0:
            summary_parts.append(f"[bold yellow]Tracks Omitted / Ambiguous:[/bold yellow] {total_omitted}")

        border = "green" if total_omitted == 0 else "yellow"
        console.print(Panel("   │   ".join(summary_parts), box=ROUNDED, border_style=border))

    @staticmethod
    def render_pre_flight_card(
        playlist_name: str,
        dest_name: str,
        exact_count: int,
        high_count: int,
        ambiguous_count: int,
        unmatched_count: int,
        total_count: int
    ):
        verified_count = exact_count + high_count
        flagged_count = ambiguous_count + unmatched_count
        dest_display = "YouTube Music" if dest_name.lower() == "youtube" else "Deezer"

        txt = Text()
        txt.append(f"Playlist: {playlist_name} ({total_count} tracks) → {dest_display}\n\n", style="bold white")
        txt.append(f"  ✓ {verified_count} tracks verified with high confidence\n", style="bold green")
        if flagged_count > 0:
            txt.append(f"  ⚠ {flagged_count} tracks flagged ({ambiguous_count} ambiguous, {unmatched_count} not found)\n", style="bold yellow")
        else:
            txt.append("  ✓ 100% of tracks matched cleanly with zero ambiguities\n", style="bold green")

        border = "green" if flagged_count == 0 else "yellow"
        console.print(Panel(txt, title="[bold white]Pre-Flight Analysis Summary[/bold white]", box=ROUNDED, border_style=border))

    @staticmethod
    def render_discrepancy_table(discrepancies: List[Any]):
        if not discrepancies:
            return

        table = Table(title=f"Discrepancies & Omitted Tracks ({len(discrepancies)})", box=ROUNDED, border_style="yellow")
        table.add_column("#", justify="right", width=4)
        table.add_column("Source Track", style="bold white", width=26)
        table.add_column("Artist", style="cyan", width=20)
        table.add_column("Type", justify="center", width=12)
        table.add_column("Diagnosis / Rationale", style="dim white")

        for d in discrepancies:
            d_dict = d if isinstance(d, dict) else d.model_dump()
            dtype = d_dict.get("discrepancy_type", "UNMATCHED")
            badge = f"[yellow]{dtype}[/yellow]" if dtype == "AMBIGUOUS" else f"[red]{dtype}[/red]"
            table.add_row(
                str(d_dict.get("track_index", 0) + 1),
                d_dict.get("source_name", ""),
                d_dict.get("source_artist", ""),
                badge,
                d_dict.get("rationale", "")
            )

        console.print(table)

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

    @staticmethod
    def select_audit_menu_action() -> str:
        choices = [
            Choice("scorecard", "📊  View Recent Sync Quality Scorecard"),
            Choice("flagged", "⚠️   Inspect Ambiguous / Low-Confidence Matches"),
            Choice("all_items", "🔍  Inspect Full Track-by-Track Audit for a Job"),
            Choice("logs_match", "📝  View Live Match Audit Log (matching.log)"),
            Choice("logs_app", "📜  View Application System Log (syncstation.log)"),
            Choice("export", "💾  Export Full Match Audit Report to CSV"),
            Separator(),
            Choice("back", "🔙  Return to Main Menu")
        ]
        return inquirer.select(
            message="Match Audit & Logs — Select an action:",
            choices=choices,
            style=INQUIRER_STYLE,
            default="scorecard",
            pointer="❯ "
        ).execute()

    @staticmethod
    def render_audit_scorecard(summary: Dict[str, Any], job_info: Optional[Dict[str, Any]] = None):
        total = summary.get("total", 0)
        exact = summary.get("exact", 0)
        high = summary.get("high", 0)
        probable = summary.get("probable", 0)
        ambiguous = summary.get("ambiguous", 0)
        no_match = summary.get("no_match", 0)
        overrides = summary.get("overrides", 0)
        accepted = summary.get("accepted", 0)

        title = "Sync Quality & Match Confidence Scorecard"
        if job_info:
            title += f" — '{job_info.get('playlist_name')}' (Job: {job_info.get('id')})"

        table = Table(title=title, box=ROUNDED, border_style="cyan")
        table.add_column("Confidence Tier", style="bold white", width=20)
        table.add_column("Threshold", style="dim", width=12)
        table.add_column("Count", justify="right", width=10)
        table.add_column("Percentage", justify="right", width=12)
        table.add_column("Status", width=22)

        def pct(cnt):
            return f"{(cnt / total * 100):.1f}%" if total > 0 else "0.0%"

        table.add_row("💎 EXACT", "≥ 92%", str(exact), pct(exact), "[bold green]Auto-Accepted[/bold green]")
        table.add_row("🟢 HIGH", "80% – 91%", str(high), pct(high), "[bold green]Auto-Accepted[/bold green]")
        table.add_row("🟡 PROBABLE", "65% – 79%", str(probable), pct(probable), "[green]Accepted (Title Safe)[/green]")
        table.add_row("🟠 AMBIGUOUS", "45% – 64%", str(ambiguous), pct(ambiguous), "[bold yellow]Requires Review[/bold yellow]" if ambiguous > 0 else "[dim]None[/dim]")
        table.add_row("🔴 NO MATCH", "< 45%", str(no_match), pct(no_match), "[bold red]Not Found / Skipped[/bold red]" if no_match > 0 else "[dim]None[/dim]")
        if overrides > 0:
            table.add_row("🟣 OVERRIDES", "Manual", str(overrides), pct(overrides), "[magenta]User Enforced[/magenta]")

        console.print(table)

        match_rate = ((exact + high + probable) / total * 100) if total > 0 else 0.0
        health_color = "bold green" if match_rate >= 90 else ("yellow" if match_rate >= 75 else "red")

        summary_panel = Panel(
            f"[bold white]Total Evaluated:[/bold white] {total}   │   "
            f"[{health_color}]High-Confidence Match Rate: {match_rate:.1f}%[/{health_color}]   │   "
            f"[bold cyan]Accepted Tracks:[/bold cyan] {accepted}/{total}",
            box=ROUNDED,
            border_style="cyan"
        )
        console.print(summary_panel)

    @staticmethod
    def render_audit_items_table(items: List[Dict[str, Any]], title: str = "Match Audit"):
        if not items:
            console.print("[yellow]No items found matching the filter.[/yellow]")
            return

        table = Table(title=title, box=ROUNDED, border_style="cyan")
        table.add_column("#", style="dim", width=4)
        table.add_column("Source Track", style="bold white", width=26)
        table.add_column("Source Artist", style="dim white", width=18)
        table.add_column("Matched Destination Track", style="cyan", width=28)
        table.add_column("Tier", justify="center", width=12)
        table.add_column("Score", justify="right", width=7)
        table.add_column("Rationale", style="dim", overflow="fold")

        for it in items:
            tier = it.get("confidence_tier", "NO_MATCH")
            tier_color = {
                "EXACT": "bold green",
                "HIGH": "green",
                "PROBABLE": "yellow",
                "AMBIGUOUS": "bold yellow",
                "NO_MATCH": "bold red"
            }.get(tier, "white")

            matched_title = it.get("matched_title") or "[dim red]No Match[/dim red]"
            matched_artist = it.get("matched_artist") or ""
            matched_display = f"{matched_title} - {matched_artist}" if matched_artist else matched_title

            table.add_row(
                str(it.get("track_index", "")),
                it.get("source_name", "")[:25],
                it.get("source_artist", "")[:17],
                matched_display[:27],
                f"[{tier_color}]{tier}[/{tier_color}]",
                f"{it.get('confidence_score', 0):.2f}",
                it.get("rationale", "")
            )

        console.print(table)

    @staticmethod
    def render_log_lines(lines: List[str], log_type: str = "matching"):
        title = f"📜 Recent {log_type.upper()} Log Entries (Last {len(lines)} lines)"
        content = "\n".join(lines) if lines else "[dim]Log is currently empty.[/dim]"
        console.print(Panel(content, title=title, box=ROUNDED, border_style="cyan"))

    @staticmethod
    def render_auth_dashboard(yt_status: Dict[str, Any], dz_status: Dict[str, Any], yt_proxy: str = "", dz_proxy: str = ""):
        table = Table(
            title="Music Service Status & Network Routing",
            box=ROUNDED,
            border_style="cyan",
            expand=True
        )
        table.add_column("Service", style="bold white", width=18)
        table.add_column("Status", justify="center", width=16)
        table.add_column("Account / Details", style="dim white", width=38)
        table.add_column("Proxy Routing", width=28)

        # YouTube row
        yt_conn = yt_status.get("connected", False)
        yt_badge = "[bold green]CONNECTED (Active)[/bold green]" if yt_conn else "[bold red]DISCONNECTED[/bold red]"
        yt_msg = yt_status.get("message", "Not configured")
        yt_proxy_label = f"[cyan]{yt_proxy}[/cyan]" if yt_proxy else "[dim]⚡ Direct (No Proxy)[/dim]"
        table.add_row("🔴 YouTube Music", yt_badge, yt_msg[:37], yt_proxy_label)

        # Deezer row
        dz_conn = dz_status.get("connected", False)
        dz_badge = "[bold green]CONNECTED[/bold green]" if dz_conn else "[bold red]DISCONNECTED[/bold red]"
        dz_msg = dz_status.get("message", "Not configured")
        dz_proxy_label = f"[cyan]{dz_proxy}[/cyan]" if dz_proxy else "[dim]⚡ Direct (No Proxy)[/dim]"
        table.add_row("🟣 Deezer", dz_badge, dz_msg[:37], dz_proxy_label)

        console.print(table)

    @staticmethod
    def render_proxy_card(service_name: str, current_proxy: str, detected_country: str = "", ping_ms: Optional[int] = None):
        text = Text()
        text.append(f"Target Service:  ", style="bold white")
        text.append(f"{service_name}\n", style="bold cyan")
        
        status_label = f"Active ({current_proxy})" if current_proxy else "Direct Connection (No Proxy)"
        text.append(f"Current Routing: ", style="bold white")
        text.append(f"{status_label}\n", style="green" if current_proxy else "dim white")

        if detected_country:
            text.append(f"Exit Country:    ", style="bold white")
            text.append(f"{detected_country}\n", style="bold yellow")

        if ping_ms is not None:
            text.append(f"Response Time:   ", style="bold white")
            text.append(f"{ping_ms} ms\n", style="cyan")

        panel = Panel(
            text,
            title=f"🌐 Proxy & Routing Configuration — {service_name}",
            box=ROUNDED,
            border_style="cyan"
        )
        console.print(panel)


