import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Optional

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

    async def run(self, profile_name: Optional[str] = None) -> None:
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

        libraries = search_config.get("libraries")
        libraries_map = libraries if isinstance(libraries, dict) else {}

        total_written = 0
        for name, profile in selected_profiles.items():
            if not isinstance(profile, dict):
                continue
            targets = profile.get("targets")
            if not isinstance(targets, dict) or not targets:
                console.print(f"[yellow]SEARCH profile '{name}' has no targets.[/yellow]")
                continue

            console.print(f"[bold cyan]Search profile:[/bold cyan] {name}")
            for tracker, target in targets.items():
                if not isinstance(target, dict):
                    continue
                tracker_name = str(tracker).strip().upper()
                source_paths = self._resolve_source_paths(target, libraries_map)
                if not source_paths:
                    console.print(f"[yellow]{tracker_name}: no source paths configured.[/yellow]")
                    continue

                content_profile = self._content_profile(name, target)
                candidates = self._scan_paths(source_paths, content_profile)
                home_releases: list[ReleaseInfo] = []
                if self._local_prefilter_enabled(search_config, target):
                    home_paths = self._resolve_home_paths(target, libraries_map)
                    home_candidates = self._scan_paths(home_paths, content_profile) if home_paths else []
                    home_releases = [self.matcher.parse_release(candidate) for candidate in home_candidates]
                    if self.debug:
                        console.print(f"[cyan]{tracker_name}:[/cyan] local prefilter loaded {len(home_releases)} home file(s)")

                cache_file = cache_dir / f"{tracker_name.lower()}_{name}_search_plan.json"
                api_cache_file = cache_dir / f"{tracker_name.lower()}_{name}_api_cache.json"
                api_cache = await self._load_api_cache(api_cache_file)
                search_plan = await self._execute_search_plan(
                    candidates,
                    tracker_name,
                    target,
                    search_config,
                    home_releases,
                    content_profile,
                    api_cache,
                )
                await self._write_json(cache_file, search_plan)
                if self._api_cache_enabled(search_config, target):
                    await self._write_json(api_cache_file, api_cache)
                queue_source_candidates = [
                    str(item["path"])
                    for item in search_plan
                    if item.get("queue", False)
                ]
                queue_candidates = self._materialize_candidates(queue_source_candidates, target, tracker_name)
                queue_name = str(target.get("queue_name") or f"search_{tracker_name.lower()}_{name}").strip()
                queue_file = queue_dir / f"{queue_name}_queue.log"
                await self._write_queue(queue_file, queue_candidates)
                total_written += len(queue_candidates)

                console.print(
                    f"[green]{tracker_name}:[/green] wrote {len(queue_candidates)} candidate(s) to "
                    f"[cyan]{queue_file}[/cyan]"
                )
                if self.debug:
                    console.print(f"[cyan]{tracker_name}:[/cyan] wrote search plan to [cyan]{cache_file}[/cyan]")
                    if self._api_cache_enabled(search_config, target):
                        console.print(f"[cyan]{tracker_name}:[/cyan] wrote API cache to [cyan]{api_cache_file}[/cyan]")
                console.print(f"[dim]Upload with: python3 upload.py --queue {queue_name} -tk {tracker_name}[/dim]")

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

    def _scan_paths(self, paths: list[Path], content_profile: str = "generic") -> list[str]:
        candidates: list[str] = []
        seen: set[str] = set()
        for path in paths:
            if not path.exists():
                console.print(f"[yellow]Search path not found: {path}[/yellow]")
                continue
            if path.is_file():
                self._add_candidate(path, candidates, seen, content_profile)
                continue
            if path.is_dir():
                self._scan_directory(path, candidates, seen, content_profile)
        return candidates

    def _scan_directory(self, root: Path, candidates: list[str], seen: set[str], content_profile: str) -> None:
        if self._add_disc_candidate(root, candidates, seen):
            return

        season_pack_dirs = self._season_pack_dirs(root) if content_profile == "tv" else set()
        if content_profile == "tv":
            for pack_dir in sorted(season_pack_dirs, key=lambda p: os.fspath(p).lower()):
                self._add_path_candidate(pack_dir, candidates, seen)
            return

        skipped_disc_roots: set[Path] = set()
        for item in sorted(root.rglob("*"), key=lambda p: os.fspath(p).lower()):
            if any(item == disc_root or disc_root in item.parents for disc_root in skipped_disc_roots):
                continue
            if item.is_dir() and self._add_disc_candidate(item, candidates, seen):
                skipped_disc_roots.add(item)
                continue
            if item.is_file():
                self._add_candidate(item, candidates, seen, content_profile)

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

    def _season_pack_dirs(self, root: Path) -> set[Path]:
        pack_dirs: set[Path] = set()
        for directory in [root, *[item for item in root.rglob("*") if item.is_dir()]]:
            if self._is_disc_root(directory):
                continue
            video_files = [
                item for item in directory.iterdir()
                if item.is_file()
                and item.suffix.lower() in VIDEO_EXTENSIONS
                and self._is_episode_name(item.name)
            ]
            if len(video_files) >= 2 and (self._is_season_pack_name(directory.name) or self._has_single_season(video_files)):
                pack_dirs.add(directory)
        return pack_dirs

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
    ) -> list[dict[str, Any]]:
        plan: list[dict[str, Any]] = []
        include_unknown = bool(target.get("queue_unknown", True))
        local_prefilter = self._local_prefilter_enabled(search_config, target)
        local_size_threshold = self._local_size_threshold(search_config, target)
        api_cache_enabled = self._api_cache_enabled(search_config, target)
        api_cache_ttl_days = self._api_cache_ttl_days(search_config, target)
        for candidate in candidates:
            release = self.matcher.parse_release(candidate)
            queries = self.matcher.build_queries(release)
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
                continue

            tracker_result = await self.provider.check_tracker(tracker_name, release, queries, content_profile)
            status = str(tracker_result.get("status") or "unknown")
            should_queue = status == "missing" or (status == "unknown" and include_unknown)
            if api_cache_enabled and self._should_cache_status(status, search_config, target):
                self._api_cache_store(api_cache, cache_key, tracker_name, content_profile, release, status, tracker_result)
            plan.append({
                "tracker": tracker_name,
                "path": candidate,
                "release": release.to_dict(),
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

    async def _write_text(self, path: Path, text: str) -> None:
        import asyncio

        await asyncio.to_thread(path.write_text, text, encoding="utf-8")
