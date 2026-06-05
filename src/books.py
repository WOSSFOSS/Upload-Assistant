import asyncio
import contextlib
import html
import json
import os
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, Optional

import aiofiles
import httpx
import langcodes
from pymediainfo import MediaInfo

from src.console import console


EBOOK_EXTENSIONS = {".pdf", ".epub", ".mobi", ".azw3", ".lit", ".cbz", ".cbr"}
AUDIOBOOK_EXTENSIONS = {".mp3", ".m4b", ".flac", ".aac", ".m4a", ".ogg", ".opus", ".wav", ".wma"}
BOOK_EXTENSIONS = EBOOK_EXTENSIONS | AUDIOBOOK_EXTENSIONS
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
EBOOK_FORMAT_PRIORITY = [".epub", ".azw3", ".mobi", ".pdf", ".lit", ".cbz", ".cbr"]
AUDIOBOOK_FORMAT_PRIORITY = [".m4b", ".mp3", ".m4a", ".aac", ".flac", ".opus", ".ogg", ".wav", ".wma"]
BOOK_LINK_ICONS = {
    "Google Books": "https://www.google.com/s2/favicons?domain=books.google.com&sz=32",
    "Open Library": "https://www.google.com/s2/favicons?domain=openlibrary.org&sz=32",
    "MyAnonamouse": "https://www.google.com/s2/favicons?domain=myanonamouse.net&sz=32",
}


def is_book_path(path: str) -> bool:
    path_obj = Path(path)
    if path_obj.is_file():
        if path_obj.suffix.lower() in EBOOK_EXTENSIONS:
            return True
        return path_obj.suffix.lower() in AUDIOBOOK_EXTENSIONS and _looks_like_audiobook(path_obj.name)
    if not path_obj.is_dir():
        return False

    ebook_files = _collect_files(path_obj, EBOOK_EXTENSIONS)
    if ebook_files:
        return True

    video_extensions = {".mkv", ".mp4", ".ts", ".avi", ".wmv", ".mov", ".m2ts"}
    audiobook_files = _collect_files(path_obj, AUDIOBOOK_EXTENSIONS)
    video_files = _collect_files(path_obj, video_extensions)
    return not video_files and bool(audiobook_files) and _looks_like_audiobook(path_obj.name)


