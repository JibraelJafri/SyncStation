import os
import sys
import time
import json
import argparse
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

from src.core.config import PROJECT_ROOT
from src.core.database import init_db, DatabaseManager
from src.core.models import Playlist, Track, ConfidenceTier, JobStatus, SyncMode, SyncPlan, PlanAction
from src.providers.spotify.export_source import SpotifyExportSource
from src.providers.youtube.ytmusic_dest import YouTubeMusicDestination
from src.providers.youtube.auth import parse_and_save_headers
from src.domain.matcher import MatchingEngine
from src.domain.normalizer import clean_query_string
from src.services.sync_service import sync_service
from src.services.mirror_service import MirrorService
from src.cli.tui import TUI, console

# ─── INTERACTIVE CLI CONTROLLER ──────────────────────────────────────────────
class InteractiveCLI:
    def __init__(self, export_file: Optional[Path] = None):
        init_db()
        self.export_source: Optional[SpotifyExportSource] = SpotifyExportSource(file_path=export_file) if export_file else SpotifyExportSource()
        self.yt_dest = YouTubeMusicDestination()

    def run(self):
        """Persistent Interactive TUI Loop."""
        self._ensure_export_loaded()

        while True:
            try:
                # Render Hero Header Panel
                meta = self.export_source.get_metadata() if self.export_source else None
                file_name = self.export_source.file_path.name if (self.export_source and self.export_source.file_path) else None
                p_cnt = len(self.export_source.get_playlists()) if self.export_source else 0
                conn_test = self.yt_dest.test_connection()
                mirrors = DatabaseManager.list_mirrors()

                TUI.print_hero_banner(
                    export_file_name=file_name,
                    playlist_count=p_cnt,
                    yt_connected=bool(conn_test.get("connected")),
                    active_mirrors_count=len(mirrors)
                )

                action = TUI.select_main_menu_action()

                if action == "wizard":
                    self.wizard_flow()
                elif action == "diff":
                    self.diff_flow()
                elif action == "quick_sync":
                    self.quick_sync_all_flow()
                elif action == "list":
                    self.list_playlists_flow()
                elif action == "mirrors":
                    self.view_mirrors_flow()
                elif action == "auth":
                    self.auth_flow()
                elif action == "switch_export":
                    self.switch_export_file_flow()
                elif action == "resume":
                    self.resume_flow()
                elif action == "exit":
                    console.print("\n[bold cyan]Thank you for using SyncStation! Goodbye.[/bold cyan]\n")
                    break

            except KeyboardInterrupt:
                console.print("\n\n[yellow]Operation cancelled by user.[/yellow]")
                continue
            except Exception as e:
                console.print(f"\n[bold red]An error occurred: {e}[/bold red]")
                time.sleep(1.5)

    def _ensure_export_loaded(self):
        if not self.export_source or not self.export_source.file_path or not self.export_source.file_path.exists():
            console.print("\n[yellow]Locating Spotify Export Data...[/yellow]")
            self.export_source = self._select_export_source()

    def wizard_flow(self):
        """Guided Step-by-Step Migration Wizard."""
        self._ensure_export_loaded()
        if not self.export_source or not self.export_source.file_path:
            console.print("[bold red]No export file loaded. Aborting.[/bold red]")
            return

        playlists = self.export_source.get_playlists()
        if not playlists:
            console.print("[bold red]No playlists found in export. Aborting.[/bold red]")
            return

        if not self._ensure_youtube_auth():
            return

        console.print(f"\n[bold white]Select Playlists to Migrate / Synchronize:[/bold white]")
        selected_playlists = TUI.select_playlists_checkbox(playlists)
        if not selected_playlists:
            console.print("[yellow]No playlists selected.[/yellow]")
            time.sleep(1)
            return

        strategy = TUI.select_strategy()

        console.print(f"\n[bold cyan]Processing {len(selected_playlists)} playlist(s)...[/bold cyan]")
        reports = []
        for idx, pl in enumerate(selected_playlists, 1):
            console.print(f"\n[bold white][{idx}/{len(selected_playlists)}][/bold white] [bold cyan]{pl.name}[/bold cyan] ({pl.track_count} tracks)")
            rep = self._process_playlist(pl, strategy)
            if rep:
                reports.append(rep)

        TUI.render_summary_table(reports)
        self._post_sync_action_menu()

    def quick_sync_all_flow(self):
        """Fast batch delta update across all discovered playlists."""
        self._ensure_export_loaded()
        if not self.export_source:
            return
        if not self._ensure_youtube_auth():
            return

        playlists = self.export_source.get_playlists()
        console.print(f"\n[bold white]Running Delta Sync on all {len(playlists)} playlists (0 duplicates guarantee)...[/bold white]")

        reports = []
        for idx, pl in enumerate(playlists, 1):
            console.print(f"\n[bold white][{idx}/{len(playlists)}][/bold white] [bold cyan]{pl.name}[/bold cyan] ({pl.track_count} tracks)")
            rep = self._process_playlist(pl, "UPDATE")
            if rep:
                reports.append(rep)

        TUI.render_summary_table(reports)
        self._post_sync_action_menu()

    def list_playlists_flow(self, pause: bool = True):
        self._ensure_export_loaded()
        if not self.export_source or not self.export_source.file_path:
            console.print("[bold red]No Spotify export file loaded.[/bold red]")
            return

        pls = self.export_source.get_playlists()
        console.print(f"\n[bold white]Playlists in {self.export_source.file_path.name} ({len(pls)} total):[/bold white]")
        for idx, p in enumerate(pls, 1):
            console.print(f"  [bold cyan]{idx:>2d}.[/bold cyan] {p.name:<40} [dim]({p.track_count:>4d} tracks)[/dim]")
        
        if pause:
            input("\nPress Enter to return to main menu...")

    def diff_flow(self, target_name: Optional[str] = None, interactive: bool = True):
        """Inspect detailed differences / dry run without making changes."""
        self._ensure_export_loaded()
        if not self.export_source:
            return
        if not self._ensure_youtube_auth():
            return

        playlists = self.export_source.get_playlists()
        if not playlists:
            console.print("[bold red]No playlists found in export file.[/bold red]")
            return

        if target_name:
            target = next((p for p in playlists if p.name.lower() == target_name.lower()), None)
            if not target:
                console.print(f"[bold red]Playlist '{target_name}' not found in export.[/bold red]")
                return
            self._inspect_single_playlist_diff(target, interactive=interactive)
            return

        if not interactive:
            for p in playlists:
                self._inspect_single_playlist_diff(p, interactive=False)
            return

        while True:
            selected_pl = TUI.select_single_playlist(playlists, "Select a playlist to inspect differences (Dry-Run):")
            if not selected_pl:
                break
            self._inspect_single_playlist_diff(selected_pl, interactive=True)

    def _inspect_single_playlist_diff(self, pl: Playlist, interactive: bool = True):
        console.print(f"\n[bold cyan]--> Fetching live YouTube Music state for '{pl.name}'...[/bold cyan]")
        plan = self._compute_sync_plan_for_playlist(pl)
        TUI.render_diff_card(pl, plan)

        if not interactive:
            return

        if plan is None:
            if TUI.confirm_action(f"Transfer '{pl.name}' to YouTube Music now?"):
                self._process_playlist(pl, "UPDATE")
        else:
            additions = [it for it in plan.items if it.action == PlanAction.ADD_TRACK]
            removals = [it for it in plan.items if it.action == PlanAction.REMOVE_TRACK]
            if additions or removals:
                if TUI.confirm_action(f"Sync '{pl.name}' now ({len(additions)} additions, {len(removals)} removals)?"):
                    self._process_playlist(pl, "UPDATE", precomputed_plan=plan)
            else:
                input("Press Enter to continue...")

    def _compute_sync_plan_for_playlist(self, pl: Playlist) -> Optional[SyncPlan]:
        try:
            mirrors = DatabaseManager.list_mirrors()
            name_clean = pl.name.lower().strip()
            matched_mirror = next((m for m in mirrors if m.name.lower().strip() == name_clean or m.spotify_id == pl.id), None)

            yt_library = self.yt_dest.get_library_playlists() if self.yt_dest.is_available() else []
            matched_yt = next((y for y in yt_library if y.get("title", "").lower().strip() == name_clean), None)

            if matched_mirror:
                yt_pid = matched_mirror.yt_playlist_id
                yt_live = self.yt_dest.get_playlist(yt_pid)
                if yt_live is None:
                    console.print(f"  [yellow]Notice: YouTube playlist for '{pl.name}' was deleted on YouTube Music. Resetting mirror.[/yellow]")
                    DatabaseManager.delete_mirror(matched_mirror.id)
                    matched_mirror = None
                    matched_yt = None

            if matched_mirror or matched_yt:
                yt_pid = matched_mirror.yt_playlist_id if matched_mirror else matched_yt.get("playlistId")
                if not matched_mirror:
                    matched_mirror = MirrorService.link_existing_mirror(pl.name, yt_pid, source_type="export", file_path=str(self.export_source.file_path))
                
                return sync_service.generate_sync_plan(matched_mirror.id, source=self.export_source)
        except Exception as e:
            console.print(f"  [yellow]Warning: Could not fetch YouTube Music playlist ({e}).[/yellow]")
        return None

    def view_mirrors_flow(self, pause: bool = True):
        mirrors = DatabaseManager.list_mirrors()
        TUI.render_mirrors_table(mirrors)
        if pause:
            input("\nPress Enter to return to main menu...")

    def auth_flow(self, interactive: bool = True):
        test = self.yt_dest.test_connection()
        console.print(f"\n[bold white]YouTube Music Session Status:[/bold white]")
        if test.get("connected"):
            console.print(f"  Status:  [bold green]CONNECTED (Active)[/bold green]")
            console.print(f"  Details: {test.get('message')}")
            if interactive:
                if TUI.confirm_action("Would you like to re-enter / renew your session headers?", default=False):
                    self._prompt_for_auth()
        else:
            console.print(f"  Status:  [bold red]DISCONNECTED / EXPIRED[/bold red]")
            console.print(f"  Details: {test.get('message')}")
            if interactive:
                self._prompt_for_auth()

    def switch_export_file_flow(self):
        console.print(f"\n[bold white]Select New Spotify Export File:[/bold white]")
        new_src = self._select_export_source(prompt_default=False)
        if new_src and new_src.file_path:
            self.export_source = new_src
            console.print(f"[bold green]✓ Switched active export file to: {new_src.file_path.name}[/bold green]")
            time.sleep(1)

    def resume_flow(self):
        interrupted = DatabaseManager.get_interrupted_jobs()
        if not interrupted:
            console.print("\n[bold green]✓ No interrupted sync jobs found.[/bold green]")
            time.sleep(1)
            return

        console.print(f"\nFound {len(interrupted)} interrupted migration jobs:")
        for j in interrupted:
            console.print(f" • Job {j['id']}: '{j['playlist_name']}' ({j['synced_tracks']}/{j['total_tracks']} tracks synced)")

        if TUI.confirm_action(f"Resume all {len(interrupted)} jobs?"):
            for j in interrupted:
                console.print(f"Resuming job {j['id']} for '{j['playlist_name']}'...")
                sync_service.execute_transfer(j["id"])
            console.print("[bold green]✓ Interrupted jobs resumed and processed.[/bold green]")

    def _post_sync_action_menu(self):
        """Interactive Action prompt displayed after playlist migration finishes."""
        while True:
            from InquirerPy import inquirer
            from InquirerPy.base.control import Choice
            
            c = inquirer.select(
                message="What would you like to do next?",
                choices=[
                    Choice("another", "🔄  Sync another playlist"),
                    Choice("mirrors", "🪞  View active mirrors"),
                    Choice("menu", "🏠  Return to Main Menu"),
                    Choice("exit", "🚪  Exit")
                ],
                default="menu",
                pointer="❯ "
            ).execute()

            if c == "another":
                self.wizard_flow()
                break
            elif c == "mirrors":
                self.view_mirrors_flow()
            elif c == "menu":
                break
            elif c == "exit":
                console.print("\n[bold cyan]Thank you for using SyncStation! Goodbye.[/bold cyan]\n")
                sys.exit(0)

    def _select_export_source(self, prompt_default: bool = True) -> Optional[SpotifyExportSource]:
        src = SpotifyExportSource()
        if prompt_default and src.file_path and src.file_path.exists():
            meta = src.get_metadata()
            p_cnt = meta.playlist_count if meta else len(src.get_playlists())
            console.print(f"Auto-detected export file: [bold cyan]{src.file_path.name}[/bold cyan] ({p_cnt} playlists)")
            if TUI.confirm_action("Use this auto-detected file?"):
                return src

        while True:
            path_input = input("Enter path to your Spotify export file (.json, .csv, or .zip) or 'q' to cancel: ").strip("\"' ")
            if path_input.lower() == "q":
                return None
            p = Path(path_input)
            if p.exists():
                custom_src = SpotifyExportSource(file_path=p)
                if custom_src.get_playlists():
                    return custom_src
                else:
                    console.print(f"[bold red]No valid playlist data found in '{p}'. Please try another file.[/bold red]")
            else:
                console.print(f"[bold red]File '{path_input}' does not exist. Please check the path.[/bold red]")

    def _ensure_youtube_auth(self) -> bool:
        test = self.yt_dest.test_connection()
        if test.get("connected"):
            return True
        return self._prompt_for_auth()

    def _prompt_for_auth(self) -> bool:
        console.print(f"\n[yellow]⚠️ YouTube Music authentication required.[/yellow]")
        console.print("To connect your YouTube Music account:")
        console.print("1. Open Chrome/Edge/Firefox and log in to [cyan]https://music.youtube.com[/cyan]")
        console.print("2. Press F12 -> Network tab -> refresh -> click any request (e.g. 'browse' or 'v1/')")
        console.print("3. Right-click the request -> Copy -> 'Copy as cURL' (or copy the 'cookie' header)\n")
        
        raw = input("Paste cURL command or cookie string here (or press Enter to cancel): ").strip()
        if not raw:
            return False

        if parse_and_save_headers(raw):
            self.yt_dest = YouTubeMusicDestination()
            test = self.yt_dest.test_connection()
            if test.get("connected"):
                console.print(f"[bold green]✓ Successfully authenticated YouTube Music![/bold green]")
                return True
            else:
                console.print(f"[bold red]Failed to validate YouTube session: {test.get('message')}[/bold red]")
                return False
        else:
            console.print(f"[bold red]Could not parse valid session headers from input.[/bold red]")
            return False

    def _process_playlist(self, pl: Playlist, strategy: str, precomputed_plan: Optional[SyncPlan] = None) -> Optional[Dict[str, Any]]:
        mirrors = DatabaseManager.list_mirrors()
        name_clean = pl.name.lower().strip()
        matched_mirror = next((m for m in mirrors if m.name.lower().strip() == name_clean or m.spotify_id == pl.id), None)

        yt_library = self.yt_dest.get_library_playlists() if self.yt_dest.is_available() else []
        matched_yt = next((y for y in yt_library if y.get("title", "").lower().strip() == name_clean), None)

        if strategy == "SKIP_EXISTING" and (matched_mirror or matched_yt):
            console.print(f"  [yellow]Skipping '{pl.name}' (already exists on YouTube Music).[/yellow]")
            return {
                "name": pl.name,
                "status": "SKIPPED",
                "synced_tracks": 0,
                "total_tracks": pl.track_count,
                "yt_url": f"https://music.youtube.com/playlist?list={matched_mirror.yt_playlist_id if matched_mirror else matched_yt.get('playlistId')}"
            }

        # If matching playlist exists and strategy is UPDATE, execute delta sync
        if strategy == "UPDATE" and (matched_mirror or matched_yt):
            yt_pid = matched_mirror.yt_playlist_id if matched_mirror else matched_yt.get("playlistId")
            
            # Verify live destination playlist exists on YouTube Music
            yt_live = self.yt_dest.get_playlist(yt_pid)
            if yt_live is None:
                console.print(f"  [yellow]⚠️ Destination playlist '{pl.name}' (ID: {yt_pid}) was deleted on YouTube Music.[/yellow]")
                console.print(f"  [yellow]Resetting mirror and creating a fresh YouTube Music playlist...[/yellow]")
                if matched_mirror:
                    DatabaseManager.delete_mirror(matched_mirror.id)
                matched_mirror = None
                matched_yt = None

        if strategy == "UPDATE" and (matched_mirror or matched_yt):
            yt_pid = matched_mirror.yt_playlist_id if matched_mirror else matched_yt.get("playlistId")
            if not matched_mirror:
                matched_mirror = MirrorService.link_existing_mirror(pl.name, yt_pid, source_type="export", file_path=str(self.export_source.file_path))

            console.print(f"  Existing mirror detected: [bold cyan]{matched_mirror.yt_playlist_name}[/bold cyan] (ID: {yt_pid})")

            if precomputed_plan:
                plan = precomputed_plan
            else:
                console.print("  Generating sync plan to detect new additions...")
                plan = sync_service.generate_sync_plan(matched_mirror.id, source=self.export_source)
            if plan:
                new_adds = plan.additions_count - plan.restorations_count
                parts = []
                if new_adds > 0:
                    parts.append(f"[bold green]+{new_adds} new tracks to add[/bold green]")
                if plan.restorations_count > 0:
                    parts.append(f"[bold cyan]↺ {plan.restorations_count} missing tracks to restore on YouTube Music[/bold cyan]")
                if plan.removals_count > 0:
                    parts.append(f"[bold red]-{plan.removals_count} tracks removed from Spotify[/bold red]")
                parts.append(f"{plan.unchanged_count} already in sync")
                console.print(f"  Analysis: {', '.join(parts)}")

                if plan.additions_count == 0 and plan.removals_count == 0:
                    console.print(f"  [bold green]✓ Playlist is already 100% in sync.[/bold green]")
                    return {
                        "name": pl.name,
                        "status": "IN_SYNC",
                        "synced_tracks": 0,
                        "total_tracks": plan.source_track_count,
                        "yt_url": f"https://music.youtube.com/playlist?list={yt_pid}"
                    }

                # Execute delta sync with Rich progress bar
                with TUI.get_progress_bar() as progress:
                    task = progress.add_task(f"Delta Sync '{pl.name}'", total=plan.additions_count, current_track="")
                    report = sync_service.execute_delta_sync(
                        matched_mirror.id,
                        plan=plan,
                        destination=self.yt_dest,
                        source=self.export_source,
                        progress_callback=lambda cur, tot, trk: progress.update(task, completed=cur, total=tot, current_track=trk)
                    )

                console.print(f"  [bold green]✓ Transferred {report.total_synced_tracks} new tracks to YouTube Music.[/bold green]")
                return {
                    "name": pl.name,
                    "status": "UPDATED",
                    "synced_tracks": report.total_synced_tracks,
                    "total_tracks": report.total_source_tracks,
                    "yt_url": report.yt_playlist_url
                }

        # Otherwise: Initial Full Transfer
        console.print(f"  Establishing new YouTube Music mirror for '{pl.name}'...")
        full_pl = self.export_source.get_playlist_tracks(pl.name)
        total_tracks = len(full_pl.tracks)
        
        # Step A: Matching phase with Rich progress bar
        with TUI.get_progress_bar() as progress:
            task = progress.add_task(f"Matching '{pl.name}'", total=total_tracks, current_track="")
            job = sync_service.analyze_for_transfer(
                self.export_source,
                pl.name,
                destination=self.yt_dest,
                progress_callback=lambda cur, tot, trk: progress.update(task, completed=cur, total=tot, current_track=trk)
            )

        # Step B: Execution phase
        console.print("  Creating YouTube Music playlist and transferring tracks...")
        report = sync_service.execute_transfer(job.id, destination=self.yt_dest)
        console.print(f"  [bold green]✓ Successfully transferred {report.total_synced_tracks}/{report.total_source_tracks} tracks.[/bold green]")
        return {
            "name": pl.name,
            "status": "CREATED",
            "synced_tracks": report.total_synced_tracks,
            "total_tracks": report.total_source_tracks,
            "yt_url": report.yt_playlist_url
        }

