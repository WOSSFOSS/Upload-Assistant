# Upload Assistant CLI Arguments

This document describes the command-line arguments parsed in `src/args.py`.

## Usage

```text
python3 upload.py [path...] [options]
```

- `path`: one or more file or directory paths. Quoting paths is recommended.
- `-h`: show short help with common options.
- `--help`: show the full argparse help.

At least one of `path`, `--queue`, `--site-upload`, `--search`, or `--webui` is required.

## Queue And Search Modes

- `--queue QUEUE_NAME`: process a named UA queue.
- `-lq`, `--limit-queue N`: limit how many queue items are processed.
- `--search`: run configured `SEARCH` profiles and create UA queue files.
- `--search-profile PROFILE`: run one configured `SEARCH` profile. This implies `--search`.
- `--refresh-scan`: ignore cached filesystem scan checkpoints for this run and write fresh checkpoints.
- `--rescan`: delete the current source/home scan checkpoints before scanning.
- `--prepare-search-cache`: update scan/TMDB/API cache files without tracker API searches or queue generation. This implies `--search`.
- `-sc`, `--site-check`: search sites for suitable uploads and create a log file, without uploading.
- `-su`, `--site-upload TRACKER`: process site search results and upload to a single tracker.
- `--unit3d`: parse a text output file from UNIT3D-Upload-Checker.

Search scan caches are split into source and home scan checkpoints. Source scans are queue-specific; home scans are tracker/profile-specific so stable home libraries can be reused across different source queues.

## Core Metadata Overrides

- `-c`, `--category {movie,tv,fanres,music,book}`: override category.
- `-t`, `--type TYPE`: override release type. Accepted values: `disc`, `remux`, `encode`, `webdl`, `web-dl`, `webrip`, `hdtv`, `dvdrip`, `flac`, `mp3`, `aac`, `alac`, `wav`, `epub`, `pdf`, `mobi`, `cbz`, `cbr`, `m4b`.
- `--source SOURCE`: override source. Accepted values: `Blu-ray`, `BluRay`, `DVD`, `DVD5`, `DVD9`, `HDDVD`, `WEB`, `HDTV`, `UHDTV`, `LaserDisc`, `DCP`, `CD`, `VINYL`.
- `-res`, `--resolution RES`: override resolution. Accepted values: `2160p`, `1080p`, `1080i`, `720p`, `576p`, `576i`, `480p`, `480i`, `8640p`, `4320p`, `other`.
- `-year`, `--year YYYY`: override detected year.
- `-g`, `--tag TAG`: override group tag. UA stores it with a leading dash.
- `-serv`, `--service SERVICE`: override streaming service.
- `-dist`, `--distributor NAME`: override disc distributor.
- `-edition`, `--edition`, `--repack TEXT`: override edition/repack text.

## External IDs

- `-tmdb`, `--tmdb ID`: set TMDB ID. Supports `movie/12345` and `tv/12345`.
- `-imdb`, `--imdb ID`: set IMDb ID.
- `-mal`, `--mal ID`: set MyAnimeList ID.
- `-tvmaze`, `--tvmaze ID`: set TVMaze ID.
- `-tvdb`, `--tvdb ID`: set TVDB ID.

## Music Metadata

- `-art`, `--artist ARTIST`: override music artist.
- `-alb`, `--album ALBUM`: override music album.
- `-mbid`, `--mbid ID`: set MusicBrainz release ID.
- `-discogs`, `--discogs ID`: set Discogs release ID.
- `-deezer`, `--deezer ID`: set Deezer album ID.

## Book And Audiobook Metadata

- `-btitle`, `--book-title`, `--book_title TITLE`: override book/audiobook title.
- `-author`, `--author AUTHOR`: override book/audiobook author.
- `-narrator`, `--narrator NAME`: override audiobook narrator.
- `-isbn`, `--isbn`, `--book-isbn`, `--book_isbn ISBN`: override ISBN.
- `-blang`, `--book-language`, `--book_language LANG`: override book/audiobook language.
- `-pub`, `--publisher PUBLISHER`: override publisher.
- `--retail`: mark book/audiobook as retail.
- `--scan`: mark eBook as scanned.
- `--ocr`: mark eBook as OCR processed.
- `--comic`: mark book upload as a comic.
- `--magazine`: mark book upload as a magazine.
- `--abridged`: mark audiobook as abridged.
- `--unabridged`: mark audiobook as unabridged.
- `--series SERIES`: set book/audiobook series name.
- `--book-number`, `--book_number NUMBER`: set book/audiobook series number.

