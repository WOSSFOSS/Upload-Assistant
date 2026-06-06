import json
import re
import time
from pathlib import Path
from typing import Any, Optional

import httpx

from src.console import console
from src.search.matcher import ReleaseInfo, SearchMatcher, SearchQuery
from src.trackersetup import tracker_class_map


class SearchProvider:
    def __init__(self, config: dict[str, Any], base_dir: str, matcher: SearchMatcher, debug: bool = False) -> None:
        self.config = config
        self.base_dir = base_dir
        self.matcher = matcher
        self.debug = debug

    async def resolve_external_ids(
        self,
        release: ReleaseInfo,
        content_profile: str,
        tmdb_cache: dict[str, Any],
        ttl_days: float = 180,
    ) -> dict[str, Any]:
        if content_profile not in {"movie", "tv"}:
            return {}
        default_config = self.config.get("DEFAULT", {})
        api_key = str(default_config.get("tmdb_api") or "").strip() if isinstance(default_config, dict) else ""
        if not api_key or not release.title:
            return {}

        cache_key = self._tmdb_cache_key(content_profile, release)
        cached = self._tmdb_cache_lookup(tmdb_cache, cache_key, ttl_days)
        if cached is not None:
            return cached

        search_type = "movie" if content_profile == "movie" else "tv"
        params: dict[str, Any] = {
            "api_key": api_key,
            "query": release.title,
            "include_adult": "false",
        }
        if release.year:
            params["year" if search_type == "movie" else "first_air_date_year"] = release.year

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                search_response = await client.get(f"https://api.themoviedb.org/3/search/{search_type}", params=params)
                search_response.raise_for_status()
                search_data = search_response.json()
                result = self._best_tmdb_result(release, search_data.get("results", []), search_type)
                if not result:
                    ids = {"tmdb": "", "imdb": "", "source": "tmdb_no_match"}
                    self._tmdb_cache_store(tmdb_cache, cache_key, ids)
                    return ids

                tmdb_id = str(result.get("id") or "")
                ids = {"tmdb": tmdb_id, "imdb": "", "source": "tmdb"}
                if tmdb_id:
                    external_response = await client.get(
                        f"https://api.themoviedb.org/3/{search_type}/{tmdb_id}/external_ids",
                        params={"api_key": api_key},
                    )
                    if external_response.status_code < 400:
                        external_data = external_response.json()
                        ids["imdb"] = str(external_data.get("imdb_id") or "")
                self._tmdb_cache_store(tmdb_cache, cache_key, ids)
                return ids
        except Exception as e:
            if self.debug:
                console.print(f"[yellow]TMDB lookup failed for {release.release_name}: {e}[/yellow]")
            return {}

    def banned_release_group(self, tracker_name: str, release: ReleaseInfo) -> tuple[bool, str]:
        if not release.group:
            return False, ""
        tracker_key = tracker_name.upper()
        tracker_class = tracker_class_map.get(tracker_key)
        if tracker_class is None:
            return False, ""
        try:
            tracker = tracker_class(self.config)
            banned_groups = list(getattr(tracker, "banned_groups", []) or [])
        except Exception:
            banned_groups = []
        banned_groups.extend(self._banned_groups_from_file(tracker_key))
        release_group = release.group.lower()
        if release_group == "taoe":
            release_group = "taoe"
        for banned_group in banned_groups:
            if isinstance(banned_group, list):
                banned_name = str(banned_group[0]) if banned_group else ""
            else:
                banned_name = str(banned_group)
            if release_group and banned_name and release_group == banned_name.lower():
                return True, banned_name
        return False, ""

    async def check_tracker(
        self,
        tracker_name: str,
        release: ReleaseInfo,
        queries: list[SearchQuery],
        content_profile: str = "movie",
        ids: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        tracker_key = tracker_name.upper()
        tracker_class = tracker_class_map.get(tracker_key)
        if tracker_class is None:
            return {
                "status": "unknown",
                "reason": "unsupported_tracker",
                "matched": False,
                "results": [],
            }

        tracker = tracker_class(self.config)
        all_results: list[dict[str, Any]] = []
        query_log: list[dict[str, Any]] = []

        for query in queries:
            meta = self._search_meta(tracker_key, release, query, content_profile, ids or {})
            try:
                raw_results = await tracker.search_existing(meta, None)
            except Exception as e:
                query_log.append({
                    "stage": query.stage,
                    "query": query.query,
                    "status": "error",
                    "error": str(e),
                })
                if self.debug:
                    console.print(f"[yellow]{tracker_key}: search query '{query.query}' failed: {e}[/yellow]")
                continue

            results = self._coerce_results(raw_results)
            query_log.append({
                "stage": query.stage,
                "query": query.query,
                "status": "ok",
                "results": len(results),
            })
            all_results.extend(results)

            for result in results:
                matched, reason = self.matcher.result_matches_release(release, result)
                if matched:
                    return {
                        "status": "exists",
                        "reason": reason,
                        "matched": True,
                        "matched_result": result,
                        "queries": query_log,
                        "results": all_results,
                    }

        if not query_log:
            return {
                "status": "unknown",
                "reason": "no_queries",
                "matched": False,
                "queries": query_log,
                "results": all_results,
            }

        if all(entry.get("status") == "error" for entry in query_log):
            return {
                "status": "unknown",
                "reason": "all_queries_failed",
                "matched": False,
                "queries": query_log,
                "results": all_results,
            }

        return {
            "status": "missing",
            "reason": "no_strong_match",
            "matched": False,
            "queries": query_log,
            "results": all_results,
        }

    def _search_meta(
        self,
        tracker_name: str,
        release: ReleaseInfo,
        query: SearchQuery,
        content_profile: str,
        ids: dict[str, Any],
    ) -> dict[str, Any]:
        ext = release.basename.rsplit(".", 1)[-1].lower() if "." in release.basename else ""
        is_tv = content_profile == "tv"
        is_disc = self._disc_type(release.path)
        uhd = "UHD" if release.resolution in {"2160p", "4320p", "8640p"} else ""
        size_gib = release.size / (1024 ** 3) if release.size else 0
        tmdb = str(ids.get("tmdb") or "0")
        imdb = str(ids.get("imdb") or "0")
        imdb_numeric = re.sub(r"^tt", "", imdb)
        return {
            "base_dir": self.base_dir,
            "path": release.path,
            "uuid": release.release_name,
            "name": release.release_name,
            "clean_name": release.release_name,
            "search_query": "" if query.stage in {"tmdb", "imdb"} else query.query,
            "search_stage": query.stage,
            "search_mode": True,
            "trackers": [tracker_name],
            "tracker_status": {tracker_name: {}},
            "debug": self.debug,
            "unattended": True,
            "unattended_confirm": False,
            "category": "TV" if is_tv else "MOVIE",
            "type": release.type or "ENCODE",
            "source": release.source or "",
            "resolution": release.resolution or "OTHER",
            "uhd": uhd,
            "sd": 1 if release.resolution in {"480p", "480i", "576p", "576i"} else 0,
            "is_disc": is_disc,
            "bdinfo": {"size": size_gib} if is_disc == "BDMV" else None,
            "dvd_size": self._dvd_size(release.size) if is_disc == "DVD" else "",
            "is_music": False,
            "is_book": False,
            "filelist": [release.path],
            "container": ext,
            "tag": f"-{release.group}" if release.group else "",
            "audio": "",
            "video_codec": "",
            "tmdb": tmdb,
            "tmdb_id": int(tmdb) if tmdb.isdigit() else 0,
            "imdb": imdb_numeric if imdb_numeric.isdigit() else "0",
            "imdb_id": int(imdb_numeric) if imdb_numeric.isdigit() else 0,
            "imdb_info": {"imdbID": imdb} if imdb.startswith("tt") else {},
            "season": self._season(release.release_name) if is_tv else "",
            "episode": "",
            "tv_pack": 1 if is_tv else 0,
            "valid_mi_settings": True,
            "keywords": "",
            "combined_genres": "",
        }

    def _disc_type(self, path: str) -> str | bool:
        from pathlib import Path

        path_obj = Path(path)
        if not path_obj.is_dir():
            return False
        if (path_obj / "BDMV" / "index.bdmv").exists() or (path_obj / "BDMV" / "BACKUP" / "index.bdmv").exists():
            return "BDMV"
        if (path_obj / "VIDEO_TS" / "VIDEO_TS.IFO").exists():
            return "DVD"
        return False

    def _dvd_size(self, size_bytes: int) -> str:
        size_gib = size_bytes / (1024 ** 3) if size_bytes else 0
        return "DVD5" if size_gib <= 4.7 else "DVD9"

    def _tmdb_cache_key(self, content_profile: str, release: ReleaseInfo) -> str:
        title_key = self.matcher.normalize_title(release.title)
        return "|".join([content_profile, title_key, release.year])

    def _tmdb_cache_lookup(self, tmdb_cache: dict[str, Any], cache_key: str, ttl_days: float) -> Optional[dict[str, Any]]:
        entries = tmdb_cache.get("entries")
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
        ids = entry.get("ids")
        return ids if isinstance(ids, dict) else None

    def _tmdb_cache_store(self, tmdb_cache: dict[str, Any], cache_key: str, ids: dict[str, Any]) -> None:
        entries = tmdb_cache.setdefault("entries", {})
        if not isinstance(entries, dict):
            tmdb_cache["entries"] = {}
            entries = tmdb_cache["entries"]
        entries[cache_key] = {
            "ids": ids,
            "checked_at": time.time(),
        }

    def _best_tmdb_result(self, release: ReleaseInfo, results: Any, search_type: str) -> Optional[dict[str, Any]]:
        if not isinstance(results, list) or not results:
            return None
        normalized_title = self.matcher.normalize_title(release.title)
        best: Optional[dict[str, Any]] = None
        for result in results:
            if not isinstance(result, dict):
                continue
            title = str(result.get("title") if search_type == "movie" else result.get("name") or "")
            date = str(result.get("release_date") if search_type == "movie" else result.get("first_air_date") or "")
            year = date[:4] if len(date) >= 4 else ""
            title_match = normalized_title == self.matcher.normalize_title(title)
            year_match = bool(release.year and year and release.year == year)
            if title_match and (not release.year or year_match):
                return result
            if best is None and year_match:
                best = result
            elif best is None and title_match:
                best = result
        return best

    def _banned_groups_from_file(self, tracker_name: str) -> list[str]:
        file_path = Path(self.base_dir) / "data" / "banned" / f"{tracker_name}_banned_groups.json"
        if not file_path.exists():
            return []
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        banned_groups = data.get("banned_groups")
        if isinstance(banned_groups, str):
            return [item.strip() for item in banned_groups.split(",") if item.strip()]
        if isinstance(banned_groups, list):
            return [str(item).strip() for item in banned_groups if str(item).strip()]
        return []

    def _season(self, release_name: str) -> str:
        import re

        match = re.search(r"(?i)(?:^|[.\s_-])s(\d{1,2})(?:[.\s_-]|$)", release_name)
        if match:
            return f"S{int(match.group(1)):02d}"
        match = re.search(r"(?i)season[.\s_-]?(\d{1,2})", release_name)
        if match:
            return f"S{int(match.group(1)):02d}"
        return ""

    def _coerce_results(self, raw_results: Any) -> list[dict[str, Any]]:
        if raw_results in (None, False):
            return []
        if not isinstance(raw_results, list):
            return []
        results: list[dict[str, Any]] = []
        for item in raw_results:
            if isinstance(item, dict):
                results.append(item)
            elif isinstance(item, str):
                results.append({"name": item})
        return results
