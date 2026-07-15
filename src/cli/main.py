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

import time
import json
import argparse
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

from src.core.config import PROJECT_ROOT, AppConfig
from src.core.database import init_db, DatabaseManager, get_db
from src.core.models import Playlist, Track, ConfidenceTier, JobStatus, SyncMode, SyncPlan, PlanAction, MirrorStatus, TransferOutcome, TrackDiscrepancy
from src.providers.spotify.export_source import SpotifyExportSource
from src.providers.youtube.ytmusic_dest import YouTubeMusicDestination
from src.providers.youtube.auth import parse_and_save_headers
from src.domain.matcher import MatchingEngine
from src.domain.normalizer import clean_query_string
from src.services.sync_service import sync_service
from src.services.mirror_service import MirrorService
from src.services.review_service import ReviewService, ExportService
from src.core.logger import get_log_tail
from src.providers.factory import DestinationRegistry
from src.providers.deezer.auth import DeezerAuthManager
from src.cli.tui import TUI, console, INQUIRER_STYLE
from InquirerPy import inquirer
from InquirerPy.base.control import Choice

# ─── INTERACTIVE CLI CONTROLLER ──────────────────────────────────────────────
class InteractiveCLI:
    def __init__(self, export_file: Optional[Path] = None, destination: Optional[str] = None):
        init_db()
        self.export_source: Optional[SpotifyExportSource] = SpotifyExportSource(file_path=export_file) if export_file else SpotifyExportSource()
        if destination:
            DestinationRegistry.set_active_destination_name(destination)
        self.active_dest_name = DestinationRegistry.get_active_destination_name()
        self.dest = DestinationRegistry.get_destination(self.active_dest_name)
        self.yt_dest = YouTubeMusicDestination()

    def run(self):
        """Persistent Interactive TUI Loop."""
        self._ensure_export_loaded()

        while True:
            try:
                TUI.clear_screen()
                # Render Hero Header Panel
                meta = self.export_source.get_metadata() if self.export_source else None
                file_name = self.export_source.file_path.name if (self.export_source and self.export_source.file_path) else None
                p_cnt = len(self.export_source.get_playlists()) if self.export_source else 0
                statuses = DestinationRegistry.get_all_statuses(force_refresh=False)
                yt_ok = bool(statuses.get("youtube", {}).get("connected"))
                dz_ok = bool(statuses.get("deezer", {}).get("connected"))
                mirrors = DatabaseManager.list_mirrors()

                TUI.print_hero_banner(
                    export_file_name=file_name,
                    playlist_count=p_cnt,
                    yt_connected=yt_ok,
                    deezer_connected=dz_ok,
                    active_destination=self.active_dest_name,
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
                elif action == "audit":
                    self.audit_flow()
                elif action == "switch_dest":
                    self.switch_dest_flow()
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

        if not self._ensure_destination_auth():
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
        """Fast batch delta update across synced playlists."""
        self._ensure_export_loaded()
        if not self.export_source:
            return
        if not self._ensure_destination_auth():
            return

        playlists = self.export_source.get_playlists()
        dest_display = "Deezer" if self.active_dest_name == "deezer" else "YouTube Music"
        mirrors = DatabaseManager.list_mirrors(destination=self.active_dest_name)
        active_mirror_names = {m.name.lower().strip() for m in mirrors if m.last_sync_status != MirrorStatus.UNREACHABLE}

        mirrored_pls = [p for p in playlists if p.name.lower().strip() in active_mirror_names]
        unmirrored_pls = [p for p in playlists if p.name.lower().strip() not in active_mirror_names]

        if not mirrored_pls:
            console.print(f"\n[yellow]No active synced playlists found for {dest_display}.[/yellow]")
            console.print(f"[dim]You have {len(unmirrored_pls)} playlists in your export that haven't been transferred yet.[/dim]")
            if inquirer.confirm(message="Would you like to open the Transfer Wizard instead?", default=True).execute():
                self.wizard_flow()
            return

        if unmirrored_pls:
            console.print(f"\n[bold cyan]Found {len(mirrored_pls)} active synced playlist(s) to update.[/bold cyan]")
            console.print(f"[dim]Notice: {len(unmirrored_pls)} playlist(s) in your export have not been transferred yet.[/dim]")
            choices = [
                Choice("sync_mirrors", f"Sync the {len(mirrored_pls)} active synced playlist(s) only (Recommended)"),
                Choice("sync_all", f"Sync {len(mirrored_pls)} and transfer {len(unmirrored_pls)} unmirrored playlists as new"),
                Choice("cancel", "Cancel")
            ]
            act = inquirer.select(message="Select batch action:", choices=choices, default="sync_mirrors").execute()
            if act == "cancel":
                return
            target_pls = playlists if act == "sync_all" else mirrored_pls
        else:
            target_pls = mirrored_pls

        console.print(f"\n[bold white]Running Delta Sync on {len(target_pls)} playlists (0 duplicates guarantee)...[/bold white]")

        reports = []
        for idx, pl in enumerate(target_pls, 1):
            console.print(f"\n[bold white][{idx}/{len(target_pls)}][/bold white] [bold cyan]{pl.name}[/bold cyan] ({pl.track_count} tracks)")
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

        if not pause:
            return

        choices = [
            Choice("back", "🔙 Return to Main Menu"),
            Choice("transfer", "🚀 Select a playlist to Transfer / Sync"),
            Choice("diff", "🔍 Inspect Diff for a playlist")
        ]
        act = inquirer.select(message="Actions on discovered playlists:", choices=choices, default="back").execute()
        if act == "back":
            return
        elif act in ["transfer", "diff"]:
            p_choices = [Choice(p.name, f"{p.name} ({p.track_count} tracks)") for p in pls]
            p_choices.append(Choice("cancel", "Cancel"))
            chosen_name = inquirer.select(message="Select playlist:", choices=p_choices).execute()
            if chosen_name == "cancel":
                return
            chosen_pl = next(p for p in pls if p.name == chosen_name)
            if act == "diff":
                self.diff_flow(target_name=chosen_pl.name)
            else:
                strat = TUI.select_strategy()
                rep = self._process_playlist(chosen_pl, strat)
                if rep:
                    TUI.render_summary_table([rep])
                    self._post_sync_action_menu()

    def diff_flow(self, target_name: Optional[str] = None, interactive: bool = True):
        """Inspect detailed differences / dry run without making changes."""
        self._ensure_export_loaded()
        if not self.export_source:
            return
        if not self._ensure_destination_auth():
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
        dest_display = "Deezer" if self.active_dest_name == "deezer" else "YouTube Music"
        console.print(f"\n[bold cyan]--> Fetching live {dest_display} state for '{pl.name}'...[/bold cyan]")
        plan = self._compute_sync_plan_for_playlist(pl)
        TUI.render_diff_card(pl, plan, destination_name=dest_display)

        if not interactive:
            return

        if plan is None:
            if TUI.confirm_action(f"Transfer '{pl.name}' to {dest_display} now?"):
                self._process_playlist(pl, "UPDATE")
        else:
            additions = [it for it in plan.items if it.action == PlanAction.ADD_TRACK]
            removals = [it for it in plan.items if it.action == PlanAction.REMOVE_TRACK]
            if additions or removals:
                if TUI.confirm_action(f"Sync '{pl.name}' with {dest_display} now ({len(additions)} additions, {len(removals)} removals)?"):
                    for it in removals:
                        it.is_accepted = True
                    self._process_playlist(pl, "UPDATE", precomputed_plan=plan)
            else:
                input("Press Enter to continue...")

    def _compute_sync_plan_for_playlist(self, pl: Playlist) -> Optional[SyncPlan]:
        dest_display = "Deezer" if self.active_dest_name == "deezer" else "YouTube Music"
        try:
            dest = self.dest or DestinationRegistry.get_destination(self.active_dest_name)
            mirrors = DatabaseManager.list_mirrors(destination=self.active_dest_name)
            name_clean = pl.name.lower().strip()
            matched_mirror = next((m for m in mirrors if m.name.lower().strip() == name_clean or m.spotify_id == pl.id), None)

            library = dest.get_library_playlists() if (hasattr(dest, "get_library_playlists") and dest.is_available()) else []
            matched_remote = next((y for y in library if y.get("title", "").lower().strip() == name_clean), None)

            if matched_mirror:
                remote_pid = matched_mirror.yt_playlist_id
                live_pl = dest.get_playlist(remote_pid)
                if live_pl is None:
                    console.print(f"  [yellow]Notice: Destination playlist for '{pl.name}' could not be reached on {dest_display}. Preserving local record.[/yellow]")
                    DatabaseManager.update_mirror_status(matched_mirror.id, MirrorStatus.UNREACHABLE.value)
                    matched_mirror = None
                    matched_remote = None

            if matched_mirror or matched_remote:
                remote_pid = matched_mirror.yt_playlist_id if matched_mirror else matched_remote.get("playlistId")
                if not matched_mirror:
                    matched_mirror = MirrorService.link_existing_mirror(
                        pl.name,
                        remote_pid,
                        source_type="export",
                        file_path=str(self.export_source.file_path),
                        destination=dest,
                        destination_name=self.active_dest_name
                    )
                
                return sync_service.generate_sync_plan(matched_mirror.id, source=self.export_source, destination=dest)
        except Exception as e:
            console.print(f"  [yellow]Warning: Could not fetch {dest_display} playlist ({e}).[/yellow]")
        return None

    def view_mirrors_flow(self, pause: bool = True):
        if pause:
            TUI.clear_screen()
        mirrors = DatabaseManager.list_mirrors()
        TUI.render_mirrors_table(mirrors)
        if not pause or not mirrors:
            if pause:
                input("\nPress Enter to return to main menu...")
            return

        choices = [
            Choice("back", "🔙 Return to Main Menu"),
            Choice("diff", "🔍 Inspect Differences / Dry Run for a synced playlist"),
            Choice("sync_one", "⚡ Sync / Update a single playlist"),
            Choice("delete", "🗑 Unlink / Remove a synced playlist record")
        ]
        action = inquirer.select(message="Select action on synced playlists:", choices=choices, default="back").execute()
        if action == "back":
            return
        elif action in ["diff", "sync_one", "delete"]:
            pl_choices = [Choice(m.id, f"{m.name} ({m.spotify_track_count} tracks) → {m.yt_playlist_name}") for m in mirrors]
            pl_choices.append(Choice("cancel", "Cancel"))
            chosen_id = inquirer.select(message="Select playlist:", choices=pl_choices).execute()
            if chosen_id == "cancel":
                return
            chosen_mirror = next(m for m in mirrors if m.id == chosen_id)
            if action == "diff":
                self.diff_flow(target_name=chosen_mirror.name)
            elif action == "sync_one":
                dummy_pl = Playlist(id=chosen_mirror.spotify_id, name=chosen_mirror.name, track_count=chosen_mirror.spotify_track_count)
                rep = self._process_playlist(dummy_pl, strategy="UPDATE")
                if rep:
                    TUI.render_summary_table([rep])
                    self._post_sync_action_menu()
            elif action == "delete":
                if inquirer.confirm(message=f"Are you sure you want to unlink '{chosen_mirror.name}'? (Destination playlist will NOT be deleted)", default=False).execute():
                    MirrorService.unlink_mirror(chosen_mirror.id)
                    console.print(f"[bold green]✓ Unlinked '{chosen_mirror.name}'.[/bold green]")
                    time.sleep(1)

    def audit_flow(
        self,
        job_id: Optional[str] = None,
        interactive: bool = True,
        flagged_only: bool = False,
        export: bool = False
    ):
        """Match Audit and Diagnostic Logging Explorer."""
        latest_job = DatabaseManager.get_job(job_id) if job_id else DatabaseManager.get_latest_job()

        if not latest_job and not interactive:
            console.print("[yellow]No sync jobs found in database to audit.[/yellow]")
            return

        if export and latest_job:
            csv_path = ExportService.export_job_csv_file(latest_job["id"])
            console.print(f"[bold green]✓ Exported match audit report to:[/bold green] {csv_path}")
            return

        if flagged_only and latest_job:
            flagged = ReviewService.get_audit_items(latest_job["id"], only_flagged=True)
            TUI.render_audit_items_table(flagged, title=f"⚠️ Flagged / Ambiguous Matches — '{latest_job['playlist_name']}'")
            return

        if not interactive and latest_job:
            summary = ReviewService.get_audit_summary(latest_job["id"])
            TUI.render_audit_scorecard(summary, job_info=latest_job)
            return

        while True:
            action = TUI.select_audit_menu_action()

            if action == "back":
                break

            if action == "logs_match":
                lines = get_log_tail("matching", lines=40)
                TUI.render_log_lines(lines, log_type="matching")
                input("\nPress Enter to continue...")
                continue

            if action == "logs_app":
                lines = get_log_tail("syncstation", lines=40)
                TUI.render_log_lines(lines, log_type="syncstation")
                input("\nPress Enter to continue...")
                continue

            if not latest_job:
                console.print("\n[yellow]No sync jobs found in database yet. Run a migration or delta sync first.[/yellow]")
                input("\nPress Enter to continue...")
                break

            cur_job_id = latest_job["id"]

            if action == "scorecard":
                summary = ReviewService.get_audit_summary(cur_job_id)
                TUI.render_audit_scorecard(summary, job_info=latest_job)
                input("\nPress Enter to continue...")

            elif action == "flagged":
                flagged_items = ReviewService.get_audit_items(cur_job_id, only_flagged=True)
                if not flagged_items:
                    console.print(f"\n[bold green]✓ Zero ambiguous or low-confidence matches in job '{latest_job['playlist_name']}'.[/bold green]")
                else:
                    TUI.render_audit_items_table(flagged_items, title=f"⚠️ Flagged Matches — '{latest_job['playlist_name']}'")
                    if TUI.confirm_action("Would you like to manually override one of these matches?", default=False):
                        self._prompt_manual_override(flagged_items)
                input("\nPress Enter to continue...")

            elif action == "all_items":
                all_items = ReviewService.get_audit_items(cur_job_id, limit=300)
                TUI.render_audit_items_table(all_items, title=f"Full Match Audit — '{latest_job['playlist_name']}'")
                input("\nPress Enter to continue...")

            elif action == "export":
                csv_path = ExportService.export_job_csv_file(cur_job_id)
                console.print(f"\n[bold green]✓ Successfully exported match audit report to:[/bold green] [cyan]{csv_path}[/cyan]")
                input("\nPress Enter to continue...")

    def _prompt_manual_override(self, items: List[Dict[str, Any]]):
        from InquirerPy import inquirer
        from InquirerPy.base.control import Choice
        choices = [
            Choice(
                value=it,
                name=f"#{it['track_index']} {it['source_name']} - {it['source_artist']} -> [{it.get('matched_video_id', 'None')}] {it.get('matched_title', 'None')}"
            )
            for it in items
        ]
        chosen = inquirer.select(message="Select track to override:", choices=choices).execute()
        if not chosen:
            return

        alts = chosen.get("alternatives", [])
        if alts:
            alt_choices = [
                Choice(
                    value=a,
                    name=f"[{a.get('video_id')}] {a.get('title')} - {a.get('artist')} ({int(a.get('duration_seconds', 0))}s)"
                )
                for a in alts
            ]
            alt_choices.append(Choice(value="custom", name="Custom destination track ID entry..."))
            sel_alt = inquirer.select(message="Select alternative match candidate:", choices=alt_choices).execute()
            if sel_alt and sel_alt != "custom":
                res = ReviewService.apply_and_push_override(
                    item_id=chosen["id"],
                    video_id=sel_alt["video_id"],
                    title=sel_alt["title"],
                    artist=sel_alt["artist"],
                    destination=self.dest
                )
                if res.get("pushed"):
                    console.print(f"[bold green]✓ Override applied and pushed to destination playlist for '{chosen['source_name']}'![/bold green]")
                else:
                    console.print(f"[bold green]✓ Override saved for '{chosen['source_name']}'.[/bold green]")
                return

        new_id = inquirer.text(message="Enter target Track/Video ID:").execute()
        if new_id and new_id.strip():
            new_title = inquirer.text(message="Enter target Track Title:", default=chosen["source_name"]).execute()
            new_artist = inquirer.text(message="Enter target Artist:", default=chosen["source_artist"]).execute()
            res = ReviewService.apply_and_push_override(
                item_id=chosen["id"],
                video_id=new_id.strip(),
                title=new_title.strip(),
                artist=new_artist.strip(),
                destination=self.dest
            )
            if res.get("pushed"):
                console.print(f"[bold green]✓ Manual override saved and pushed to destination playlist for '{chosen['source_name']}'![/bold green]")
            else:
                console.print(f"[bold green]✓ Manual override saved for '{chosen['source_name']}'.[/bold green]")

    def switch_dest_flow(self):
        new_dest = TUI.select_destination_choice(self.active_dest_name)
        DestinationRegistry.set_active_destination_name(new_dest)
        self.active_dest_name = new_dest
        self.dest = DestinationRegistry.get_destination(new_dest)
        name_display = "YouTube Music" if new_dest == "youtube" else "Deezer"
        console.print(f"\n[bold green]✓ Active sync destination switched to: {name_display}[/bold green]\n")
        time.sleep(0.4)

    def auth_flow(self, interactive: bool = True):
        while True:
            statuses = DestinationRegistry.get_all_statuses(force_refresh=False)
            yt_test = statuses.get("youtube", {})
            dz_test = statuses.get("deezer", {})

            settings = AppConfig.get_settings()
            yt_proxy = settings.get("youtube", {}).get("proxy", "")
            dz_proxy = settings.get("deezer", {}).get("proxy", "")

            if not interactive:
                console.print(f"\n[bold white]Music Service Authentication & Diagnostics:[/bold white]")
                console.print(f" • [bold red]YouTube Music:[/bold red] {'[bold green]CONNECTED (Active)[/bold green]' if yt_test.get('connected') else '[bold red]DISCONNECTED / EXPIRED[/bold red]'} — {yt_test.get('message', '')}")
                console.print(f" • [bold magenta]Deezer:[/bold magenta]        {'[bold green]CONNECTED[/bold green]' if dz_test.get('connected') else '[bold red]DISCONNECTED[/bold red]'} — {dz_test.get('message', '')}\n")
                TUI.render_auth_dashboard(yt_test, dz_test, yt_proxy=yt_proxy, dz_proxy=dz_proxy)
                return

            TUI.clear_screen()
            TUI.render_auth_dashboard(yt_test, dz_test, yt_proxy=yt_proxy, dz_proxy=dz_proxy)
            console.print()

            c = inquirer.select(
                message="Authentication & Network Routing:",
                choices=[
                    Choice("yt", "🔴 YouTube Music Authentication & Session"),
                    Choice("dz", "🟣 Deezer Authentication (ARL / OAuth)"),
                    Choice("proxy", "🌐 Proxy & Routing Settings (Webshare / Presets)"),
                    Choice("test_all", "🔄 Run Full Diagnostic Check (Live Network Probe)"),
                    Choice("back", "🔙 Return to Main Menu")
                ],
                pointer="❯ "
            ).execute()

            if c == "back":
                break
            elif c == "yt":
                self._manage_yt_auth()
            elif c == "dz":
                self._manage_dz_auth()
            elif c == "proxy":
                self._manage_proxies_menu()
            elif c == "test_all":
                console.print("\n[cyan]Probing music service endpoints (live network check)...[/cyan]")
                DestinationRegistry.get_all_statuses(force_refresh=True)
                console.print("[bold green]✓ Diagnostic check completed.[/bold green]")
                time.sleep(0.8)

    def _manage_yt_auth(self):
        while True:
            TUI.clear_screen()
            statuses = DestinationRegistry.get_all_statuses(force_refresh=False)
            yt_test = statuses.get("youtube", {})
            console.print(f"\n[bold white]🔴 YouTube Music Session Management[/bold white]")
            status_badge = "[bold green]CONNECTED[/bold green]" if yt_test.get("connected") else "[bold red]DISCONNECTED[/bold red]"
            console.print(f" • Status: {status_badge} — {yt_test.get('message', '')}\n")

            c = inquirer.select(
                message="YouTube Music Options:",
                choices=[
                    Choice("setup", "🔑 Configure / Renew Session (Paste cURL or headers.txt)"),
                    Choice("test", "🔄 Test Live Connection"),
                    Choice("clear", "🗑️ Clear Credentials / Logout"),
                    Choice("back", "🔙 Back to Authentication Menu")
                ],
                pointer="❯ "
            ).execute()

            if c == "back":
                break
            elif c == "setup":
                if self._prompt_for_auth():
                    DestinationRegistry.invalidate_status_cache()
                    if self.active_dest_name == "youtube":
                        self.dest = DestinationRegistry.get_destination("youtube")
            elif c == "test":
                DestinationRegistry.invalidate_status_cache()
                test_res = DestinationRegistry.get_destination("youtube").test_connection()
                if test_res.get("connected"):
                    console.print(f"[bold green]✓ {test_res.get('message')}[/bold green]")
                else:
                    console.print(f"[bold red]✗ {test_res.get('message')}[/bold red]")
                input("\nPress Enter to continue...")
            elif c == "clear":
                if inquirer.confirm(message="Clear stored YouTube Music session headers?", default=False).execute():
                    AppConfig.save_youtube_headers("", auth_type="none")
                    DestinationRegistry.invalidate_status_cache()
                    console.print("[yellow]YouTube Music session cleared.[/yellow]")
                    input("\nPress Enter to continue...")

    def _manage_dz_auth(self):
        while True:
            TUI.clear_screen()
            statuses = DestinationRegistry.get_all_statuses(force_refresh=False)
            dz_test = statuses.get("deezer", {})
            console.print(f"\n[bold white]🟣 Deezer Authentication Management[/bold white]")
            status_badge = "[bold green]CONNECTED[/bold green]" if dz_test.get("connected") else "[bold red]DISCONNECTED[/bold red]"
            console.print(f" • Status: {status_badge} — {dz_test.get('message', '')}\n")

            c = inquirer.select(
                message="Deezer Options:",
                choices=[
                    Choice("arl", "🍪 Configure Deezer ARL Cookie (Recommended)"),
                    Choice("oauth", "🌐 Browser OAuth 2.0 (App ID / Secret)"),
                    Choice("token", "🔑 Direct Access Token"),
                    Choice("test", "🔄 Test Live Connection"),
                    Choice("logout", "🚪 Logout & Clear Credentials"),
                    Choice("back", "🔙 Back to Authentication Menu")
                ],
                pointer="❯ "
            ).execute()

            if c == "back":
                break
            elif c == "arl":
                arl = inquirer.text(message="Paste your Deezer 'arl' cookie value:").execute().strip()
                if arl:
                    res = DeezerAuthManager.login_with_arl(arl)
                    if res.get("connected"):
                        DestinationRegistry.invalidate_status_cache()
                        if self.active_dest_name == "deezer":
                            self.dest = DestinationRegistry.get_destination("deezer")
                        console.print(f"[bold green]✓ {res.get('message')}[/bold green]")
                    else:
                        console.print(f"[bold red]✗ {res.get('message')}[/bold red]")
                    input("\nPress Enter to continue...")
            elif c == "oauth":
                app_id = inquirer.text(message="Enter Deezer App ID:").execute()
                secret = inquirer.text(message="Enter Deezer App Secret:").execute()
                if app_id and secret:
                    console.print("[cyan]Opening browser for OAuth authorization on http://localhost:8080/callback...[/cyan]")
                    res = DeezerAuthManager.login_with_oauth(app_id, secret)
                    if res.get("connected"):
                        DestinationRegistry.invalidate_status_cache()
                        if self.active_dest_name == "deezer":
                            self.dest = DestinationRegistry.get_destination("deezer")
                        console.print(f"[bold green]✓ {res.get('message')}[/bold green]")
                    else:
                        console.print(f"[bold red]✗ {res.get('message')}[/bold red]")
                    input("\nPress Enter to continue...")
            elif c == "token":
                tok = inquirer.text(message="Paste your Deezer Access Token:").execute().strip()
                if tok:
                    res = DeezerAuthManager.login_with_token(tok)
                    if res.get("connected"):
                        DestinationRegistry.invalidate_status_cache()
                        if self.active_dest_name == "deezer":
                            self.dest = DestinationRegistry.get_destination("deezer")
                        console.print(f"[bold green]✓ {res.get('message')}[/bold green]")
                    else:
                        console.print(f"[bold red]✗ {res.get('message')}[/bold red]")
                    input("\nPress Enter to continue...")
            elif c == "test":
                DestinationRegistry.invalidate_status_cache()
                test_res = DeezerAuthManager.test_connection()
                if test_res.get("connected"):
                    console.print(f"[bold green]✓ {test_res.get('message')}[/bold green]")
                else:
                    console.print(f"[bold red]✗ {test_res.get('message')}[/bold red]")
                input("\nPress Enter to continue...")
            elif c == "logout":
                if inquirer.confirm(message="Clear stored Deezer credentials?", default=False).execute():
                    AppConfig.save_deezer_arl("")
                    AppConfig.save_deezer_token("")
                    DestinationRegistry.invalidate_status_cache()
                    console.print("[yellow]Deezer session logged out.[/yellow]")
                    input("\nPress Enter to continue...")

    def _manage_proxies_menu(self):
        while True:
            TUI.clear_screen()
            settings = AppConfig.get_settings()
            dz_proxy = settings.get("deezer", {}).get("proxy", "")
            yt_proxy = settings.get("youtube", {}).get("proxy", "")

            from rich.table import Table
            from rich.box import ROUNDED
            table = Table(title="🌐 Proxy & Network Routing Settings", box=ROUNDED, border_style="cyan", expand=True)
            table.add_column("Service", style="bold white", width=18)
            table.add_column("Status", justify="center", width=16)
            table.add_column("Active Proxy Endpoint", style="cyan", width=42)

            dz_status = "[bold green]ENABLED[/bold green]" if dz_proxy else "[dim]DISABLED (Direct)[/dim]"
            yt_status = "[bold green]ENABLED[/bold green]" if yt_proxy else "[dim]DISABLED (Direct)[/dim]"
            table.add_row("🟣 Deezer", dz_status, dz_proxy or "Direct Connection (No proxy)")
            table.add_row("🔴 YouTube Music", yt_status, yt_proxy or "Direct Connection (No proxy)")
            console.print()
            console.print(table)

            c = inquirer.select(
                message="Proxy & Routing Options:",
                choices=[
                    Choice("dz", "🟣 Configure Deezer Proxy (Select Preset or Custom)"),
                    Choice("yt", "🔴 Configure YouTube Music Proxy (Select Preset or Custom)"),
                    Choice("test_proxies", "⚡ Test Active Proxy Speeds & Geo-IP"),
                    Choice("back", "🔙 Back to Authentication Menu")
                ],
                pointer="❯ "
            ).execute()

            if c == "back":
                break
            elif c == "dz":
                self._configure_service_proxy("deezer")
            elif c == "yt":
                self._configure_service_proxy("youtube")
            elif c == "test_proxies":
                self._test_proxy_diagnostics()

    def _configure_service_proxy(self, service: str):
        from src.core.config import DEFAULT_PROXY_PRESETS
        service_title = "Deezer" if service == "deezer" else "YouTube Music"
        settings = AppConfig.get_settings()
        current_proxy = settings.get(service, {}).get("proxy", "")

        console.print(f"\n[bold white]Configure Proxy Routing for {service_title}:[/bold white]")
        console.print(f"[dim]Current: {current_proxy or 'Direct Connection (No Proxy)'}[/dim]\n")

        choices = []
        for p in DEFAULT_PROXY_PRESETS:
            is_active = (p["url"] == current_proxy)
            marker = " [bold green](Active)[/bold green]" if is_active else ""
            choices.append(Choice(p["url"], f"{p['name']}{marker}"))

        choices.append(Choice("custom", "✏️ Enter Custom Proxy URL (http://user:pass@ip:port)"))
        choices.append(Choice("direct", "⚡ Direct Connection (Disable Proxy)"))
        choices.append(Choice("back", "🔙 Cancel"))

        sel = inquirer.select(
            message=f"Select proxy routing for {service_title}:",
            choices=choices,
            pointer="❯ "
        ).execute()

        if sel == "back":
            return

        target_proxy = ""
        if sel == "direct":
            target_proxy = ""
        elif sel == "custom":
            target_proxy = inquirer.text(
                message="Enter proxy URL (http://user:pass@host:port or socks5://...):",
                default=current_proxy
            ).execute().strip()
        else:
            target_proxy = sel

        if service == "deezer":
            AppConfig.save_deezer_proxy(target_proxy)
            if self.active_dest_name == "deezer":
                self.dest = DestinationRegistry.get_destination("deezer")
        else:
            AppConfig.save_youtube_proxy(target_proxy)
            if self.active_dest_name == "youtube":
                self.dest = DestinationRegistry.get_destination("youtube")

        DestinationRegistry.invalidate_status_cache()

        if target_proxy:
            console.print(f"\n[bold green]✓ {service_title} proxy set to:[/bold green] [cyan]{target_proxy}[/cyan]")
            self._probe_proxy_endpoint(service, target_proxy)
        else:
            console.print(f"\n[yellow]✓ {service_title} proxy disabled. Using direct connection.[/yellow]")

        input("\nPress Enter to continue...")

    def _probe_proxy_endpoint(self, service: str, proxy_url: str):
        import requests
        console.print("[dim]Probing proxy connection...[/dim]")
        proxies = {"http": proxy_url, "https": proxy_url}
        t0 = time.time()
        try:
            if service == "deezer":
                resp = requests.get("https://api.deezer.com/infos", proxies=proxies, timeout=6.0).json()
                latency = int((time.time() - t0) * 1000)
                country = resp.get("country", "Unknown")
                country_iso = resp.get("country_iso", "")
                is_open = resp.get("open", False)
                if is_open:
                    console.print(f"[bold green]✓ Connected to Deezer via {country} ({country_iso})! Latency: {latency}ms | Status: OPEN[/bold green]")
                else:
                    console.print(f"[bold yellow]⚠️ Exit IP is in {country} ({country_iso}) which Deezer marks as CLOSED. Try UK or Germany preset.[/bold yellow]")
            else:
                resp = requests.get("https://music.youtube.com", proxies=proxies, timeout=6.0)
                latency = int((time.time() - t0) * 1000)
                console.print(f"[bold green]✓ Connected to YouTube Music via proxy! Latency: {latency}ms[/bold green]")
        except Exception as e:
            console.print(f"[bold red]✗ Proxy connection check failed: {e}[/bold red]")
            console.print("[dim]Tip: Check proxy credentials or try switching to another preset (e.g. Frankfurt or London).[/dim]")

    def _test_proxy_diagnostics(self):
        settings = AppConfig.get_settings()
        dz_proxy = settings.get("deezer", {}).get("proxy", "")
        yt_proxy = settings.get("youtube", {}).get("proxy", "")

        console.print(f"\n[bold white]⚡ Running Proxy & Network Diagnostics...[/bold white]")
        if dz_proxy:
            console.print(f"\n[bold magenta]🟣 Testing Deezer Proxy:[/bold magenta] {dz_proxy}")
            self._probe_proxy_endpoint("deezer", dz_proxy)
        else:
            console.print("\n[dim]🟣 Deezer: Direct connection (no proxy active).[/dim]")

        if yt_proxy:
            console.print(f"\n[bold red]🔴 Testing YouTube Music Proxy:[/bold red] {yt_proxy}")
            self._probe_proxy_endpoint("youtube", yt_proxy)
        else:
            console.print("\n[dim]🔴 YouTube Music: Direct connection (no proxy active).[/dim]")

        input("\nPress Enter to continue...")

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

        # Discover export files in repository
        search_dirs = [PROJECT_ROOT, PROJECT_ROOT / "exports", PROJECT_ROOT / "data", Path.cwd()]
        candidate_files = []
        seen = set()
        for d in search_dirs:
            if d.exists() and d.is_dir():
                for ext in ["*.json", "*.csv", "*.zip"]:
                    for f in d.glob(ext):
                        if f.is_file() and f.resolve() not in seen:
                            seen.add(f.resolve())
                            candidate_files.append(f)

        choices = []
        for cf in candidate_files[:10]:
            sz = f"{cf.stat().st_size / 1024:.1f} KB" if cf.stat().st_size < 1024 * 1024 else f"{cf.stat().st_size / (1024 * 1024):.1f} MB"
            choices.append(Choice(value=str(cf), name=f"📄 {cf.name} ({sz}) [in {cf.parent.name}]"))

        choices.append(Choice(value="custom", name="📂 Enter custom path to export file..."))
        choices.append(Choice(value="cancel", name="Cancel"))

        action = inquirer.select(
            message="Select Spotify export file to load:",
            choices=choices,
            style=INQUIRER_STYLE,
            default=choices[0].value if choices else "custom"
        ).execute()

        if action == "cancel":
            return None
        elif action != "custom":
            p = Path(action)
            custom_src = SpotifyExportSource(file_path=p)
            if custom_src.get_playlists():
                return custom_src
            else:
                console.print(f"[bold red]No valid playlist data found in '{p}'.[/bold red]")
                return None

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

    def _ensure_destination_auth(self) -> bool:
        test = self.dest.test_connection()
        if test.get("connected"):
            return True
        if self.active_dest_name == "deezer":
            console.print("\n[yellow]⚠️ Deezer authentication required.[/yellow]")
            tok = input("Paste your Deezer Access Token or ARL (or press Enter to cancel): ").strip()
            if not tok:
                return False
            if len(tok) == 192:  # Typical ARL length
                res = DeezerAuthManager.login_with_arl(tok)
            else:
                res = DeezerAuthManager.login_with_token(tok)
            if res.get("connected"):
                self.dest = DestinationRegistry.get_destination("deezer")
                console.print(f"[bold green]✓ {res.get('message')}[/bold green]")
                return True
            console.print(f"[bold red]Failed to authenticate Deezer: {res.get('message')}[/bold red]")
            return False
        return self._prompt_for_auth()

    def _ensure_youtube_auth(self) -> bool:
        return self._ensure_destination_auth()

    def _prompt_for_auth(self) -> bool:
        console.print(f"\n[yellow]⚠️ YouTube Music authentication required.[/yellow]")
        console.print("To connect your YouTube Music account:")
        console.print("1. Open Chrome/Edge/Firefox and log in to [cyan]https://music.youtube.com[/cyan]")
        console.print("2. Press F12 -> Network tab -> refresh -> click any request (e.g. 'browse' or 'v1/')")
        console.print("3. Right-click the request -> Copy -> 'Copy as cURL' (or copy the 'cookie' header)\n")

        default_headers_file = PROJECT_ROOT / "headers.txt"
        auth_choices = [
            Choice("paste", "📋 Paste cURL command or cookie string"),
        ]
        if default_headers_file.exists():
            auth_choices.append(Choice("file_default", f"📄 Load from {default_headers_file.name}"))
        auth_choices.append(Choice("file_custom", "📂 Load from custom headers file"))
        auth_choices.append(Choice("cancel", "Cancel"))

        method = inquirer.select(
            message="Select authentication input method:",
            choices=auth_choices,
            style=INQUIRER_STYLE,
            default="paste"
        ).execute()

        if method == "cancel":
            return False
        elif method == "file_default":
            raw = default_headers_file.read_text(encoding="utf-8")
        elif method == "file_custom":
            p_str = input("Enter path to headers file: ").strip("\"' ")
            p = Path(p_str)
            if not p.exists():
                console.print(f"[bold red]File '{p_str}' does not exist.[/bold red]")
                return False
            raw = p.read_text(encoding="utf-8")
        else:
            console.print("[dim]Paste your cURL command or cookie below (press Enter on empty line to finish):[/dim]")
            lines = []
            while True:
                try:
                    line = input()
                    if not line:
                        if lines:
                            break
                        else:
                            return False
                    lines.append(line)
                    if len(lines) == 1 and not line.endswith("\\") and not line.endswith("^"):
                        break
                except EOFError:
                    break
            raw = "\n".join(lines).strip()

        if not raw:
            return False

        if parse_and_save_headers(raw):
            self.yt_dest = YouTubeMusicDestination()
            if self.active_dest_name == "youtube":
                self.dest = self.yt_dest
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

    def _resolve_discrepancies_inline(self, job_id: str, mirror_id: Optional[str] = None):
        """Interactive loop to review and resolve omitted/flagged tracks for a job."""
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("""
            SELECT * FROM sync_items
            WHERE job_id = ? AND (is_accepted = 0 OR matched_video_id IS NULL)
            ORDER BY track_index ASC
            """, (job_id,))
            flagged = [dict(r) for r in cur.fetchall()]

        if not flagged:
            console.print("[bold green]✓ No unresolved discrepancies found for this job.[/bold green]")
            return

        console.print(f"\n[bold white]Resolving {len(flagged)} Discrepancy Items:[/bold white]\n")

        for idx, item in enumerate(flagged, 1):
            dur = item.get("source_duration") or 0
            dur_str = f"{int(dur//60)}:{int(dur%60):02d}" if dur else "unknown"
            console.print(f"[bold cyan]────────────────────────────────────────────────────────────[/bold cyan]")
            console.print(f"[{idx}/{len(flagged)}] [bold white]{item['source_name']}[/bold white] — [bold yellow]{item['source_artist']}[/bold yellow] ({dur_str})")
            if item.get("rationale"):
                console.print(f"  [dim]Diagnostic reason: {item['rationale']}[/dim]")

            raw_alts = item.get("alternatives_json")
            alts = []
            if raw_alts:
                try:
                    alts = json.loads(raw_alts)
                except Exception:
                    alts = []

            choices = []
            for a_idx, a in enumerate(alts[:5]):
                a_dur = a.get("duration_seconds") or 0
                a_dur_str = f"{int(a_dur//60)}:{int(a_dur%60):02d}"
                score_str = f"{float(a.get('score', 0)):.0%}" if "score" in a else ""
                choices.append(Choice(
                    value=f"alt_{a_idx}",
                    name=f"Use: '{a.get('title')}' by {a.get('artist')} ({a_dur_str}) {score_str} [ID: {a.get('video_id')}]"
                ))

            choices.append(Choice(value="search", name="🔍 Search destination manually for this track"))
            choices.append(Choice(value="paste", name="🔗 Enter Track / Video ID manually"))
            choices.append(Choice(value="skip", name="⏭ Skip this track"))
            choices.append(Choice(value="exit", name="🚪 Stop resolving and return"))

            choice = inquirer.select(
                message="Select resolution:",
                choices=choices,
                style=INQUIRER_STYLE,
                default="skip" if not alts else "alt_0"
            ).execute()

            if choice == "exit":
                break
            elif choice == "skip":
                continue
            elif choice.startswith("alt_"):
                alt_idx = int(choice.split("_")[1])
                chosen_alt = alts[alt_idx]
                res = ReviewService.apply_and_push_override(
                    item_id=item["id"],
                    video_id=chosen_alt["video_id"],
                    title=chosen_alt["title"],
                    artist=chosen_alt["artist"],
                    destination=self.dest
                )
                if res.get("pushed"):
                    console.print(f"  [bold green]✓ Pushed '{chosen_alt['title']}' to live playlist and updated manifest![/bold green]")
                else:
                    console.print(f"  [yellow]{res.get('message')}[/yellow]")
            elif choice == "paste":
                v_id = inquirer.text(message="Enter Track/Video ID:").execute().strip()
                if v_id:
                    v_title = inquirer.text(message="Enter Title:", default=item["source_name"]).execute().strip()
                    v_artist = inquirer.text(message="Enter Artist:", default=item["source_artist"]).execute().strip()
                    res = ReviewService.apply_and_push_override(
                        item_id=item["id"],
                        video_id=v_id,
                        title=v_title,
                        artist=v_artist,
                        destination=self.dest
                    )
                    if res.get("pushed"):
                        console.print(f"  [bold green]✓ Pushed '{v_title}' to live playlist and updated manifest![/bold green]")
                    else:
                        console.print(f"  [yellow]{res.get('message')}[/yellow]")
            elif choice == "search":
                q = inquirer.text(message="Search query:", default=f"{item['source_name']} {item['source_artist']}").execute().strip()
                if q:
                    dummy_track = Track(name=q, artist="", duration_seconds=item.get("source_duration", 0))
                    try:
                        cands = self.dest.search_candidates(dummy_track, limit=5)
                    except Exception as e:
                        console.print(f"  [bold red]Search failed: {e}[/bold red]")
                        cands = []
                    if not cands:
                        console.print("  [yellow]No candidates found for query.[/yellow]")
                        continue
                    c_choices = [
                        Choice(
                            value=f"cand_{c_i}",
                            name=f"'{c.title}' by {c.artist} ({int(c.duration_seconds//60)}:{int(c.duration_seconds%60):02d}) [ID: {c.video_id}]"
                        )
                        for c_i, c in enumerate(cands)
                    ]
                    c_choices.append(Choice(value="cancel", name="Cancel search"))
                    c_sel = inquirer.select(message="Select search result:", choices=c_choices).execute()
                    if c_sel != "cancel":
                        c_idx = int(c_sel.split("_")[1])
                        chosen_c = cands[c_idx]
                        res = ReviewService.apply_and_push_override(
                            item_id=item["id"],
                            video_id=chosen_c.video_id,
                            title=chosen_c.title,
                            artist=chosen_c.artist,
                            destination=self.dest
                        )
                        if res.get("pushed"):
                            console.print(f"  [bold green]✓ Pushed '{chosen_c.title}' to live playlist and updated manifest![/bold green]")
                        else:
                            console.print(f"  [yellow]{res.get('message')}[/yellow]")

    def _process_playlist(self, pl: Playlist, strategy: str, precomputed_plan: Optional[SyncPlan] = None) -> Optional[Dict[str, Any]]:
        mirrors = DatabaseManager.list_mirrors(destination=self.active_dest_name)
        name_clean = pl.name.lower().strip()
        matched_mirror = next((m for m in mirrors if m.name.lower().strip() == name_clean or m.spotify_id == pl.id), None)
        dest_display = "Deezer" if self.active_dest_name == "deezer" else "YouTube Music"

        library = []
        if hasattr(self.dest, "is_available") and self.dest.is_available():
            try:
                library = self.dest.get_library_playlists()
            except Exception:
                library = []
        elif hasattr(self.dest, "client") and (self.dest.client.access_token or self.dest.client.arl):
            try:
                library = self.dest.client.get_user_playlists()
            except Exception:
                library = []

        matched_dest = next((y for y in library if (y.get("title") or "").lower().strip() == name_clean), None)

        if strategy == "SKIP_EXISTING" and (matched_mirror or matched_dest):
            dest_pid = matched_mirror.yt_playlist_id if matched_mirror else (matched_dest.get("playlistId") or matched_dest.get("id"))
            dest_url = f"https://www.deezer.com/playlist/{dest_pid}" if self.active_dest_name == "deezer" else f"https://music.youtube.com/playlist?list={dest_pid}"
            console.print(f"  [yellow]Skipping '{pl.name}' (already exists on {dest_display}).[/yellow]")
            return {
                "name": pl.name,
                "status": "SKIPPED",
                "synced_tracks": 0,
                "total_tracks": pl.track_count,
                "yt_url": dest_url
            }

        # If matching playlist exists and strategy is UPDATE, execute delta sync
        if strategy == "UPDATE" and (matched_mirror or matched_dest):
            dest_pid = str(matched_mirror.yt_playlist_id if matched_mirror else (matched_dest.get("playlistId") or matched_dest.get("id")))
            
            # Verify live destination playlist exists
            dest_live = self.dest.get_playlist(dest_pid)
            if dest_live is None:
                console.print(f"  [yellow]⚠️ Destination playlist '{pl.name}' (ID: {dest_pid}) could not be reached on {dest_display}.[/yellow]")
                console.print(f"  [yellow]Preserving local sync history. It has been marked as UNREACHABLE.[/yellow]")
                if matched_mirror:
                    DatabaseManager.update_mirror_status(matched_mirror.id, MirrorStatus.UNREACHABLE.value)
                matched_mirror = None
                matched_dest = None

        if strategy == "UPDATE" and (matched_mirror or matched_dest):
            dest_pid = str(matched_mirror.yt_playlist_id if matched_mirror else (matched_dest.get("playlistId") or matched_dest.get("id")))
            if not matched_mirror:
                matched_mirror = MirrorService.link_existing_mirror(
                    pl.name,
                    dest_pid,
                    source_type="export",
                    file_path=str(self.export_source.file_path),
                    destination=self.dest,
                    destination_name=self.active_dest_name
                )

            console.print(f"  Existing mirror detected: [bold cyan]{matched_mirror.yt_playlist_name}[/bold cyan] (ID: {dest_pid})")

            if precomputed_plan:
                plan = precomputed_plan
            else:
                console.print("  Generating sync plan to detect new additions & removals...")
                plan = sync_service.generate_sync_plan(matched_mirror.id, source=self.export_source, destination=self.dest)
                if plan and plan.removals_count > 0:
                    for it in plan.items:
                        if it.action == PlanAction.REMOVE_TRACK:
                            it.is_accepted = True
            if plan:
                new_adds = plan.additions_count - plan.restorations_count
                parts = []
                if new_adds > 0:
                    parts.append(f"[bold green]+{new_adds} new tracks to add[/bold green]")
                if plan.restorations_count > 0:
                    parts.append(f"[bold cyan]↺ {plan.restorations_count} missing tracks to restore on {dest_display}[/bold cyan]")
                if plan.removals_count > 0:
                    parts.append(f"[bold red]-{plan.removals_count} tracks to remove on {dest_display}[/bold red]")
                parts.append(f"{plan.unchanged_count} already in sync")
                console.print(f"  Analysis: {', '.join(parts)}")

                dest_url = f"https://www.deezer.com/playlist/{dest_pid}" if self.active_dest_name == "deezer" else f"https://music.youtube.com/playlist?list={dest_pid}"

                if plan.additions_count == 0 and plan.removals_count == 0:
                    console.print(f"  [bold green]✓ Playlist is already 100% in sync.[/bold green]")
                    return {
                        "name": pl.name,
                        "status": "IN_SYNC",
                        "synced_tracks": 0,
                        "total_tracks": plan.source_track_count,
                        "yt_url": dest_url
                    }

                # Execute delta sync with Rich progress bar
                with TUI.get_progress_bar() as progress:
                    task = progress.add_task(f"Delta Sync '{pl.name}'", total=plan.additions_count + plan.removals_count, current_track="")
                    report = sync_service.execute_delta_sync(
                        matched_mirror.id,
                        plan=plan,
                        destination=self.dest,
                        source=self.export_source,
                        progress_callback=lambda cur, tot, trk: progress.update(task, completed=cur, total=tot, current_track=trk)
                    )

                total_in_sync = report.already_synchronized_tracks + report.total_synced_tracks
                if report.unmatched_tracks == 0 and total_in_sync == report.total_source_tracks:
                    console.print(f"  [bold green]✓ Transferred {report.total_synced_tracks} new tracks to {dest_display}. Playlist is 100% in sync![/bold green]")
                    st_val = "COMPLETED"
                elif report.unmatched_tracks > 0:
                    console.print(f"  [bold yellow]⚠️ Delta sync added {report.total_synced_tracks} tracks ({report.unmatched_tracks} tracks omitted/unmatched).[/bold yellow]")
                    if report.discrepancies:
                        TUI.render_discrepancy_table(report.discrepancies)
                        res_choices = [
                            Choice("resolve", f"Review & resolve {len(report.discrepancies)} omitted track(s) inline now"),
                            Choice("continue", "Continue without resolving (leave omitted)")
                        ]
                        res_action = inquirer.select(message="Discrepancy Action:", choices=res_choices, default="resolve").execute()
                        if res_action == "resolve":
                            self._resolve_discrepancies_inline(report.job_id, report.mirror_id)
                            live_mf = DatabaseManager.get_manifest_tracks(report.mirror_id)
                            total_in_sync = len(live_mf)
                            report.unmatched_tracks = max(0, report.total_source_tracks - total_in_sync)
                    st_val = "COMPLETED" if report.unmatched_tracks == 0 and total_in_sync == report.total_source_tracks else "PARTIAL_SUCCESS"
                else:
                    console.print(f"  [bold green]✓ Transferred {report.total_synced_tracks} new tracks to {dest_display}.[/bold green]")
                    st_val = "UPDATED"

                return {
                    "name": pl.name,
                    "status": st_val,
                    "synced_tracks": total_in_sync,
                    "delta_transferred": report.total_synced_tracks,
                    "total_tracks": report.total_source_tracks,
                    "unmatched_tracks": report.unmatched_tracks,
                    "yt_url": report.yt_playlist_url
                }

        # Otherwise: Initial Full Transfer
        console.print(f"  Establishing new {dest_display} mirror for '{pl.name}'...")
        full_pl = self.export_source.get_playlist_tracks(pl.name)
        total_tracks = len(full_pl.tracks)
        
        # Step A: Matching phase with Rich progress bar
        with TUI.get_progress_bar() as progress:
            task = progress.add_task(f"Matching '{pl.name}'", total=total_tracks, current_track="")
            job = sync_service.analyze_for_transfer(
                self.export_source,
                pl.name,
                destination=self.dest,
                progress_callback=lambda cur, tot, trk: progress.update(task, completed=cur, total=tot, current_track=trk)
            )

        # Pre-flight Review Gate: Check for ambiguous/unmatched tracks
        summary = ReviewService.get_audit_summary(job.id)
        accepted = summary.get("accepted", 0)
        exact = summary.get("exact", 0)
        high = summary.get("high", 0)
        ambiguous = summary.get("ambiguous", 0)
        no_match = summary.get("no_match", 0)
        flagged = ambiguous + no_match

        if flagged > 0:
            TUI.render_pre_flight_card(
                playlist_name=pl.name,
                dest_name=self.active_dest_name,
                exact_count=exact,
                high_count=high,
                ambiguous_count=ambiguous,
                unmatched_count=no_match,
                total_count=total_tracks
            )
            choices = [
                Choice("transfer_verified", f"Transfer {accepted} verified tracks now (resolve {flagged} after) [Recommended]"),
                Choice("review_first", f"Review {flagged} flagged track(s) before creating playlist"),
                Choice("cancel", "Cancel transfer")
            ]
            action = inquirer.select(
                message="Select how to proceed:",
                choices=choices,
                style=INQUIRER_STYLE,
                default="transfer_verified"
            ).execute()

            if action == "cancel":
                console.print("[yellow]Transfer cancelled by user.[/yellow]")
                sync_service.cancel_job(job.id)
                return {
                    "name": pl.name,
                    "status": "CANCELLED",
                    "synced_tracks": 0,
                    "total_tracks": total_tracks,
                    "yt_url": ""
                }
            elif action == "review_first":
                self._resolve_discrepancies_inline(job.id)
                summary = ReviewService.get_audit_summary(job.id)
                accepted = summary.get("accepted", 0)
        else:
            console.print(f"  [bold green]✓ 100% High-Confidence Matches ({accepted}/{total_tracks} tracks verified). Ready to transfer.[/bold green]")

        # Step B: Execution phase with Rich progress bar
        console.print(f"  Creating {dest_display} playlist and transferring tracks...")
        with TUI.get_progress_bar() as progress:
            upload_task = progress.add_task(f"Uploading to {dest_display}", total=accepted, current_track="")
            report = sync_service.execute_transfer(
                job.id,
                destination=self.dest,
                progress_callback=lambda cur, tot, trk: progress.update(upload_task, completed=cur, total=tot, current_track=trk)
            )

        if report.status == "COMPLETED" or report.outcome == TransferOutcome.COMPLETE_SUCCESS:
            console.print(f"  [bold green]✓ Successfully transferred all {report.total_synced_tracks}/{report.total_source_tracks} tracks to {dest_display}.[/bold green]")
            return {
                "name": pl.name,
                "status": "COMPLETED",
                "synced_tracks": report.total_synced_tracks,
                "total_tracks": report.total_source_tracks,
                "yt_url": report.yt_playlist_url
            }
        elif report.status == "PARTIAL_SUCCESS" or report.outcome == TransferOutcome.PARTIAL_SUCCESS:
            discrepancy_count = len(report.discrepancies)
            console.print(f"  [bold yellow]⚠️ Transferred {report.total_synced_tracks}/{report.total_source_tracks} tracks to {dest_display} ({discrepancy_count} tracks omitted).[/bold yellow]")
            TUI.render_discrepancy_table(report.discrepancies)

            res_choices = [
                Choice("resolve", f"Review & resolve {discrepancy_count} omitted track(s) inline now"),
                Choice("continue", "Continue (can resolve later in Audit Explorer)")
            ]
            res_action = inquirer.select(message="Discrepancy Action:", choices=res_choices, default="resolve").execute()
            if res_action == "resolve":
                self._resolve_discrepancies_inline(job.id, report.mirror_id)
                updated_job = sync_service.get_job(job.id)
                updated_synced = updated_job.synced_tracks if updated_job else report.total_synced_tracks
                return {
                    "name": pl.name,
                    "status": "COMPLETED" if updated_synced >= report.total_source_tracks else "PARTIAL_SUCCESS",
                    "synced_tracks": updated_synced,
                    "total_tracks": report.total_source_tracks,
                    "yt_url": report.yt_playlist_url
                }

            return {
                "name": pl.name,
                "status": "PARTIAL_SUCCESS",
                "synced_tracks": report.total_synced_tracks,
                "total_tracks": report.total_source_tracks,
                "yt_url": report.yt_playlist_url
            }
        else:
            console.print(f"  [bold red]✖ Transfer failed or was cancelled ({report.status}).[/bold red]")
            return {
                "name": pl.name,
                "status": report.status,
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
    parser_sync = subparsers.add_parser("sync", help="Synchronize Spotify playlists to destination (YouTube Music or Deezer)")
    parser_sync.add_argument("--name", "-n", help="Specific playlist name to sync")
    parser_sync.add_argument("--all", "-a", action="store_true", help="Sync all playlists automatically")
    parser_sync.add_argument("--file", "-f", help="Path to Spotify export file")
    parser_sync.add_argument("--dest", "-d", choices=["youtube", "deezer"], help="Target destination service")
    parser_sync.add_argument("--privacy", default="PRIVATE", choices=["PUBLIC", "PRIVATE", "UNLISTED"], help="Privacy for newly created playlists")

    # Command: diff
    parser_diff = subparsers.add_parser("diff", help="Inspect differences / dry run between Spotify and destination")
    parser_diff.add_argument("--name", "-n", help="Specific playlist name to inspect")
    parser_diff.add_argument("--file", "-f", help="Path to Spotify export file")
    parser_diff.add_argument("--dest", "-d", choices=["youtube", "deezer"], help="Target destination service")

    # Command: mirrors
    parser_mirrors = subparsers.add_parser("mirrors", help="List all active mirrored playlists and sync states")
    parser_mirrors.add_argument("--dest", "-d", choices=["youtube", "deezer"], help="Filter mirrors by destination")

    # Command: auth
    parser_auth = subparsers.add_parser("auth", help="Check or configure service authentication")
    parser_auth.add_argument("--dest", "-d", choices=["youtube", "deezer"], help="Music platform to check")

    # Command: resume
    subparsers.add_parser("resume", help="Resume any interrupted sync operations")

    # Command: audit
    parser_audit = subparsers.add_parser("audit", help="Inspect match audit scorecards, decisions, and overrides")
    parser_audit.add_argument("--job", "-j", help="Specific job ID to inspect")
    parser_audit.add_argument("--flagged", action="store_true", help="Show only ambiguous / flagged tracks")
    parser_audit.add_argument("--export", "-e", action="store_true", help="Export match audit to CSV")

    # Command: logs
    parser_logs = subparsers.add_parser("logs", help="View recent application or matching logs")
    parser_logs.add_argument("--type", "-t", default="matching", choices=["matching", "syncstation", "app"], help="Log type to inspect")
    parser_logs.add_argument("--lines", "-n", type=int, default=50, help="Number of lines to view")

    args = parser.parse_args()

    if not args.command:
        # Default: Launch Interactive TUI Manager
        cli = InteractiveCLI()
        cli.run()
        return

    init_db()

    if args.command == "audit":
        cli = InteractiveCLI()
        cli.audit_flow(job_id=args.job, interactive=False, flagged_only=args.flagged, export=args.export)

    elif args.command == "logs":
        log_type = "syncstation" if args.type in ["syncstation", "app"] else "matching"
        lines = get_log_tail(log_type=log_type, lines=args.lines)
        TUI.render_log_lines(lines, log_type=log_type)

    elif args.command == "diff":
        cli = InteractiveCLI(export_file=Path(args.file) if args.file else None, destination=getattr(args, "dest", None))
        cli.diff_flow(target_name=args.name, interactive=False)

    elif args.command == "list":
        cli = InteractiveCLI(export_file=Path(args.file) if args.file else None)
        cli.list_playlists_flow(pause=False)

    elif args.command == "mirrors":
        cli = InteractiveCLI(destination=getattr(args, "dest", None))
        mirrors = DatabaseManager.list_mirrors(destination=getattr(args, "dest", None))
        TUI.render_mirrors_table(mirrors)

    elif args.command == "auth":
        cli = InteractiveCLI(destination=getattr(args, "dest", None))
        cli.auth_flow(interactive=False)

    elif args.command == "resume":
        cli = InteractiveCLI()
        cli.resume_flow()

    elif args.command == "sync":
        cli = InteractiveCLI(export_file=Path(args.file) if args.file else None, destination=getattr(args, "dest", None))
        if not cli.export_source:
            console.print("[bold red]No valid export file found.[/bold red]")
            return
        if not cli._ensure_destination_auth():
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
