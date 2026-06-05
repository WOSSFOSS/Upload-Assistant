# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any, Optional

import aiofiles
import httpx
from pymediainfo import MediaInfo

from src.console import console


MUSIC_EXTENSIONS = {
    ".aac", ".ac3", ".alac", ".dff", ".dsf", ".eac3", ".flac", ".m4a", ".mp3",
    ".oga", ".ogg", ".opus", ".spx", ".wav", ".wma", ".wsd",
}

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
LOG_EXTENSIONS = {".cue", ".log", ".m3u", ".m3u8", ".nfo", ".txt"}
LOSSLESS_TYPES = {"ALAC", "DFF", "DSD", "DSF", "FLAC", "PCM", "WAV", "WMA LOSSLESS"}
MUSIC_LINK_ICONS = {
    "MusicBrainz": "https://upload.wikimedia.org/wikipedia/commons/f/f2/MusicBrainz_Logo_Mini_%282016%29.svg",
    "Discogs": "https://www.discogs.com/favicon.ico",
    "Deezer": "https://www.google.com/s2/favicons?domain=deezer.com&sz=32",
}


def is_music_path(path: str) -> bool:
    path_obj = Path(path)
    if path_obj.is_file():
        return path_obj.suffix.lower() in MUSIC_EXTENSIONS
    if not path_obj.is_dir():
        return False

    music_files = _collect_files(path_obj, MUSIC_EXTENSIONS)
    if not music_files:
        return False

    video_extensions = {".mkv", ".mp4", ".ts", ".avi", ".wmv", ".mov", ".m2ts"}
    video_files = _collect_files(path_obj, video_extensions)
    return len(music_files) >= len(video_files)


def _collect_files(root: Path, extensions: set[str]) -> list[Path]:
    if root.is_file():
        return [root] if root.suffix.lower() in extensions else []
    return sorted(
        [p for p in root.rglob("*") if p.is_file() and not p.name.startswith("._") and p.suffix.lower() in extensions],
        key=lambda p: os.fspath(p).lower(),
    )


