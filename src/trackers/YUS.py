import asyncio
import io
import os
import re
from pathlib import Path
from typing import Any, Optional

import aiofiles
import cli_ui
import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

from src.console import console
from src.trackers.COMMON import COMMON
from src.trackers.UNIT3D import UNIT3D

Meta = dict[str, Any]
Config = dict[str, Any]


class YUS(UNIT3D):
    COVER_MAX_BYTES = 256 * 1024
    COVER_MAX_DIMENSION = 900

    def __init__(self, config: Config) -> None:
        super().__init__(config, tracker_name='YUS')
        self.config = config
        self.common = COMMON(config)
        self.tracker = 'YUS'
        self.base_url = 'https://yu-scene.net'
        self.id_url = f'{self.base_url}/api/torrents/'
        self.upload_url = f'{self.base_url}/api/torrents/upload'
        self.search_url = f'{self.base_url}/api/torrents/filter'
        self.torrent_url = f'{self.base_url}/torrents/'
        self.banned_groups = [
            'ADDICTION', 'B3LLUM', 'BANDOLEROS', 'BigEasy', 'CINEMAXIS', 'D3US', 'd3g', 'DUMMESCHWEDEN', 'FGT', 'GRANiTEN',
            'KiNGDOM', 'Lama', 'MeGusta', 'MezRips', 'mHD', 'mRS', 'msd', 'NeXus', 'NhaNc3', 'nHD',
            'NorTekst', 'NORViNE', 'PANDEMONiUM', 'PiTBULL', 'RAPiDCOWS', 'RARBG', 'Radarr', 'RCDiVX', 'RDN', 'ROCKETRACCOON',
            'SANTi', 'SHOWTiME', 'SOOSi', 'SUXWIC', 'TOXVIO', 'TWA', 'VXT', 'Will1869', 'x0r', 'XS',
            'YIFY', 'YOLAND', 'YTS', 'ZKBL', 'ZmN', 'ZMNT']
        pass

    async def get_additional_checks(self, meta: Meta) -> bool:
        should_continue = True

        genres = f"{meta.get('keywords', '')} {meta.get('combined_genres', '')}"
        adult_keywords = ['xxx', 'erotic', 'porn', 'adult', 'orgy', 'hentai', 'adult animation', 'softcore']
        if any(re.search(rf'(^|,\s*){re.escape(keyword)}(\s*,|$)', genres, re.IGNORECASE) for keyword in adult_keywords):
            if (not meta['unattended'] or (meta['unattended'] and meta.get('unattended_confirm', False))):
                console.print('[bold red]Porn/xxx is not allowed at YUS.')
                if cli_ui.ask_yes_no("Do you want to upload anyway?", default=False):
                    pass
                else:
                    return False
            else:
                return False

        return should_continue

    async def get_category_id(
        self,
        meta: Meta,
        category: Optional[str] = None,
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        category_id = {
            'MOVIE': '1',
            'TV': '2',
            'MUSIC': '8',
        }
        if mapping_only:
            return category_id
        elif reverse:
            return {v: k for k, v in category_id.items()}
        elif category is not None:
            return {'category_id': category_id.get(category, '0')}
        else:
            meta_category = meta.get('category', '')
            return {'category_id': category_id.get(meta_category, '0')}

    async def get_type_id(
        self,
        meta: Meta,
        type: Optional[str] = None,
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        type_id = {
            'DISC': '17',
            'REMUX': '2',
            'WEBDL': '4',
            'WEBRIP': '5',
            'HDTV': '6',
            'ENCODE': '3',
            'FLAC': '16',
            'MP3': '9',
        }
        if mapping_only:
            return type_id
        elif reverse:
            return {v: k for k, v in type_id.items()}
        elif type is not None:
            return {'type_id': type_id.get(type, '0')}
        else:
            meta_type = meta.get('type', '')
            resolved_id = type_id.get(meta_type, '0')
            return {'type_id': resolved_id}

    async def get_tracker_specific_files(self, meta: Meta) -> dict[str, tuple[str, bytes, str]]:
        if not meta.get('is_music'):
            return {}

        cover = meta.get('user_cover') or meta.get('album_cover')
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
                console.print(f"[yellow]YUS: Skipping invalid cover image: {e}[/yellow]")
            return {}

        if len(cover_bytes) > self.COVER_MAX_BYTES:
            if meta.get('debug'):
                console.print(
                    "[yellow]YUS: Skipping torrent-cover because it is still too large "
                    f"({len(cover_bytes) / 1024:.1f} KiB > {self.COVER_MAX_BYTES / 1024:.0f} KiB).[/yellow]"
                )
            return {}

        if meta.get('debug'):
            console.print(f"[cyan]YUS: Attaching torrent-cover ({len(cover_bytes) / 1024:.1f} KiB).[/cyan]")
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
                console.print(f"[yellow]YUS: Failed to download album cover: {e}[/yellow]")
            return None

    def _cover_mime_type(self, cover_path: Path) -> str:
        suffix = cover_path.suffix.lower()
        if suffix == '.png':
            return 'image/png'
        if suffix == '.webp':
            return 'image/webp'
        return 'image/jpeg'
