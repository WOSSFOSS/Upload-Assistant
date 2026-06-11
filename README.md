[![Create and publish a Docker image](https://github.com/fr1day13/Upload-Assistant/actions/workflows/docker-image.yml/badge.svg?branch=master)](https://github.com/fr1day13/Upload-Assistant/actions/workflows/docker-image.yml)
[![Python Code Analysis](https://github.com/fr1day13/Upload-Assistant/actions/workflows/python-code-analysis.yml/badge.svg?branch=master)](https://github.com/fr1day13/Upload-Assistant/actions/workflows/python-code-analysis.yml)
[![Python](https://img.shields.io/badge/python-3.9%20%7C%203.10%20%7C%203.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue)](https://www.python.org/downloads/)
[![Security: Bandit](https://img.shields.io/badge/security-bandit-yellow.svg)](https://github.com/PyCQA/bandit)
[![Security: Safety](https://img.shields.io/badge/security-safety-green.svg)](https://github.com/pyupio/safety)
[![Lint: Ruff](https://img.shields.io/badge/lint-ruff-4B8BBE.svg?logo=ruff&logoColor=white)](https://github.com/astral-sh/ruff)
[![Type Checker: Pyright](https://img.shields.io/badge/type%20checker-pyright-2D7FF9.svg?logo=python&logoColor=white)](https://github.com/microsoft/pyright)

Discord support https://discord.gg/QHHAZu7e2A

# Current Fork Highlights

This fork contains a number of newer workflow improvements on top of the original Upload Assistant behavior. The older README is kept below this section for the general project overview, setup notes, supported tracker list, and Docker information.

## Major Additions

- **Music upload support**
  - Music uploads can be prepared from tagged audio files.
  - MusicBrainz, Discogs, and Deezer metadata are used where available.
  - Generated descriptions include cover art, release info, tracklist, total duration, logs, and external links.
  - Supported UNIT3D trackers can receive cover files through tracker-specific API fields such as `torrent-cover`.

- **Book and audiobook support**
  - eBooks and audiobooks are detected and named separately from video/music uploads.
  - Google Books, Open Library, and optional Audible lookup support can enrich metadata.
  - eBook formats such as EPUB/AZW3/MOBI/PDF/CBZ/CBR can be included in naming and descriptions.
  - Audiobook naming supports narrator/series/abridged state, bitrate mode, ISBN/ASIN, and retail flags where available.

- **Search mode / upload queue discovery** (still experimental)
  - `python3 upload.py --search` scans configured source libraries, checks target trackers, and writes UA queue files for missing uploads.
  - Search profiles are configured in `data/config.py` under `SEARCH`.
  - Source and home library scans are cached separately:
    - source scan cache is queue-specific;
    - home scan cache is tracker/profile-specific.
  - `--prepare-search-cache` can be used for cron-friendly scan/TMDB cache preparation without tracker API searches.
  - `--refresh-scan` ignores scan cache for the current run.
  - `--rescan` deletes the current source/home scan checkpoints before scanning.
  - Search uses local prefiltering, torrent-file index checks where available, TMDB/IMDb lookup caching, tracker API checks, and UA's dupe filtering logic.

- **Torrent file search/index improvements**
  - UA can reuse existing `.torrent` files from qBittorrent `BT_backup`/torrent storage directories.
  - File-based torrent search can be cached in a local index to avoid repeatedly scanning huge client folders.
  - Matching torrent information now shows useful details such as piece size, piece count, and infohash.

## Upload Workflow Improvements

- The first metadata confirmation now shows more technical context:
  - name,
  - duration,
  - video bitrate,
  - audio tracks,
  - included subtitles,
  - file size.
- The first confirmation supports skipping immediately, useful when processing large queues.
- The metadata correction prompt allows returning/backing out instead of trapping the user in the edit loop.
- Before upload, UA can warn about risky rule combinations such as:
  - non-English audio without English subtitles,
  - 1080p-or-lower x265/HEVC encodes where tracker rules may forbid them.
- Supported trackers can be sent to mod queue/draft directly from the final upload confirmation, independent of the default config value.

## Dupe And Tracker Check Improvements

- Dupe output includes sizes and links where tracker APIs provide them.
- Dupe sizes that closely match the current upload can be highlighted.
- "Other uploads" are shown separately before the final upload confirmation so existing releases in the same group are visible even when they are not strict dupes.
- "Other uploads" are sorted by resolution and can highlight releases with the same resolution as the current upload.
- Image host validation/rehosting can be enforced per tracker where only certain hosts are allowed.
- Several tracker-specific duplicate checks have been tightened, including Gazelle/UNIT3D/BHD-style responses.

## CLI Documentation

The CLI reference has been refreshed and moved/kept here:

- [docs/cli-args.md](docs/cli-args.md)

It includes the newer search, music, book/audiobook, torrent-client, and moderation/draft arguments.

---

# Original README

# Upload Assistant

A simple tool to take the work out of uploading.

This project is a fork of the original work of L4G https://github.com/L4GSP1KE/Upload-Assistant
Immense thanks to him for establishing this project. Without his (and supporters) time and effort, this fork would not be a thing.
Many thanks to all who have contributed.

## What It Can Do:
  - Generates and Parses MediaInfo/BDInfo.
  - Generates and Uploads screenshots. HDR tonemapping if config.
  - Uses srrdb to fix scene names used at sites.
  - Can grab descriptions from PTP/BLU/Aither/LST/OE/BHD (with config option automatically on filename match, or using arg).
  - Can strip and use existing screenshots from descriptions to skip screenshot generation and uploading.
  - Obtains TMDb/IMDb/MAL/TVDB/TVMAZE identifiers.
  - Converts absolute to season episode numbering for Anime. Non-Anime support with TVDB credentials
  - Generates custom .torrents without useless top level folders/nfos.
  - Can re-use existing torrents instead of hashing new.
  - Can automagically search qBitTorrent version 5+ clients for matching existing torrent.
  - Includes support for [qui](https://github.com/autobrr/qui)
  - Generates proper name for your upload using Mediainfo/BDInfo and TMDb/IMDb conforming to site rules.
  - Checks for existing releases already on site.
  - Adds to your client with fast resume, seeding instantly (rtorrent/qbittorrent/deluge/watch folder).
  - ALL WITH MINIMAL INPUT!
  - Currently works with .mkv/.mp4/Blu-ray/DVD/HD-DVDs.

## Supported Sites:

|Name|Acronym|Name|Acronym|
|-|:-:|-|:-:|
|Zenith (!)|ZNTH|Aither|AITHER|
|Amigos-Share|ASC|Anthelion|ANT|
|AsianCinema|ACM|Aura4K|A4K|
|AvistaZ|AZ|Beyond-HD|BHD|
|BitHDTV|BHDTV|Blutopia|BLU|
|BrasilJapão-Share|BJS|BrasilTracker|BT|
|CapybaraBR|CBR|CinemaZ|CZ|
|Cinematik|TIK|DarkPeers|DP|
|DigitalCore|DC|DesiTorrents|DT|
|Emuwarez|EMUW|
|FearNoPeer|FNP|FileList|FL|
|Friki|FRIKI|FunFile|FF|
|GreatPosterWall|GPW|hawke-uno|HUNO|
|HDBits|HDB|HD-Space|HDS|
|HD-Torrents|HDT|HomieHelpDesk|HHD|
|ImmortalSeed|IS|InfinityHD|IHD|
|ItaTorrents|ITT|LastDigitalUnderground|LDU|
|Lat-Team|LT|Locadora|LCD|
|LST|LST|Luminarr|LUME|
|MoreThanTV|MTV|Nebulance|NBL|
|OldToonsWorld|OTW|OnlyEncodes+|OE|
|PassThePopcorn|PTP|PolishTorrent|PTT|
|Portugas|PT|PrivateHD|PHD|
|PTerClub|PTER|PTSKIT|PTS|
|Racing4Everyone|R4E|Rastastugan|RAS|
|ReelFLiX|RF|RetroFlix|RTF|
|Samaritano|SAM|seedpool|SP|
|ShareIsland|SHRI|SkipTheCommerials|STC|
|SpeedApp|SPD|Swarmazon|SN|
|The Leach Zone|TLZ|TheOldSchool|TOS|
|ToTheGlory|TTG|TorrentHR|THR|
|Torrenteros|TTR|TorrentLeech|TL|
|TVChaosUK|TVC|ULCX|ULCX|
|UTOPIA|UTP|YOiNKED|YOINK|
|YUSCENE|YUS|Alpharatio|AR|

## **Setup:**
   - **REQUIRES AT LEAST PYTHON 3.9 AND PIP3**
   - Also needs MediaInfo and ffmpeg installed on your system
      - On Windows systems, ffmpeg must be added to PATH (https://windowsloop.com/install-ffmpeg-windows-10/)
      - On linux systems, get it from your favorite package manager
      - If you have issues with ffmpeg, such as `max workers` errors, see this [wiki](https://github.com/fr1day13/Upload-Assistant)
    - Get the source:
      - Clone the repo to your system `git clone https://github.com/fr1day13/Upload-Assistant`
      - Fetch all of the release tags `git fetch --all --tags`
      - Check out the specifc release: see [releases](https://github.com/fr1day13/Upload-Assistant)
      - `git checkout tags/tagname` where `tagname` is the release name, eg `v5.0.0`
      - or download a zip of the source from the releases page and create/overwrite a local copy.
   - Install necessary python modules `pip3 install --user -U -r requirements.txt`
      - `sudo apt install pip` if needed
  - If you receive an error about externally managed environment, or otherwise wish to keep UA python separate:
      - Install virtual python environment `python3 -m venv venv`
      - Activate the virtual environment `source venv/bin/activate`
      - Then install the requirements `pip install -r requirements.txt`
   - From the installation directory, run `python3 config-generator.py`
   - OR
   - Copy `data/example-config.py` to `data/config.py`, leaving `data/example-config.py` intact.
   - NOTE: New users who use the webui will have the config file generated automatically.
   - Edit `config.py` to use your information (more detailed information in example config options: [docs/example-config.md](docs/example-config.md))
      - tmdb_api key can be obtained from https://www.themoviedb.org/settings/api
      - image host api keys can be obtained from their respective sites

  **Additional Resources are found in the [wiki](https://github.com/fr1day13/Upload-Assistant)**

   Feel free to contact me if you need help, I'm not that hard to find.

## **Updating:**
  - To update first navigate into the Upload-Assistant directory: `cd Upload-Assistant`
  - `git fetch --all --tags`
  - `git checkout tags/tagname`
  - Or download a fresh zip from the releases page and overwrite existing files
  - Run `python3 -m pip install --user -U -r requirements.txt` to ensure dependencies are up to date
  - Run `python3 config-generator.py` and select to grab new UA config options.

## **CLI Usage:**

  `python3 upload.py "/path/to/content" --args`

  Args are OPTIONAL and ALWAYS follow path, for a list of acceptable args, pass `--help`.
  Path works best in quotes.
  - CLI arguments: [docs/cli-args.md](docs/cli-args.md)

## **Docker Usage:**
  Visit our wonderful [docker usage](docs/docker-wiki-full.md)

  Also see this excellent video put together by a community member https://videos.badkitty.zone/ua

  Web UI setup (Docker GUI / Unraid): [docs/docker-gui-wiki-full.md](docs/docker-gui-wiki-full.md)
  Web UI docs: [docs/web-ui.md](docs/web-ui.md)

## **Attributions:**

Built with updated BDInfoCLI from https://github.com/rokibhasansagar/BDInfoCLI-ng

<p>
  <a href="https://github.com/autobrr/mkbrr"><img src="https://github.com/autobrr/mkbrr/blob/main/.github/assets/mkbrr-dark.png?raw=true" alt="mkbrr" height="40px;"></a>&nbsp;&nbsp;
  <a href="https://github.com/autobrr/qui"><img src="https://github.com/autobrr/qui/blob/develop/documentation/static/img/qui.png?raw=true" alt="qui" height="40px;"></a>&nbsp;&nbsp;
  <a href="https://ffmpeg.org/"><img src="https://i.postimg.cc/xdj3BS7S/FFmpeg-Logo-new-svg.png" alt="FFmpeg" height="40px;"></a>&nbsp;&nbsp;
  <a href="https://mediaarea.net/en/MediaInfo"><img src="https://i.postimg.cc/vTkjXmHh/Media-Info-Logo-svg.png" alt="Mediainfo" height="40px;"></a>&nbsp;&nbsp;
  <a href="https://www.themoviedb.org/"><img src="https://i.postimg.cc/1tpXHx3k/blue-square-2-d537fb228cf3ded904ef09b136fe3fec72548ebc1fea3fbbd1ad9e36364db38b.png" alt="TMDb" height="40px;"></a>&nbsp;&nbsp;
  <a href="https://www.imdb.com/"><img src="https://i.postimg.cc/CLVmvwr1/IMDb-Logo-Rectangle-Gold-CB443386186.png" alt="IMDb" height="40px;"></a>&nbsp;&nbsp;
  <a href="https://thetvdb.com/"><img src="https://i.postimg.cc/Hs1KKqsS/logo1.png" alt="TheTVDB" height="40px;"></a>&nbsp;&nbsp;
  <a href="https://www.tvmaze.com/"><img src="https://i.postimg.cc/2jdRzkJp/tvm-header-logo.png" alt="TVmaze" height="40px"></a>
</p>