def _mi_value(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        if isinstance(value, dict):
            continue
        value_text = str(value).strip()
        if value_text:
            return value_text
    return ""


def _clean_tag_value(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _duration_string(value: Any) -> str:
    seconds = _duration_seconds(value)
    if seconds is None:
        return ""
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}:{seconds:02d}"


def _duration_seconds(value: Any) -> Optional[int]:
    if isinstance(value, str):
        text = value.strip()
        colon_match = re.match(r"^(?:(\d+):)?(\d+):(\d{2})$", text)
        if colon_match:
            hours = int(colon_match.group(1) or 0)
            minutes = int(colon_match.group(2))
            seconds = int(colon_match.group(3))
            return (hours * 3600) + (minutes * 60) + seconds
        text_match = re.match(
            r"(?:(\d+)\s*h(?:ours?)?)?\s*(?:(\d+)\s*min(?:utes?)?)?\s*(?:(\d+)\s*s(?:ec(?:onds?)?)?)?",
            text,
            re.IGNORECASE,
        )
        if text_match and any(text_match.groups()):
            hours = int(text_match.group(1) or 0)
            minutes = int(text_match.group(2) or 0)
            seconds = int(text_match.group(3) or 0)
            return (hours * 3600) + (minutes * 60) + seconds
    try:
        duration = float(value)
    except (TypeError, ValueError):
        return None
    return int(round(duration / 1000 if duration >= 10000 else duration))


def _strip_discogs_suffix(value: str) -> str:
    return re.sub(r"\s+\(\d+\)$", "", value).strip()


class MusicProcessor:
    def __init__(self, config: dict[str, Any], base_dir: str) -> None:
        self.config = config
        self.base_dir = base_dir
        self.user_agent = "Upload Assistant music metadata"

    async def process(self, meta: dict[str, Any]) -> dict[str, Any]:
        path = Path(meta["path"])
        music_files = _collect_files(path, MUSIC_EXTENSIONS)
        image_files = _collect_files(path, IMAGE_EXTENSIONS)
        log_files = _collect_files(path, LOG_EXTENSIONS)

        if not music_files:
            raise RuntimeError("No supported music files found")

        first_file = music_files[0]
        mediainfo = await self._parse_mediainfo(first_file)
        await self._write_mediainfo_files(meta, first_file, mediainfo)
        general_track = self._first_track(mediainfo, "General")
        audio_track = self._first_track(mediainfo, "Audio")

        artist = _clean_tag_value(_mi_value(
            meta.get("artist"),
            general_track.get("Album_Performer"),
            general_track.get("Performer"),
            general_track.get("extra", {}).get("ARTISTS") if isinstance(general_track.get("extra"), dict) else "",
        ))
        album = _clean_tag_value(_mi_value(
            meta.get("album"),
            general_track.get("Album"),
            general_track.get("extra", {}).get("ALBUM") if isinstance(general_track.get("extra"), dict) else "",
        ))
        year = self._extract_year(general_track)
        if not artist or not album:
            folder_artist, folder_album = self._artist_album_from_path(path)
            artist = artist or folder_artist
            album = album or folder_album

        media_type = str(meta.get("manual_type") or self._music_type(general_track, audio_track)).upper().replace("-", "")
        bitrate = self._format_bitrate(audio_track)
        bit_depth = _mi_value(audio_track.get("BitDepth"))
        sampling_rate = self._format_sampling_rate(audio_track)

        source = meta.get("manual_source") or meta.get("source") or self._guess_source(meta["uuid"])
        service = meta.get("service") or self._guess_service(meta["uuid"])
        tag = meta.get("tag") or self._extract_tag(meta["uuid"])
        tracklist = await self._tracklist(music_files)
        user_cover, album_back, proof = self._find_images(image_files)
        source_size = sum(path.stat().st_size for path in music_files if path.exists())

        meta.update({
            "is_music": True,
            "is_disc": False,
            "isdir": path.is_dir(),
            "category": "MUSIC",
            "title": album or path.stem,
            "artist": artist or "Unknown Artist",
            "album": album or path.stem,
            "year": year or "",
            "overview": "",
            "genres": "",
            "tmdb_id": 0,
            "tmdb": 0,
            "imdb_id": 0,
            "imdb": "0",
            "tvdb_id": 0,
            "tvmaze_id": 0,
            "mal_id": 0,
            "resolution": "OTHER",
            "sd": 1,
            "type": media_type,
            "source": source,
            "service": service,
            "tag": tag,
            "audio": media_type,
            "video": os.fspath(first_file),
            "filelist": [os.fspath(p) for p in music_files],
            "music_files": [os.fspath(p) for p in music_files],
            "track_count": len(music_files),
            "source_size": source_size,
            "tracklist": tracklist,
            "log_files": [os.fspath(p) for p in log_files],
            "user_cover": os.fspath(user_cover) if user_cover else "",
            "album_back": os.fspath(album_back) if album_back else "",
            "proof": os.fspath(proof) if proof else "",
            "bitrate": bitrate,
            "bit_depth": bit_depth,
            "sampling_rate": sampling_rate,
            "is_lossy": media_type not in LOSSLESS_TYPES,
            "screens": 0,
            "image_list": [],
            "skip_imghost_upload": True,
            "mediainfo": mediainfo,
            "bdinfo": None,
            "discs": [],
            "scene": False,
            "valid_mi": True,
            "skip_trackers": False,
            "base_torrent_created": False,
            "we_checked_them_all": False,
        })

        await self._enrich(meta)
        await self._write_description(meta)
        return meta

    async def _parse_mediainfo(self, path: Path) -> dict[str, Any]:
        def parse() -> dict[str, Any]:
            media_info_json = MediaInfo.parse(os.fspath(path), output="JSON")
            return json.loads(media_info_json)

        return await asyncio.to_thread(parse)

    async def _write_mediainfo_files(self, meta: dict[str, Any], path: Path, mediainfo: dict[str, Any]) -> None:
        tmp_dir = Path(meta["base_dir"]) / "tmp" / meta["uuid"]
        tmp_dir.mkdir(parents=True, exist_ok=True)

        def parse_text() -> str:
            return str(MediaInfo.parse(os.fspath(path), output="STRING", full=False))

        media_info_text = await asyncio.to_thread(parse_text)
        filtered_text = "\n".join(
            line for line in media_info_text.splitlines()
            if not line.strip().startswith("ReportBy") and not line.strip().startswith("Report created by ")
        )
        clean_text = filtered_text.replace(os.fspath(path), path.name)

        async with aiofiles.open(tmp_dir / "MEDIAINFO.txt", "w", encoding="utf-8", newline="") as mi_file:
            await mi_file.write(clean_text)
        async with aiofiles.open(tmp_dir / "MEDIAINFO_CLEANPATH.txt", "w", encoding="utf-8", newline="") as mi_file:
            await mi_file.write(clean_text)
        async with aiofiles.open(tmp_dir / "MediaInfo.json", "w", encoding="utf-8") as json_file:
            await json_file.write(json.dumps(mediainfo, indent=4))

    def _first_track(self, mediainfo: dict[str, Any], track_type: str) -> dict[str, Any]:
        tracks = mediainfo.get("media", {}).get("track") or []
        for track in tracks:
            if isinstance(track, dict) and track.get("@type") == track_type:
                return track
        return {}

    def _extract_year(self, general_track: dict[str, Any]) -> str:
        extra = general_track.get("extra") if isinstance(general_track.get("extra"), dict) else {}
        raw = _mi_value(general_track.get("Recorded_Date"), general_track.get("Released_Date"), general_track.get("Year"), extra.get("YEAR"))
        match = re.search(r"\b(19|20)\d{2}\b", raw)
        return match.group(0) if match else ""

    def _artist_album_from_path(self, path: Path) -> tuple[str, str]:
        name = path.stem if path.is_file() else path.name
        match = re.match(r"^(.*?)\s+-\s+(.*)$", name)
        if match:
            return match.group(1).strip(), match.group(2).strip()
        return "", name.strip()

    def _music_type(self, general_track: dict[str, Any], audio_track: dict[str, Any]) -> str:
        fmt = _mi_value(audio_track.get("Format"), general_track.get("Format")).upper()
        commercial = _mi_value(audio_track.get("Format_Commercial_IfAny"), general_track.get("Format_Commercial_IfAny")).upper()
        combined = f"{commercial} {fmt}"
        if "FLAC" in combined:
            return "FLAC"
        if "ALAC" in combined:
            return "ALAC"
        if "MPEG AUDIO" in combined or fmt == "MPEG AUDIO":
            return "MP3"
        if "AAC" in combined:
            return "AAC"
        if "PCM" in combined:
            return "WAV"
        if "DSD" in combined or "DSF" in combined:
            return "DSF"
        return fmt.replace("-", "").replace(" ", "") or "AUDIO"

    def _format_bitrate(self, audio_track: dict[str, Any]) -> str:
        bitrate = _mi_value(audio_track.get("BitRate"), audio_track.get("OverallBitRate"))
        try:
            value = float(bitrate)
        except ValueError:
            return _mi_value(audio_track.get("BitRate_String"))
        if value >= 1_000_000:
            return f"{value / 1_000_000:.2f} Mb/s"
        if value >= 1_000:
            return f"{value / 1_000:.0f} kb/s"
        return ""

    def _format_sampling_rate(self, audio_track: dict[str, Any]) -> str:
        sampling_rate = _mi_value(audio_track.get("SamplingRate"))
        try:
            value = float(sampling_rate)
        except ValueError:
            return _mi_value(audio_track.get("SamplingRate_String"))
        if value >= 1000:
            khz = value / 1000
            return f"{khz:g} kHz"
        return f"{value:g} Hz"

    def _guess_source(self, name: str) -> str:
        lower = name.lower()
        if "vinyl" in lower:
            return "VINYL"
        if re.search(r"\bcd\b", lower):
            return "CD"
        if "web" in lower:
            return "WEB"
        return "WEB"

    def _guess_service(self, name: str) -> str:
        lower = name.lower()
        services = {
            "amazon": "AMZN", "amzn": "AMZN", "apple": "iTunes", "deezer": "Deezer",
            "qobuz": "Qobuz", "tidal": "TIDAL", "spotify": "Spotify",
        }
        for needle, label in services.items():
            if needle in lower:
                return label
        return ""

    def _extract_tag(self, name: str) -> str:
        match = re.search(r"-([A-Za-z0-9][A-Za-z0-9._-]{1,20})$", name)
        return f"-{match.group(1)}" if match else ""

    async def _tracklist(self, music_files: list[Path]) -> dict[str, dict[str, str]]:
        discs: dict[str, dict[str, str]] = {}
        for index, path in enumerate(music_files, start=1):
            mediainfo = await self._parse_mediainfo(path)
            general = self._first_track(mediainfo, "General")
            extra = general.get("extra") if isinstance(general.get("extra"), dict) else {}
            title = _clean_tag_value(_mi_value(general.get("Title"), extra.get("TITLE"), path.stem))
            position = _mi_value(general.get("Track_Position"), extra.get("TRACKNUMBER"))
            disc_position = _mi_value(general.get("Part_Position"), extra.get("DISCNUMBER"))
            track_no = self._track_number(position) or index
            disc_no = self._track_number(disc_position) or 1
            duration = _duration_string(general.get("Duration"))
            disc = f"Disc {disc_no}"
            discs.setdefault(disc, {})[f"{track_no:02d}. {title}"] = duration
        return discs

    def _track_number(self, value: str) -> Optional[int]:
        match = re.match(r"(\d+)", value or "")
        return int(match.group(1)) if match else None

    def _find_images(self, image_files: list[Path]) -> tuple[Optional[Path], Optional[Path], Optional[Path]]:
        cover = None
        back = None
        proof = None
        for image in image_files:
            name = image.stem.lower().replace(" ", "")
            if proof is None and "proof" in name:
                proof = image
            elif back is None and any(token in name for token in ("back", "rear")):
                back = image
            elif cover is None and any(token in name for token in ("front", "cover", "folder", "album")):
                cover = image
        if cover is None and image_files:
            cover = next((img for img in image_files if img != back and img != proof), image_files[0])
        return cover, back, proof

    async def _enrich(self, meta: dict[str, Any]) -> None:
        mb_info = await self._musicbrainz_lookup(meta)
        if mb_info:
            meta["mbid"] = mb_info.get("id", "")
            meta["mb_info"] = mb_info
            meta["artist"] = meta.get("artist") or mb_info.get("artist", "")
            meta["album"] = meta.get("album") or mb_info.get("title", "")
            meta["year"] = meta.get("year") or mb_info.get("date", "")[:4]
            meta["release_date"] = meta.get("release_date") or mb_info.get("date", "")
            meta["genres"] = ", ".join(mb_info.get("genres", []))
            meta["label"] = meta.get("label") or mb_info.get("label", "")
            meta["catalog_number"] = meta.get("catalog_number") or mb_info.get("catalog_number", "")
            meta["barcode"] = meta.get("barcode") or mb_info.get("barcode", "")
            if mb_info.get("cover"):
                meta["album_cover"] = mb_info["cover"]
            if mb_info.get("back_cover"):
                meta["album_back"] = meta.get("album_back") or mb_info["back_cover"]

        discogs_info = await self._discogs_lookup(meta)
        if discogs_info:
            meta["discogs_info"] = discogs_info
            meta["discogs_id"] = discogs_info.get("id", "")
            meta["release_date"] = meta.get("release_date") or discogs_info.get("released", "")
            meta["label"] = meta.get("label") or discogs_info.get("label", "")
            meta["catalog_number"] = meta.get("catalog_number") or discogs_info.get("catalog_number", "")
            meta["barcode"] = meta.get("barcode") or discogs_info.get("barcode", "")
            if not meta.get("genres") and discogs_info.get("genres"):
                meta["genres"] = ", ".join(discogs_info["genres"])
            if not meta.get("album_cover") and discogs_info.get("cover"):
                meta["album_cover"] = discogs_info["cover"]

        deezer_info = await self._deezer_lookup(meta)
        if deezer_info:
            meta["deezer_info"] = deezer_info
            if not meta.get("album_cover") and deezer_info.get("cover"):
                meta["album_cover"] = deezer_info["cover"]
            if not meta.get("genres") and deezer_info.get("genres"):
                meta["genres"] = ", ".join(deezer_info["genres"])

    async def _get_json(self, url: str, params: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
        headers = {"User-Agent": self.user_agent}
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, headers=headers) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else None
        except Exception as e:
            if self.config.get("DEFAULT", {}).get("debug", False):
                console.print(f"[yellow]Music metadata request failed: {url} - {e}[/yellow]")
            return None

    async def _musicbrainz_lookup(self, meta: dict[str, Any]) -> Optional[dict[str, Any]]:
        manual_mbid = meta.get("mbid") or meta.get("musicbrainz_id")
        if manual_mbid:
            release = await self._musicbrainz_release(str(manual_mbid))
            return release

        artist = meta.get("artist", "")
        album = meta.get("album", "")
        if not artist or not album:
            return None
        query = f'release:"{album}" AND artist:"{artist}"'
        search = await self._get_json("https://musicbrainz.org/ws/2/release", {
            "fmt": "json", "limit": "5", "query": query,
        })
        releases = search.get("releases", []) if search else []
        if not releases:
            return None
        best = releases[0]
        mbid = best.get("id")
        return await self._musicbrainz_release(mbid) if mbid else None

    async def _musicbrainz_release(self, mbid: str) -> Optional[dict[str, Any]]:
        release = await self._get_json(f"https://musicbrainz.org/ws/2/release/{mbid}", {
            "fmt": "json", "inc": "artist-credits+genres+labels+url-rels",
        })
        if not release:
            return None
        artist_credit = release.get("artist-credit") or []
        artist = ""
        if artist_credit and isinstance(artist_credit[0], dict):
            artist = _mi_value(artist_credit[0].get("name"), artist_credit[0].get("artist", {}).get("name"))
        relations = release.get("relations") or []
        discogs_url = ""
        for relation in relations:
            resource = relation.get("url", {}).get("resource") if isinstance(relation, dict) else ""
            if isinstance(resource, str) and "discogs.com" in resource:
                discogs_url = resource
                break
        label_info = release.get("label-info") or []
        labels: list[str] = []
        catalog_numbers: list[str] = []
        for label_entry in label_info:
            if not isinstance(label_entry, dict):
                continue
            label = label_entry.get("label", {})
            label_name = _mi_value(label.get("name") if isinstance(label, dict) else "")
            catalog_number = _mi_value(label_entry.get("catalog-number"))
            if label_name and label_name not in labels:
                labels.append(label_name)
            if catalog_number and catalog_number not in catalog_numbers:
                catalog_numbers.append(catalog_number)
        cover = await self._coverart_archive(mbid)
        return {
            "id": mbid,
            "artist": artist,
            "title": release.get("title", ""),
            "date": release.get("date", ""),
            "genres": [g.get("name", "") for g in release.get("genres", []) if isinstance(g, dict) and g.get("name")],
            "label": ", ".join(labels),
            "catalog_number": ", ".join(catalog_numbers),
            "barcode": release.get("barcode", ""),
            "discogs_url": discogs_url,
            **cover,
        }

    async def _coverart_archive(self, mbid: str) -> dict[str, str]:
        data = await self._get_json(f"https://coverartarchive.org/release/{mbid}")
        images = data.get("images", []) if data else []
        result: dict[str, str] = {}
        for image in images:
            if not isinstance(image, dict) or not image.get("approved", True):
                continue
            types = image.get("types", [])
            thumbnails = image.get("thumbnails", {})
            url = thumbnails.get("large") or image.get("image", "")
            if "Front" in types and not result.get("cover"):
                result["cover"] = url
            if "Back" in types and not result.get("back_cover"):
                result["back_cover"] = url
        return result

    async def _discogs_lookup(self, meta: dict[str, Any]) -> Optional[dict[str, Any]]:
        discogs_id = meta.get("discogs_id")
        discogs_url = meta.get("mb_info", {}).get("discogs_url", "")
        if not discogs_id and discogs_url:
            match = re.search(r"/release/(\d+)", discogs_url)
            if match:
                discogs_id = match.group(1)
        if discogs_id:
            release = await self._get_json(f"https://api.discogs.com/releases/{discogs_id}")
        else:
            artist = meta.get("artist", "")
            album = meta.get("album", "")
            if not artist or not album:
                return None
            search = await self._get_json("https://api.discogs.com/database/search", {
                "q": f"{artist} {album}", "type": "release", "per_page": "1",
            })
            results = search.get("results", []) if search else []
            if not results:
                return None
            release = await self._get_json(f"https://api.discogs.com/releases/{results[0].get('id')}")
        if not release:
            return None
        artists = release.get("artists", [])
        labels = release.get("labels", [])
        label_names: list[str] = []
        catalog_numbers: list[str] = []
        for label in labels:
            if not isinstance(label, dict):
                continue
            label_name = _strip_discogs_suffix(_mi_value(label.get("name")))
            catalog_number = _mi_value(label.get("catno"))
            if label_name and label_name not in label_names:
                label_names.append(label_name)
            if catalog_number and catalog_number not in catalog_numbers:
                catalog_numbers.append(catalog_number)
        images = release.get("images", [])
        cover = ""
        for image in images:
            if isinstance(image, dict) and image.get("type") == "primary":
                cover = image.get("resource_url", "")
                break
        return {
            "id": release.get("id", ""),
            "artist": _strip_discogs_suffix(_mi_value(artists[0].get("name") if artists else "")),
            "title": release.get("title", ""),
            "year": str(release.get("year", "")),
            "released": release.get("released", ""),
            "label": ", ".join(label_names),
            "catalog_number": ", ".join(catalog_numbers),
            "barcode": release.get("barcode", ""),
            "genres": release.get("genres", []) or [],
            "styles": release.get("styles", []) or [],
            "cover": cover,
            "url": release.get("uri", ""),
        }

    async def _deezer_lookup(self, meta: dict[str, Any]) -> Optional[dict[str, Any]]:
        if meta.get("deezer_id"):
            data = await self._get_json(f"https://api.deezer.com/album/{meta['deezer_id']}")
            if data:
                return {
                    "id": data.get("id", ""),
                    "title": data.get("title", ""),
                    "link": data.get("link", ""),
                    "cover": data.get("cover_big") or data.get("cover_xl") or data.get("cover", ""),
                    "genres": [g.get("name", "") for g in data.get("genres", {}).get("data", []) if isinstance(g, dict)],
                }
        artist = meta.get("artist", "")
        album = meta.get("album", "")
        if not artist or not album:
            return None
        search = await self._get_json("https://api.deezer.com/search/album", {
            "q": f'artist:"{artist}" album:"{album}"', "limit": "1",
        })
        results = search.get("data", []) if search else []
        if not results:
            return None
        album_id = results[0].get("id")
        data = await self._get_json(f"https://api.deezer.com/album/{album_id}") if album_id else None
        if not data:
            return None
        return {
            "id": data.get("id", ""),
            "title": data.get("title", ""),
            "link": data.get("link", ""),
            "cover": data.get("cover_big") or data.get("cover_xl") or data.get("cover", ""),
            "genres": [g.get("name", "") for g in data.get("genres", {}).get("data", []) if isinstance(g, dict)],
        }

    async def _write_description(self, meta: dict[str, Any]) -> None:
        description_path = Path(meta["base_dir"]) / "tmp" / meta["uuid"] / "DESCRIPTION.txt"
        description_path.parent.mkdir(parents=True, exist_ok=True)
        lines: list[str] = []

        cover = meta.get("album_cover")
        if isinstance(cover, str) and cover.startswith(("http://", "https://")):
            lines.extend([f"[center][img]{cover}[/img][/center]", ""])

        info_block = self._music_info_block(meta)
        if info_block:
            lines.extend(["[code][b]Release Info[/b]", *info_block, "[/code]", ""])

        tracklist = meta.get("tracklist") or {}
        if tracklist:
            lines.extend(["[code][b]Tracklist[/b]"])
            total_seconds = 0
            for disc, tracks in tracklist.items():
                if len(tracklist) > 1:
                    lines.append("")
                    lines.append(disc)
                for title, duration in tracks.items():
                    lines.append(f"{title} [{duration}]" if duration else title)
                    duration_seconds = _duration_seconds(duration)
                    if duration_seconds is not None:
                        total_seconds += duration_seconds
            if total_seconds:
                lines.append("")
                lines.append(f"Total runtime: {_duration_string(total_seconds)}")
            lines.extend(["[/code]", ""])

        log_files = meta.get("log_files") or []
        for log_file in log_files:
            if not str(log_file).lower().endswith(".log"):
                continue
            content = await self._read_text_file(log_file)
            if content:
                lines.extend([f"[spoiler=Log][code]{content}[/code][/spoiler]", ""])

        links = self._music_links(meta)
        if links:
            lines.extend(["[b]Links[/b]"])
            lines.extend(links)
            lines.append("")

        async with aiofiles.open(description_path, "w", encoding="utf-8", newline="") as desc_file:
            await desc_file.write("\n".join(lines).strip() + "\n")

    def _music_info_block(self, meta: dict[str, Any]) -> list[str]:
        info = [
            ("Artist", meta.get("artist")),
            ("Album", meta.get("album")),
            ("Year", meta.get("year")),
            ("Release date", meta.get("release_date")),
            ("Label", meta.get("label")),
            ("Catalog number", meta.get("catalog_number")),
            ("Barcode", meta.get("barcode")),
        ]
        return [f"{label}: {value}" for label, value in info if _mi_value(value)]

    async def _read_text_file(self, path: str) -> str:
        for encoding in ("utf-8", "latin1"):
            try:
                async with aiofiles.open(path, encoding=encoding) as text_file:
                    return await text_file.read()
            except UnicodeDecodeError:
                continue
            except OSError:
                return ""
        return ""

    def _music_links(self, meta: dict[str, Any]) -> list[str]:
        links: list[str] = []
        if meta.get("mbid"):
            links.append(self._music_link_line("MusicBrainz", f"https://musicbrainz.org/release/{meta['mbid']}"))
        if meta.get("discogs_id"):
            links.append(self._music_link_line("Discogs", f"https://www.discogs.com/release/{meta['discogs_id']}"))
        elif meta.get("discogs_info", {}).get("url"):
            links.append(self._music_link_line("Discogs", meta["discogs_info"]["url"]))
        if meta.get("deezer_info", {}).get("link"):
            links.append(self._music_link_line("Deezer", meta["deezer_info"]["link"]))
        return links

    def _music_link_line(self, service: str, url: str) -> str:
        icon = MUSIC_LINK_ICONS.get(service)
        prefix = f"[img=20]{icon}[/img] " if icon else ""
        return f"{prefix}{service}: {url}"
