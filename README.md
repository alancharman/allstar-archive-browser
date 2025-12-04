# AllStar Archive Browser

A tiny Flask app to browse AllStarLink recordings and stream them via on-the-fly MP3 (ffmpeg).

## Quick start
```bash
sudo apt-get update && sudo apt-get install -y python3-venv ffmpeg
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 archive_browser.py
```

## Config
Configure: edit ARCHIVE_ROOT in archive_browser.py to match your node path
(e.g. /var/spool/asterisk/monitor/67146).

## Install as a systemd service
Prerequisite: you should already have a working AllStarLink node with the archive/recording function configured and verified (recordings landing under `/var/spool/asterisk/monitor/<your-node>`).

```bash
# Download the installer
wget -O install-archweb.sh https://raw.githubusercontent.com/alancharman/allstar-archive-browser/main/install-archweb.sh

# Run as root/sudo; script will prompt for your node number and restart archweb.service
sudo bash install-archweb.sh
```
