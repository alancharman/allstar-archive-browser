# AllStar Archive Browser

A tiny Flask app to browse AllStarLink recordings and stream them via on-the-fly MP3 (ffmpeg).

## Quick start
```bash
sudo apt-get update && sudo apt-get install -y python3-venv ffmpeg
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 archive_browser.py

## Config
Configure: edit ARCHIVE_ROOT in archive_browser.py to match your node path
(e.g. /var/spool/asterisk/monitor/67146).
