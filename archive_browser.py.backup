#!/usr/bin/env python3
import os
import mimetypes
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from flask import (
    Flask, request, send_from_directory, abort,
    render_template_string, url_for, Response, stream_with_context
)

# ====== CONFIG ======
# Set this to your archive folder (note: typically "asterisk", not "asterix")
ARCHIVE_ROOT = Path("/var/spool/asterisk/monitor/67146").resolve()

# Bind + port
BIND_HOST = "0.0.0.0"
BIND_PORT = 5000

# Allowlist of audio extensions we’ll show a player for (still OK to download others)
AUDIO_EXTS = {".wav", ".WAV", ".mp3", ".MP3", ".gsm", ".ulaw", ".alaw"}

# ====== APP ======
app = Flask(__name__)

TEMPLATE = r"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>AllStar Archive — {{ rel if rel else '/' }}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root { font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial; }
    body { margin: 2rem; }
    a { text-decoration: none; }
    .crumbs a { color: #0b57d0; }
    table { border-collapse: collapse; width: 100%; margin-top: 1rem; }
    th, td { border-bottom: 1px solid #e5e7eb; padding: .6rem .4rem; text-align: left; }
    th { font-weight: 600; }
    tr:hover { background: #f9fafb; }
    .muted { color: #6b7280; font-size: .9em; }
    .dir { font-weight: 600; }
    .audio { display: block; margin-top: .3rem; width: 100%; max-width: 520px; }
    .wrap { word-break: break-all; }
    .controls { display:flex; gap:.5rem; align-items:center; margin:.25rem 0 1rem 0;}
    input[type="search"]{ padding:.4rem .6rem; border:1px solid #d1d5db; border-radius:.5rem; width: min(480px, 95%);}
    .pill { font-size:.8em; background:#eef2ff; color:#3730a3; padding:.15rem .5rem; border-radius:999px;}
    .btn { display:inline-block; padding:.25rem .6rem; border:1px solid #d1d5db; border-radius:.5rem; font-size:.85em; }
    .btn:hover { background:#f3f4f6; }
  </style>
</head>
<body>
  <h1>AllStar Archive <span class="pill">read-only</span></h1>
  <div class="crumbs">
    {% for name, link in breadcrumbs %}
      <a href="{{ link }}">{{ name }}</a>{% if not loop.last %} / {% endif %}
    {% endfor %}
  </div>

  <div class="controls">
    <form method="get">
      <input type="hidden" name="sort" value="{{ sort }}">
      <input type="search" name="q" value="{{ q or '' }}" placeholder="Filter by filename…" />
    </form>
    <div class="muted">Sorted by {{ 'newest' if sort=='time' else 'name' }} —
      <a href="?sort={{ 'name' if sort=='time' else 'time' }}{% if q %}&q={{ q|e }}{% endif %}">switch</a>
    </div>
  </div>

  {% if parent_link %}
    <p><a href="{{ parent_link }}">⬅ Up one level</a></p>
  {% endif %}

  <table>
    <thead>
      <tr>
        <th>Name</th>
        <th>Size</th>
        <th>Modified</th>
      </tr>
    </thead>
    <tbody>
      {% for item in items %}
        <tr>
          <td class="wrap">
            {% if item.is_dir %}
              <span class="dir">📁 <a href="{{ url_for('browse', subpath=item.rel) }}">{{ item.name }}</a></span>
            {% else %}
              <span>🎵 <a href="{{ url_for('serve_file', subpath=item.rel) }}">{{ item.name }}</a></span>
              {% if item.is_audio %}
                <div>
                  <audio class="audio" controls preload="none">
                    <!-- Prefer transcoded MP3 (works for GSM/u-law/a-law), fall back to raw file -->
                    <source src="{{ url_for('stream_transcoded', subpath=item.rel) }}" type="audio/mpeg">
                    <source src="{{ url_for('serve_file', subpath=item.rel) }}" type="{{ item.mimetype or 'audio/wav' }}">
                    Your browser can’t play this file; try downloading instead.
                  </audio>
                  <div>
                    <a class="btn" href="{{ url_for('stream_transcoded', subpath=item.rel) }}" download="{{ item.name.rsplit('.',1)[0] }}.mp3">Download MP3</a>
                    <a class="btn" href="{{ url_for('download_file', subpath=item.rel) }}">Download Original</a>
                  </div>
                </div>
              {% endif %}
            {% endif %}
          </td>
          <td>{{ item.size_human if not item.is_dir else '—' }}</td>
          <td class="muted" title="{{ item.time_iso }}">{{ item.time_human }}</td>
        </tr>
      {% endfor %}
    </tbody>
  </table>

  {% if not items %}
    <p class="muted">No files here.</p>
  {% endif %}
</body>
</html>
"""

def within_root(path: Path) -> Path:
    """Ensure path stays inside ARCHIVE_ROOT and block traversal."""
    try:
        resolved = path.resolve()
    except FileNotFoundError:
        resolved = path
    if ARCHIVE_ROOT not in resolved.parents and resolved != ARCHIVE_ROOT:
        abort(404)
    return resolved

def fmt_size(n: int) -> str:
    for unit in ("B","KB","MB","GB","TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit=="B" else f"{n:.1f} {unit}"
        n /= 1024

def fmt_time(ts: float) -> tuple[str, str]:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()
    return dt.strftime("%Y-%m-%d %H:%M:%S"), dt.isoformat()

def build_breadcrumbs(rel: str):
    parts = [p for p in Path(rel).parts if p]
    crumbs = [("Home", url_for('browse', subpath=""))]
    acc = Path()
    for p in parts:
        acc /= p
        crumbs.append((p, url_for('browse', subpath=str(acc))))
    return crumbs

@app.route("/", defaults={"subpath": ""})
@app.route("/browse/", defaults={"subpath": ""})
@app.route("/browse/<path:subpath>")
def browse(subpath: str):
    rel = Path(subpath)
    base = within_root(ARCHIVE_ROOT / rel)
    if not base.exists() or not base.is_dir():
        abort(404)

    sort = request.args.get("sort", "time")  # 'time' or 'name'
    q = (request.args.get("q") or "").strip().lower()

    entries = []
    try:
        with os.scandir(base) as it:
            for de in it:
                if de.name.startswith("."):
                    continue
                if q and q not in de.name.lower():
                    continue
                p = Path(de.path)
                is_dir = de.is_dir(follow_symlinks=False)
                try:
                    stat = de.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue

                size = stat.st_size if not is_dir else 0
                t_human, t_iso = fmt_time(stat.st_mtime)
                ext = p.suffix
                mimetype = mimetypes.guess_type(p.name)[0]
                is_audio = (ext in AUDIO_EXTS) or (mimetype and mimetype.startswith("audio"))

                entries.append({
                    "name": de.name,
                    "rel": str((rel / de.name).as_posix()),
                    "is_dir": is_dir,
                    "size_human": fmt_size(size),
                    "time_human": t_human,
                    "time_iso": t_iso,
                    "is_audio": is_audio,
                    "mimetype": mimetype,
                    "sort_key": (0 if is_dir else 1,
                                 -stat.st_mtime if sort=="time" else de.name.lower())
                })
    except PermissionError:
        abort(403)

    entries.sort(key=lambda x: x["sort_key"])
    breadcrumbs = build_breadcrumbs(rel.as_posix())
    parent_link = None
    if rel.as_posix() not in ("", "."):
        parent_link = url_for('browse', subpath=str(rel.parent.as_posix()))

    return render_template_string(
        TEMPLATE,
        items=entries,
        rel=rel.as_posix(),
        breadcrumbs=breadcrumbs,
        parent_link=parent_link,
        sort=sort,
        q=q
    )

@app.route("/file/<path:subpath>")
def serve_file(subpath: str):
    rel = Path(subpath)
    full = within_root(ARCHIVE_ROOT / rel)
    if not full.exists() or not full.is_file():
        abort(404)
    directory = str(full.parent)
    filename = full.name
    return send_from_directory(directory, filename, as_attachment=False, conditional=True)

@app.route("/download/<path:subpath>")
def download_file(subpath: str):
    rel = Path(subpath)
    full = within_root(ARCHIVE_ROOT / rel)
    if not full.exists() or not full.is_file():
        abort(404)
    return send_from_directory(str(full.parent), full.name, as_attachment=True, conditional=True)

@app.route("/stream/<path:subpath>")
def stream_transcoded(subpath: str):
    """
    Stream any source audio as MP3 so browsers can play GSM/u-law/a-law etc.
    Requires: ffmpeg
    """
    rel = Path(subpath)
    full = within_root(ARCHIVE_ROOT / rel)
    if not full.exists() or not full.is_file():
        abort(404)

    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", str(full),
        "-ac", "1",        # mono
        "-ar", "16000",     # keep phoneband for small files; change to 16000/24000 if you prefer
        "-f", "mp3", "-"   # write MP3 to stdout
    ]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    except FileNotFoundError:
        abort(500)  # ffmpeg missing

    if not proc.stdout:
        abort(500)

    def generate():
        try:
            for chunk in iter(lambda: proc.stdout.read(64 * 1024), b""):
                yield chunk
        finally:
            try:
                proc.stdout.close()
            except Exception:
                pass
            proc.terminate()

    # No content-length because it’s a live transcode; allow seeking via browser’s internal buffer.
    headers = {
        "Content-Type": "audio/mpeg",
        "Cache-Control": "no-store"
    }
    return Response(stream_with_context(generate()), headers=headers)

if __name__ == "__main__":
    app.run(host=BIND_HOST, port=BIND_PORT, debug=False)
