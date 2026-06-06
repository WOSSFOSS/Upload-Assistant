import os
import re
from pathlib import Path
from typing import Any

import aiofiles
import httpx

from src.trackers.COMMON import COMMON
from src.trackers.UNIT3D import UNIT3D


class ZNTH(UNIT3D):
    def __init__(self, config: dict[str, Any]):
        super().__init__(config, tracker_name='ZNTH')
        self.config = config
        self.common = COMMON(config)
        self.tracker = 'ZNTH'
        self.base_url = 'https://znth.cx'
        self.id_url = f'{self.base_url}/api/torrents/'
        self.upload_url = f'{self.base_url}/api/torrents/upload'
        self.requests_url = f'{self.base_url}/api/requests/filter'
        self.search_url = f'{self.base_url}/api/torrents/filter'
        self.torrent_url = f'{self.base_url}/torrents/'
        self.banned_url = f'{self.base_url}/api/bannedReleaseGroups'
        self.banned_groups: list[str] = []

    async def get_name(self, meta: dict[str, Any]) -> dict[str, str]:
        if meta.get('is_book'):
            return {'name': self._get_book_name(meta)}

        znth_name = meta['name']
        if meta['category'] == 'TV' and meta.get('episode_title', "") != "":
            znth_name = znth_name.replace(f"{meta['episode_title']} {meta['resolution']}", f"{meta['resolution']}", 1)
        return {'name': znth_name}

    def _get_book_name(self, meta: dict[str, Any]) -> str:
        author = self._clean_name_part(meta.get('author')) or 'Unknown Author'
        title = self._clean_name_part(meta.get('title')) or self._clean_name_part(meta.get('name')) or 'Unknown Title'
        year = self._clean_year(meta.get('year'))
        book_format = self._clean_format(meta.get('type'))
        tag = self._clean_tag(meta.get('tag'))

        if meta.get('is_audiobook'):
            isbn = self._clean_isbn(meta.get('isbn') or meta.get('audible_asin'))
            parts = [f"{author} - {title}", year, book_format]
            bitrate = self._book_bitrate(meta)
            if bitrate and book_format.upper() in {'MP3', 'AAC', 'OPUS', 'OGG', 'VORBIS', 'M4A', 'M4B'}:
                parts.append(bitrate)
            if isbn:
                parts.append(isbn)
            if meta.get('retail'):
                parts.append('Retail')
            name = ' '.join(part for part in parts if part)
            return self._append_group_tag(name, tag)

        isbn = self._clean_isbn(meta.get('isbn'))
        parts = [f"{author} - {title}", year]
        edition = self._clean_name_part(meta.get('edition'))
        if edition:
            parts.append(edition)
        parts.append(book_format)
        if isbn:
            parts.append(isbn)
        if meta.get('retail'):
            parts.append('Retail')
        if meta.get('scan'):
            parts.append('Scan')
        if meta.get('ocr'):
            parts.append('OCR')
        return ' '.join(part for part in parts if part)

    def _book_bitrate(self, meta: dict[str, Any]) -> str:
        bitrate = meta.get('audiobook_bitrate') or meta.get('bitrate')
        if bitrate in (None, ''):
            return ''
        text = str(bitrate)
        match = re.search(r'\d+(?:\.\d+)?', text)
        if not match:
            return ''
        value = float(match.group(0))
        if value > 1000:
            value = value / 1000
        return str(int(round(value)))

    def _append_group_tag(self, name: str, tag: str) -> str:
        if not tag:
            return name
        return f"{name}-{tag}"

    def _clean_name_part(self, value: Any) -> str:
        text = str(value or '').strip()
        text = re.sub(r'\s+', ' ', text)
        return text

    def _clean_year(self, value: Any) -> str:
        match = re.search(r'\b(18|19|20)\d{2}\b', str(value or ''))
        return match.group(0) if match else ''

    def _clean_format(self, value: Any) -> str:
        text = str(value or '').strip().upper().replace('VORBIS', 'OGG')
        return text or 'UNKNOWN'

    def _clean_isbn(self, value: Any) -> str:
        return re.sub(r'[-\s]', '', str(value or '')).upper()

    def _clean_tag(self, value: Any) -> str:
        tag = str(value or '').strip()
        return tag[1:] if tag.startswith('-') else tag

    async def get_category_id(
        self, meta: dict[str, Any], category: str = "", reverse: bool = False, mapping_only: bool = False
    ) -> dict[str, str]:
        category_id = {
            'MOVIE': '1',
            'TV': '2',
            'GAMES': '3',
            'MUSIC': '5',
            'BOOKS': '6',
            'AUDIOBOOKS': '7',
        }
        if mapping_only:
            return category_id
        elif reverse:
            return {v: k for k, v in category_id.items()}
        elif category:
            if category == 'BOOK':
                category = 'AUDIOBOOKS' if meta.get('is_audiobook') else 'BOOKS'
            return {'category_id': category_id.get(category, '9')}
        else:
            meta_category = meta.get('category', '')
            if meta_category == 'BOOK':
                meta_category = 'AUDIOBOOKS' if meta.get('is_audiobook') else 'BOOKS'
            return {'category_id': category_id.get(meta_category, '9')}

    async def get_type_id(
        self, meta: dict[str, Any], type: str = "", reverse: bool = False, mapping_only: bool = False
    ) -> dict[str, str]:
        type_id = {
            'DISC': '1',
            'REMUX': '2',
            'ENCODE': '3',
            'DVDRIP': '11',
            'WEBDL': '4',
            'WEBRIP': '5',
            'HDTV': '6',
            'FLAC': '7',
            'MP3': '8',
            'UHDTV': '9',
            'SDTV': '10',
            'BOOK': '8',
            'COMIC': '8',
            'MAGAZINE': '8',
            'UNABRIDGED': '8',
            'ABRIDGED': '8',
        }
        if mapping_only:
            return type_id
        elif reverse:
            return {v: k for k, v in type_id.items()}
        elif type:
            return {'type_id': type_id.get(type, '0')}
        else:
            meta_type = self._get_book_type(meta) if meta.get('is_book') else meta.get('type', '')
            resolved_id = type_id.get(meta_type, '0')
            return {'type_id': resolved_id}

    def _get_book_type(self, meta: dict[str, Any]) -> str:
        if meta.get('is_audiobook'):
            return 'ABRIDGED' if meta.get('abridged') else 'UNABRIDGED'
        if meta.get('is_magazine') or meta.get('magazine'):
            return 'MAGAZINE'
        if meta.get('is_comic') or meta.get('comic'):
            return 'COMIC'
        formats = meta.get('book_formats') or []
        if isinstance(formats, list) and any(str(fmt).upper() in {'CBR', 'CBZ'} for fmt in formats):
            return 'COMIC'
        meta_type = str(meta.get('type') or '').upper()
        if any(fmt in meta_type.split('+') for fmt in {'CBR', 'CBZ'}):
            return 'COMIC'
        return 'BOOK'

    async def get_additional_data(self, meta: dict[str, Any]) -> dict[str, str]:
        return {
            'mod_queue_opt_in': await self.get_flag(meta, 'modq'),
        }

    async def get_tracker_specific_files(self, meta: dict[str, Any]) -> dict[str, tuple[str, bytes, str]]:
        if not meta.get('is_music') and not meta.get('is_book'):
            return {}

        cover = (
            meta.get('user_cover')
            or meta.get('album_cover')
            or meta.get('book_cover')
            or meta.get('cover_file')
            or meta.get('poster')
        )
        if not cover or not isinstance(cover, str):
            return {}

        cover_path = Path(cover)
        if cover_path.exists():
            return await self._cover_file_payload(cover_path)

        if cover.startswith(('http://', 'https://')):
            downloaded_cover = await self._download_cover(meta, cover)
            if downloaded_cover:
                return await self._cover_file_payload(downloaded_cover)

        return {}

    async def _cover_file_payload(self, cover_path: Path) -> dict[str, tuple[str, bytes, str]]:
        mime = self._cover_mime_type(cover_path)
        async with aiofiles.open(cover_path, 'rb') as cover_file:
            cover_bytes = await cover_file.read()
        return {'torrent-cover': (cover_path.name, cover_bytes, mime)}

    async def _download_cover(self, meta: dict[str, Any], cover_url: str) -> Path | None:
        suffix = Path(cover_url.split('?', 1)[0]).suffix.lower()
        if suffix not in {'.jpg', '.jpeg', '.png', '.webp'}:
            suffix = '.jpg'
        tmp_dir = Path(meta['base_dir']) / 'tmp' / meta['uuid']
        tmp_dir.mkdir(parents=True, exist_ok=True)
        cover_path = tmp_dir / f'album_cover{suffix}'

        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                response = await client.get(cover_url)
                response.raise_for_status()
            async with aiofiles.open(cover_path, 'wb') as cover_file:
                await cover_file.write(response.content)
            meta['cover_file'] = os.fspath(cover_path)
            return cover_path
        except Exception as e:
            if meta.get('debug'):
                from src.console import console
                console.print(f"[yellow]ZNTH: Failed to download album cover: {e}[/yellow]")
            return None

    def _cover_mime_type(self, cover_path: Path) -> str:
        suffix = cover_path.suffix.lower()
        if suffix == '.png':
            return 'image/png'
        if suffix == '.webp':
            return 'image/webp'
        return 'image/jpeg'
