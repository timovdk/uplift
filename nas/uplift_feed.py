#!/usr/bin/env python3
"""Uplift feed service: listen to the lift feed around the clock and publish the data.

Runs in a small Docker container on a home server (see nas/README.md). It:
- keeps one connection to the GOVI/OpenGeo ZeroMQ stream (fair use: 1 connection per user),
  and reconnects when it goes dead (see listen_lifts.py);
- saves every message in DATA_DIR/raw/YYYY-MM-DD/ (UTC date), for later analysis;
- applies each message to the running status (same code as build_lifts.py): the daily full
  status (~03:02 UTC) is the new baseline, live changes build on it. At start-up it replays the
  archive of the last two days, so a restart or an update starts from last night's baseline;
- uploads lifts.json to a public Cloudflare R2 bucket: at most every PUBLISH_EVERY_MIN minutes
  when something changed, and at least every PUBLISH_AT_LEAST_MIN minutes so the app can see
  the feed is alive;
- rebuilds the station list every STATIONS_EVERY_DAYS days (build_stations.py, in the
  background), checks it, and uploads it as stations.json. A list that fails the check is not
  used; the previous one stays.

The app reads both files from the bucket (js/config.js).

Settings (environment):
  R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY  R2 API token (Object Read & Write,
                    this bucket only). Without them it still listens and archives, but publishes nothing.
  R2_BUCKET (uplift)
  DATA_DIR (/data), PUBLISH_EVERY_MIN (1), PUBLISH_AT_LEAST_MIN (10), STATIONS_EVERY_DAYS (30),
  LIFTS_ZMQ
"""
import glob
import gzip
import json
import os
import signal
import sys
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'scripts'))

import zmq  # noqa: E402

import build_stations  # noqa: E402
from build_lifts import STATIONS, ingest, load_json, load_known, new_state, output, parse_time, write_json  # noqa: E402
from listen_lifts import SILENCE_S, connect  # noqa: E402

DATA = os.environ.get('DATA_DIR', '/data')
EVERY_S = float(os.environ.get('PUBLISH_EVERY_MIN', 1)) * 60
AT_LEAST_S = float(os.environ.get('PUBLISH_AT_LEAST_MIN', 10)) * 60
STATIONS_EVERY_S = float(os.environ.get('STATIONS_EVERY_DAYS', 30)) * 86400
R2 = {k: os.environ.get(f'R2_{k.upper()}', '') for k in ('account_id', 'access_key_id', 'secret_access_key')}
BUCKET = os.environ.get('R2_BUCKET', 'uplift')
ALIVE = '/tmp/alive'  # touched on every message; the Docker healthcheck looks at it

# Short for the status, so browsers and Cloudflare's cache pick up changes quickly.
CACHE_STATUS = 'public, max-age=60'
CACHE_STATIONS = 'public, max-age=3600'