## TV And Title Shaping

- `-season`, `--season N`: override season.
- `-episode`, `--episode N`: override episode.
- `-met`, `--manual-episode-title TITLE`: override episode title. Passing the option without text sets an empty title.
- `-daily`, `--daily YYYY-MM-DD`: set air date for daily-style TV episodes.
- `--not-anime`: mark the release as not anime.
- `--no-season`: remove season from title.
- `--no-year`: remove year from title.
- `--no-aka`: remove AKA from title.
- `--no-dub`: remove dubbed marker from title.
- `--no-dual`: remove dual-audio marker from title.
- `--no-tag`: remove group tag from title.
- `--no-edition`: remove edition from title.
- `--dual-audio`: add dual-audio marker to title.

## Language And Track Flags

- `-ol`, `--original-language LANG`: set original audio language.
- `-oil`, `--only-if-languages LANG ...`: require at least one listed language.
- `-mc`, `--commentary`: indicate commentary tracks are included.
- `-sfxs`, `--sfx-subtitles`: indicate subtitles with visual effects/backgrounds are included.
- `-hc`, `--hardcoded-subs`: indicate hardcoded subtitles.

## Screenshots And Images

- `-s`, `--screens N`: number of screenshots. Default comes from config.
- `-mf`, `--manual_frames LIST`: comma-separated frame numbers to use for screenshots.
- `-comps`, `--comparison PATH`: use comparison images from a folder.
- `-comps_index`, `--comparison_index N`: choose which comparison index is the main image set.
- `-menus`, `--disc-menus PATH`: raw disc only; folder containing disc menu screenshots.
- `-ih`, `--imghost HOST`: choose image host. Accepted values: `imgbb`, `ptpimg`, `imgbox`, `pixhost`, `lensdump`, `ptscreens`, `onlyimage`, `dalexni`, `zipline`, `passtheimage`, `seedpool_cdn`, `utppm`.
- `-siu`, `--skip-imagehost-upload`: skip uploading images to an image host.

## Description Inputs

- `-pb`, `--desclink URL`: custom description link.
- `-df`, `--descfile PATH`: custom description file path.
- `-nfo`, `--nfo`: use `.nfo` in the directory for description.
- `-k`, `--keywords TEXT`: add comma-separated keywords.

## Existing Tracker/Torrent References

These options accept an ID or URL where supported and are used to pull metadata from existing tracker uploads.

- `-ptp`, `--ptp ID_OR_URL`: PTP torrent ID/permalink.
- `-blu`, `--blu ID_OR_URL`: BLU torrent ID/link.
- `-aither`, `--aither ID_OR_URL`: Aither torrent ID/link.
- `-lst`, `--lst ID_OR_URL`: LST torrent ID/link.
- `-oe`, `--oe ID_OR_URL`: OE torrent ID/link.
- `-hdb`, `--hdb ID_OR_URL`: HDB torrent ID/link.
- `-btn`, `--btn ID_OR_URL`: BTN torrent ID/link.
- `-bhd`, `--bhd ID_OR_URL`: BHD torrent ID/link.
- `-huno`, `--huno ID_OR_URL`: HUNO torrent ID/link.
- `-ulcx`, `--ulcx ID_OR_URL`: ULCX torrent ID/link.
- `-th`, `--torrenthash HASH`: reuse metadata from a torrent hash/comment where supported.

## Upload Selection, Dupe Handling, And Requests

- `-tk`, `--trackers LIST`: upload/search only these trackers. Comma-separated tracker acronyms are supported.
- `-rtk`, `--trackers-remove LIST`: remove these trackers from the configured default tracker list.
- `-tpc`, `--trackers-pass N`: number of trackers that must pass checks for upload processing to complete.
- `-req`, `--search_requests`: search for matching requests on supported trackers.
- `-sat`, `--skip_auto_torrent`: skip automated torrent client torrent searching.
- `-onlyID`, `--onlyID`: only grab metadata IDs from trackers, not description/image links.
- `-sdc`, `--skip-dupe-check`: ignore dupes and upload anyway.
- `-sda`, `--skip-dupe-asking`: do not prompt about dupes; treat found dupes as actual dupes.
- `-ddc`, `--double-dupe-check`: run another dupe check before uploading to trackers that passed earlier checks.
- `-dr`, `--draft`: send to drafts where supported.
- `-mq`, `--modq`: send to moderation queue where supported.
- `-fl`, `--freeleech N`: set freeleech percentage.
- `-excl`, `--exclusive VALUE`: set exclusive flag on supported trackers.

