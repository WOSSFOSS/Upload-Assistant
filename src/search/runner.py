import asyncio
import json
import itertools
import os
import re
import shutil
import sqlite3
import time
import urllib.parse
from pathlib import Path
from typing import Any, Optional

from torf import Torrent

from src.console import console
from src.search.matcher import ReleaseInfo, SearchMatcher
from src.search.provider import SearchProvider


VIDEO_EXTENSIONS = {".mkv", ".mp4", ".ts", ".avi", ".mov", ".m2ts"}
MUSIC_EXTENSIONS = {".flac", ".mp3", ".m4a", ".aac", ".alac", ".wav", ".ogg", ".opus"}
BOOK_EXTENSIONS = {".epub", ".pdf", ".mobi", ".azw3", ".lit", ".cbz", ".cbr", ".m4b"}
SUPPORTED_EXTENSIONS = VIDEO_EXTENSIONS | MUSIC_EXTENSIONS | BOOK_EXTENSIONS
EPISODE_RE = re.compile(r"(?i)(?:^|[.\s_\-])(?:s\d{1,2}e\d{1,3}|s\d{1,2}e\d{1,3}e\d{1,3}|\d{1,2}x\d{1,3})(?:[.\s_\-]|$)")
SEASON_PACK_RE = re.compile(r"(?i)(?:^|[.\s_\-])(?:s\d{1,2}|season[.\s_\-]?\d{1,2}|complete)(?:[.\s_\-]|$)")
DISC_MARKERS = {
    "BDMV": {"BDMV/index.bdmv", "BDMV/BACKUP/index.bdmv"},
    "DVD": {"VIDEO_TS/VIDEO_TS.IFO"},
}


