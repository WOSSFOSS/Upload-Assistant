import json
import os
import shutil
from pathlib import Path
from typing import Any, Optional

from src.console import console


VIDEO_EXTENSIONS = {".mkv", ".mp4", ".ts", ".avi", ".mov", ".m2ts"}
MUSIC_EXTENSIONS = {".flac", ".mp3", ".m4a", ".aac", ".alac", ".wav", ".ogg", ".opus"}
BOOK_EXTENSIONS = {".epub", ".pdf", ".mobi", ".azw3", ".lit", ".cbz", ".cbr", ".m4b"}
SUPPORTED_EXTENSIONS = VIDEO_EXTENSIONS | MUSIC_EXTENSIONS | BOOK_EXTENSIONS


class SearchRunner:
    def __init__(self, config: dict[str, Any], base_dir: str, debug: bool = False) -> None:
        self.config = config
        self.base_dir = base_dir
        self.debug = debug

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

                candidates = self._scan_paths(source_paths)
                queue_candidates = self._materialize_candidates(candidates, target, tracker_name)
                queue_name = str(target.get("queue_name") or f"search_{tracker_name.lower()}_{name}").strip()
                queue_file = queue_dir / f"{queue_name}_queue.log"
                await self._write_queue(queue_file, queue_candidates)
                total_written += len(queue_candidates)

                console.print(
                    f"[green]{tracker_name}:[/green] wrote {len(queue_candidates)} candidate(s) to "
                    f"[cyan]{queue_file}[/cyan]"
                )
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
        paths: list[Path] = []
        source_libraries = target.get("source_libraries") or []
        if isinstance(source_libraries, str):
            source_libraries = [source_libraries]
        if isinstance(source_libraries, list):
            for library_name in source_libraries:
                library_paths = libraries.get(str(library_name))
                paths.extend(self._coerce_paths(library_paths))

        paths.extend(self._coerce_paths(target.get("source_paths") or []))
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

    def _scan_paths(self, paths: list[Path]) -> list[str]:
        candidates: list[str] = []
        seen: set[str] = set()
        for path in paths:
            if not path.exists():
                console.print(f"[yellow]Search path not found: {path}[/yellow]")
                continue
            if path.is_file():
                self._add_candidate(path, candidates, seen)
                continue
            for item in sorted(path.rglob("*"), key=lambda p: os.fspath(p).lower()):
                if item.is_file():
                    self._add_candidate(item, candidates, seen)
        return candidates

    def _add_candidate(self, path: Path, candidates: list[str], seen: set[str]) -> None:
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return
        resolved = os.fspath(path.resolve())
        if resolved in seen:
            return
        candidates.append(resolved)
        seen.add(resolved)

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

    async def _write_text(self, path: Path, text: str) -> None:
        import asyncio

        await asyncio.to_thread(path.write_text, text, encoding="utf-8")
