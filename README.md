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
Runtime config is supplied by environment variables:

- `ARCHIVE_ROOT` defaults to `/var/spool/asterisk/monitor/67146`
- `BIND_HOST` defaults to `0.0.0.0`
- `BIND_PORT` defaults to `5000`

Examples:

```bash
ARCHIVE_ROOT=/var/spool/asterisk/monitor/67146 BIND_PORT=5000 python3 archive_browser.py
```

```powershell
$env:ARCHIVE_ROOT="D:\path\to\sample-archive\67146"
$env:BIND_PORT="5002"
python archive_browser.py
```

## Local Windows Testing
Put test recordings under `sample-archive/67146/`.

One-time setup:

```powershell
python -m venv .venv-local
.\.venv-local\Scripts\python -m pip install -r requirements.txt
```

Requirements:

- `ffmpeg` must be installed on Windows and available on `PATH`
- `.venv-local` is used for local testing because the repo's Linux-style `.venv` may not match this machine

Run locally:

```powershell
.\run-local.ps1
```

That starts the app with:

- `ARCHIVE_ROOT=sample-archive\67146`
- `BIND_PORT=5002`

Then open:

```text
http://127.0.0.1:5002/browse/
```

## Install as a systemd service
Prerequisite: you should already have a working AllStarLink node with the archive/recording function configured and verified (recordings landing under `/var/spool/asterisk/monitor/<your-node>`).

```bash
# Download the feature-branch installer
wget -O install-archweb-qso.sh https://raw.githubusercontent.com/alancharman/allstar-archive-browser/feature-qso-builder/install-archweb-qso.sh

# Run as root/sudo; script will prompt for your node number and restart archweb-qso.service
sudo bash install-archweb-qso.sh
```