class SearchRunner:
    def __init__(self, config: dict[str, Any], base_dir: str, debug: bool = False) -> None:
        self.config = config
        self.base_dir = base_dir
        self.debug = debug
        search_config = config.get("SEARCH", {})
        fuzzy_threshold = 0.02
        if isinstance(search_config, dict):
            try:
                fuzzy_threshold = float(search_config.get("fuzzy_size_threshold", 0.02))
            except (TypeError, ValueError):
                fuzzy_threshold = 0.02
        self.matcher = SearchMatcher(fuzzy_size_threshold=fuzzy_threshold)
        self.provider = SearchProvider(config, base_dir, self.matcher, debug=debug)

    async def run(
        self,
        profile_name: Optional[str] = None,
        target_filter: Optional[list[str]] = None,
        refresh_scan: bool = False,
        prepare_cache: bool = False,
    ) -> None:
        search_config = self.config.get("SEARCH")
        if not isinstance(search_config, dict):
            console.print("[red]No SEARCH config block found.[/red]")
            return

        profiles = search_config.get("profiles")
        if not isinstance(profiles, dict) or not profiles:
            console.print("[yellow]No SEARCH profiles configured.[/yellow]")
            return

        selected_profiles = self._selected_profiles(profiles, profile_name)
        if not selected_profiles:
            if profile_name:
                console.print(f"[red]Search profile '{profile_name}' was not found.[/red]")
            else:
                console.print("[yellow]No enabled SEARCH profiles found.[/yellow]")
            return

        queue_dir = self._resolve_data_path(str(search_config.get("queue_dir") or "data/queues"))
        cache_dir = self._resolve_data_path(str(search_config.get("cache_dir") or "data/search_cache"))
        queue_dir.mkdir(parents=True, exist_ok=True)
        cache_dir.mkdir(parents=True, exist_ok=True)
        console.print(f"[cyan]Search queue dir:[/cyan] {queue_dir}")
        console.print(f"[cyan]Search cache dir:[/cyan] {cache_dir}")
        tmdb_cache_file = cache_dir / "tmdb_ids_cache.json"
        tmdb_cache = await self._load_api_cache(tmdb_cache_file)
        await self._write_run_state(
            cache_dir / "search_run_state.json",
            profile_name,
            target_filter,
            stage="started",
            refresh_scan=refresh_scan,
            prepare_cache=prepare_cache,
        )

        libraries = search_config.get("libraries")
        libraries_map = libraries if isinstance(libraries, dict) else {}
        selected_targets = {target.upper() for target in target_filter or [] if target.strip()}
        if selected_targets:
            console.print(f"[cyan]Search target filter:[/cyan] {', '.join(sorted(selected_targets))}")

        total_written = 0
        scan_memory_cache: dict[tuple[str, tuple[str, ...]], list[str]] = {}
        for name, profile in selected_profiles.items():
            if not isinstance(profile, dict):
                continue
            targets = profile.get("targets")
            if not isinstance(targets, dict) or not targets:
                console.print(f"[yellow]SEARCH profile '{name}' has no targets.[/yellow]")
                continue

            console.print(f"[bold cyan]Search profile:[/bold cyan] {name}")
            matched_targets = 0
            profile_contexts: list[dict[str, Any]] = []
            for tracker, target in targets.items():
                if not isinstance(target, dict):
                    continue
                tracker_name = str(tracker).strip().upper()
                if selected_targets and tracker_name not in selected_targets:
                    continue
                matched_targets += 1
                source_paths = self._resolve_source_paths(target, libraries_map)
                if not source_paths:
                    console.print(f"[yellow]{tracker_name}: no source paths configured.[/yellow]")
                    continue

                content_profile = self._content_profile(name, target)
                progress_interval = self._progress_interval(search_config, target)
                checkpoint_interval = self._checkpoint_interval(search_config, target)
                cache_file = cache_dir / f"{tracker_name.lower()}_{name}_search_plan.json"
                scan_checkpoint_file = cache_dir / f"{tracker_name.lower()}_{name}_scan_checkpoint.json"
                api_cache_file = cache_dir / f"{tracker_name.lower()}_{name}_api_cache.json"
                await self._write_run_state(
                    cache_dir / "search_run_state.json",
                    profile_name,
                    target_filter,
                    stage="target_started",
                    tracker=tracker_name,
                    profile=name,
                    refresh_scan=refresh_scan,
                    prepare_cache=prepare_cache,
                )
                home_releases: list[ReleaseInfo] = []
                home_candidates: list[str] = []
                candidates: list[str] = []
                scan_checkpoint = None
                if refresh_scan:
                    console.print(f"[cyan]{tracker_name}:[/cyan] refresh scan requested, ignoring cached scan checkpoint")
                else:
                    scan_checkpoint = await self._load_scan_checkpoint(
                        scan_checkpoint_file,
                        tracker_name,
                        name,
                        content_profile,
                        search_config,
                        target,
                    )
                if scan_checkpoint:
                    candidates = list(scan_checkpoint.get("source_candidates", []))
                    home_candidates = list(scan_checkpoint.get("home_candidates", []))
                    console.print(
                        f"[cyan]{tracker_name}:[/cyan] using cached scan checkpoint "
                        f"({len(candidates)} source, {len(home_candidates)} home candidate(s))"
                    )
                else:
                    console.print(f"[cyan]{tracker_name}:[/cyan] scanning source libraries for {content_profile} candidates...")
                    candidates = self._scan_paths_cached(
                        source_paths,
                        content_profile,
                        label=f"{tracker_name} source",
                        progress_interval=progress_interval,
                        scan_memory_cache=scan_memory_cache,
                    )
                    console.print(f"[cyan]{tracker_name}:[/cyan] found {len(candidates)} source candidate(s)")
                    await self._write_scan_checkpoint(
                        scan_checkpoint_file,
                        tracker_name,
                        name,
                        content_profile,
                        candidates,
                        [],
                        stage="source_scanned",
                    )
                if self._local_prefilter_enabled(search_config, target):
                    if not scan_checkpoint:
                        home_paths = self._resolve_home_paths(target, libraries_map)
                        if home_paths:
                            console.print(f"[cyan]{tracker_name}:[/cyan] scanning target home libraries for local prefilter...")
                            home_candidates = self._scan_paths_cached(
                                home_paths,
                                content_profile,
                                label=f"{tracker_name} home",
                                progress_interval=progress_interval,
                                scan_memory_cache=scan_memory_cache,
                            )
                    home_releases = [self.matcher.parse_release(candidate) for candidate in home_candidates]
                    console.print(f"[cyan]{tracker_name}:[/cyan] local prefilter loaded {len(home_releases)} home candidate(s)")
                    if not scan_checkpoint:
                        await self._write_scan_checkpoint(
                            scan_checkpoint_file,
                            tracker_name,
                            name,
                            content_profile,
                            candidates,
                            home_candidates,
                            stage="home_scanned",
                        )

                api_cache = await self._load_api_cache(api_cache_file)
                if prepare_cache:
                    if self._tmdb_lookup_enabled(search_config, target) and self._tmdb_cache_enabled(search_config, target):
                        await self._prepare_tmdb_cache(
                            candidates,
                            tracker_name,
                            target,
                            search_config,
                            home_releases,
                            content_profile,
                            tmdb_cache,
                            tmdb_cache_file,
                            progress_interval,
                            checkpoint_interval,
                        )
                    if self._api_cache_enabled(search_config, target):
                        await self._write_json(api_cache_file, api_cache)
                    if self._tmdb_cache_enabled(search_config, target):
                        await self._write_json(tmdb_cache_file, tmdb_cache)
                    continue
                profile_contexts.append({
                    "candidates": candidates,
                    "tracker_name": tracker_name,
                    "target": target,
                    "search_config": search_config,
                    "home_releases": home_releases,
                    "content_profile": content_profile,
                    "api_cache": api_cache,
                    "tmdb_cache": tmdb_cache,
                    "progress_interval": progress_interval,
                    "checkpoint_interval": checkpoint_interval,
                    "cache_file": cache_file,
                    "api_cache_file": api_cache_file,
                    "tmdb_cache_file": tmdb_cache_file,
                    "queue_dir": queue_dir,
                    "profile_name": name,
                })

            if selected_targets and matched_targets == 0:
                console.print(
                    f"[yellow]SEARCH profile '{name}' has no matching target for: "
                    f"{', '.join(sorted(selected_targets))}[/yellow]"
                )
            if prepare_cache:
                console.print(f"[green]Prepared search cache for profile '{name}' without tracker API searches.[/green]")
            elif profile_contexts:
                total_written += await self._run_profile_api_searches(profile_contexts)

        await self._write_run_state(
            cache_dir / "search_run_state.json",
            profile_name,
            target_filter,
            stage="finished",
            total_written=total_written,
            refresh_scan=refresh_scan,
            prepare_cache=prepare_cache,
        )
        console.print(f"[bold green]Search queue generation complete.[/bold green] {total_written} total candidate(s).")

    def _selected_profiles(self, profiles: dict[str, Any], profile_name: Optional[str]) -> dict[str, Any]:
        if profile_name:
            profile = profiles.get(profile_name)
            return {profile_name: profile} if isinstance(profile, dict) else {}
        return {
            name: profile
            for name, profile in profiles.items()
            if isinstance(profile, dict) and profile.get("enabled", False)
        }

    async def _run_profile_api_searches(self, contexts: list[dict[str, Any]]) -> int:
        console.print(f"[cyan]Running API searches for {len(contexts)} target(s) in parallel...[/cyan]")
        results = await asyncio.gather(
            *(self._run_target_api_search(context) for context in contexts),
            return_exceptions=True,
        )
        total_written = 0
        for context, result in zip(contexts, results):
            tracker_name = str(context.get("tracker_name") or "UNKNOWN")
            if isinstance(result, Exception):
                console.print(f"[red]{tracker_name}: search task failed: {result}[/red]")
                continue
            total_written += int(result or 0)
        return total_written

    async def _run_target_api_search(self, context: dict[str, Any]) -> int:
        candidates = list(context["candidates"])
        tracker_name = str(context["tracker_name"])
        target = context["target"]
        search_config = context["search_config"]
        home_releases = context["home_releases"]
        content_profile = str(context["content_profile"])
        api_cache = context["api_cache"]
        tmdb_cache = context["tmdb_cache"]
        progress_interval = int(context["progress_interval"])
        checkpoint_interval = int(context["checkpoint_interval"])
        cache_file = context["cache_file"]
        api_cache_file = context["api_cache_file"]
        tmdb_cache_file = context["tmdb_cache_file"]
        queue_dir = context["queue_dir"]
        profile_name = str(context["profile_name"])
        queue_name = str(target.get("queue_name") or f"search_{tracker_name.lower()}_{profile_name}").strip()
        queue_file = queue_dir / f"{queue_name}_queue.log"

        search_plan = await self._execute_search_plan(
            candidates,
            tracker_name,
            target,
            search_config,
            home_releases,
            content_profile,
            api_cache,
            tmdb_cache,
            progress_interval,
            checkpoint_interval,
            cache_file,
            api_cache_file,
            tmdb_cache_file,
            queue_file,
        )
        await self._write_json(cache_file, search_plan)
        if self._api_cache_enabled(search_config, target):
            await self._write_json(api_cache_file, api_cache)
        if self._tmdb_cache_enabled(search_config, target):
            await self._write_json(tmdb_cache_file, tmdb_cache)
        queue_source_candidates = [
            str(item["path"])
            for item in search_plan
            if item.get("queue", False)
        ]
        queue_candidates = self._materialize_candidates(queue_source_candidates, target, tracker_name)
        await self._write_queue(queue_file, queue_candidates)

        console.print(
            f"[green]{tracker_name}:[/green] wrote {len(queue_candidates)} candidate(s) to "
            f"[cyan]{queue_file}[/cyan]"
        )
        if self.debug:
            console.print(f"[cyan]{tracker_name}:[/cyan] wrote search plan to [cyan]{cache_file}[/cyan]")
            if self._api_cache_enabled(search_config, target):
                console.print(f"[cyan]{tracker_name}:[/cyan] wrote API cache to [cyan]{api_cache_file}[/cyan]")
        console.print(f"[dim]Upload with: python3 upload.py --queue {queue_name} -tk {tracker_name}[/dim]")
        return len(queue_candidates)

    async def _prepare_tmdb_cache(
        self,
        candidates: list[str],
        tracker_name: str,
        target: dict[str, Any],
        search_config: dict[str, Any],
        home_releases: list[ReleaseInfo],
        content_profile: str,
        tmdb_cache: dict[str, Any],
        tmdb_cache_file: Path,
        progress_interval: int,
        checkpoint_interval: int,
    ) -> None:
        if content_profile not in {"movie", "tv"}:
            return
        total_candidates = len(candidates)
        if total_candidates:
            console.print(f"[cyan]{tracker_name}:[/cyan] preparing TMDB/IMDb ID cache for {total_candidates} candidate(s)...")
        local_prefilter = self._local_prefilter_enabled(search_config, target)
        local_size_threshold = self._local_size_threshold(search_config, target)
        torrent_index_prefilter = self._torrent_index_prefilter_enabled(search_config, target)
        tmdb_cache_ttl_days = self._tmdb_cache_ttl_days(search_config, target)
        tmdb_delay_seconds = self._tmdb_delay_seconds(search_config, target)
        tmdb_error_backoff_seconds = self._tmdb_error_backoff_seconds(search_config, target)
        prepared = 0
        skipped = 0
        for index, candidate in enumerate(candidates, start=1):
            if progress_interval > 0 and (index == 1 or index % progress_interval == 0):
                console.print(
                    f"[dim]{tracker_name}: prepared TMDB IDs for {index - 1}/{total_candidates} "
                    f"candidate(s), {prepared} cached, {skipped} skipped...[/dim]"
                )
            release = self.matcher.parse_release(candidate)
            banned, _ = self.provider.banned_release_group(tracker_name, release)
            if banned:
                skipped += 1
                continue
            if torrent_index_prefilter:
                client_exists, _ = self._torrent_index_candidate_exists(release, tracker_name, search_config, target)
                if client_exists:
                    skipped += 1
                    continue
            if local_prefilter and home_releases:
                local_exists, _, _ = self.matcher.local_duplicate_exists(
                    release,
                    home_releases,
                    size_threshold=local_size_threshold,
                )
                if local_exists:
                    skipped += 1
                    continue
            ids = await self.provider.resolve_external_ids(
                release,
                content_profile,
                tmdb_cache,
                ttl_days=tmdb_cache_ttl_days,
                delay_seconds=tmdb_delay_seconds,
                error_backoff_seconds=tmdb_error_backoff_seconds,
            )
            if ids:
                prepared += 1
            if checkpoint_interval > 0 and index % checkpoint_interval == 0:
                await self._write_json(tmdb_cache_file, tmdb_cache)
                if self.debug:
                    console.print(f"[dim]{tracker_name}: TMDB cache checkpoint saved after {index} candidate(s)[/dim]")
        await self._write_json(tmdb_cache_file, tmdb_cache)
        if total_candidates:
            console.print(
                f"[green]{tracker_name}:[/green] prepared TMDB/IMDb cache "
                f"({prepared} cached, {skipped} skipped)"
            )

    def _resolve_data_path(self, value: str) -> Path:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = Path(self.base_dir) / path
        return path

    def _resolve_source_paths(self, target: dict[str, Any], libraries: dict[str, Any]) -> list[Path]:
        return self._resolve_named_paths(target, libraries, "source_libraries", "source_paths")

    def _resolve_home_paths(self, target: dict[str, Any], libraries: dict[str, Any]) -> list[Path]:
        return self._resolve_named_paths(target, libraries, "home_libraries", "home_paths")

    def _resolve_named_paths(
        self,
        target: dict[str, Any],
        libraries: dict[str, Any],
        library_key: str,
        path_key: str,
    ) -> list[Path]:
        paths: list[Path] = []
        configured_libraries = target.get(library_key) or []
        if isinstance(configured_libraries, str):
            configured_libraries = [configured_libraries]
        if isinstance(configured_libraries, list):
            for library_name in configured_libraries:
                library_paths = libraries.get(str(library_name))
                paths.extend(self._coerce_paths(library_paths))

        paths.extend(self._coerce_paths(target.get(path_key) or []))
        unique_paths: list[Path] = []
        seen: set[str] = set()
        for path in paths:
            path_key = os.fspath(path)
            if path_key not in seen:
                unique_paths.append(path)
                seen.add(path_key)
        return unique_paths

    def _coerce_paths(self, value: Any) -> list[Path]:
        if isinstance(value, str):
            values = [value]
        elif isinstance(value, list):
            values = [str(item) for item in value if str(item).strip()]
        else:
            values = []
        return [Path(item).expanduser() for item in values if item.strip()]

    def _scan_paths(
        self,
        paths: list[Path],
        content_profile: str = "generic",
        label: str = "search",
        progress_interval: int = 5000,
    ) -> list[str]:
        candidates: list[str] = []
        seen: set[str] = set()
        scanned = 0
        for path in paths:
            if not path.exists():
                console.print(f"[yellow]Search path not found: {path}[/yellow]")
                continue
            console.print(f"[dim]{label}: scanning {path}[/dim]")
            if path.is_file():
                self._add_candidate(path, candidates, seen, content_profile)
                continue
            if path.is_dir():
                scanned += self._scan_directory(path, candidates, seen, content_profile, label, progress_interval)
        return candidates

    def _scan_paths_cached(
        self,
        paths: list[Path],
        content_profile: str,
        label: str,
        progress_interval: int,
        scan_memory_cache: dict[tuple[str, tuple[str, ...]], list[str]],
    ) -> list[str]:
        cache_key = self._scan_memory_cache_key(paths, content_profile)
        cached = scan_memory_cache.get(cache_key)
        if cached is not None:
            console.print(f"[dim]{label}: using in-run scan cache with {len(cached)} candidate(s)[/dim]")
            return list(cached)

        candidates = self._scan_paths(paths, content_profile, label, progress_interval)
        scan_memory_cache[cache_key] = list(candidates)
        return candidates

    def _scan_memory_cache_key(self, paths: list[Path], content_profile: str) -> tuple[str, tuple[str, ...]]:
        normalized_paths: list[str] = []
        for path in paths:
            try:
                normalized_paths.append(os.fspath(path.expanduser().resolve()))
            except OSError:
                normalized_paths.append(os.fspath(path.expanduser()))
        return content_profile, tuple(sorted(normalized_paths))

    def _scan_directory(
        self,
        root: Path,
        candidates: list[str],
        seen: set[str],
        content_profile: str,
        label: str,
        progress_interval: int,
    ) -> int:
        if content_profile != "tv" and self._add_disc_candidate(root, candidates, seen):
            return 1

        season_pack_dirs = self._season_pack_dirs(root, label, progress_interval) if content_profile == "tv" else set()
        if content_profile == "tv":
            for pack_dir in sorted(season_pack_dirs, key=lambda p: os.fspath(p).lower()):
                self._add_path_candidate(pack_dir, candidates, seen)
            return len(season_pack_dirs)

        skipped_disc_roots: set[Path] = set()
        scanned = 0
        for item in root.rglob("*"):
            scanned += 1
            if progress_interval > 0 and scanned % progress_interval == 0:
                console.print(f"[dim]{label}: scanned {scanned} filesystem item(s), found {len(candidates)} candidate(s)...[/dim]")
            if any(item == disc_root or disc_root in item.parents for disc_root in skipped_disc_roots):
                continue
            if item.is_dir() and self._add_disc_candidate(item, candidates, seen):
                skipped_disc_roots.add(item)
                continue
            if item.is_file():
                self._add_candidate(item, candidates, seen, content_profile)
        if scanned and progress_interval > 0:
            console.print(f"[dim]{label}: scanned {scanned} filesystem item(s), found {len(candidates)} candidate(s)[/dim]")
        return scanned

    def _add_candidate(self, path: Path, candidates: list[str], seen: set[str], content_profile: str = "generic") -> None:
        suffix = path.suffix.lower()
        if content_profile == "movie":
            if suffix not in VIDEO_EXTENSIONS or self._is_episode_name(path.name):
                return
        elif content_profile == "tv":
            if suffix not in VIDEO_EXTENSIONS or not self._is_season_pack_name(path.name):
                return
        elif content_profile == "music":
            if suffix not in MUSIC_EXTENSIONS:
                return
        elif content_profile == "book":
            if suffix not in BOOK_EXTENSIONS:
                return
        elif suffix not in SUPPORTED_EXTENSIONS:
            return
        self._add_path_candidate(path, candidates, seen)

    def _add_path_candidate(self, path: Path, candidates: list[str], seen: set[str]) -> None:
        resolved = os.fspath(path.resolve())
        if resolved in seen:
            return
        candidates.append(resolved)
        seen.add(resolved)

    def _add_disc_candidate(self, path: Path, candidates: list[str], seen: set[str]) -> bool:
        if not path.is_dir() or not self._is_disc_root(path):
            return False
        self._add_path_candidate(path, candidates, seen)
        return True

    def _is_disc_root(self, path: Path) -> bool:
        for marker_set in DISC_MARKERS.values():
            for marker in marker_set:
                if (path / marker).exists():
                    return True
        return False

    def _season_pack_dirs(self, root: Path, label: str, progress_interval: int) -> set[Path]:
        pack_dirs: set[Path] = set()
        scanned = 0
        directories = (item for item in root.rglob("*") if item.is_dir())
        for directory in itertools.chain([root], directories):
            scanned += 1
            if progress_interval > 0 and scanned % progress_interval == 0:
                console.print(f"[dim]{label}: scanned {scanned} folder(s), found {len(pack_dirs)} season pack(s)...[/dim]")
            if self._is_disc_root(directory):
                self._add_tv_disc_container_dirs(directory, root, pack_dirs)
                continue
            video_files = [
                item for item in directory.iterdir()
                if item.is_file()
                and item.suffix.lower() in VIDEO_EXTENSIONS
                and self._is_episode_name(item.name)
            ]
            if len(video_files) >= 2 and (self._is_season_pack_name(directory.name) or self._has_single_season(video_files)):
                pack_dirs.add(directory)
        if scanned and progress_interval > 0:
            console.print(f"[dim]{label}: scanned {scanned} folder(s), found {len(pack_dirs)} season pack(s)[/dim]")
        return pack_dirs

    def _add_tv_disc_container_dirs(self, disc_root: Path, scan_root: Path, pack_dirs: set[Path]) -> None:
        parent = disc_root.parent
        if parent == disc_root:
            return

        added_matching_parent = False
        for directory in itertools.chain([parent], parent.parents):
            if directory != scan_root and scan_root not in directory.parents:
                break
            if self._is_season_pack_name(directory.name):
                pack_dirs.add(directory)
                added_matching_parent = True
            if directory == scan_root:
                break

        if not added_matching_parent and parent != scan_root:
            pack_dirs.add(parent)

    def _has_single_season(self, paths: list[Path]) -> bool:
        seasons: set[str] = set()
        for path in paths:
            match = re.search(r"(?i)s(\d{1,2})e\d{1,3}", path.name)
            if match:
                seasons.add(match.group(1).zfill(2))
        return len(seasons) == 1

    def _is_episode_name(self, name: str) -> bool:
        return bool(EPISODE_RE.search(name))

    def _is_season_pack_name(self, name: str) -> bool:
        return bool(SEASON_PACK_RE.search(name)) and not self._is_episode_name(name)

    def _content_profile(self, profile_name: str, target: dict[str, Any]) -> str:
        configured = str(target.get("content") or target.get("category") or "").strip().lower()
        if configured:
            if configured in {"movie", "movies"}:
                return "movie"
            if configured in {"tv", "series", "show", "shows"}:
                return "tv"
            if configured in {"music", "audio"}:
                return "music"
            if configured in {"book", "books", "ebook", "audiobook"}:
                return "book"

        normalized = profile_name.lower()
        if any(term in normalized for term in ("tv", "series", "show")):
            return "tv"
        if "music" in normalized:
            return "music"
        if any(term in normalized for term in ("book", "ebook", "audiobook")):
            return "book"
        return "movie"

    def _materialize_candidates(self, candidates: list[str], target: dict[str, Any], tracker_name: str) -> list[str]:
        linking = str(target.get("linking") or "").strip().lower()
        link_destination = str(target.get("link_destination") or "").strip()
        if linking not in {"hardlink", "symlink", "copy"} or not link_destination:
            return candidates

        destination_root = Path(link_destination).expanduser()
        if not destination_root.is_absolute():
            destination_root = Path(self.base_dir) / destination_root
        destination_root.mkdir(parents=True, exist_ok=True)

        linked_candidates: list[str] = []
        for candidate in candidates:
            source = Path(candidate)
            if not source.is_file():
                linked_candidates.append(candidate)
                continue
            if linking == "copy":
                existing_copy = destination_root / source.name
                if existing_copy.exists():
                    try:
                        if existing_copy.stat().st_size == source.stat().st_size:
                            linked_candidates.append(os.fspath(existing_copy.resolve()))
                            continue
                    except OSError:
                        pass
            destination = self._unique_destination(destination_root, source)
            if destination.exists():
                linked_candidates.append(os.fspath(destination.resolve()))
                continue
            try:
                if linking == "hardlink":
                    os.link(source, destination)
                elif linking == "symlink":
                    os.symlink(source, destination)
                else:
                    shutil.copy2(source, destination)
                linked_candidates.append(os.fspath(destination.resolve()))
            except OSError as e:
                console.print(f"[yellow]{tracker_name}: could not {linking} {source.name}: {e}. Using original path.[/yellow]")
                linked_candidates.append(candidate)
        return linked_candidates

    async def _execute_search_plan(
        self,
        candidates: list[str],
        tracker_name: str,
        target: dict[str, Any],
        search_config: dict[str, Any],
        home_releases: list[ReleaseInfo],
        content_profile: str,
        api_cache: dict[str, Any],
        tmdb_cache: dict[str, Any],
        progress_interval: int,
        checkpoint_interval: int,
        cache_file: Path,
        api_cache_file: Path,
        tmdb_cache_file: Path,
        queue_file: Path,
    ) -> list[dict[str, Any]]:
        plan: list[dict[str, Any]] = []
        include_unknown = bool(target.get("queue_unknown", True))
        local_prefilter = self._local_prefilter_enabled(search_config, target)
        local_size_threshold = self._local_size_threshold(search_config, target)
        torrent_index_prefilter = self._torrent_index_prefilter_enabled(search_config, target)
        api_cache_enabled = self._api_cache_enabled(search_config, target)
        api_cache_ttl_days = self._api_cache_ttl_days(search_config, target)
        tmdb_lookup = self._tmdb_lookup_enabled(search_config, target)
        tmdb_cache_ttl_days = self._tmdb_cache_ttl_days(search_config, target)
        tmdb_delay_seconds = self._tmdb_delay_seconds(search_config, target)
        tmdb_error_backoff_seconds = self._tmdb_error_backoff_seconds(search_config, target)
        api_delay_seconds = self._api_delay_seconds(search_config, target)
        api_error_backoff_seconds = self._api_error_backoff_seconds(search_config, target)
        total_candidates = len(candidates)
        if total_candidates:
            console.print(f"[cyan]{tracker_name}:[/cyan] checking {total_candidates} candidate(s) against filters/cache/API...")
        for index, candidate in enumerate(candidates, start=1):
            if progress_interval > 0 and (index == 1 or index % progress_interval == 0):
                console.print(f"[dim]{tracker_name}: checked {index - 1}/{total_candidates} candidate(s)...[/dim]")
            release = self.matcher.parse_release(candidate)
            ids = {}
            if tmdb_lookup:
                ids = await self.provider.resolve_external_ids(
                    release,
                    content_profile,
                    tmdb_cache,
                    ttl_days=tmdb_cache_ttl_days,
                    delay_seconds=tmdb_delay_seconds,
                    error_backoff_seconds=tmdb_error_backoff_seconds,
                )
            queries = self.matcher.build_queries(release, ids)
            banned, banned_group = self.provider.banned_release_group(tracker_name, release)
            if banned:
                plan.append({
                    "tracker": tracker_name,
                    "path": candidate,
                    "release": release.to_dict(),
                    "ids": ids,
                    "queries": [query.__dict__ for query in queries],
                    "status": "banned_group",
                    "reason": f"banned_group:{banned_group}",
                    "queue": False,
                    "matched_result": None,
                    "local_match": None,
                    "query_results": [],
                    "cache_hit": False,
                })
                if self.debug:
                    console.print(f"[yellow]{tracker_name}: {release.basename} -> banned_group ({banned_group})[/yellow]")
                await self._checkpoint_search_progress(
                    index,
                    checkpoint_interval,
                    cache_file,
                    plan,
                    api_cache_file,
                    api_cache,
                    tmdb_cache_file,
                    tmdb_cache,
                    search_config,
                    target,
                    tracker_name,
                    queue_file,
                )
                continue
            if torrent_index_prefilter:
                client_exists, client_reason = self._torrent_index_candidate_exists(release, tracker_name, search_config, target)
                if client_exists:
                    plan.append({
                        "tracker": tracker_name,
                        "path": candidate,
                        "release": release.to_dict(),
                        "ids": ids,
                        "queries": [query.__dict__ for query in queries],
                        "status": "client_exists",
                        "reason": client_reason,
                        "queue": False,
                        "matched_result": None,
                        "local_match": None,
                        "query_results": [],
                        "cache_hit": False,
                    })
                    if self.debug:
                        console.print(f"[yellow]{tracker_name}: {release.basename} -> client_exists ({client_reason})[/yellow]")
                    await self._checkpoint_search_progress(
                        index,
                        checkpoint_interval,
                        cache_file,
                        plan,
                        api_cache_file,
                        api_cache,
                        tmdb_cache_file,
                        tmdb_cache,
                        search_config,
                        target,
                        tracker_name,
                        queue_file,
                    )
                    continue
            if local_prefilter and home_releases:
                local_exists, local_reason, local_match = self.matcher.local_duplicate_exists(
                    release,
                    home_releases,
                    size_threshold=local_size_threshold,
                )
                if local_exists:
                    plan.append({
                        "tracker": tracker_name,
                        "path": candidate,
                        "release": release.to_dict(),
                        "ids": ids,
                        "queries": [query.__dict__ for query in queries],
                        "status": "local_exists",
                        "reason": local_reason,
                        "queue": False,
                        "matched_result": None,
                        "local_match": local_match.to_dict() if local_match else None,
                        "query_results": [],
                        "cache_hit": False,
                    })
                    if self.debug:
                        match_name = local_match.basename if local_match else "unknown"
                        console.print(f"[yellow]{tracker_name}: {release.basename} -> local_exists ({local_reason}: {match_name})[/yellow]")
                    await self._checkpoint_search_progress(
                        index,
                        checkpoint_interval,
                        cache_file,
                        plan,
                        api_cache_file,
                        api_cache,
                        tmdb_cache_file,
                        tmdb_cache,
                        search_config,
                        target,
                        tracker_name,
                        queue_file,
                    )
                    continue

            cache_key = self._api_cache_key(tracker_name, content_profile, release)
            cached_result = self._api_cache_lookup(api_cache, cache_key, api_cache_ttl_days) if api_cache_enabled else None
            if cached_result is not None:
                status = str(cached_result.get("status") or "unknown")
                should_queue = status == "missing" or (status == "unknown" and include_unknown)
                plan.append({
                    "tracker": tracker_name,
                    "path": candidate,
                    "release": release.to_dict(),
                    "ids": ids,
                    "queries": [query.__dict__ for query in queries],
                    "status": status,
                    "reason": cached_result.get("reason", "api_cache_hit"),
                    "queue": should_queue,
                    "matched_result": cached_result.get("matched_result"),
                    "local_match": None,
                    "query_results": cached_result.get("query_results", []),
                    "cache_hit": True,
                })
                if self.debug:
                    color = "green" if should_queue else "yellow"
                    console.print(f"[{color}]{tracker_name}: {release.basename} -> {status} (api_cache_hit)[/{color}]")
                await self._checkpoint_search_progress(
                    index,
                    checkpoint_interval,
                    cache_file,
                    plan,
                    api_cache_file,
                    api_cache,
                    tmdb_cache_file,
                    tmdb_cache,
                    search_config,
                    target,
                    tracker_name,
                    queue_file,
                )
                continue

            tracker_result = await self.provider.check_tracker(
                tracker_name,
                release,
                queries,
                content_profile,
                ids,
                api_delay_seconds=api_delay_seconds,
                api_error_backoff_seconds=api_error_backoff_seconds,
            )
            status = str(tracker_result.get("status") or "unknown")
            should_queue = status == "missing" or (status == "unknown" and include_unknown)
            if api_cache_enabled and self._should_cache_status(status, search_config, target):
                self._api_cache_store(api_cache, cache_key, tracker_name, content_profile, release, status, tracker_result)
            plan.append({
                "tracker": tracker_name,
                "path": candidate,
                "release": release.to_dict(),
                "ids": ids,
                "queries": [query.__dict__ for query in queries],
                "status": status,
                "reason": tracker_result.get("reason"),
                "queue": should_queue,
                "matched_result": tracker_result.get("matched_result"),
                "local_match": None,
                "query_results": tracker_result.get("queries", []),
                "cache_hit": False,
            })
            if self.debug:
                color = "green" if should_queue else "yellow"
                console.print(f"[{color}]{tracker_name}: {release.basename} -> {status} ({tracker_result.get('reason')})[/{color}]")
            await self._checkpoint_search_progress(
                index,
                checkpoint_interval,
                cache_file,
                plan,
                api_cache_file,
                api_cache,
                tmdb_cache_file,
                tmdb_cache,
                search_config,
                target,
                tracker_name,
                queue_file,
            )
        if total_candidates:
            console.print(f"[cyan]{tracker_name}:[/cyan] finished checking {total_candidates} candidate(s)")
        return plan

    def _local_prefilter_enabled(self, search_config: dict[str, Any], target: dict[str, Any]) -> bool:
        if "local_prefilter" in target:
            return bool(target.get("local_prefilter"))
        return bool(search_config.get("local_prefilter", True))

    def _local_size_threshold(self, search_config: dict[str, Any], target: dict[str, Any]) -> float:
        value = target.get("local_size_threshold", search_config.get("local_size_threshold", self.matcher.fuzzy_size_threshold))
        try:
            threshold = float(value)
        except (TypeError, ValueError):
            threshold = self.matcher.fuzzy_size_threshold
        return max(0.0, min(threshold, 0.25))

    def _torrent_index_prefilter_enabled(self, search_config: dict[str, Any], target: dict[str, Any]) -> bool:
        if "torrent_index_prefilter" in target:
            return bool(target.get("torrent_index_prefilter"))
        return bool(search_config.get("torrent_index_prefilter", True))

    def _torrent_index_path(self, search_config: dict[str, Any], target: dict[str, Any]) -> Path:
        value = str(target.get("torrent_index_path") or search_config.get("torrent_index_path") or "tmp/torrent_file_index.sqlite")
        return self._resolve_data_path(value)

    def _torrent_index_candidate_exists(
        self,
        release: ReleaseInfo,
        tracker_name: str,
        search_config: dict[str, Any],
        target: dict[str, Any],
    ) -> tuple[bool, str]:
        db_path = self._torrent_index_path(search_config, target)
        if not db_path.exists():
            return False, "torrent_index_missing"
        tracker_hosts = self._tracker_announce_hosts(tracker_name)
        if not tracker_hosts:
            return False, "tracker_announce_missing"
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
        except sqlite3.Error:
            return False, "torrent_index_open_failed"
        try:
            file_count, file_basenames, torrent_name = self._torrent_index_match_values(release)
            rows = conn.execute(
                """
                SELECT * FROM torrent_file_index
                WHERE (file_count = ? AND file_basenames = ?)
                   OR LOWER(torrent_name) = ?
                """,
                (file_count, file_basenames, torrent_name),
            ).fetchall()
        except sqlite3.Error:
            return False, "torrent_index_query_failed"
        finally:
            conn.close()

        release_size = int(release.size or 0)
        for row in rows:
            if not self._torrent_index_row_is_current(row):
                continue
            try:
                total_size = int(row["total_size"])
            except (TypeError, ValueError):
                total_size = 0
            if release_size and total_size and not self.matcher._size_values_match(release_size, total_size, size_threshold=0.02):
                continue
            torrent_path = str(row["path"])
            if self._torrent_file_has_tracker(torrent_path, tracker_hosts):
                return True, f"torrent_index_tracker_match:{str(row['infohash'])}"
        return False, "no_torrent_index_tracker_match"

    def _torrent_index_match_values(self, release: ReleaseInfo) -> tuple[int, str, str]:
        path = Path(release.path)
        if path.exists() and path.is_file():
            file_basenames = [path.name.lower()]
            torrent_name = path.stem.lower()
        elif path.exists() and path.is_dir():
            file_basenames = sorted(item.name.lower() for item in path.rglob("*") if item.is_file())
            torrent_name = path.name.lower()
        else:
            file_basenames = [release.basename.lower()] if release.basename else []
            torrent_name = release.release_name.lower()
        return len(file_basenames), json.dumps(file_basenames, separators=(",", ":")), torrent_name

    def _torrent_index_row_is_current(self, row: sqlite3.Row) -> bool:
        try:
            stat = os.stat(str(row["path"]))
            return int(row["mtime_ns"]) == stat.st_mtime_ns and int(row["file_size"]) == stat.st_size
        except (OSError, TypeError, ValueError):
            return False

    def _tracker_announce_hosts(self, tracker_name: str) -> set[str]:
        tracker_config = self.config.get("TRACKERS", {}).get(tracker_name.upper(), {})
        announce = str(tracker_config.get("announce_url") or "").strip()
        host = urllib.parse.urlparse(announce).hostname if announce else ""
        return {host.lower()} if host else set()

    def _torrent_file_has_tracker(self, torrent_path: str, tracker_hosts: set[str]) -> bool:
        try:
            torrent = Torrent.read(torrent_path)
        except Exception:
            return False
        urls: list[str] = []
        metainfo = getattr(torrent, "metainfo", {})
        if isinstance(metainfo, dict):
            announce = metainfo.get("announce")
            if announce:
                urls.append(str(announce))
            announce_list = metainfo.get("announce-list")
            if isinstance(announce_list, list):
                for tier in announce_list:
                    if isinstance(tier, list):
                        urls.extend(str(item) for item in tier if item)
                    elif tier:
                        urls.append(str(tier))
        for tracker_url in urls:
            host = urllib.parse.urlparse(tracker_url).hostname
            if host and self._host_matches_any(host.lower(), tracker_hosts):
                return True
        return False

    def _host_matches_any(self, host: str, tracker_hosts: set[str]) -> bool:
        return any(host == tracker_host or host.endswith(f".{tracker_host}") for tracker_host in tracker_hosts)

    def _api_cache_enabled(self, search_config: dict[str, Any], target: dict[str, Any]) -> bool:
        if "api_cache" in target:
            return bool(target.get("api_cache"))
        return bool(search_config.get("api_cache", True))

    def _api_cache_ttl_days(self, search_config: dict[str, Any], target: dict[str, Any]) -> float:
        value = target.get("api_cache_ttl_days", search_config.get("api_cache_ttl_days", 30))
        try:
            ttl_days = float(value)
        except (TypeError, ValueError):
            ttl_days = 30.0
        return max(0.0, ttl_days)

    def _tmdb_lookup_enabled(self, search_config: dict[str, Any], target: dict[str, Any]) -> bool:
        if "tmdb_lookup" in target:
            return bool(target.get("tmdb_lookup"))
        return bool(search_config.get("tmdb_lookup", True))

    def _tmdb_cache_enabled(self, search_config: dict[str, Any], target: dict[str, Any]) -> bool:
        if "tmdb_cache" in target:
            return bool(target.get("tmdb_cache"))
        return bool(search_config.get("tmdb_cache", True))

    def _tmdb_cache_ttl_days(self, search_config: dict[str, Any], target: dict[str, Any]) -> float:
        value = target.get("tmdb_cache_ttl_days", search_config.get("tmdb_cache_ttl_days", 180))
        try:
            ttl_days = float(value)
        except (TypeError, ValueError):
            ttl_days = 180.0
        return max(0.0, ttl_days)

    def _tmdb_delay_seconds(self, search_config: dict[str, Any], target: dict[str, Any]) -> float:
        value = target.get("tmdb_delay_seconds", search_config.get("tmdb_delay_seconds", 0.25))
        try:
            delay = float(value)
        except (TypeError, ValueError):
            delay = 0.25
        return max(0.0, delay)

    def _tmdb_error_backoff_seconds(self, search_config: dict[str, Any], target: dict[str, Any]) -> float:
        value = target.get("tmdb_error_backoff_seconds", search_config.get("tmdb_error_backoff_seconds", 5))
        try:
            delay = float(value)
        except (TypeError, ValueError):
            delay = 5.0
        return max(0.0, delay)

    def _progress_interval(self, search_config: dict[str, Any], target: dict[str, Any]) -> int:
        value = target.get("progress_interval", search_config.get("progress_interval", 5000))
        try:
            interval = int(value)
        except (TypeError, ValueError):
            interval = 5000
        return max(0, interval)

    def _checkpoint_interval(self, search_config: dict[str, Any], target: dict[str, Any]) -> int:
        value = target.get("checkpoint_interval", search_config.get("checkpoint_interval", 500))
        try:
            interval = int(value)
        except (TypeError, ValueError):
            interval = 500
        return max(0, interval)

    def _api_delay_seconds(self, search_config: dict[str, Any], target: dict[str, Any]) -> float:
        value = target.get("api_delay_seconds", search_config.get("api_delay_seconds", 0))
        try:
            delay = float(value)
        except (TypeError, ValueError):
            delay = 0.0
        return max(0.0, delay)

    def _api_error_backoff_seconds(self, search_config: dict[str, Any], target: dict[str, Any]) -> float:
        value = target.get("api_error_backoff_seconds", search_config.get("api_error_backoff_seconds", 5))
        try:
            delay = float(value)
        except (TypeError, ValueError):
            delay = 5.0
        return max(0.0, delay)

    def _scan_cache_enabled(self, search_config: dict[str, Any], target: dict[str, Any]) -> bool:
        if "scan_cache" in target:
            return bool(target.get("scan_cache"))
        return bool(search_config.get("scan_cache", True))

    def _scan_cache_ttl_hours(self, search_config: dict[str, Any], target: dict[str, Any]) -> float:
        value = target.get("scan_cache_ttl_hours", search_config.get("scan_cache_ttl_hours", 24))
        try:
            ttl_hours = float(value)
        except (TypeError, ValueError):
            ttl_hours = 24.0
        return max(0.0, ttl_hours)

    async def _load_scan_checkpoint(
        self,
        path: Path,
        tracker_name: str,
        profile_name: str,
        content_profile: str,
        search_config: dict[str, Any],
        target: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        if not self._scan_cache_enabled(search_config, target) or not path.exists():
            return None
        try:
            content = await self._read_text(path)
            data = json.loads(content)
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        if str(data.get("tracker") or "").upper() != tracker_name:
            return None
        if str(data.get("profile") or "") != profile_name:
            return None
        if str(data.get("content") or "") != content_profile:
            return None
        if data.get("stage") != "home_scanned":
            return None
        source_candidates = data.get("source_candidates")
        home_candidates = data.get("home_candidates")
        if not isinstance(source_candidates, list) or not isinstance(home_candidates, list):
            return None
        ttl_hours = self._scan_cache_ttl_hours(search_config, target)
        updated_at = data.get("updated_at")
        try:
            updated_at_float = float(updated_at)
        except (TypeError, ValueError):
            return None
        if ttl_hours > 0 and (time.time() - updated_at_float) > ttl_hours * 3600:
            return None
        data["source_candidates"] = [str(item) for item in source_candidates if str(item).strip()]
        data["home_candidates"] = [str(item) for item in home_candidates if str(item).strip()]
        return data

    async def _write_scan_checkpoint(
        self,
        path: Path,
        tracker_name: str,
        profile_name: str,
        content_profile: str,
        source_candidates: list[str],
        home_candidates: list[str],
        stage: str,
    ) -> None:
        await self._write_json(path, {
            "version": 1,
            "stage": stage,
            "tracker": tracker_name,
            "profile": profile_name,
            "content": content_profile,
            "source_count": len(source_candidates),
            "home_count": len(home_candidates),
            "source_candidates": source_candidates,
            "home_candidates": home_candidates,
            "updated_at": time.time(),
        })
        console.print(f"[cyan]{tracker_name}:[/cyan] wrote scan checkpoint to [cyan]{path}[/cyan]")

    async def _write_run_state(
        self,
        path: Path,
        profile_name: Optional[str],
        target_filter: Optional[list[str]],
        stage: str,
        **extra: Any,
    ) -> None:
        data = {
            "version": 1,
            "stage": stage,
            "profile": profile_name or "",
            "target_filter": target_filter or [],
            "updated_at": time.time(),
        }
        data.update(extra)
        await self._write_json(path, data)

    async def _checkpoint_search_progress(
        self,
        index: int,
        checkpoint_interval: int,
        cache_file: Path,
        plan: list[dict[str, Any]],
        api_cache_file: Path,
        api_cache: dict[str, Any],
        tmdb_cache_file: Path,
        tmdb_cache: dict[str, Any],
        search_config: dict[str, Any],
        target: dict[str, Any],
        tracker_name: str,
        queue_file: Path,
    ) -> None:
        if checkpoint_interval <= 0 or index % checkpoint_interval != 0:
            return
        await self._write_json(cache_file, plan)
        if self._api_cache_enabled(search_config, target):
            await self._write_json(api_cache_file, api_cache)
        if self._tmdb_cache_enabled(search_config, target):
            await self._write_json(tmdb_cache_file, tmdb_cache)
        queue_source_candidates = [
            str(item["path"])
            for item in plan
            if item.get("queue", False)
        ]
        queue_candidates = self._materialize_candidates(queue_source_candidates, target, tracker_name)
        await self._write_queue(queue_file, queue_candidates)
        if self.debug:
            console.print(
                f"[dim]{tracker_name}: checkpoint saved after {index} candidate(s), "
                f"{len(queue_candidates)} queued[/dim]"
            )

    def _api_cache_key(self, tracker_name: str, content_profile: str, release: ReleaseInfo) -> str:
        release_key = self.matcher.normalize_release_name(release.release_name)
        return "|".join([
            tracker_name.upper(),
            content_profile,
            release_key,
            str(release.size),
        ])

    def _api_cache_lookup(self, api_cache: dict[str, Any], cache_key: str, ttl_days: float) -> Optional[dict[str, Any]]:
        entries = api_cache.get("entries")
        if not isinstance(entries, dict):
            return None
        entry = entries.get(cache_key)
        if not isinstance(entry, dict):
            return None
        checked_at = entry.get("checked_at")
        try:
            checked_at_float = float(checked_at)
        except (TypeError, ValueError):
            return None
        if ttl_days > 0 and (time.time() - checked_at_float) > ttl_days * 86400:
            return None
        return entry

    def _api_cache_store(
        self,
        api_cache: dict[str, Any],
        cache_key: str,
        tracker_name: str,
        content_profile: str,
        release: ReleaseInfo,
        status: str,
        tracker_result: dict[str, Any],
    ) -> None:
        entries = api_cache.setdefault("entries", {})
        if not isinstance(entries, dict):
            api_cache["entries"] = {}
            entries = api_cache["entries"]
        entries[cache_key] = {
            "tracker": tracker_name,
            "content": content_profile,
            "release": release.to_dict(),
            "status": status,
            "reason": tracker_result.get("reason"),
            "matched_result": tracker_result.get("matched_result"),
            "query_results": tracker_result.get("queries", []),
            "checked_at": time.time(),
        }

    def _should_cache_status(self, status: str, search_config: dict[str, Any], target: dict[str, Any]) -> bool:
        if status in {"exists", "missing"}:
            return True
        cache_unknown = target.get("api_cache_unknown", search_config.get("api_cache_unknown", False))
        return bool(cache_unknown)

    async def _load_api_cache(self, cache_file: Path) -> dict[str, Any]:
        import asyncio

        if not cache_file.exists():
            return {"version": 1, "entries": {}}
        try:
            content = await asyncio.to_thread(cache_file.read_text, encoding="utf-8")
            data = json.loads(content)
            if isinstance(data, dict) and isinstance(data.get("entries"), dict):
                data["version"] = data.get("version", 1)
                return data
        except (OSError, json.JSONDecodeError):
            pass
        return {"version": 1, "entries": {}}

    def _unique_destination(self, destination_root: Path, source: Path) -> Path:
        destination = destination_root / source.name
        if not destination.exists():
            return destination
        try:
            if destination.samefile(source):
                return destination
        except OSError:
            pass
        stem = destination.stem
        suffix = destination.suffix
        counter = 2
        while True:
            candidate = destination_root / f"{stem}.{counter}{suffix}"
            if not candidate.exists():
                return candidate
            counter += 1

    async def _write_queue(self, queue_file: Path, candidates: list[str]) -> None:
        queue_file.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(candidates, indent=4)
        await self._write_text(queue_file, content + "\n")

    async def _write_json(self, path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        await self._write_text(path, json.dumps(data, indent=4) + "\n")
        if not path.exists():
            console.print(f"[red]Search cache write failed: {path} was not created[/red]")

    async def _write_text(self, path: Path, text: str) -> None:
        import asyncio

        await asyncio.to_thread(path.write_text, text, encoding="utf-8")

    async def _read_text(self, path: Path) -> str:
        import asyncio

        return await asyncio.to_thread(path.read_text, encoding="utf-8")