def _looks_like_audiobook(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", " ", value.lower())
    return any(token in normalized.split() for token in {"audiobook", "audible", "m4b"}) or "audio book" in normalized


def _collect_files(root: Path, extensions: set[str]) -> list[Path]:
    if root.is_file():
        return [root] if root.suffix.lower() in extensions else []
    return sorted(
        [p for p in root.rglob("*") if p.is_file() and not p.name.startswith("._") and p.suffix.lower() in extensions],
        key=lambda p: os.fspath(p).lower(),
    )


def _mi_value(*values: Any) -> str:
    for value in values:
        if value is None or isinstance(value, dict):
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _clean_text(value: str) -> str:
    value = html.unescape(value or "")
    if any(0x80 <= ord(char) <= 0x9F for char in value):
        fixed_chars: list[str] = []
        for char in value:
            codepoint = ord(char)
            if 0x80 <= codepoint <= 0x9F:
                with contextlib.suppress(Exception):
                    fixed_chars.append(bytes([codepoint]).decode("cp1252"))
                    continue
            fixed_chars.append(char)
        value = "".join(fixed_chars)
    value = re.sub(r"<[^>]+>", "", value)
    return re.sub(r"\s+", " ", value).strip()


def _clean_isbn(value: str) -> str:
    return re.sub(r"[-\s]", "", value or "").upper()


def _validate_isbn(value: str) -> str:
    cleaned = _clean_isbn(value)
    if len(cleaned) == 13 and cleaned.isdigit():
        total = sum(int(cleaned[i]) * (1 if i % 2 == 0 else 3) for i in range(13))
        return cleaned if total % 10 == 0 else ""
    if len(cleaned) == 10 and cleaned[:9].isdigit() and (cleaned[9].isdigit() or cleaned[9] == "X"):
        total = sum((10 if cleaned[i] == "X" else int(cleaned[i])) * (10 - i) for i in range(10))
        return cleaned if total % 11 == 0 else ""
    return ""


def _resolve_language(raw: str) -> tuple[str, str]:
    raw = raw.strip()
    if not raw:
        return "", ""
    try:
        lc = langcodes.get(raw.lower())
        full_name = lc.display_name("en") or raw.title()
        alpha3 = lc.to_alpha3() or ""
        if full_name and full_name.lower() != raw.lower():
            return full_name, alpha3
    except Exception:
        pass
    try:
        lc = langcodes.find(raw)
        return lc.display_name("en") or raw.title(), lc.to_alpha3() or ""
    except Exception:
        return raw.title(), ""


def _is_valid_language(full: str, iso: str) -> bool:
    if not full or not iso:
        return False
    return full.strip().lower() not in {"unknown", "unknown language", "undetermined", "none", "null"} and iso.strip().lower() not in {"", "und", "zxx"}


def _duration_seconds(value: Any) -> Optional[int]:
    try:
        duration = float(value)
    except (TypeError, ValueError):
        return None
    return int(round(duration / 1000 if duration >= 10000 else duration))


def _duration_string(seconds: int) -> str:
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    return f"{hours:02d}h {minutes:02d}m {secs:02d}s" if hours else f"{minutes:02d}m {secs:02d}s"


class BookProcessor:
    def __init__(self, config: dict[str, Any], base_dir: str) -> None:
        self.config = config
        self.base_dir = base_dir
        self.user_agent = (
            str(config.get("DEFAULT", {}).get("open_library_user_agent", "")).strip()
            or "Upload Assistant book metadata (https://github.com/fr1day13/Upload-Assistant)"
        )

    async def process(self, meta: dict[str, Any]) -> dict[str, Any]:
        path = Path(meta["path"])
        book_files = _collect_files(path, BOOK_EXTENSIONS)
        image_files = _collect_files(path, IMAGE_EXTENSIONS)
        if not book_files:
            raise RuntimeError("No supported book or audiobook files found")

        ebook_files = [file for file in book_files if file.suffix.lower() in EBOOK_EXTENSIONS]
        audiobook_files = [file for file in book_files if file.suffix.lower() in AUDIOBOOK_EXTENSIONS]
        is_audiobook = bool(audiobook_files) and not ebook_files
        primary_file = self._primary_file(ebook_files if ebook_files else audiobook_files)
        book_formats = self._book_formats(book_files)
        source_size = sum(p.stat().st_size for p in book_files if p.exists())
        mediainfo = await self._parse_mediainfo(primary_file)
        await self._write_mediainfo_files(meta, primary_file, mediainfo)

        local_metadata = self._folder_metadata(path)
        self._apply_metadata(meta, local_metadata, overwrite=False)
        local_metadata = await self._local_metadata(primary_file, mediainfo)
        self._apply_metadata(meta, local_metadata, overwrite=False)
        self._apply_cli_overrides(meta)

        if meta.get("isbn"):
            book_metadata = await self._google_books_lookup(str(meta["isbn"]), meta)
            if not book_metadata:
                book_metadata = await self._open_library_lookup(str(meta["isbn"]), meta)
            self._apply_metadata(meta, book_metadata or {}, overwrite=False)
            self._apply_cli_overrides(meta)

        title = _mi_value(meta.get("title"), meta.get("book_title"), primary_file.stem)
        author = _mi_value(meta.get("author"), meta.get("book_author"), "Unknown Author")
        year = _mi_value(meta.get("year"), self._year_from_filename(path.name))
        book_type = str(meta.get("manual_type") or "+".join(book_formats)).upper()
        language, language_iso = self._language_values(meta)
        is_comic = bool(meta.get("comic", False)) or any(file.suffix.lower() in {".cbz", ".cbr"} for file in book_files)
        is_magazine = bool(meta.get("magazine", False))
        cover = self._find_cover(image_files)
        if not cover and meta.get("poster"):
            cover = str(meta["poster"])

        if is_audiobook:
            duration_seconds, duration_display = await self._audiobook_duration(book_files)
            bitrate = await self._audiobook_bitrate(book_files)
        else:
            duration_seconds, duration_display, bitrate = 0, "", None

        meta.update({
            "is_book": True,
            "is_audiobook": is_audiobook,
            "is_comic": is_comic,
            "is_magazine": is_magazine,
            "is_disc": False,
            "isdir": path.is_dir(),
            "category": "BOOK",
            "title": title,
            "author": author,
            "year": year,
            "book_language": language,
            "book_language_iso": language_iso,
            "edition": _mi_value(meta.get("edition"), meta.get("manual_edition")),
            "book_series": _mi_value(meta.get("book_series")),
            "book_number": _mi_value(meta.get("book_number")),
            "overview": _mi_value(meta.get("overview")),
            "genres": _mi_value(meta.get("genres"), meta.get("keywords")),
            "tmdb_id": 0,
            "tmdb": 0,
            "imdb_id": 0,
            "imdb": "0",
            "tvdb_id": 0,
            "tvmaze_id": 0,
            "mal_id": 0,
            "resolution": "OTHER",
            "sd": 1,
            "type": book_type,
            "source": "AUDIBLE" if is_audiobook and "audible" in path.name.lower() else "WEB",
            "audio": book_type if is_audiobook else "",
            "tag": meta.get("tag") or "",
            "stream": 0,
            "keywords": meta.get("keywords") or "",
            "video": os.fspath(primary_file),
            "filelist": [os.fspath(p) for p in book_files],
            "book_files": [os.fspath(p) for p in book_files],
            "book_formats": book_formats,
            "source_size": source_size,
            "book_file_count": len(book_files),
            "book_cover": os.fspath(cover) if isinstance(cover, Path) else str(cover or ""),
            "audiobook_duration": duration_seconds,
            "audiobook_duration_formatted": duration_display,
            "audiobook_bitrate": bitrate,
            "retail": bool(meta.get("retail", False)),
            "scan": bool(meta.get("scan", False)),
            "ocr": bool(meta.get("ocr", False)),
            "abridged": bool(meta.get("abridged", False)),
            "unabridged": bool(meta.get("unabridged", False)),
            "screens": 0,
            "image_list": [],
            "skip_imghost_upload": True,
            "mediainfo": mediainfo,
            "bdinfo": None,
            "discs": [],
            "scene": False,
            "valid_mi": True,
            "valid_mi_settings": True,
            "skip_trackers": False,
            "base_torrent_created": False,
            "we_checked_them_all": False,
        })

        await self._write_description(meta)
        return meta

    def _apply_cli_overrides(self, meta: dict[str, Any]) -> None:
        mapping = {
            "book_title": "title",
            "book_author": "author",
            "book_publisher": "publisher",
            "book_isbn": "isbn",
        }
        for source_key, target_key in mapping.items():
            if meta.get(source_key):
                meta[target_key] = str(meta[source_key]).strip()
        if meta.get("book_language"):
            full, iso = _resolve_language(str(meta["book_language"]))
            meta["book_language"] = full or str(meta["book_language"]).strip()
            meta["book_language_iso"] = iso
        if meta.get("manual_year"):
            meta["year"] = str(meta["manual_year"])

    def _apply_metadata(self, meta: dict[str, Any], metadata: dict[str, Any], overwrite: bool = False) -> None:
        for key, value in metadata.items():
            if value in (None, "", []):
                continue
            if overwrite or not meta.get(key):
                meta[key] = value

    def _primary_file(self, files: list[Path]) -> Path:
        if not files:
            raise RuntimeError("No supported book or audiobook files found")
        priorities = AUDIOBOOK_FORMAT_PRIORITY if files[0].suffix.lower() in AUDIOBOOK_EXTENSIONS else EBOOK_FORMAT_PRIORITY
        for suffix in priorities:
            match = next((file for file in files if file.suffix.lower() == suffix), None)
            if match:
                return match
        return max(files, key=lambda p: p.stat().st_size if p.exists() else 0)

    def _book_formats(self, files: list[Path]) -> list[str]:
        suffixes = {file.suffix.lower() for file in files}
        priority = AUDIOBOOK_FORMAT_PRIORITY if suffixes <= AUDIOBOOK_EXTENSIONS else EBOOK_FORMAT_PRIORITY + AUDIOBOOK_FORMAT_PRIORITY
        ordered = [suffix.lstrip(".").upper() for suffix in priority if suffix in suffixes]
        remaining = sorted(suffix.lstrip(".").upper() for suffix in suffixes if suffix not in priority)
        return ordered + remaining

    async def _local_metadata(self, path: Path, mediainfo: dict[str, Any]) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        if path.suffix.lower() == ".epub":
            metadata.update(self._epub_metadata(path))
        elif path.suffix.lower() in {".cbz", ".cbr"}:
            metadata.update(self._comic_metadata(path))
        elif path.suffix.lower() == ".pdf":
            isbn = await asyncio.to_thread(self._pdf_isbn, path)
            if isbn:
                metadata["isbn"] = isbn

        metadata.update(self._mediainfo_metadata(mediainfo))
        if metadata.get("book_language_raw"):
            full, iso = _resolve_language(str(metadata.pop("book_language_raw")))
            if _is_valid_language(full, iso):
                metadata["book_language"] = full
                metadata["book_language_iso"] = iso
        return metadata

    def _folder_metadata(self, path: Path) -> dict[str, Any]:
        if not path.is_dir():
            return {}
        for opf_path in sorted(path.glob("*.opf"), key=lambda p: (p.name.lower() != "metadata.opf", p.name.lower())):
            try:
                metadata = self._opf_metadata(opf_path.read_bytes())
            except Exception as e:
                if self.config.get("DEFAULT", {}).get("debug", False):
                    console.print(f"[yellow]Warning: Error parsing OPF metadata from {opf_path.name}: {e}[/yellow]")
                continue
            if metadata:
                return metadata
        return {}

    def _epub_metadata(self, path: Path) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        if not zipfile.is_zipfile(path):
            return metadata
        try:
            with zipfile.ZipFile(path, "r") as archive:
                rootfile = ""
                with contextlib.suppress(Exception):
                    root = ET.fromstring(archive.read("META-INF/container.xml"))
                    for elem in root.iter():
                        if elem.tag.endswith("rootfile") and elem.attrib.get("full-path"):
                            rootfile = elem.attrib["full-path"]
                            break
                if not rootfile:
                    rootfile = next((name for name in archive.namelist() if name.lower().endswith(".opf")), "")
                if not rootfile:
                    return metadata
                metadata.update(self._opf_metadata(archive.read(rootfile)))
        except Exception as e:
            if self.config.get("DEFAULT", {}).get("debug", False):
                console.print(f"[yellow]Warning: Error parsing EPUB metadata: {e}[/yellow]")
        return metadata

    def _opf_metadata(self, content: bytes) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        root = ET.fromstring(content)
        creators: list[str] = []
        subjects: list[str] = []
        for elem in root.iter():
            tag = elem.tag.split("}")[-1].lower()
            value = _clean_text(elem.text or "")
            if not value:
                continue
            if tag == "title" and not metadata.get("title"):
                metadata["title"] = value
            elif tag == "creator":
                creators.append(value)
            elif tag == "language":
                metadata["book_language_raw"] = value
            elif tag == "date":
                year = self._extract_year(value)
                if year:
                    metadata["year"] = year
            elif tag == "identifier":
                isbn = _validate_isbn(value)
                if isbn:
                    metadata["isbn"] = isbn
                scheme = str(elem.attrib.get("{http://www.idpf.org/2007/opf}scheme") or elem.attrib.get("scheme") or "").upper()
                if scheme == "GOOGLE" and value:
                    metadata["google_books_link"] = f"https://books.google.com/books?id={value}"
                elif scheme in {"BARNESNOBLE", "BN"} and value:
                    metadata["barnes_noble_link"] = f"https://www.barnesandnoble.com/w/{value}"
            elif tag == "description":
                metadata["overview"] = value
            elif tag == "publisher":
                metadata["publisher"] = value
            elif tag == "subject":
                subjects.append(value.replace(".", " / "))
        if creators:
            metadata["author"] = ", ".join(dict.fromkeys(creators))
        if subjects:
            metadata["genres"] = ", ".join(dict.fromkeys(subjects))
        return metadata

    def _comic_metadata(self, path: Path) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        xml_data: Optional[bytes] = None
        try:
            if path.suffix.lower() == ".cbz" or zipfile.is_zipfile(path):
                with zipfile.ZipFile(path, "r") as archive:
                    xml_name = next((name for name in archive.namelist() if name.lower().endswith("comicinfo.xml")), "")
                    if xml_name:
                        xml_data = archive.read(xml_name)
            elif path.suffix.lower() == ".cbr":
                try:
                    from rarfile import RarFile
                except ImportError:
                    RarFile = None
                if RarFile:
                    with RarFile(path, "r") as archive:
                        xml_name = next((name for name in archive.namelist() if name.lower().endswith("comicinfo.xml")), "")
                        if xml_name:
                            xml_data = archive.read(xml_name)
        except Exception as e:
            if self.config.get("DEFAULT", {}).get("debug", False):
                console.print(f"[yellow]Warning: Error reading comic archive metadata: {e}[/yellow]")
        if not xml_data:
            return metadata
        try:
            root = ET.fromstring(xml_data)
            values: dict[str, str] = {}
            for elem in root.iter():
                tag = elem.tag.split("}")[-1]
                values[tag] = _clean_text(elem.text or "")
            metadata["title"] = values.get("Series") or values.get("Title") or ""
            metadata["author"] = values.get("Writer") or values.get("Penciller") or ""
            metadata["publisher"] = values.get("Publisher", "")
            metadata["overview"] = values.get("Summary", "")
            metadata["book_language_raw"] = values.get("LanguageISO", "")
            if values.get("Year"):
                metadata["year"] = self._extract_year(values["Year"])
            if values.get("Genre"):
                metadata["genres"] = ", ".join(part.strip() for part in values["Genre"].split(",") if part.strip())
        except Exception as e:
            if self.config.get("DEFAULT", {}).get("debug", False):
                console.print(f"[yellow]Warning: Error parsing ComicInfo.xml: {e}[/yellow]")
        return metadata

    def _pdf_isbn(self, path: Path) -> str:
        try:
            import fitz
        except ImportError:
            return ""
        try:
            with contextlib.suppress(Exception):
                fitz.TOOLS.mupdf_display_errors(False)
            with fitz.open(path) as doc:
                page_count = len(doc)
                pages = list(range(min(30, page_count)))
                for page in range(max(0, page_count - 30), page_count):
                    if page not in pages:
                        pages.append(page)
                for page_num in pages:
                    text = doc[page_num].get_text()
                    for candidate in re.findall(r"\b(?:ISBN(?:-1[03])?:?\s*)?((?:97[89][- ]?)?\d(?:[- ]?\d){8,11}[- ]?[\dX])\b", text, re.IGNORECASE):
                        isbn = _validate_isbn(candidate)
                        if isbn:
                            return isbn
        except Exception:
            return ""
        return ""

    def _mediainfo_metadata(self, mediainfo: dict[str, Any]) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        general = self._first_track(mediainfo, "General")
        extra = general.get("extra") if isinstance(general.get("extra"), dict) else {}
        metadata["title"] = _mi_value(general.get("Album"), general.get("Title"), general.get("Track"), extra.get("TITLE"))
        metadata["author"] = _mi_value(general.get("Album_Performer"), general.get("Performer"), extra.get("ARTIST"))
        metadata["narrator"] = _mi_value(general.get("Composer"), extra.get("NARRATOR"))
        metadata["publisher"] = _mi_value(general.get("Publisher"), extra.get("PUBLISHER"))
        metadata["isbn"] = _validate_isbn(_mi_value(general.get("ISBN"), extra.get("ISBN")))
        metadata["overview"] = _mi_value(general.get("Description"), general.get("Comment"), extra.get("DESCRIPTION"))
        metadata["genres"] = _mi_value(general.get("Genre"), extra.get("GENRE"))
        metadata["year"] = self._extract_year(_mi_value(general.get("Recorded_Date"), general.get("Released_Date"), general.get("Year"), extra.get("DATE")))
        metadata["book_language_raw"] = _mi_value(general.get("Language"), extra.get("LANGUAGE"))
        return {key: value for key, value in metadata.items() if value}

    async def _google_books_lookup(self, isbn: str, meta: dict[str, Any]) -> Optional[dict[str, Any]]:
        clean_isbn = _clean_isbn(isbn)
        if not clean_isbn:
            return None
        cache_dir = Path(meta["base_dir"]) / "tmp" / "google_books_cache"
        cache_file = cache_dir / f"{clean_isbn}.json"
        cache_dir.mkdir(parents=True, exist_ok=True)
        data: Optional[dict[str, Any]] = None
        if cache_file.exists():
            with contextlib.suppress(Exception):
                async with aiofiles.open(cache_file, encoding="utf-8") as f:
                    cached = json.loads(await f.read())
                    data = cached if isinstance(cached, dict) else None
        if data is None:
            params = {"q": f"isbn:{clean_isbn}"}
            api_key = str(self.config.get("DEFAULT", {}).get("google_books_api_key", "")).strip()
            if api_key:
                params["key"] = api_key
            try:
                async with httpx.AsyncClient(timeout=12.0, follow_redirects=True, headers={"User-Agent": self.user_agent}) as client:
                    response = await client.get("https://www.googleapis.com/books/v1/volumes", params=params)
                    response.raise_for_status()
                    data = response.json()
                async with aiofiles.open(cache_file, "w", encoding="utf-8") as f:
                    await f.write(json.dumps(data, indent=2))
            except Exception as e:
                if meta.get("debug"):
                    console.print(f"[yellow]Google Books lookup failed for ISBN {clean_isbn}: {e}[/yellow]")
                return None
        return self._parse_google_books(data, clean_isbn)

    async def _open_library_lookup(self, isbn: str, meta: dict[str, Any]) -> Optional[dict[str, Any]]:
        clean_isbn = _clean_isbn(isbn)
        if not clean_isbn:
            return None
        cache_dir = Path(meta["base_dir"]) / "tmp" / "open_library_cache"
        cache_file = cache_dir / f"{clean_isbn}.json"
        cache_dir.mkdir(parents=True, exist_ok=True)
        data: Optional[dict[str, Any]] = None
        if cache_file.exists():
            with contextlib.suppress(Exception):
                async with aiofiles.open(cache_file, encoding="utf-8") as f:
                    cached = json.loads(await f.read())
                    data = cached if isinstance(cached, dict) else None
        if data is None:
            try:
                async with httpx.AsyncClient(timeout=12.0, follow_redirects=True, headers={"User-Agent": self.user_agent}) as client:
                    response = await client.get(
                        "https://openlibrary.org/api/books",
                        params={"bibkeys": f"ISBN:{clean_isbn}", "jscmd": "data", "format": "json"},
                    )
                    response.raise_for_status()
                    api_books_data = response.json()
                    if isinstance(api_books_data, dict) and api_books_data.get(f"ISBN:{clean_isbn}"):
                        data = {"api_books": api_books_data}
                    else:
                        response = await client.get(
                            "https://openlibrary.org/search.json",
                            params={
                                "isbn": clean_isbn,
                                "fields": "key,title,author_name,publisher,first_publish_year,publish_year,language,isbn,cover_i,subject",
                                "limit": 1,
                            },
                        )
                        response.raise_for_status()
                        search_data = response.json()
                        data = {"search": search_data} if isinstance(search_data, dict) else {}
                async with aiofiles.open(cache_file, "w", encoding="utf-8") as f:
                    await f.write(json.dumps(data, indent=2))
            except Exception as e:
                if meta.get("debug"):
                    console.print(f"[yellow]Open Library lookup failed for ISBN {clean_isbn}: {e}[/yellow]")
                return None
        parsed = self._parse_open_library(data, clean_isbn)
        if not parsed and meta.get("debug"):
            console.print(f"[yellow]Open Library lookup found no match for ISBN {clean_isbn}[/yellow]")
        return parsed

    def _parse_open_library(self, data: dict[str, Any], isbn: str) -> Optional[dict[str, Any]]:
        if not isinstance(data, dict) or not data:
            return None
        api_books = data.get("api_books")
        if isinstance(api_books, dict):
            api_books_entry = api_books.get(f"ISBN:{isbn}")
            if isinstance(api_books_entry, dict):
                return self._parse_open_library_api_books(api_books_entry, isbn)
        search = data.get("search")
        if isinstance(search, dict):
            return self._parse_open_library_search(search, isbn)
        api_books_entry = data.get(f"ISBN:{isbn}")
        if isinstance(api_books_entry, dict):
            return self._parse_open_library_api_books(api_books_entry, isbn)
        metadata: dict[str, Any] = {"isbn": isbn}
        metadata["title"] = _mi_value(data.get("title"))
        if isinstance(data.get("publishers"), list):
            metadata["publisher"] = ", ".join(str(pub) for pub in data["publishers"] if pub)
        metadata["year"] = self._extract_year(_mi_value(data.get("publish_date")))
        description = data.get("description")
        if isinstance(description, dict):
            metadata["overview"] = _clean_text(_mi_value(description.get("value")))
        else:
            metadata["overview"] = _clean_text(_mi_value(description))
        subjects = data.get("subjects")
        if isinstance(subjects, list):
            metadata["genres"] = ", ".join(str(subject) for subject in subjects[:8] if subject)
        languages = data.get("languages")
        if isinstance(languages, list) and languages:
            key = _mi_value(languages[0].get("key") if isinstance(languages[0], dict) else "")
            if key:
                full, iso = _resolve_language(key.rsplit("/", 1)[-1])
                if _is_valid_language(full, iso):
                    metadata["book_language"] = full
                    metadata["book_language_iso"] = iso
        covers = data.get("covers")
        if isinstance(covers, list) and covers:
            metadata["poster"] = f"https://covers.openlibrary.org/b/id/{covers[0]}-L.jpg"
        metadata["open_library_link"] = f"https://openlibrary.org/isbn/{isbn}"
        return {key: value for key, value in metadata.items() if value}

    def _parse_open_library_api_books(self, data: dict[str, Any], isbn: str) -> Optional[dict[str, Any]]:
        metadata: dict[str, Any] = {"isbn": isbn}
        metadata["title"] = _mi_value(data.get("title"))
        authors = data.get("authors")
        if isinstance(authors, list):
            metadata["author"] = ", ".join(str(author.get("name")) for author in authors if isinstance(author, dict) and author.get("name"))
        publishers = data.get("publishers")
        if isinstance(publishers, list):
            metadata["publisher"] = ", ".join(str(pub.get("name")) for pub in publishers if isinstance(pub, dict) and pub.get("name"))
        metadata["year"] = self._extract_year(_mi_value(data.get("publish_date")))
        subjects = data.get("subjects")
        if isinstance(subjects, list):
            metadata["genres"] = ", ".join(str(subject.get("name")) for subject in subjects[:8] if isinstance(subject, dict) and subject.get("name"))
        cover = data.get("cover")
        if isinstance(cover, dict):
            metadata["poster"] = _mi_value(cover.get("large"), cover.get("medium"), cover.get("small"))
        metadata["open_library_link"] = _mi_value(data.get("url"), f"https://openlibrary.org/isbn/{isbn}")
        return {key: value for key, value in metadata.items() if value}

    def _parse_open_library_search(self, data: dict[str, Any], isbn: str) -> Optional[dict[str, Any]]:
        docs = data.get("docs")
        if not isinstance(docs, list) or not docs:
            return None
        doc = docs[0] if isinstance(docs[0], dict) else {}
        if not doc:
            return None
        metadata: dict[str, Any] = {"isbn": isbn}
        metadata["title"] = _mi_value(doc.get("title"))
        authors = doc.get("author_name")
        if isinstance(authors, list):
            metadata["author"] = ", ".join(str(author) for author in authors if author)
        publishers = doc.get("publisher")
        if isinstance(publishers, list):
            metadata["publisher"] = ", ".join(str(pub) for pub in publishers[:3] if pub)
        metadata["year"] = self._extract_year(_mi_value(doc.get("first_publish_year"), *(doc.get("publish_year") or [])))
        languages = doc.get("language")
        if isinstance(languages, list) and languages:
            full, iso = _resolve_language(str(languages[0]))
            if _is_valid_language(full, iso):
                metadata["book_language"] = full
                metadata["book_language_iso"] = iso
        subjects = doc.get("subject")
        if isinstance(subjects, list):
            metadata["genres"] = ", ".join(str(subject) for subject in subjects[:8] if subject)
        cover_id = doc.get("cover_i")
        if cover_id:
            metadata["poster"] = f"https://covers.openlibrary.org/b/id/{cover_id}-L.jpg"
        key = _mi_value(doc.get("key"))
        metadata["open_library_link"] = f"https://openlibrary.org{key}" if key.startswith("/") else f"https://openlibrary.org/isbn/{isbn}"
        return {key: value for key, value in metadata.items() if value}

    def _parse_google_books(self, data: dict[str, Any], isbn: str) -> Optional[dict[str, Any]]:
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list) or not items:
            return None
        volume = items[0].get("volumeInfo", {}) if isinstance(items[0], dict) else {}
        if not isinstance(volume, dict):
            return None
        metadata: dict[str, Any] = {"isbn": isbn}
        title = _mi_value(volume.get("title"))
        subtitle = _mi_value(volume.get("subtitle"))
        metadata["title"] = f"{title}: {subtitle}" if title and subtitle else title
        authors = volume.get("authors")
        if isinstance(authors, list):
            metadata["author"] = ", ".join(str(author) for author in authors if author)
        metadata["publisher"] = _mi_value(volume.get("publisher"))
        metadata["overview"] = _clean_text(_mi_value(volume.get("description")))
        metadata["year"] = self._extract_year(_mi_value(volume.get("publishedDate")))
        lang = _mi_value(volume.get("language"))
        if lang:
            full, iso = _resolve_language(lang)
            if _is_valid_language(full, iso):
                metadata["book_language"] = full
                metadata["book_language_iso"] = iso
        categories = volume.get("categories")
        if isinstance(categories, list):
            metadata["genres"] = ", ".join(str(cat) for cat in categories if cat)
        image_links = volume.get("imageLinks")
        if isinstance(image_links, dict):
            metadata["poster"] = image_links.get("thumbnail") or image_links.get("smallThumbnail") or ""
        elif items[0].get("id"):
            metadata["poster"] = f"https://books.google.com/books/content?id={items[0]['id']}&printsec=frontcover&img=1"
        metadata["google_books_link"] = volume.get("infoLink", "")
        return {key: value for key, value in metadata.items() if value}

    async def _parse_mediainfo(self, path: Path) -> dict[str, Any]:
        def parse() -> dict[str, Any]:
            media_info_json = MediaInfo.parse(os.fspath(path), output="JSON")
            return json.loads(media_info_json)

        try:
            return await asyncio.to_thread(parse)
        except Exception:
            return {}

    async def _write_mediainfo_files(self, meta: dict[str, Any], path: Path, mediainfo: dict[str, Any]) -> None:
        tmp_dir = Path(meta["base_dir"]) / "tmp" / meta["uuid"]
        tmp_dir.mkdir(parents=True, exist_ok=True)

        def parse_text() -> str:
            return str(MediaInfo.parse(os.fspath(path), output="STRING", full=False))

        try:
            media_info_text = await asyncio.to_thread(parse_text)
        except Exception:
            media_info_text = ""
        clean_text = "\n".join(
            line for line in media_info_text.splitlines()
            if not line.strip().startswith("ReportBy") and not line.strip().startswith("Report created by ")
        ).replace(os.fspath(path), path.name)

        async with aiofiles.open(tmp_dir / "MEDIAINFO.txt", "w", encoding="utf-8", newline="") as mi_file:
            await mi_file.write(clean_text)
        async with aiofiles.open(tmp_dir / "MEDIAINFO_CLEANPATH.txt", "w", encoding="utf-8", newline="") as mi_file:
            await mi_file.write(clean_text)
        async with aiofiles.open(tmp_dir / "MediaInfo.json", "w", encoding="utf-8") as json_file:
            await json_file.write(json.dumps(mediainfo, indent=4))

    async def _audiobook_duration(self, files: list[Path]) -> tuple[int, str]:
        audio_files = [file for file in files if file.suffix.lower() in AUDIOBOOK_EXTENSIONS]
        total_seconds = 0
        for file in audio_files:
            mediainfo = await self._parse_mediainfo(file)
            general = self._first_track(mediainfo, "General")
            duration = _duration_seconds(general.get("Duration"))
            if duration:
                total_seconds += duration
        return total_seconds, _duration_string(total_seconds) if total_seconds else ""

    async def _audiobook_bitrate(self, files: list[Path]) -> Optional[int]:
        audio_files = [file for file in files if file.suffix.lower() in AUDIOBOOK_EXTENSIONS][:5]
        bitrates: list[int] = []
        for file in audio_files:
            mediainfo = await self._parse_mediainfo(file)
            audio = self._first_track(mediainfo, "Audio")
            general = self._first_track(mediainfo, "General")
            bitrate = _mi_value(audio.get("BitRate"), general.get("OverallBitRate"))
            with contextlib.suppress(ValueError):
                value = int(float(bitrate))
                bitrates.append(int(value / 1000) if value >= 1000 else value)
        return int(sum(bitrates) / len(bitrates)) if bitrates else None

    def _first_track(self, mediainfo: dict[str, Any], track_type: str) -> dict[str, Any]:
        tracks = mediainfo.get("media", {}).get("track") if isinstance(mediainfo, dict) else []
        for track in tracks or []:
            if isinstance(track, dict) and track.get("@type") == track_type:
                return track
        return {}

    def _language_values(self, meta: dict[str, Any]) -> tuple[str, str]:
        full = _mi_value(meta.get("book_language"))
        iso = _mi_value(meta.get("book_language_iso"))
        if full and not iso:
            full, iso = _resolve_language(full)
        return full, iso

    def _find_cover(self, image_files: list[Path]) -> Optional[Path]:
        for image in image_files:
            name = image.stem.lower().replace(" ", "")
            if any(token in name for token in ("cover", "front", "folder", "poster")):
                return image
        return image_files[0] if image_files else None

    def _year_from_filename(self, name: str) -> str:
        return self._extract_year(name)

    def _extract_year(self, value: str) -> str:
        match = re.search(r"\b(19|20)\d{2}\b", value or "")
        return match.group(0) if match else ""

    async def _write_description(self, meta: dict[str, Any]) -> None:
        description_path = Path(meta["base_dir"]) / "tmp" / meta["uuid"] / "DESCRIPTION.txt"
        description_path.parent.mkdir(parents=True, exist_ok=True)
        lines: list[str] = []

        cover = _mi_value(meta.get("book_cover"), meta.get("poster"))
        if cover.startswith(("http://", "https://")):
            lines.extend([f"[center][img]{cover}[/img][/center]", ""])

        info = self._book_info_block(meta)
        if info:
            lines.extend(["[code][b]Book Info[/b]", *info, "[/code]", ""])

        overview = _clean_text(_mi_value(meta.get("overview")))
        if overview:
            lines.extend(["[b]Overview[/b]", overview, ""])

        links = self._book_links(meta)
        if links:
            lines.extend(["[b]Links[/b]", *links, ""])

        async with aiofiles.open(description_path, "w", encoding="utf-8", newline="") as desc_file:
            await desc_file.write("\n".join(lines).strip() + "\n")

    def _book_info_block(self, meta: dict[str, Any]) -> list[str]:
        info = [
            ("Title", meta.get("title")),
            ("Author", meta.get("author")),
            ("Narrator", meta.get("narrator")),
            ("Year", meta.get("year")),
            ("Edition", meta.get("edition")),
            ("Language", meta.get("book_language")),
            ("Publisher", meta.get("publisher")),
            ("ISBN", meta.get("isbn")),
            ("Series", meta.get("book_series")),
            ("Book Number", meta.get("book_number")),
            ("Format", "Audiobook" if meta.get("is_audiobook") else "eBook"),
            ("Release", self._book_release_flags(meta)),
            ("Files", meta.get("book_file_count")),
            ("Duration", meta.get("audiobook_duration_formatted")),
            ("Bitrate", f"{meta.get('audiobook_bitrate')} kb/s" if meta.get("audiobook_bitrate") else ""),
        ]
        return [f"{label}: {value}" for label, value in info if _mi_value(value)]

    def _book_release_flags(self, meta: dict[str, Any]) -> str:
        flags: list[str] = []
        if meta.get("retail"):
            flags.append("Retail")
        if meta.get("scan"):
            flags.append("Scan")
        if meta.get("ocr"):
            flags.append("OCR")
        if meta.get("abridged"):
            flags.append("Abridged")
        if meta.get("unabridged"):
            flags.append("Unabridged")
        return ", ".join(flags)

    def _book_links(self, meta: dict[str, Any]) -> list[str]:
        links: list[str] = []
        if meta.get("google_books_link"):
            links.append(self._book_link_line("Google Books", str(meta["google_books_link"])))
        if meta.get("open_library_link"):
            links.append(self._book_link_line("Open Library", str(meta["open_library_link"])))
        if meta.get("mam_link"):
            links.append(self._book_link_line("MyAnonamouse", str(meta["mam_link"])))
        return links

    def _book_link_line(self, service: str, url: str) -> str:
        icon = BOOK_LINK_ICONS.get(service)
        prefix = f"[img=20]{icon}[/img] " if icon else ""
        return f"{prefix}{service}: {url}"
