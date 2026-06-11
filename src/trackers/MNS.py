import asyncio
import io
import os
from pathlib import Path
from typing import Any, cast

import aiofiles
import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

from src.console import console
from src.languages import languages_manager
from src.trackers.COMMON import COMMON
from src.trackers.UNIT3D import UNIT3D

Meta = dict[str, Any]
Config = dict[str, Any]


class MNS(UNIT3D):
    COVER_MAX_BYTES = 256 * 1024
    COVER_MAX_DIMENSION = 900

    def __init__(self, config: Config) -> None:
        super().__init__(config, tracker_name='MNS')
        self.config = config
        self.common = COMMON(config)
        self.tracker = 'MNS'
        self.base_url = 'https://midnightscene.cc'
        self.id_url = f'{self.base_url}/api/torrents/'
        self.upload_url = f'{self.base_url}/api/torrents/upload'
        self.search_url = f'{self.base_url}/api/torrents/filter'
        self.torrent_url = f'{self.base_url}/torrents/'
        self.banned_groups = [
            '4K4U', 'AROMA', 'aXXo', 'BRrip', 'CK4', 'CM8', 'core', 'CrEwSaDe', 'd3g',
            'DNL', 'EMBER', 'EVO', 'FaNGDiNG0', 'FGT', 'FooKaS', 'FRDS', 'FROZEN',
            'GalaxyRG', 'Grym', 'GrymLegacy', 'HD2DVD', 'HDTime', 'ION10', 'Judas',
            'LAMA', 'Leffe', 'LycanHD', 'MeGusta', 'MezRips', 'mHD', 'msd', 'mSD',
            'NeXus', 'NhaNc3', 'nHD', 'nikt0', 'nSD', 'OFT', 'OsC', 'PRODJi',
            'ProRes', 'PYC', 'QxR', 'RARBG', 'RCDiVX', 'RDN', 'SAMPA', 'SANTi',
            'Sicario', 'Silence', 'SM737', 'STUTTERSHIT', 'Tigole', 'TSP', 'TSPxL',
            'UTR', 'ViSION', 'WAF', 'Will1869', 'x0r', 'YIFY', 'YTS', 'ZMNT',
        ]

    async def get_additional_data(self, meta: Meta) -> dict[str, str]:
        return {
            'mod_queue_opt_in': await self.get_flag(meta, 'modq'),
        }

    async def get_category_id(
        self,
        meta: Meta,
        category: str = "",
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        category_map = {
            'MOVIE': '1',
            'TV': '2',
            'MUSIC': '3',
            'GAMES': '4',
            'HOBBY': '5',
        }
        if mapping_only:
            return category_map
        if reverse:
            return {v: k for k, v in category_map.items()}
        selected = str(category or meta.get('category', '')).upper()
        return {'category_id': category_map.get(selected, '0')}

    async def get_type_id(
        self,
        meta: Meta,
        type: str = "",
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        type_map = {
            'DISC': '1',
            'REMUX': '2',
            'ENCODE': '3',
            'WEBDL': '4',
            'WEBRIP': '5',
            'HDTV': '6',
            'MP3': '7',
            'FLAC': '8',
            'PC': '9',
            'PLAYSTATION': '10',
            'NINTENDO': '11',
            'XBOX': '12',
            'DOCUMENTARY': '13',
            'TTRPG': '14',
            '3DPRINT': '15',
            'OTHER': '16',
        }
        if mapping_only:
            return type_map
        if reverse:
            return {v: k for k, v in type_map.items()}
        selected = self._get_music_type(meta) if meta.get('is_music') and not type else str(type or meta.get('type', '')).upper()
        return {'type_id': type_map.get(selected, '0')}

    def _get_music_type(self, meta: Meta) -> str:
        type_map = {'MP3', 'FLAC'}
        for value in (meta.get('type'), meta.get('audio')):
            selected = str(value or '').upper()
            if selected in type_map:
                return selected

        music_files = meta.get('music_files') or meta.get('filelist') or []
        if isinstance(music_files, list):
            suffixes = {Path(str(path)).suffix.lower() for path in music_files}
            if '.flac' in suffixes:
                return 'FLAC'
            if '.mp3' in suffixes:
                return 'MP3'

        return str(meta.get('type') or '').upper()

    async def get_resolution_id(
        self,
        meta: Meta,
        resolution: str = "",
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        resolution_map = {
            '4320p': '1',
            '2160p': '2',
            '1080p': '3',
            '1080i': '4',
            '720p': '5',
        }
        if mapping_only:
            return resolution_map
        if reverse:
            return {v: k for k, v in resolution_map.items()}
        selected = resolution or meta['resolution']
        return {'resolution_id': resolution_map.get(selected, '10')}

    async def get_name(self, meta: Meta) -> dict[str, str]:
        mns_name = str(meta.get('name', ''))
        if meta.get('is_music'):
            return {'name': mns_name}

        resolution = str(meta.get('resolution', ''))

        if not meta.get('audio_languages'):
            await languages_manager.process_desc_language(meta, tracker=self.tracker)

        audio_languages_value = meta.get('audio_languages', [])
        audio_languages = cast(list[str], audio_languages_value) if isinstance(audio_languages_value, list) else []

        # Language is included only when there is no English audio. Full discs are exempt.
        if meta.get('is_disc') not in ["BDMV", "DVD"]:
            if audio_languages and not await languages_manager.has_english_language(audio_languages):
                foreign_lang = str(audio_languages[0]).upper()
                if foreign_lang not in mns_name.upper():
                    mns_name = mns_name.replace(f"{resolution}", f"{foreign_lang} {resolution}", 1)

        if not meta.get('tag'):
            mns_name += "-NOGROUP"

        return {'name': mns_name}

    async def get_tracker_specific_files(self, meta: Meta) -> dict[str, tuple[str, bytes, str]]:
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
        if await asyncio.to_thread(cover_path.exists):
            return await self._cover_file_payload(meta, cover_path)

        if cover.startswith(('http://', 'https://')):
            downloaded_cover = await self._download_cover(meta, cover)
            if downloaded_cover:
                return await self._cover_file_payload(meta, downloaded_cover)

        return {}

    async def _cover_file_payload(self, meta: Meta, cover_path: Path) -> dict[str, tuple[str, bytes, str]]:
        try:
            cover_bytes = await asyncio.to_thread(self._prepare_cover_bytes, cover_path)
        except (OSError, UnidentifiedImageError) as e:
            if meta.get('debug'):
                console.print(f"[yellow]MNS: Skipping invalid cover image: {e}[/yellow]")
            return {}

        if len(cover_bytes) > self.COVER_MAX_BYTES:
            if meta.get('debug'):
                console.print(
                    "[yellow]MNS: Skipping torrent-cover because it is still too large "
                    f"({len(cover_bytes) / 1024:.1f} KiB > {self.COVER_MAX_BYTES / 1024:.0f} KiB).[/yellow]"
                )
            return {}

        if meta.get('debug'):
            console.print(f"[cyan]MNS: Attaching torrent-cover ({len(cover_bytes) / 1024:.1f} KiB).[/cyan]")
        return {'torrent-cover': (f'{cover_path.stem}.jpg', cover_bytes, 'image/jpeg')}

    def _prepare_cover_bytes(self, cover_path: Path) -> bytes:
        with Image.open(cover_path) as image:
            image = ImageOps.exif_transpose(image)
            image.thumbnail((self.COVER_MAX_DIMENSION, self.COVER_MAX_DIMENSION), Image.Resampling.LANCZOS)

            if image.mode in ('RGBA', 'LA') or (image.mode == 'P' and 'transparency' in image.info):
                background = Image.new('RGB', image.size, (255, 255, 255))
                background.paste(image, mask=image.convert('RGBA').split()[-1])
                image = background
            elif image.mode != 'RGB':
                image = image.convert('RGB')

            for max_dimension in (self.COVER_MAX_DIMENSION, 700, 500):
                resized = image.copy()
                resized.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
                for quality in (85, 75, 65, 55):
                    output = io.BytesIO()
                    resized.save(output, format='JPEG', quality=quality, optimize=True, progressive=True)
                    cover_bytes = output.getvalue()
                    if len(cover_bytes) <= self.COVER_MAX_BYTES:
                        return cover_bytes

            output = io.BytesIO()
            image.save(output, format='JPEG', quality=50, optimize=True, progressive=True)
            return output.getvalue()

    async def _download_cover(self, meta: Meta, cover_url: str) -> Path | None:
        suffix = Path(cover_url.split('?', 1)[0]).suffix.lower()
        if suffix not in {'.jpg', '.jpeg', '.png', '.webp'}:
            suffix = '.jpg'
        tmp_dir = Path(meta['base_dir']) / 'tmp' / meta['uuid']
        await asyncio.to_thread(tmp_dir.mkdir, parents=True, exist_ok=True)
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
                console.print(f"[yellow]MNS: Failed to download cover: {e}[/yellow]")
            return None
