import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional


RESOLUTION_RE = re.compile(r"\b(4320p|2160p|1080p|1080i|720p|576p|576i|480p|480i)\b", re.IGNORECASE)
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
SOURCE_TERMS = {
    "bluray": "BLURAY",
    "blu-ray": "BLURAY",
    "bdrip": "BLURAY",
    "brrip": "BLURAY",
    "web-dl": "WEB",
    "webdl": "WEB",
    "webrip": "WEB",
    "web": "WEB",
    "hdtv": "HDTV",
    "uhdtv": "UHDTV",
    "dvd": "DVD",
    "dvdrip": "DVD",
}
TYPE_TERMS = {
    "remux": "REMUX",
    "web-dl": "WEBDL",
    "webdl": "WEBDL",
    "webrip": "WEBRIP",
    "hdtv": "HDTV",
    "dvdrip": "DVDRIP",
    "x264": "ENCODE",
    "h264": "ENCODE",
    "x265": "ENCODE",
    "h265": "ENCODE",
    "hevc": "ENCODE",
}


@dataclass
class SearchQuery:
    stage: str
    query: str
    require_strong_match: bool = True


@dataclass
class ReleaseInfo:
    path: str
    basename: str
    release_name: str
    title: str = ""
    year: str = ""
    group: str = ""
    resolution: str = ""
    source: str = ""
    type: str = ""
    size: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SearchMatcher:
    def __init__(self, fuzzy_size_threshold: float = 0.02) -> None:
        self.fuzzy_size_threshold = fuzzy_size_threshold

    def parse_release(self, path: str) -> ReleaseInfo:
        path_obj = Path(path)
        basename = path_obj.name
        release_name = path_obj.stem
        size = path_obj.stat().st_size if path_obj.exists() and path_obj.is_file() else 0
        group = self._parse_group(release_name)
        year = self._parse_year(release_name)
        resolution = self._parse_resolution(release_name)
        source = self._parse_source(release_name)
        release_type = self._parse_type(release_name)
        title = self._parse_title(release_name, year)
        return ReleaseInfo(
            path=os.fspath(path_obj.resolve()) if path_obj.exists() else path,
            basename=basename,
            release_name=release_name,
            title=title,
            year=year,
            group=group,
            resolution=resolution,
            source=source,
            type=release_type,
            size=size,
        )

    def build_queries(self, release: ReleaseInfo, ids: Optional[dict[str, Any]] = None) -> list[SearchQuery]:
        queries: list[SearchQuery] = []
        ids = ids or {}
        tmdb = str(ids.get("tmdb") or "").strip()
        imdb = str(ids.get("imdb") or "").strip()
        if tmdb:
            queries.append(SearchQuery(stage="tmdb", query=tmdb))
        if imdb:
            queries.append(SearchQuery(stage="imdb", query=imdb))

        title_year = " ".join(part for part in [release.title, release.year] if part).strip()
        if title_year:
            queries.append(SearchQuery(stage="name_year", query=title_year))
        if title_year and release.group:
            queries.append(SearchQuery(stage="name_year_group", query=f"{title_year} {release.group}"))
        if release.release_name:
            queries.append(SearchQuery(stage="release_name", query=self.humanize(release.release_name), require_strong_match=False))
        return self._dedupe_queries(queries)

    def result_matches_release(self, release: ReleaseInfo, result: dict[str, Any]) -> tuple[bool, str]:
        result_name = str(result.get("name") or result.get("release_name") or result.get("title") or "")
        if not result_name:
            return False, "missing_result_name"

        result_release = self.parse_release_name(result_name)
        release_key = self.normalize_release_name(release.release_name)
        result_key = self.normalize_release_name(result_name)
        if release_key and release_key == result_key:
            return True, "exact_release_name"

        title_matches = bool(release.title and result_release.title and self.normalize_title(release.title) == self.normalize_title(result_release.title))
        year_matches = bool(release.year and result_release.year and release.year == result_release.year)
        group_matches = bool(release.group and result_release.group and release.group.lower() == result_release.group.lower())
        resolution_matches = bool(release.resolution and result_release.resolution and release.resolution.lower() == result_release.resolution.lower())
        source_matches = bool(release.source and result_release.source and release.source == result_release.source)
        size_matches = self._size_matches(release.size, result)

        if title_matches and year_matches and group_matches and resolution_matches:
            return True, "title_year_group_resolution"
        if title_matches and year_matches and group_matches and source_matches:
            return True, "title_year_group_source"
        if title_matches and year_matches and resolution_matches and source_matches and size_matches:
            return True, "title_year_resolution_source_size"
        if title_matches and year_matches and group_matches:
            return True, "title_year_group"
        return False, "no_strong_match"

    def parse_release_name(self, release_name: str) -> ReleaseInfo:
        group = self._parse_group(release_name)
        year = self._parse_year(release_name)
        return ReleaseInfo(
            path="",
            basename=release_name,
            release_name=release_name,
            title=self._parse_title(release_name, year),
            year=year,
            group=group,
            resolution=self._parse_resolution(release_name),
            source=self._parse_source(release_name),
            type=self._parse_type(release_name),
        )

    def normalize_release_name(self, value: str) -> str:
        value = Path(value).stem
        return re.sub(r"[^a-z0-9]+", "", value.lower())

    def normalize_title(self, value: str) -> str:
        value = re.sub(r"[^a-z0-9]+", " ", value.lower())
        return re.sub(r"\s+", " ", value).strip()

    def humanize(self, value: str) -> str:
        value = re.sub(r"[._]+", " ", value)
        value = re.sub(r"\s+", " ", value)
        return value.strip()

    def _parse_group(self, release_name: str) -> str:
        match = re.search(r"-(?P<group>[A-Za-z0-9][A-Za-z0-9._-]{1,20})$", release_name)
        if not match:
            return ""
        group = match.group("group").strip(".-_")
        return group if group else ""

    def _parse_year(self, release_name: str) -> str:
        match = YEAR_RE.search(release_name)
        return match.group(0) if match else ""

    def _parse_resolution(self, release_name: str) -> str:
        match = RESOLUTION_RE.search(release_name)
        return match.group(1) if match else ""

    def _parse_source(self, release_name: str) -> str:
        normalized = release_name.lower().replace(".", " ").replace("_", " ")
        for term, source in SOURCE_TERMS.items():
            if re.search(rf"\b{re.escape(term)}\b", normalized):
                return source
        return ""

    def _parse_type(self, release_name: str) -> str:
        normalized = release_name.lower().replace(".", " ").replace("_", " ")
        for term, release_type in TYPE_TERMS.items():
            if re.search(rf"\b{re.escape(term)}\b", normalized):
                return release_type
        return ""

    def _parse_title(self, release_name: str, year: str) -> str:
        title_part = release_name
        if year and year in title_part:
            title_part = title_part.split(year, 1)[0]
        title_part = re.sub(r"-(?P<group>[A-Za-z0-9][A-Za-z0-9._-]{1,20})$", "", title_part)
        title_part = self.humanize(title_part)
        return title_part.strip()

    def _size_matches(self, release_size: int, result: dict[str, Any]) -> bool:
        result_size = result.get("size") or result.get("file_size") or result.get("filesize") or result.get("bytes")
        try:
            result_size_int = int(result_size)
        except (TypeError, ValueError):
            return False
        if release_size <= 0 or result_size_int <= 0:
            return False
        lower = release_size * (1 - self.fuzzy_size_threshold)
        upper = release_size * (1 + self.fuzzy_size_threshold)
        return lower <= result_size_int <= upper

    def _dedupe_queries(self, queries: list[SearchQuery]) -> list[SearchQuery]:
        seen: set[tuple[str, str]] = set()
        deduped: list[SearchQuery] = []
        for query in queries:
            key = (query.stage, query.query.lower())
            if key in seen:
                continue
            seen.add(key)
            deduped.append(query)
        return deduped
