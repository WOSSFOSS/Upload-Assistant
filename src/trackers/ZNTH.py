# Upload Assistant (local custom tracker)
import os
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
        znth_name = meta['name']
        if meta['category'] == 'TV' and meta.get('episode_title', "") != "":
            znth_name = znth_name.replace(f"{meta['episode_title']} {meta['resolution']}", f"{meta['resolution']}", 1)
        return {'name': znth_name}

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
        }
        if mapping_only:
            return type_id
        elif reverse:
            return {v: k for k, v in type_id.items()}
        elif type:
            return {'type_id': type_id.get(type, '0')}
        else:
            meta_type = meta.get('type', '')
            resolved_id = type_id.get(meta_type, '0')
            return {'type_id': resolved_id}

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