## Torrent Creation And Hashing

- `-mps`, `--max-piece-size {1,2,4,8,16,32,64,128}`: set max piece size in MiB.
- `-nh`, `--nohash`: do not hash the torrent.
- `-rh`, `--rehash`: force hashing.
- `-mkbrr`, `--mkbrr`: use `mkbrr` for torrent hashing.
- `-rt`, `--randomized N`: create additional torrents with random infohashes.
- `-entropy`, `--entropy N`: use entropy in created torrents.
- `--infohash HASH`: set V1 info hash.
- `-frc`, `--force-recheck`: qBittorrent only; force recheck after adding/finding torrent.

## Torrent Client Integration

- `-client`, `--client NAME`: use this torrent client instead of the default.
- `-client_cat`, `--client-category`, `--client_category CATEGORY`: add to the selected torrent client with this category/label.
- `-client_tag`, `--client-tag`, `--client_tag TAG`: add to the selected torrent client with this tag.
- `-client_link`, `--client-link-path`, `--client_link_path PATH`: override the linked-folder/link target path for this upload.
- `-qbt`, `--qbit-tag TAG`: add to qBittorrent with this tag.
- `-qbc`, `--qbit-cat CATEGORY`: add to qBittorrent with this category.
- `-rtl`, `--rtorrent-label LABEL`: add to rTorrent with this label.

## Release Flags

- `-a`, `--anon`: upload anonymously.
- `-ns`, `--no-seed`: do not add torrent to the client.
- `-st`, `--stream`: mark as stream optimized.
- `-webdv`, `--webdv`: mark as Dolby Vision layer converted with `dovi_tool`.
- `-pr`, `--personalrelease`: mark as personal release.
- `-e`, `--extras`: indicate extras are included.
- `-sort`, `--sorted-filelist`: use the largest video file for processing instead of the first.
- `-kf`, `--keep-folder`: keep the containing folder for a single-file input directory.
- `-knfo`, `--keep-nfo`: keep NFO files where supported.
- `-reg`, `--region REGION`: set disc region.

## Tracker-Specific Category Flags

- `--foreign`: set TIK foreign category.
- `--opera`: set TIK opera/musical category.
- `--asian`: set TIK Asian category.
- `-disctype`, `--disctype TYPE`: set TIK disc type.
- `--untouched`: mark as a completely untouched disc for TIK.
- `-manual_dvds`, `--manual_dvds VALUE`: override automatic DVD count/type text.
- `-ch`, `--channel ID_OR_TAG`: SPD only; set upload channel ID or tag without `@`.

## Cleanup

- `-dm`, `--delete-meta`: delete only `meta.json` from the tmp directory.
- `-dtmp`, `--delete-tmp`: delete the tmp directory for the working file/folder.
- `-cleanup`, `--cleanup`: clean up tmp directory.

## Debugging And UI

- `-debug`, `--debug`: debug mode; run without uploading.
- `-ffdebug`, `--ffdebug`: show ffmpeg output while taking screenshots.
- `-uptimer`, `--upload-timer`: print upload timing per tracker.
- `-vs`, `--vapoursynth`: use VapourSynth for screenshots.
- `-webui`, `--webui [HOST:PORT]`: start the web UI server only. Default is `127.0.0.1:5000`.

## Emby

- `-emby`, `--emby`: create an Emby-compliant NFO file and optionally symlink content.
- `-emby_cat`, `--emby_cat CATEGORY`: set expected Emby category, such as `movie` or `tv`.
- `-emby_debug`, `--emby_debug`: enable Emby-specific debug behavior.

## Hidden / Unattended

These exist in argparse but are suppressed from normal help output.

- `-ua`, `--unattended`: run unattended.
- `-uac`, `--unattended_confirm`: unattended mode with selected confirmations.
