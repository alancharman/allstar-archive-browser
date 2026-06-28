#!/usr/bin/env python3
import mimetypes
import os
import re
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    render_template_string,
    request,
    send_from_directory,
    stream_with_context,
    url_for,
)

# ====== CONFIG ======
# Runtime overrides via environment keep local/dev/test installs from rewriting code.
ARCHIVE_ROOT = Path(os.environ.get("ARCHIVE_ROOT", "/var/spool/asterisk/monitor/67146")).resolve()

# Bind + port
BIND_HOST = os.environ.get("BIND_HOST", "0.0.0.0")
BIND_PORT = int(os.environ.get("BIND_PORT", "5000"))

# Allowlist of audio extensions we'll show a player for (still OK to download others)
AUDIO_EXTS = {".wav", ".WAV", ".mp3", ".MP3", ".gsm", ".ulaw", ".alaw"}
PER_PAGE_OPTIONS = ("10", "20", "30", "all")
DEFAULT_PER_PAGE = "20"
CACHE_DB_PATH = Path(
    os.environ.get(
        "ARCHIVE_CACHE_DB",
        str(Path.cwd() / "cache" / "archive_browser_cache.sqlite3"),
    )
)

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
    :root {
      color-scheme: light;
      font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
      --bg: #f3f6fb;
      --panel: rgba(255, 255, 255, 0.88);
      --panel-strong: #ffffff;
      --line: #d7dfec;
      --line-soft: #e7edf6;
      --text: #142033;
      --muted: #5f6f86;
      --accent: #0f62fe;
      --accent-soft: #e8f0ff;
      --accent-strong: #123f99;
      --danger-bg: #fff1f0;
      --danger-line: #f3bbb2;
      --danger-text: #a63321;
      --shadow: 0 18px 48px rgba(18, 40, 82, 0.10);
      --radius: 18px;
      --radius-sm: 12px;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background:
        radial-gradient(circle at top left, rgba(15, 98, 254, 0.14), transparent 26%),
        radial-gradient(circle at top right, rgba(34, 197, 94, 0.10), transparent 22%),
        linear-gradient(180deg, #f8fbff 0%, var(--bg) 100%);
      color: var(--text);
    }
    a { color: inherit; text-decoration: none; }
    .shell {
      width: min(1280px, calc(100% - 2rem));
      margin: 1.25rem auto 2rem;
    }
    .hero {
      background: linear-gradient(135deg, rgba(15, 98, 254, 0.92), rgba(22, 78, 190, 0.88));
      color: #fff;
      border-radius: 24px;
      padding: 1.4rem 1.5rem 1.2rem;
      box-shadow: var(--shadow);
      position: relative;
      overflow: hidden;
    }
    .hero::after {
      content: "";
      position: absolute;
      inset: auto -8% -42% auto;
      width: 260px;
      height: 260px;
      border-radius: 50%;
      background: rgba(255, 255, 255, 0.10);
      filter: blur(8px);
    }
    h1 {
      margin: 0;
      font-size: clamp(1.8rem, 3vw, 2.5rem);
      line-height: 1.05;
      letter-spacing: -0.03em;
    }
    .hero-copy {
      margin-top: .5rem;
      color: rgba(255, 255, 255, 0.86);
      max-width: 60rem;
    }
    .crumbs {
      margin-top: 1rem;
      display: flex;
      flex-wrap: wrap;
      gap: .4rem;
      align-items: center;
      color: rgba(255, 255, 255, 0.86);
      font-size: .95rem;
    }
    .crumbs a {
      padding: .3rem .55rem;
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.12);
      color: #fff;
    }
    .content {
      margin-top: 1rem;
      background: var(--panel);
      backdrop-filter: blur(18px);
      border: 1px solid rgba(255, 255, 255, 0.6);
      border-radius: 24px;
      box-shadow: var(--shadow);
      padding: 1rem;
    }
    .sticky-area {
      position: sticky;
      top: .75rem;
      z-index: 30;
      margin: -.15rem -.15rem 1rem;
      padding: .15rem;
    }
    .sticky-card {
      background: rgba(243, 247, 253, 0.92);
      backdrop-filter: blur(18px);
      border: 1px solid rgba(255, 255, 255, 0.7);
      border-radius: 20px;
      padding: .8rem .9rem;
      box-shadow: 0 14px 36px rgba(18, 40, 82, 0.12);
    }
    .controls {
      display: flex;
      gap: .85rem;
      align-items: center;
      flex-wrap: wrap;
      padding: .35rem 0 .75rem;
    }
    .controls form {
      display: inline-flex;
      gap: .55rem;
      align-items: center;
    }
    input[type="search"], input[type="date"], select {
      min-height: 42px;
      padding: .55rem .8rem;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: var(--panel-strong);
      color: var(--text);
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.65);
    }
    input[type="search"] { width: min(420px, 92vw); }
    input[type="search"]:focus, input[type="date"]:focus, select:focus {
      outline: 2px solid rgba(15, 98, 254, 0.18);
      border-color: rgba(15, 98, 254, 0.35);
    }
    .status {
      display: inline-flex;
      flex-wrap: wrap;
      gap: .55rem;
      align-items: center;
      color: var(--muted);
      font-size: .92rem;
    }
    .muted { color: var(--muted); font-size: .92rem; }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: .35rem;
      font-size: .78rem;
      font-weight: 600;
      background: var(--accent-soft);
      color: var(--accent-strong);
      padding: .28rem .7rem;
      border-radius: 999px;
      letter-spacing: .01em;
    }
    .up-link {
      display: inline-flex;
      align-items: center;
      gap: .45rem;
      margin: .35rem 0 0;
      padding: .55rem .8rem;
      border-radius: 999px;
      color: var(--accent-strong);
      background: #eef4ff;
      font-weight: 600;
    }
    .qso-tools, .pager {
      display: flex;
      gap: .75rem;
      align-items: center;
      flex-wrap: wrap;
      margin: 1rem 0;
    }
    .btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: .45rem;
      min-height: 42px;
      padding: .55rem .9rem;
      border: 1px solid var(--line);
      border-radius: 999px;
      font-size: .9rem;
      font-weight: 600;
      color: var(--text);
      background: linear-gradient(180deg, #ffffff 0%, #f5f8fd 100%);
      cursor: pointer;
      box-shadow: 0 6px 18px rgba(18, 40, 82, 0.06);
    }
    .btn:hover {
      border-color: rgba(15, 98, 254, 0.34);
      background: linear-gradient(180deg, #ffffff 0%, #eef4ff 100%);
    }
    .btn-primary {
      background: linear-gradient(135deg, #0f62fe 0%, #1654d1 100%);
      color: #fff;
      border-color: transparent;
      box-shadow: 0 14px 26px rgba(15, 98, 254, 0.22);
    }
    .btn-primary:hover {
      background: linear-gradient(135deg, #0c57e3 0%, #1249b6 100%);
      border-color: transparent;
    }
    .sel {
      width: 4.2rem;
      text-align: center;
    }
    .sel-cell {
      text-align: center;
    }
    .table-wrap {
      overflow-x: auto;
      border: 1px solid var(--line-soft);
      border-radius: var(--radius);
      background: rgba(255, 255, 255, 0.82);
    }
    table {
      border-collapse: separate;
      border-spacing: 0;
      width: 100%;
    }
    th, td {
      border-bottom: 1px solid var(--line-soft);
      padding: .9rem .8rem;
      text-align: left;
      vertical-align: top;
    }
    th {
      position: sticky;
      top: 0;
      z-index: 1;
      font-size: .78rem;
      letter-spacing: .08em;
      text-transform: uppercase;
      color: var(--muted);
      background: rgba(241, 245, 252, 0.96);
    }
    tbody tr:hover { background: rgba(15, 98, 254, 0.045); }
    tbody tr:last-child td { border-bottom: none; }
    .meta-label {
      display: none;
      font-size: .72rem;
      font-weight: 700;
      letter-spacing: .06em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: .2rem;
    }
    .dir, .file-label {
      display: inline-flex;
      align-items: center;
      gap: .55rem;
      font-weight: 700;
      color: var(--text);
      max-width: 100%;
    }
    .dir::before, .file-label::before {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 2rem;
      height: 2rem;
      border-radius: 12px;
      font-size: .92rem;
      font-weight: 700;
      flex: 0 0 auto;
    }
    .dir::before {
      content: "D";
      background: #edf4ff;
      color: #1f4aa8;
    }
    .file-label::before {
      content: "A";
      background: #eefbf2;
      color: #1f7a38;
    }
    .audio {
      display: block;
      margin-top: .6rem;
      width: 100%;
      max-width: 520px;
      border-radius: 999px;
      filter: saturate(.95);
    }
    .file-actions {
      display: flex;
      flex-wrap: wrap;
      gap: .55rem;
      margin-top: .55rem;
    }
    .wrap {
      overflow-wrap: anywhere;
      word-break: normal;
    }
    .empty {
      padding: 1rem;
      border-radius: var(--radius-sm);
      background: #f7f9fc;
    }
    .error {
      margin-top: 1rem;
      padding: .85rem 1rem;
      border: 1px solid var(--danger-line);
      background: var(--danger-bg);
      color: var(--danger-text);
      border-radius: var(--radius-sm);
      font-weight: 600;
    }
    .helper-card {
      padding: .9rem 1rem;
      border-radius: var(--radius);
      background: linear-gradient(180deg, #fbfcff 0%, #f3f7fe 100%);
      border: 1px solid var(--line-soft);
    }
    .summary-bar {
      display: inline-flex;
      align-items: center;
      gap: .55rem;
      flex-wrap: wrap;
      margin-left: auto;
      padding: .45rem .75rem;
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.88);
      border: 1px solid var(--line-soft);
      color: var(--muted);
      font-size: .88rem;
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.72);
    }
    .summary-bar strong { color: var(--text); }
    .load-meter {
      display: flex;
      align-items: center;
      gap: .65rem;
      margin-left: auto;
      min-width: min(320px, 100%);
    }
    .load-meter[hidden] { display: none; }
    .load-track {
      position: relative;
      flex: 1 1 auto;
      min-width: 140px;
      height: 10px;
      overflow: hidden;
      border-radius: 999px;
      background: rgba(15, 98, 254, 0.12);
      border: 1px solid rgba(15, 98, 254, 0.12);
    }
    .load-fill {
      position: absolute;
      inset: 0 auto 0 0;
      width: 0%;
      border-radius: inherit;
      background: linear-gradient(90deg, #0f62fe 0%, #36a2ff 100%);
      transition: width .22s ease;
    }
    .load-meter.is-indeterminate .load-fill {
      width: 38%;
      animation: load-sweep 1.15s ease-in-out infinite;
    }
    .load-text {
      flex: 0 0 auto;
      color: var(--muted);
      font-size: .84rem;
      white-space: nowrap;
    }
    @keyframes load-sweep {
      0% { transform: translateX(-110%); }
      100% { transform: translateX(290%); }
    }
    @media (max-width: 780px) {
      .shell { width: min(100% - 1rem, 100%); margin: .5rem auto 1rem; }
      .hero { padding: 1.1rem 1rem; border-radius: 20px; }
      .content { padding: .8rem; border-radius: 20px; }
      .btn, input[type="search"], input[type="date"], select { width: 100%; }
      .controls form { width: 100%; }
      .status { width: 100%; }
      .controls { padding-bottom: .35rem; }
      .status { gap: .4rem; }
      .qso-tools .btn { width: 100%; }
      .sticky-area {
        position: static;
        top: auto;
        margin: 0 0 .85rem;
        padding: 0;
      }
      .sticky-card {
        padding: .8rem;
        box-shadow: 0 10px 24px rgba(18, 40, 82, 0.10);
      }
      .helper-card { padding: .75rem .8rem; }
      .qso-tools, .pager { gap: .6rem; margin: .75rem 0; }
      .qso-tools > .muted {
        width: 100%;
        font-size: .84rem;
      }
      .summary-bar { margin-left: 0; width: 100%; justify-content: space-between; }
      .load-meter { margin-left: 0; width: 100%; min-width: 0; }
      .load-text {
        font-size: .78rem;
        white-space: normal;
      }
      .table-wrap {
        overflow: visible;
        border: none;
        background: transparent;
      }
      table, thead, tbody, tr, td {
        display: block;
        width: 100%;
      }
      thead {
        position: absolute;
        width: 1px;
        height: 1px;
        overflow: hidden;
        clip: rect(0 0 0 0);
      }
      tbody {
        display: grid;
        gap: .7rem;
      }
      tbody tr {
        background: rgba(255, 255, 255, 0.88);
        border: 1px solid var(--line-soft);
        border-radius: 18px;
        padding: .7rem .75rem .8rem;
        box-shadow: 0 10px 22px rgba(18, 40, 82, 0.07);
      }
      tbody tr:hover { background: rgba(255, 255, 255, 0.94); }
      th, td {
        padding: 0;
        border: none;
      }
      td {
        margin-top: .55rem;
      }
      td:first-child {
        margin-top: 0;
      }
      .sel, .sel-cell {
        width: auto;
        text-align: left;
      }
      .sel-cell input[type="checkbox"] {
        width: 1.1rem;
        height: 1.1rem;
      }
      .meta-label {
        display: block;
      }
      .wrap {
        overflow-wrap: anywhere;
        word-break: break-word;
      }
      .dir, .file-label {
        align-items: flex-start;
        line-height: 1.35;
      }
      .dir::before, .file-label::before {
        margin-top: .05rem;
      }
      .audio {
        max-width: 100%;
        margin-top: .75rem;
      }
      .file-actions {
        flex-direction: column;
        gap: .45rem;
      }
      .file-actions .btn {
        width: 100%;
      }
    }
  </style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <h1>AllStar Archive <span class="pill">Read Only</span></h1>
      <p class="hero-copy">Browse recordings, listen in the browser, and assemble a full QSO from matching clips without leaving the page.</p>
      <div class="crumbs">
        {% for name, link in breadcrumbs %}
          <a href="{{ link }}">{{ name }}</a>
        {% endfor %}
      </div>
    </section>

    <section class="content">
      <div class="sticky-area">
        <div class="sticky-card">
          <div class="controls">
            <form method="get">
              <input type="hidden" name="sort" value="{{ sort }}">
              <input type="hidden" name="dir" value="{{ direction }}">
              {% if date_filter %}<input type="hidden" name="date" value="{{ date_filter }}">{% endif %}
              <input type="hidden" name="per_page" value="{{ per_page }}">
              <input type="search" name="q" value="{{ q or '' }}" placeholder="Filter by filename..." />
            </form>
            <form method="get">
              <input type="hidden" name="sort" value="{{ sort }}">
              <input type="hidden" name="dir" value="{{ direction }}">
              {% if q %}<input type="hidden" name="q" value="{{ q|e }}">{% endif %}
              <input type="hidden" name="per_page" value="{{ per_page }}">
              <input type="date" name="date" value="{{ date_filter or '' }}" onchange="this.form.submit()" />
            </form>
            <form method="get">
              <input type="hidden" name="sort" value="{{ sort }}">
              <input type="hidden" name="dir" value="{{ direction }}">
              {% if q %}<input type="hidden" name="q" value="{{ q|e }}">{% endif %}
              {% if date_filter %}<input type="hidden" name="date" value="{{ date_filter }}">{% endif %}
              <label class="muted" for="per_page">Per page</label>
              <select id="per_page" name="per_page" onchange="this.form.submit()">
                {% for option in per_page_options %}
                  <option value="{{ option }}" {% if per_page == option %}selected{% endif %}>{{ option|upper }}</option>
                {% endfor %}
              </select>
            </form>
            <div class="status">
              <span>Sorted by <strong>{{ 'oldest' if direction == 'asc' else 'newest' }}</strong></span>
              <a class="pill" href="?dir={{ 'asc' if direction == 'desc' else 'desc' }}{% if q %}&q={{ q|e }}{% endif %}{% if date_filter %}&date={{ date_filter }}{% endif %}&per_page={{ per_page }}{% if page > 1 %}&page={{ page }}{% endif %}">
                {{ 'Oldest First' if direction == 'desc' else 'Newest First' }}
              </a>
              {% if date_filter %}<span class="pill">Date {{ date_filter }}</span>{% endif %}
            </div>
          </div>

          {% if parent_link %}
            <p><a class="up-link" href="{{ parent_link }}">Back up one level</a></p>
          {% endif %}

          <div class="helper-card">
            <div class="qso-tools">
              <button class="btn btn-primary" type="submit" form="qso-form" name="scope" value="selected">Build Combined Clip</button>
              <button class="btn" type="submit" form="qso-form" name="scope" value="all_day">Build All Audio For This Day</button>
              <span class="muted">Combined clips always follow timestamp order and ignore non-audio files.</span>
              <span class="summary-bar">
                <span><strong id="selected-count">0</strong> selected</span>
                <span>Total <strong id="selected-duration">0:00</strong></span>
              </span>
              <span id="duration-loader" class="load-meter" hidden>
                <span class="load-track"><span id="duration-loader-fill" class="load-fill"></span></span>
                <span id="duration-loader-text" class="load-text">Processing clips...</span>
              </span>
            </div>
          </div>
        </div>
      </div>

      <form id="qso-form" method="post" action="{{ url_for('qso_builder') }}">
        <input type="hidden" name="subpath" value="{{ rel if rel != '.' else '' }}">
        <input type="hidden" name="sort" value="{{ sort }}">
        <input type="hidden" name="dir" value="{{ direction }}">
        <input type="hidden" name="q" value="{{ q or '' }}">
        <input type="hidden" name="date" value="{{ date_filter or '' }}">
        <input type="hidden" name="per_page" value="{{ per_page }}">
        <input type="hidden" name="page" value="{{ page }}">

        {% if total_pages > 1 %}
          <div class="pager">
            {% if page > 1 %}
              <a class="btn" href="{{ url_for('browse', subpath=rel if rel != '.' else '', sort=sort, dir=direction, q=q or None, date=date_filter, per_page=per_page, page=page-1) }}">Previous</a>
            {% endif %}
            <span class="muted">Page {{ page }} of {{ total_pages }}{% if total_items %} ({{ total_items }} items){% endif %}</span>
            {% if page < total_pages %}
              <a class="btn" href="{{ url_for('browse', subpath=rel if rel != '.' else '', sort=sort, dir=direction, q=q or None, date=date_filter, per_page=per_page, page=page+1) }}">Next</a>
            {% endif %}
          </div>
        {% endif %}

        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th class="sel"><input id="toggle-all" type="checkbox" title="Select or deselect all audio clips on this page"></th>
                <th>Name</th>
                <th>Duration</th>
                <th>Size</th>
                <th>Modified</th>
              </tr>
            </thead>
            <tbody>
              {% for item in items %}
                <tr>
                  <td class="sel sel-cell">
                    <span class="meta-label">QSO</span>
                    {% if item.is_audio %}
                      <input class="qso-checkbox" type="checkbox" name="files" value="{{ item.rel }}" data-duration="{{ item.duration_seconds if item.duration_seconds is not none else '' }}">
                    {% endif %}
                  </td>
                  <td class="wrap">
                    <span class="meta-label">Name</span>
                    {% if item.is_dir %}
                      <span class="dir"><a href="{{ url_for('browse', subpath=item.rel) }}">{{ item.name }}</a></span>
                    {% else %}
                      <span class="file-label"><a href="{{ url_for('serve_file', subpath=item.rel) }}">{{ item.name }}</a></span>
                      {% if item.is_audio %}
                        <div>
                          <audio class="audio" controls preload="none">
                            <source src="{{ url_for('stream_transcoded', subpath=item.rel) }}" type="audio/mpeg">
                            <source src="{{ url_for('serve_file', subpath=item.rel) }}" type="{{ item.mimetype or 'audio/wav' }}">
                            Your browser cannot play this file; try downloading instead.
                          </audio>
                          <div class="file-actions">
                            <a class="btn" href="{{ url_for('stream_transcoded', subpath=item.rel) }}" download="{{ item.name.rsplit('.',1)[0] }}.mp3">Download MP3</a>
                            <a class="btn" href="{{ url_for('download_file', subpath=item.rel) }}">Download Original</a>
                          </div>
                        </div>
                      {% endif %}
                    {% endif %}
                  </td>
                  <td class="muted">
                    <span class="meta-label">Duration</span>
                    {% if item.is_audio %}
                      <span class="duration-display" data-rel="{{ item.rel }}">...</span>
                    {% else %}
                      -
                    {% endif %}
                  </td>
                  <td>
                    <span class="meta-label">Size</span>
                    {{ item.size_human if not item.is_dir else '-' }}
                  </td>
                  <td class="muted" title="{{ item.time_iso }}">
                    <span class="meta-label">Modified</span>
                    {{ item.time_human }}
                  </td>
                </tr>
              {% endfor %}
            </tbody>
          </table>
        </div>

        {% if total_pages > 1 %}
          <div class="pager">
            {% if page > 1 %}
              <a class="btn" href="{{ url_for('browse', subpath=rel if rel != '.' else '', sort=sort, dir=direction, q=q or None, date=date_filter, per_page=per_page, page=page-1) }}">Previous</a>
            {% endif %}
            <span class="muted">Page {{ page }} of {{ total_pages }}{% if total_items %} ({{ total_items }} items){% endif %}</span>
            {% if page < total_pages %}
              <a class="btn" href="{{ url_for('browse', subpath=rel if rel != '.' else '', sort=sort, dir=direction, q=q or None, date=date_filter, per_page=per_page, page=page+1) }}">Next</a>
            {% endif %}
          </div>
        {% endif %}
      </form>

      {% if error_message %}
        <div class="error">{{ error_message }}</div>
      {% endif %}

      {% if not items %}
        <div class="empty muted">No files here.</div>
      {% endif %}
    </section>
  </div>
  <script>
    (function () {
      const checkboxes = Array.from(document.querySelectorAll('.qso-checkbox'));
      const countEl = document.getElementById('selected-count');
      const durationEl = document.getElementById('selected-duration');
      const toggleAll = document.getElementById('toggle-all');
      const qsoForm = document.getElementById('qso-form');
      const durationDisplays = new Map(
        Array.from(document.querySelectorAll('.duration-display')).map((el) => [el.dataset.rel, el])
      );
      const loader = document.getElementById('duration-loader');
      const loaderFill = document.getElementById('duration-loader-fill');
      const loaderText = document.getElementById('duration-loader-text');
      let lastIndex = null;
      let buildPending = false;

      function formatDuration(totalSeconds) {
        const rounded = Math.max(0, Math.round(totalSeconds));
        const hours = Math.floor(rounded / 3600);
        const minutes = Math.floor((rounded % 3600) / 60);
        const seconds = rounded % 60;
        if (hours > 0) {
          return `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
        }
        return `${minutes}:${String(seconds).padStart(2, '0')}`;
      }

      function setLoaderProgress(done, total) {
        if (!loader || !loaderFill || !loaderText) {
          return;
        }
        if (buildPending) {
          return;
        }
        if (total <= 0) {
          loader.hidden = true;
          return;
        }
        loader.hidden = false;
        loader.classList.remove('is-indeterminate');
        const pct = Math.max(0, Math.min(100, (done / total) * 100));
        loaderFill.style.width = `${pct}%`;
        loaderFill.style.transform = '';
        loaderText.textContent = done >= total ? 'Processing complete' : `Processing clips ${done}/${total}`;
        if (done >= total) {
          setTimeout(() => {
            if (!buildPending) {
              loader.hidden = true;
            }
          }, 600);
        }
      }

      function startBuildState(scope) {
        if (!loader || !loaderFill || !loaderText) {
          return;
        }
        buildPending = true;
        loader.hidden = false;
        loader.classList.add('is-indeterminate');
        loaderFill.style.width = '38%';
        loaderText.textContent = scope === 'all_day' ? 'Building day clip list...' : 'Building combined clip...';
      }

      function updateSummary() {
        let selectedCount = 0;
        let totalDuration = 0;
        for (const checkbox of checkboxes) {
          if (!checkbox.checked) {
            continue;
          }
          selectedCount += 1;
          const duration = parseFloat(checkbox.dataset.duration || '');
          if (!Number.isNaN(duration)) {
            totalDuration += duration;
          }
        }
        if (countEl) {
          countEl.textContent = String(selectedCount);
        }
        if (durationEl) {
          durationEl.textContent = formatDuration(totalDuration);
        }
        if (toggleAll) {
          toggleAll.checked = checkboxes.length > 0 && selectedCount === checkboxes.length;
          toggleAll.indeterminate = selectedCount > 0 && selectedCount < checkboxes.length;
        }
      }

      checkboxes.forEach((checkbox, index) => {
        checkbox.addEventListener('click', function (event) {
          if (event.shiftKey && lastIndex !== null) {
            const start = Math.min(lastIndex, index);
            const end = Math.max(lastIndex, index);
            const targetState = checkbox.checked;
            for (let i = start; i <= end; i += 1) {
              checkboxes[i].checked = targetState;
            }
          }
          lastIndex = index;
          updateSummary();
        });
      });

      if (toggleAll) {
        toggleAll.addEventListener('change', function () {
          for (const checkbox of checkboxes) {
            checkbox.checked = toggleAll.checked;
          }
          updateSummary();
        });
      }

      if (qsoForm) {
        qsoForm.addEventListener('submit', function (event) {
          const submitter = event.submitter;
          const scope = submitter && submitter.name === 'scope' ? submitter.value : 'selected';
          startBuildState(scope);
        });
      }

      async function loadDurations() {
        const audioRels = checkboxes.map((checkbox) => checkbox.value);
        const total = audioRels.length;
        if (total === 0) {
          setLoaderProgress(0, 0);
          return;
        }

        const chunkSize = 5;
        let completed = 0;
        setLoaderProgress(0, total);

        for (let start = 0; start < total; start += chunkSize) {
          const chunk = audioRels.slice(start, start + chunkSize);
          try {
            const response = await fetch('{{ url_for("api_durations") }}', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ files: chunk })
            });
            if (!response.ok) {
              throw new Error(`Duration request failed: ${response.status}`);
            }
            const payload = await response.json();
            for (const item of payload.items || []) {
              const display = durationDisplays.get(item.rel);
              if (display) {
                display.textContent = item.duration_human || '-';
              }
              const checkbox = checkboxes.find((entry) => entry.value === item.rel);
              if (checkbox) {
                checkbox.dataset.duration = item.duration_seconds ?? '';
              }
            }
          } catch (error) {
            console.error(error);
            if (loaderText) {
              loaderText.textContent = 'Processing failed';
            }
            return;
          }

          completed += chunk.length;
          setLoaderProgress(completed, total);
          updateSummary();
        }
      }

      updateSummary();
      loadDurations();
    }());
  </script>
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
    :root {
      font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
      --bg: #f3f6fb;
      --panel: rgba(255, 255, 255, 0.9);
      --line: #dce4ef;
      --text: #142033;
      --muted: #5f6f86;
      --accent: #0f62fe;
      --accent-soft: #e8f0ff;
      --accent-strong: #123f99;
      --shadow: 0 18px 48px rgba(18, 40, 82, 0.10);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background:
        radial-gradient(circle at top left, rgba(15, 98, 254, 0.14), transparent 26%),
        linear-gradient(180deg, #f8fbff 0%, var(--bg) 100%);
      color: var(--text);
    }
    .shell { width: min(980px, calc(100% - 2rem)); margin: 1.25rem auto 2rem; }
    .panel {
      background: var(--panel);
      border: 1px solid rgba(255, 255, 255, 0.65);
      border-radius: 24px;
      box-shadow: var(--shadow);
      padding: 1.4rem;
      backdrop-filter: blur(18px);
    }
    h1 {
      margin: 0;
      font-size: clamp(1.7rem, 3vw, 2.4rem);
      line-height: 1.05;
      letter-spacing: -0.03em;
    }
    .muted { color: var(--muted); }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: .35rem;
      font-size:.78rem;
      font-weight: 700;
      background: var(--accent-soft);
      color: var(--accent-strong);
      padding:.25rem .65rem;
      border-radius:999px;
    }
    .actions { display:flex; gap:.75rem; align-items:center; flex-wrap:wrap; margin: 1rem 0; }
    .btn {
      display:inline-flex;
      align-items:center;
      justify-content:center;
      min-height:42px;
      padding:.55rem .9rem;
      border:1px solid var(--line);
      border-radius:999px;
      font-size:.92rem;
      font-weight: 600;
      text-decoration:none;
      color:inherit;
      background:#fff;
      box-shadow: 0 8px 20px rgba(18, 40, 82, 0.06);
    }
    .btn:hover { background:#f4f8ff; border-color: rgba(15, 98, 254, 0.34); }
    audio {
      width:100%;
      max-width:720px;
      margin: 1rem 0;
      border-radius: 999px;
    }
    .list-card {
      margin-top: 1rem;
      padding: 1rem 1.1rem;
      border: 1px solid var(--line);
      border-radius: 18px;
      background: rgba(255,255,255,0.68);
    }
    ol { padding-left: 1.4rem; margin-bottom: 0; }
    li + li { margin-top: .35rem; }
    @media (max-width: 780px) {
      .shell { width: min(100% - 1rem, 100%); margin: .5rem auto 1rem; }
      .panel { padding: 1rem; border-radius: 20px; }
      .actions .btn { width: 100%; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <section class="panel">
      <h1>QSO Builder <span class="pill">{{ count }} clips</span></h1>
      <p class="muted">The combined clip follows timestamp order from the selected archive view.</p>
      <p class="muted">Combined duration <span class="pill">{{ total_duration_human }}</span></p>

      <div class="actions">
        <a class="btn" href="{{ stream_url }}">Play Combined Stream</a>
        <a class="btn" href="{{ download_url }}">Download Combined MP3</a>
        <a class="btn" href="{{ back_url }}">Back to Archive</a>
      </div>

      <audio controls preload="none" src="{{ stream_url }}">
        Your browser cannot play this combined clip; try the download link instead.
      </audio>

      <div class="list-card">
        <h2>Included Clips</h2>
        <ol>
          {% for item in items %}
            <li>{{ item.name }} <span class="muted">({{ item.duration_human }})</span></li>
          {% endfor %}
        </ol>
      </div>
    </section>
  </div>
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


def normalize_sort_direction(direction: str | None) -> str:
    if direction in {"asc", "desc"}:
        return direction
    return "desc"


def get_cache_connection() -> sqlite3.Connection:
    CACHE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(CACHE_DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audio_duration_cache (
            rel_path TEXT PRIMARY KEY,
            mtime REAL NOT NULL,
            size INTEGER NOT NULL,
            duration REAL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_audio_duration_cache_updated_at ON audio_duration_cache(updated_at)"
    )
    return conn


def get_cached_duration(rel_path: str, *, mtime: float, size: int) -> float | None | object:
    conn = get_cache_connection()
    try:
        row = conn.execute(
            "SELECT mtime, size, duration FROM audio_duration_cache WHERE rel_path = ?",
            (rel_path,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return _CACHE_MISS
    cached_mtime, cached_size, duration = row
    if cached_mtime != mtime or cached_size != size:
        return _CACHE_MISS
    return duration


def store_cached_duration(rel_path: str, *, mtime: float, size: int, duration: float | None) -> None:
    conn = get_cache_connection()
    try:
        conn.execute(
            """
            INSERT INTO audio_duration_cache(rel_path, mtime, size, duration, updated_at)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(rel_path) DO UPDATE SET
                mtime=excluded.mtime,
                size=excluded.size,
                duration=excluded.duration,
                updated_at=excluded.updated_at
            """,
            (rel_path, mtime, size, duration, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


_CACHE_MISS = object()


def fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    rounded = max(0, int(round(seconds)))
    hours, rem = divmod(rounded, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


_FFMPEG_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def probe_audio_duration(path: Path) -> float | None:
    ffprobe_cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        result = subprocess.run(ffprobe_cmd, capture_output=True, text=True, check=False, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        result = None
    else:
        try:
            if result.returncode == 0:
                return float((result.stdout or "").strip())
        except ValueError:
            pass

    # Some local Windows installs expose ffmpeg without ffprobe.
    ffmpeg_cmd = ["ffmpeg", "-i", str(path), "-f", "null", "-"]
    try:
        result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, check=False, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    match = _FFMPEG_DURATION_RE.search(result.stderr or "")
    if not match:
        return None
    hours = int(match.group(1))
    minutes = int(match.group(2))
    seconds = float(match.group(3))
    return (hours * 3600) + (minutes * 60) + seconds


def get_audio_duration(path: Path, *, rel_path: str | None = None, stat_result: os.stat_result | None = None) -> float | None:
    try:
        stat = stat_result or path.stat()
    except FileNotFoundError:
        return None

    if rel_path is None:
        try:
            rel_path = path.relative_to(ARCHIVE_ROOT).as_posix()
        except ValueError:
            rel_path = str(path)

    cached = get_cached_duration(rel_path, mtime=stat.st_mtime, size=stat.st_size)
    if cached is not _CACHE_MISS:
        return cached

    duration = probe_audio_duration(path)

    store_cached_duration(rel_path, mtime=stat.st_mtime, size=stat.st_size, duration=duration)
    return duration


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


def collect_entries(base: Path, rel: Path, *, direction: str, q: str, date_filter):
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

                sort_value = stat.st_mtime if direction == "asc" else -stat.st_mtime

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
                            sort_value,
                        ),
                    }
                )
    except PermissionError:
        abort(403)

    entries.sort(key=lambda x: x["sort_key"])
    return entries


def render_browse_page(subpath: str, *, direction: str | None = None, q: str | None = None, date_raw: str | None = None, date_param_present: bool | None = None, per_page_raw: str | None = None, page_raw: str | None = None, error_message: str | None = None):
    rel = Path(subpath)
    base = within_root(ARCHIVE_ROOT / rel)
    if not base.exists() or not base.is_dir():
        abort(404)

    sort = "time"
    direction = normalize_sort_direction(direction or request.args.get("dir"))
    q = q if q is not None else (request.args.get("q") or "").strip().lower()

    if date_param_present is None:
        date_param_present = "date" in request.args
    if date_raw is None:
        date_raw = (request.args.get("date") or "").strip() if date_param_present else ""
    date_filter = parse_date_filter(date_raw, date_param_present=date_param_present)
    date_filter_str = date_filter.isoformat() if date_filter else None
    per_page = parse_per_page(per_page_raw if per_page_raw is not None else request.args.get("per_page"))
    page = parse_page_number(page_raw if page_raw is not None else request.args.get("page"))
    entries = collect_entries(base, rel, direction=direction, q=q, date_filter=date_filter)
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
        direction=direction,
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


@app.post("/api/durations")
def api_durations():
    data = request.get_json(silent=True) or {}
    rel_paths = data.get("files") or []
    if not isinstance(rel_paths, list):
        abort(400)

    items = []
    for rel_path in rel_paths:
        if not isinstance(rel_path, str):
            abort(400)
        rel = Path(rel_path)
        full = within_root(ARCHIVE_ROOT / rel)
        if not full.exists() or not full.is_file() or not is_audio_path(full):
            continue
        try:
            stat = full.stat()
        except FileNotFoundError:
            continue
        duration = get_audio_duration(full, rel_path=rel.as_posix(), stat_result=stat)
        items.append(
            {
                "rel": rel.as_posix(),
                "duration_seconds": duration,
                "duration_human": fmt_duration(duration),
            }
        )
    return jsonify({"items": items})


@app.post("/qso")
def qso_builder():
    selected = request.form.getlist("files")
    scope = request.form.get("scope", "selected")
    subpath = request.form.get("subpath", "")
    sort = "time"
    direction = normalize_sort_direction(request.form.get("dir"))
    q = (request.form.get("q") or "").strip().lower()
    date_raw = (request.form.get("date") or "").strip()
    date_param_present = "date" in request.form
    per_page = parse_per_page(request.form.get("per_page"))
    page = parse_page_number(request.form.get("page"))

    if scope == "all_day":
        rel = Path(subpath)
        base = within_root(ARCHIVE_ROOT / rel)
        date_filter = parse_date_filter(date_raw, date_param_present=date_param_present)
        selected = [
            entry["rel"]
            for entry in collect_entries(base, rel, direction=direction, q=q, date_filter=date_filter)
            if entry["is_audio"]
        ]

    if not selected:
        return render_browse_page(
            subpath,
            direction=direction,
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
    item_details = []
    total_duration_seconds = 0.0
    has_duration = False
    for path in paths:
        duration = get_audio_duration(path, rel_path=path.relative_to(ARCHIVE_ROOT).as_posix())
        if duration is not None:
            total_duration_seconds += duration
            has_duration = True
        item_details.append({"name": path.name, "duration_human": fmt_duration(duration)})
    try:
        back_rel = first_parent.relative_to(ARCHIVE_ROOT).as_posix()
    except ValueError:
        back_rel = ""

    return render_template_string(
        QSO_TEMPLATE,
        count=len(paths),
        items=item_details,
        total_duration_human=fmt_duration(total_duration_seconds if has_duration else None),
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
