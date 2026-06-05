from typing import Any, Optional

from src.console import console
from src.trackers.COMMON import COMMON
from src.trackers.UNIT3D import UNIT3D

Meta = dict[str, Any]
Config = dict[str, Any]


class LST(UNIT3D):
    def __init__(self, config: Config) -> None:
        super().__init__(config, tracker_name='LST')
        self.config: Config = config
        self.common = COMMON(config)
        self.tracker = 'LST'
        self.base_url = 'https://lst.gg'
        self.banned_url = f'{self.base_url}/api/bannedReleaseGroups'
        self.id_url = f'{self.base_url}/api/torrents/'
        self.upload_url = f'{self.base_url}/api/torrents/upload'
        self.search_url = f'{self.base_url}/api/torrents/filter'
        self.torrent_url = f'{self.base_url}/torrents/'
        self.trumping_url = f'{self.base_url}/api/reports/torrents/'
        self.banned_groups = []
        pass

    async def get_additional_checks(self, meta: Meta) -> bool:
        should_continue = True
        if meta.get('is_music') or meta.get('is_book'):
            return should_continue

        if not meta['valid_mi_settings']:
            console.print(f"[bold red]No encoding settings in mediainfo, skipping {self.tracker} upload.[/bold red]")
            return False

        if meta['is_disc'] not in ["BDMV", "DVD"] and not await self.common.check_language_requirements(
            meta, self.tracker, languages_to_check=["english"], check_audio=True, check_subtitle=True, original_language=True
        ):
            return False

        return should_continue

    async def get_category_id(
        self,
        meta: Meta,
        category: Optional[str] = None,
        reverse: bool = False,
        mapping_only: bool = False
    ) -> dict[str, str]:
        category_id = {
            'MOVIE': '1',
            'TV': '2',
            'MUSIC': '3',
        }
        if mapping_only:
            return category_id
        elif reverse:
            return {v: k for k, v in category_id.items()}
        elif category is not None:
            return {'category_id': category_id.get(category, '0')}

        meta_category = meta.get('category', '')
        resolved_id = category_id.get(meta_category, '0')
        keywords = str(meta.get('keywords') or '')
        adult = bool(meta.get('adult', False))
        anime = bool(meta.get('anime', False))
        keyword_list = [keyword.strip() for keyword in keywords.lower().split(',')]
        if (adult and anime) or 'hentai' in keyword_list or (adult and 'animation' in keyword_list):
            resolved_id = '8'
        return {'category_id': resolved_id}

    async def get_type_id(
        self,
        meta: Meta,
        type: Optional[str] = None,
        reverse: bool = False,
        mapping_only: bool = False
    ) -> dict[str, str]:
        _ = (reverse, mapping_only)
        type = str(meta.get('type', '')).upper()
        type_id = {
            'DISC': '1',
            'REMUX': '2',
            'WEBDL': '4',
            'WEBRIP': '5',
            'HDTV': '6',
            'ENCODE': '3',
            'DVDRIP': '3',
            'FLAC': '7',
        }.get(type, '0')
        return {'type_id': type_id}

    async def get_additional_data(self, meta: Meta) -> dict[str, Any]:
        data: dict[str, Any] = {
            'mod_queue_opt_in': await self.get_flag(meta, 'modq'),
            'draft_queue_opt_in': await self.get_flag(meta, 'draft'),
        }

        # Only add edition_id if we have a valid edition
        edition_id = await self.get_edition(meta)
        if edition_id is not None:
            data['edition_id'] = edition_id

        return data

    async def get_edition(self, meta: Meta) -> Optional[int]:
        edition_mapping = {
            'Alternative Cut': 12,
            'Collector\'s Edition': 1,
            'Director\'s Cut': 2,
            'Extended Cut': 3,
            'Extended Uncut': 4,
            'Extended Unrated': 5,
            'Limited Edition': 6,
            'Special Edition': 7,
            'Theatrical Cut': 8,
            'Uncut': 9,
            'Unrated': 10,
            'X Cut': 11,
            'Other': 0  # Default value for "Other"
        }
        edition = meta.get('edition', '')
        if edition in edition_mapping:
            return edition_mapping[edition]
        else:
            return None

    async def get_name(self, meta: Meta) -> dict[str, str]:
        lst_name = str(meta.get('name', ''))
        resolution = str(meta.get('resolution', ''))
        video_encode = str(meta.get('video_encode', ''))
        name_type = meta.get('type', "")

        if name_type == "DVDRIP":
            if meta.get('category') == "MOVIE":
                lst_name = lst_name.replace(f"{meta.get('source', '')}{meta.get('video_encode', '')}", f"{resolution}", 1)
                lst_name = lst_name.replace(str(meta.get('audio', '')), f"{meta.get('audio', '')}{video_encode}", 1)
            else:
                lst_name = lst_name.replace(str(meta.get('source', '')), f"{resolution}", 1)
                lst_name = lst_name.replace(str(meta.get('video_codec', '')), f"{meta.get('audio', '')} {meta.get('video_codec', '')}", 1)

        if meta.get('trump_reason') == 'exact_match':
            lst_name = lst_name + " - TRUMP"

        return {'name': lst_name}
