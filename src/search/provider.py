from typing import Any

from src.console import console
from src.search.matcher import ReleaseInfo, SearchMatcher, SearchQuery
from src.trackersetup import tracker_class_map


class SearchProvider:
    def __init__(self, config: dict[str, Any], base_dir: str, matcher: SearchMatcher, debug: bool = False) -> None:
        self.config = config
        self.base_dir = base_dir
        self.matcher = matcher
        self.debug = debug

    async def check_tracker(
        self,
        tracker_name: str,
        release: ReleaseInfo,
        queries: list[SearchQuery],
        content_profile: str = "movie",
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
            meta = self._search_meta(tracker_key, release, query, content_profile)
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

    def _search_meta(self, tracker_name: str, release: ReleaseInfo, query: SearchQuery, content_profile: str) -> dict[str, Any]:
        ext = release.basename.rsplit(".", 1)[-1].lower() if "." in release.basename else ""
        is_tv = content_profile == "tv"
        is_disc = self._disc_type(release.path)
        return {
            "base_dir": self.base_dir,
            "path": release.path,
            "uuid": release.release_name,
            "name": release.release_name,
            "clean_name": release.release_name,
            "search_query": query.query,
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
            "sd": 1 if release.resolution in {"480p", "480i", "576p", "576i"} else 0,
            "is_disc": is_disc,
            "is_music": False,
            "is_book": False,
            "filelist": [release.path],
            "container": ext,
            "tag": f"-{release.group}" if release.group else "",
            "tmdb": "0",
            "tmdb_id": 0,
            "imdb": "0",
            "imdb_id": 0,
            "imdb_info": {},
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