def log(*a):
    print(datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ'), *a, flush=True)


def touch(path):
    with open(path, 'a'):
        os.utime(path)


def save_raw(body, now):
    day = os.path.join(DATA, 'raw', now.strftime('%Y-%m-%d'))
    os.makedirs(day, exist_ok=True)
    # Time-based names sort in arrival order, also across restarts.
    path = os.path.join(day, f'{now.strftime("%H%M%S")}_{time.time_ns() % 10**9:09d}.xml')
    with open(path, 'wb') as f:
        f.write(body)


# --- publishing ------------------------------------------------------------------------------

_client = None


def r2():
    """S3 client for R2, or None when not configured."""
    global _client
    if _client is None and all(R2.values()):
        import boto3  # only needed when publishing
        _client = boto3.client(
            's3', endpoint_url=f'https://{R2["account_id"]}.r2.cloudflarestorage.com',
            aws_access_key_id=R2['access_key_id'], aws_secret_access_key=R2['secret_access_key'],
            region_name='auto')
    return _client


def publish(key, data, cache_control, client=None):
    """Upload `data` as JSON to the bucket. True on success (or when there's nowhere to publish)."""
    client = client or r2()
    if not client:
        return True  # nothing to publish to; the local copy is still written
    body = (json.dumps(data, ensure_ascii=False, separators=(',', ':')) + '\n').encode()
    try:
        client.put_object(Bucket=BUCKET, Key=key, Body=body, ContentType='application/json',
                          CacheControl=cache_control)
    except Exception as e:
        log(f'Publishing {key} failed: {e}')
        return False
    return True


# --- station list ----------------------------------------------------------------------------

def stations_path():
    return os.path.join(DATA, 'stations.json')


def stations_age_s():
    """Seconds since our station list was built (infinite when we only have the bundled one)."""
    data = load_json(stations_path()) if os.path.exists(stations_path()) else None
    at = parse_time((data or {}).get('generated'))
    return time.time() - at.timestamp() if at else float('inf')


def current_known():
    return load_known(stations_path() if os.path.exists(stations_path()) else STATIONS)


class StationRefresh(threading.Thread):
    """Rebuild, check and publish the station list without blocking the listener. The main
    loop picks up `known` (the new feed id mapping) when it's done."""

    def __init__(self):
        super().__init__(daemon=True)
        self.known = None

    def run(self):
        try:
            data = build_stations.build(cache=os.path.join(DATA, 'cache'))
            previous = load_json(stations_path()) if os.path.exists(stations_path()) else load_json(STATIONS)
            problems = build_stations.check(data, previous)
            if problems:
                log('New station list NOT used, it looks wrong: ' + '; '.join(problems))
                return
            write_json(stations_path(), data)
            ok = publish('stations.json', data, CACHE_STATIONS)
            log(f'Station list rebuilt: {build_stations.summary(data)}{"" if ok else " (upload failed)"}.')
            self.known = current_known()
        except Exception as e:  # a source is down or changed: try again later
            log(f'Rebuilding the station list failed: {e}')


# --- start-up --------------------------------------------------------------------------------

def replay(known, days=2):
    """Rebuild the status from the archived messages of the last `days` days, so a restart (or
    new rules after an update) starts from last night's snapshot plus everything since, with the
    lifts' history. None when there's no archive yet."""
    dirs = sorted(glob.glob(os.path.join(DATA, 'raw', '????-??-??')))[-days:]
    files = sorted(f for d in dirs for f in glob.glob(os.path.join(d, '*.xml')))
    if not files:
        return None
    state, now = new_state(None, 'siri-fm'), datetime.now(timezone.utc)
    for path in files:
        try:
            with open(path, 'rb') as f:
                ingest(state, f.read(), known, now)
        except (OSError, ET.ParseError) as e:
            log(f'Skipping {path}: {e}')
    log(f'Replayed {len(files)} messages from the archive; baseline: snapshot of {state.get("snapshot") or "(none yet)"}.')
    return state


# --- main loop -------------------------------------------------------------------------------

def main():
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # docker stop: exit cleanly
    os.makedirs(DATA, exist_ok=True)
    state_path = os.path.join(DATA, 'lifts.json')
    known = current_known()
    state = replay(known) or new_state(load_json(state_path) if os.path.exists(state_path) else None, 'siri-fm')
    log(f'Starting: {len(state["lifts"])} lifts known, publishing to '
        f'{f"R2 bucket {BUCKET}" if all(R2.values()) else "nowhere (no R2_* settings)"}.')

    refresh, refresh_tried = None, 0.0
    ctx = zmq.Context()
    sock = connect(ctx)
    last_rx = time.time()
    last_push, dirty, failed = 0.0, True, False
    while True:
        try:
            parts = sock.recv_multipart()
        except zmq.Again:
            parts = None
            if time.time() - last_rx > SILENCE_S:
                log(f'Nothing for {SILENCE_S // 60} min, reconnecting.')
                sock.close()
                sock = connect(ctx)
                last_rx = time.time()
        if parts:
            body = b''.join(parts[1:])
            if body[:2] == b'\x1f\x8b':
                body = gzip.decompress(body)
            if b'FacilityMonitoringDelivery' in body or b'HeartbeatNotification' in body:
                now = datetime.now(timezone.utc)
                last_rx = time.time()
                touch(ALIVE)
                save_raw(body, now)
                try:
                    if ingest(state, body, known, now):
                        dirty = True
                except ET.ParseError as e:
                    log(f'Skipping a message that is not valid XML: {e}')

        since = time.time() - last_push
        if (dirty and since >= EVERY_S) or since >= AT_LEAST_S or (failed and since >= 60):
            now = datetime.now(timezone.utc)
            data = output(state, 'siri-fm', now)
            write_json(state_path, data)
            ok = publish('lifts.json', data, CACHE_STATUS)
            changed, dirty = dirty, dirty and not ok
            last_push, failed = time.time(), not ok
            if changed or not ok:  # not every 10-minute sign of life
                down = sum(1 for l in state['lifts'].values() if l.get('s') == 'notAvailable')
                log(f'{"Published" if ok and r2() else "Saved (not published)"}: {len(state["lifts"])} lifts known, '
                    f'{down} not available, feed {state["feed"]}.')

        # Station list: when it's due, at most one attempt every 6 hours.
        if refresh is None and time.time() - refresh_tried > 6 * 3600 and stations_age_s() > STATIONS_EVERY_S:
            refresh, refresh_tried = StationRefresh(), time.time()
            refresh.start()
        elif refresh is not None and not refresh.is_alive():
            if refresh.known:
                known = refresh.known
            refresh = None


if __name__ == '__main__':
    main()
