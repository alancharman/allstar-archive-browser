#!/usr/bin/env python3
import mimetypes
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from flask import (
    Flask,
    Response,
    abort,
    render_template_string,
    request,
    send_from_directory,
    stream_with_context,
    url_for,
)

# ====== CONFIG ======
# Set this to your archive folder
ARCHIVE_ROOT = Path("/var/spool/asterisk/monitor/67146").resolve()

# Bind + port
BIND_HOST = "0.0.0.0"
BIND_PORT = 5000

# Allowlist of audio extensions we'll show a player for (still OK to download others)
AUDIO_EXTS = {".wav", ".WAV", ".mp3", ".MP3", ".gsm", ".ulaw", ".alaw"}
PER_PAGE_OPTIONS = ("10", "20", "30", "all")
DEFAULT_PER_PAGE = "20"

# ====== APP ======
app = Flask(__name__)

TEMPLATE = r"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>AllStar Archive - {{ rel if rel else '/' }}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root { font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial; }
    body { margin: 2rem; }
    a { text-decoration: none; }
    .crumbs a { color: #0b57d0; }
    table { border-collapse: collapse; width: 100%; margin-top: 1rem; }
    th, td { border-bottom: 1px solid #e5e7eb; padding: .6rem .4rem; text-align: left; vertical-align: top; }
    th { font-weight: 600; }
    tr:hover { background: #f9fafb; }
    .muted { color: #6b7280; font-size: .9em; }
    .dir { font-weight: 600; }
    .audio { display: block; margin-top: .3rem; width: 100%; max-width: 520px; }
    .wrap { word-break: break-all; }
    .controls { display:flex; gap:.75rem; align-items:center; margin:.25rem 0 1rem 0; flex-wrap:wrap; }
    input[type="search"], input[type="date"] { padding:.4rem .6rem; border:1px solid #d1d5db; border-radius:.5rem; width: min(480px, 95%); }
    .pill { font-size:.8em; background:#eef2ff; color:#3730a3; padding:.15rem .5rem; border-radius:999px; }
    .btn { display:inline-block; padding:.25rem .6rem; border:1px solid #d1d5db; border-radius:.5rem; font-size:.85em; color:inherit; background:#fff; cursor:pointer; }
    .btn:hover { background:#f3f4f6; }
    .sel { width: 3.5rem; text-align:center; }
    .qso-tools { margin: 1rem 0; display:flex; gap:.75rem; align-items:center; flex-wrap:wrap; }
    .error { margin-top: 1rem; padding: .75rem 1rem; border: 1px solid #fecaca; background: #fef2f2; color: #991b1b; border-radius: .5rem; }
    .pager { display:flex; gap:.75rem; align-items:center; flex-wrap:wrap; margin: 1rem 0; }
    .pager form { display:inline-flex; gap:.5rem; align-items:center; }
    select { padding:.35rem .5rem; border:1px solid #d1d5db; border-radius:.5rem; }
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
      {% if date_filter %}<input type="hidden" name="date" value="{{ date_filter }}">{% endif %}
      <input type="hidden" name="per_page" value="{{ per_page }}">
      <input type="search" name="q" value="{{ q or '' }}" placeholder="Filter by filename..." />
    </form>
    <form method="get">
      <input type="hidden" name="sort" value="{{ sort }}">
      {% if q %}<input type="hidden" name="q" value="{{ q|e }}">{% endif %}
      <input type="hidden" name="per_page" value="{{ per_page }}">
      <input type="date" name="date" value="{{ date_filter or '' }}" onchange="this.form.submit()" />
    </form>
    <form method="get">
      <input type="hidden" name="sort" value="{{ sort }}">
      {% if q %}<input type="hidden" name="q" value="{{ q|e }}">{% endif %}
      {% if date_filter %}<input type="hidden" name="date" value="{{ date_filter }}">{% endif %}
      <label class="muted" for="per_page">Per page</label>
      <select id="per_page" name="per_page" onchange="this.form.submit()">
        {% for option in per_page_options %}
          <option value="{{ option }}" {% if per_page == option %}selected{% endif %}>{{ option|upper }}</option>
        {% endfor %}
      </select>
    </form>
    <div class="muted">Sorted by {{ 'newest' if sort=='time' else 'name' }} -
      <a href="?sort={{ 'name' if sort=='time' else 'time' }}{% if q %}&q={{ q|e }}{% endif %}{% if date_filter %}&date={{ date_filter }}{% endif %}&per_page={{ per_page }}{% if page > 1 %}&page={{ page }}{% endif %}">switch</a>
      {% if date_filter %}<span class="pill">Date: {{ date_filter }}</span>{% endif %}
    </div>
  </div>

  {% if parent_link %}
    <p><a href="{{ parent_link }}">Up one level</a></p>
  {% endif %}

  <form method="post" action="{{ url_for('qso_builder') }}">
    <input type="hidden" name="subpath" value="{{ rel if rel != '.' else '' }}">
    <input type="hidden" name="sort" value="{{ sort }}">
    <input type="hidden" name="q" value="{{ q or '' }}">
    <input type="hidden" name="date" value="{{ date_filter or '' }}">
    <input type="hidden" name="per_page" value="{{ per_page }}">
    <input type="hidden" name="page" value="{{ page }}">

    <div class="qso-tools">
      <button class="btn" type="submit" name="scope" value="selected">Build Combined Clip</button>
      <button class="btn" type="submit" name="scope" value="all_day">Build All Audio For This Day</button>
      <span class="muted">Combined clips always follow timestamp order and ignore non-audio files.</span>
    </div>

    {% if total_pages > 1 %}
      <div class="pager">
        {% if page > 1 %}
          <a class="btn" href="{{ url_for('browse', subpath=rel if rel != '.' else '', sort=sort, q=q or None, date=date_filter, per_page=per_page, page=page-1) }}">Previous</a>
        {% endif %}
        <span class="muted">Page {{ page }} of {{ total_pages }}{% if total_items %} ({{ total_items }} items){% endif %}</span>
        {% if page < total_pages %}
          <a class="btn" href="{{ url_for('browse', subpath=rel if rel != '.' else '', sort=sort, q=q or None, date=date_filter, per_page=per_page, page=page+1) }}">Next</a>
        {% endif %}
      </div>
    {% endif %}

    <table>
      <thead>
        <tr>
          <th class="sel">QSO</th>
          <th>Name</th>
          <th>Size</th>
          <th>Modified</th>
        </tr>
      </thead>
      <tbody>
        {% for item in items %}
          <tr>
            <td class="sel">
              {% if item.is_audio %}
                <input type="checkbox" name="files" value="{{ item.rel }}">
              {% endif %}
            </td>
            <td class="wrap">
              {% if item.is_dir %}
                <span class="dir">DIR <a href="{{ url_for('browse', subpath=item.rel) }}">{{ item.name }}</a></span>
              {% else %}
                <span>AUDIO <a href="{{ url_for('serve_file', subpath=item.rel) }}">{{ item.name }}</a></span>
                {% if item.is_audio %}
                  <div>
                    <audio class="audio" controls preload="none">
                      <source src="{{ url_for('stream_transcoded', subpath=item.rel) }}" type="audio/mpeg">
                      <source src="{{ url_for('serve_file', subpath=item.rel) }}" type="{{ item.mimetype or 'audio/wav' }}">
                      Your browser cannot play this file; try downloading instead.
                    </audio>
                    <div>
                      <a class="btn" href="{{ url_for('stream_transcoded', subpath=item.rel) }}" download="{{ item.name.rsplit('.',1)[0] }}.mp3">Download MP3</a>
                      <a class="btn" href="{{ url_for('download_file', subpath=item.rel) }}">Download Original</a>
                    </div>
                  </div>
                {% endif %}
              {% endif %}
            </td>
            <td>{{ item.size_human if not item.is_dir else '-' }}</td>
            <td class="muted" title="{{ item.time_iso }}">{{ item.time_human }}</td>
          </tr>
        {% endfor %}
      </tbody>
    </table>

    {% if total_pages > 1 %}
      <div class="pager">
        {% if page > 1 %}
          <a class="btn" href="{{ url_for('browse', subpath=rel if rel != '.' else '', sort=sort, q=q or None, date=date_filter, per_page=per_page, page=page-1) }}">Previous</a>
        {% endif %}
        <span class="muted">Page {{ page }} of {{ total_pages }}{% if total_items %} ({{ total_items }} items){% endif %}</span>
        {% if page < total_pages %}
          <a class="btn" href="{{ url_for('browse', subpath=rel if rel != '.' else '', sort=sort, q=q or None, date=date_filter, per_page=per_page, page=page+1) }}">Next</a>
        {% endif %}
      </div>
    {% endif %}

  </form>

  {% if error_message %}
    <div class="error">{{ error_message }}</div>
  {% endif %}

  {% if not items %}
    <p class="muted">No files here.</p>
  {% endif %}
</body>
</html>
"""

QSO_TEMPLATE = r"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>QSO Builder</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root { font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial; }
    body { margin: 2rem; max-width: 960px; }
    .muted { color: #6b7280; }
    .pill { font-size:.8em; background:#eef2ff; color:#3730a3; padding:.15rem .5rem; border-radius:999px; }
    .actions { display:flex; gap:.75rem; align-items:center; flex-wrap:wrap; margin: 1rem 0; }
    .btn { display:inline-block; padding:.4rem .7rem; border:1px solid #d1d5db; border-radius:.5rem; font-size:.9em; text-decoration:none; color:inherit; background:#fff; }
    .btn:hover { background:#f3f4f6; }
    audio { width:100%; max-width:720px; margin: 1rem 0; }
    ol { padding-left: 1.4rem; }
  </style>
</head>
<body>
  <h1>QSO Builder <span class="pill">{{ count }} clips</span></h1>
  <p class="muted">The combined clip follows the order from the page where you selected the items.</p>

  <div class="actions">
    <a class="btn" href="{{ stream_url }}">Play Combined Stream</a>
    <a class="btn" href="{{ download_url }}">Download Combined MP3</a>
    <a class="btn" href="{{ back_url }}">Back to Archive</a>
  </div>

  <audio controls preload="none" src="{{ stream_url }}">
    Your browser cannot play this combined clip; try the download link instead.
  </audio>

  <h2>Included Clips</h2>
  <ol>
    {% for item in items %}
      <li>{{ item }}</li>
    {% endfor %}
  </ol>
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
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def fmt_time(ts: float, *, dt: datetime | None = None) -> tuple[str, str]:
    if dt is None:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()
    return dt.strftime("%Y-%m-%d %H:%M:%S"), dt.isoformat()


def build_breadcrumbs(rel: str):
    parts = [p for p in Path(rel).parts if p]
    crumbs = [("Home", url_for("browse", subpath=""))]
    acc = Path()
    for p in parts:
        acc /= p
        crumbs.append((p, url_for("browse", subpath=str(acc))))
    return crumbs


def is_audio_path(path: Path) -> bool:
    ext = path.suffix
    mimetype = mimetypes.guess_type(path.name)[0]
    return (ext in AUDIO_EXTS) or bool(mimetype and mimetype.startswith("audio"))


def resolve_selected_audio(subpaths: list[str]) -> list[Path]:
    files: list[tuple[float, str, Path]] = []
    seen: set[Path] = set()
    for subpath in subpaths:
        rel = Path(subpath)
        full = within_root(ARCHIVE_ROOT / rel)
        if not full.exists() or not full.is_file():
            abort(404)
        if not is_audio_path(full):
            abort(400)
        if full in seen:
            continue
        seen.add(full)
        try:
            stat = full.stat()
        except FileNotFoundError:
            abort(404)
        files.append((stat.st_mtime, full.name.lower(), full))
    if not files:
        abort(400)
    files.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in files]


def parse_date_filter(date_raw: str, *, date_param_present: bool):
    date_filter = None
    if date_raw:
        try:
            date_filter = datetime.strptime(date_raw, "%Y-%m-%d").date()
        except ValueError:
            date_filter = None
    elif not date_param_present:
        date_filter = datetime.now().astimezone().date()
    return date_filter


def parse_page_number(page_raw: str | None) -> int:
    try:
        page = int(page_raw or "1")
    except ValueError:
        page = 1
    return max(page, 1)


def parse_per_page(per_page_raw: str | None) -> str:
    per_page = (per_page_raw or DEFAULT_PER_PAGE).lower()
    if per_page not in PER_PAGE_OPTIONS:
        return DEFAULT_PER_PAGE
    return per_page


def stream_ffmpeg_mp3(cmd: list[str], *, download_name: str | None = None) -> Response:
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
        )
    except FileNotFoundError:
        abort(500)

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

    headers = {
        "Content-Type": "audio/mpeg",
        "Cache-Control": "no-store",
    }
    if download_name:
        headers["Content-Disposition"] = f'attachment; filename="{download_name}"'
    return Response(stream_with_context(generate()), headers=headers)


def stream_transcoded_file(path: Path, *, download_name: str | None = None) -> Response:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(path),
        "-ac",
        "1",
        "-ar",
        "16000",
        "-f",
        "mp3",
        "-",
    ]
    return stream_ffmpeg_mp3(cmd, download_name=download_name)


def stream_combined_mp3(paths: list[Path], *, download_name: str | None = None) -> Response:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
    for path in paths:
        cmd.extend(["-i", str(path)])

    audio_inputs = "".join(f"[{idx}:a]" for idx in range(len(paths)))
    cmd.extend(
        [
            "-filter_complex",
            f"{audio_inputs}concat=n={len(paths)}:v=0:a=1[outa]",
            "-map",
            "[outa]",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "mp3",
            "-",
        ]
    )
    return stream_ffmpeg_mp3(cmd, download_name=download_name)


def collect_entries(base: Path, rel: Path, *, sort: str, q: str, date_filter):
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

                dt = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).astimezone()
                if date_filter and dt.date() != date_filter:
                    continue

                size = stat.st_size if not is_dir else 0
                t_human, t_iso = fmt_time(stat.st_mtime, dt=dt)
                mimetype = mimetypes.guess_type(p.name)[0]
                is_audio = is_audio_path(p)

                entries.append(
                    {
                        "name": de.name,
                        "rel": str((rel / de.name).as_posix()),
                        "is_dir": is_dir,
                        "size_human": fmt_size(size),
                        "time_human": t_human,
                        "time_iso": t_iso,
                        "is_audio": is_audio,
                        "mimetype": mimetype,
                        "mtime": stat.st_mtime,
                        "sort_key": (
                            0 if is_dir else 1,
                            -stat.st_mtime if sort == "time" else de.name.lower(),
                        ),
                    }
                )
    except PermissionError:
        abort(403)

    entries.sort(key=lambda x: x["sort_key"])
    return entries


def render_browse_page(subpath: str, *, sort: str | None = None, q: str | None = None, date_raw: str | None = None, date_param_present: bool | None = None, per_page_raw: str | None = None, page_raw: str | None = None, error_message: str | None = None):
    rel = Path(subpath)
    base = within_root(ARCHIVE_ROOT / rel)
    if not base.exists() or not base.is_dir():
        abort(404)

    sort = sort or request.args.get("sort", "time")
    q = q if q is not None else (request.args.get("q") or "").strip().lower()

    if date_param_present is None:
        date_param_present = "date" in request.args
    if date_raw is None:
        date_raw = (request.args.get("date") or "").strip() if date_param_present else ""
    date_filter = parse_date_filter(date_raw, date_param_present=date_param_present)
    date_filter_str = date_filter.isoformat() if date_filter else None
    per_page = parse_per_page(per_page_raw if per_page_raw is not None else request.args.get("per_page"))
    page = parse_page_number(page_raw if page_raw is not None else request.args.get("page"))
    entries = collect_entries(base, rel, sort=sort, q=q, date_filter=date_filter)
    total_items = len(entries)

    if per_page == "all":
        paged_entries = entries
        total_pages = 1 if total_items else 1
        page = 1
    else:
        per_page_num = int(per_page)
        total_pages = max((total_items + per_page_num - 1) // per_page_num, 1)
        page = min(page, total_pages)
        start = (page - 1) * per_page_num
        end = start + per_page_num
        paged_entries = entries[start:end]

    breadcrumbs = build_breadcrumbs(rel.as_posix())
    parent_link = None
    if rel.as_posix() not in ("", "."):
        parent_link = url_for("browse", subpath=str(rel.parent.as_posix()))

    return render_template_string(
        TEMPLATE,
        items=paged_entries,
        rel=rel.as_posix(),
        breadcrumbs=breadcrumbs,
        parent_link=parent_link,
        sort=sort,
        q=q,
        date_filter=date_filter_str,
        per_page=per_page,
        per_page_options=PER_PAGE_OPTIONS,
        page=page,
        total_pages=total_pages,
        total_items=total_items,
        error_message=error_message,
    )


@app.route("/", defaults={"subpath": ""})
@app.route("/browse/", defaults={"subpath": ""})
@app.route("/browse/<path:subpath>")
def browse(subpath: str):
    return render_browse_page(subpath)


@app.post("/qso")
def qso_builder():
    selected = request.form.getlist("files")
    scope = request.form.get("scope", "selected")
    subpath = request.form.get("subpath", "")
    sort = request.form.get("sort", "time")
    q = (request.form.get("q") or "").strip().lower()
    date_raw = (request.form.get("date") or "").strip()
    date_param_present = "date" in request.form
    per_page = parse_per_page(request.form.get("per_page"))
    page = parse_page_number(request.form.get("page"))

    if scope == "all_day":
        rel = Path(subpath)
        base = within_root(ARCHIVE_ROOT / rel)
        date_filter = parse_date_filter(date_raw, date_param_present=date_param_present)
        selected = [entry["rel"] for entry in collect_entries(base, rel, sort=sort, q=q, date_filter=date_filter) if entry["is_audio"]]

    if not selected:
        return render_browse_page(
            subpath,
            sort=sort,
            q=q,
            date_raw=date_raw,
            date_param_present=date_param_present,
            per_page_raw=per_page,
            page_raw=str(page),
            error_message="Select at least one audio clip to build a QSO.",
        )

    paths = resolve_selected_audio(selected)
    rel_paths = [str(path.relative_to(ARCHIVE_ROOT).as_posix()) for path in paths]
    first_parent = paths[0].parent
    try:
        back_rel = first_parent.relative_to(ARCHIVE_ROOT).as_posix()
    except ValueError:
        back_rel = ""

    return render_template_string(
        QSO_TEMPLATE,
        count=len(paths),
        items=[path.name for path in paths],
        stream_url=url_for("qso_stream", files=rel_paths),
        download_url=url_for("qso_download", files=rel_paths),
        back_url=url_for("browse", subpath=back_rel),
    )


@app.route("/file/<path:subpath>")
def serve_file(subpath: str):
    rel = Path(subpath)
    full = within_root(ARCHIVE_ROOT / rel)
    if not full.exists() or not full.is_file():
        abort(404)
    return send_from_directory(str(full.parent), full.name, as_attachment=False, conditional=True)


@app.route("/download/<path:subpath>")
def download_file(subpath: str):
    rel = Path(subpath)
    full = within_root(ARCHIVE_ROOT / rel)
    if not full.exists() or not full.is_file():
        abort(404)
    return send_from_directory(str(full.parent), full.name, as_attachment=True, conditional=True)


@app.route("/qso/stream")
def qso_stream():
    paths = resolve_selected_audio(request.args.getlist("files"))
    return stream_combined_mp3(paths)


@app.route("/qso/download")
def qso_download():
    paths = resolve_selected_audio(request.args.getlist("files"))
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    return stream_combined_mp3(paths, download_name=f"qso-{stamp}.mp3")


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
    return stream_transcoded_file(full)


if __name__ == "__main__":
    app.run(host=BIND_HOST, port=BIND_PORT, debug=False)