# ─── COMMAND LINE ENTRYPOINT ────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="SyncStation — High-precision Spotify to YouTube Music Playlist Migration & Sync Engine."
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Command: list
    parser_list = subparsers.add_parser("list", help="List all playlists in the Spotify export file")
    parser_list.add_argument("--file", "-f", help="Path to Spotify export file (.json, .csv, .zip)")

    # Command: sync
    parser_sync = subparsers.add_parser("sync", help="Synchronize Spotify playlists to YouTube Music")
    parser_sync.add_argument("--name", "-n", help="Specific playlist name to sync")
    parser_sync.add_argument("--all", "-a", action="store_true", help="Sync all playlists automatically")
    parser_sync.add_argument("--file", "-f", help="Path to Spotify export file")
    parser_sync.add_argument("--privacy", default="PRIVATE", choices=["PUBLIC", "PRIVATE", "UNLISTED"], help="Privacy for newly created playlists")

    # Command: diff
    parser_diff = subparsers.add_parser("diff", help="Inspect differences / dry run between Spotify and YouTube Music")
    parser_diff.add_argument("--name", "-n", help="Specific playlist name to inspect")
    parser_diff.add_argument("--file", "-f", help="Path to Spotify export file")

    # Command: mirrors
    subparsers.add_parser("mirrors", help="List all active mirrored playlists and sync states")

    # Command: auth
    subparsers.add_parser("auth", help="Check or configure YouTube Music authentication")

    # Command: resume
    subparsers.add_parser("resume", help="Resume any interrupted sync operations")

    args = parser.parse_args()

    if not args.command:
        # Default: Launch Interactive TUI Manager
        cli = InteractiveCLI()
        cli.run()
        return

    init_db()

    if args.command == "diff":
        cli = InteractiveCLI(export_file=Path(args.file) if args.file else None)
        cli.diff_flow(target_name=args.name, interactive=False)

    elif args.command == "list":
        cli = InteractiveCLI(export_file=Path(args.file) if args.file else None)
        cli.list_playlists_flow(pause=False)

    elif args.command == "mirrors":
        cli = InteractiveCLI()
        cli.view_mirrors_flow(pause=False)

    elif args.command == "auth":
        cli = InteractiveCLI()
        cli.auth_flow(interactive=False)

    elif args.command == "resume":
        cli = InteractiveCLI()
        cli.resume_flow()

    elif args.command == "sync":
        cli = InteractiveCLI(export_file=Path(args.file) if args.file else None)
        if not cli.export_source:
            console.print("[bold red]No valid export file found.[/bold red]")
            return
        if not cli._ensure_youtube_auth():
            return

        pls = cli.export_source.get_playlists()
        if args.name:
            target = next((p for p in pls if p.name.lower() == args.name.lower()), None)
            if not target:
                console.print(f"[bold red]Playlist '{args.name}' not found in export.[/bold red]")
                return
            to_sync = [target]
        else:
            to_sync = pls

        console.print(f"\n[bold white]Synchronizing {len(to_sync)} playlist(s)...[/bold white]")
        reports = []
        for p in to_sync:
            rep = cli._process_playlist(p, "UPDATE")
            if rep:
                reports.append(rep)
        TUI.render_summary_table(reports)

if __name__ == "__main__":
    main()
