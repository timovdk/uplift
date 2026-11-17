#!/usr/bin/env python3
"""Build data/lifts.json: the latest known status of every lift, from SIRI-FM messages.

How the feed works (BISON TMI9, Dutch SIRI-FM profile): DOVA polls ProRail every 5 minutes
and publishes a message for every status *change*, plus once a day the status of *all* lifts.
It also re-sends every lift that is still out of service each round. So one message doesn't tell
the whole story. This script keeps a running state: it starts from the previous lifts.json (the
one that's currently published), applies the new messages on top (newest wins), and then uses
the listen window to draw conclusions from silence (see infer_from_window).

Feed ids ("NL:CHB:LiftEquipment:8400058_013") are mapped to lift codes ("ASD-LIF-013") via
data/stations.json, which build_stations.py makes from DOVA's NeTEx-EPIAP file.

Usage:
  python3 scripts/listen_lifts.py .cache/feed            # collect messages (11 min)
  python3 scripts/build_lifts.py --previous FILE_OR_URL --window .cache/feed/window.json .cache/feed/msg_*.xml
  python3 scripts/build_lifts.py --example               # build from the made-up test fixture
"""
import argparse
import gzip
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data', 'lifts.json')
STATIONS = os.path.join(ROOT, 'data', 'stations.json')
EXAMPLE = os.path.join(ROOT, 'test', 'fixtures', 'siri-fm-example.xml')

STATUSES = {'available', 'notAvailable', 'partiallyAvailable', 'unknown', 'neverMonitored', 'added', 'removed'}
LIFT_ID = re.compile(r'([A-Z]+-LIF-\d+)', re.I)


def local(tag):
    return tag.rsplit('}', 1)[-1]


def child(el, name):
    for c in el:
        if local(c.tag) == name:
            return c
    return None


def text(el, *path):
    for name in path:
        if el is None:
            return None
        el = child(el, name)
    return el.text.strip() if el is not None and el.text and el.text.strip() else None


def parse_time(s):
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace('Z', '+00:00'))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def iso(d):
    return d.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z') if d else None


def parse_message(xml_bytes):
    """-> (conditions, heartbeat_time). Each condition: dict(ref, status, start, end, desc, at)."""
    root = ET.fromstring(xml_bytes)
    conditions, heartbeat = [], None
    for el in root.iter():
        name = local(el.tag)
        if name == 'HeartbeatNotification':
            heartbeat = parse_time(text(el, 'RequestTimestamp'))
        elif name == 'FacilityMonitoringDelivery':
            at = parse_time(text(el, 'ResponseTimestamp'))
            for fc in el:
                if local(fc.tag) != 'FacilityCondition':
                    continue
                status = text(fc, 'FacilityStatus', 'Status')
                # The lift code ("WDN-LIF-002") can be in FacilityRef or in Facility/FacilityCode
                # (seen in a real message from Jan 2024); take whichever has it.
                refs = [text(fc, 'FacilityRef')] + [
                    e.text.strip() for e in fc.iter() if local(e.tag) == 'FacilityCode' and e.text]
                refs = [r for r in refs if r]
                ref = next((r for r in refs if LIFT_ID.search(r)), refs[0] if refs else None)
                if not ref or status not in STATUSES:
                    continue
                conditions.append({
                    'ref': ref,
                    'status': status,
                    'desc': text(fc, 'FacilityStatus', 'Description'),
                    'start': parse_time(text(fc, 'ValidityPeriod', 'StartTime')),
                    'end': parse_time(text(fc, 'ValidityPeriod', 'EndTime')),
                    'at': at,
                })
    return conditions, heartbeat


def load_known(path=STATIONS):
    known = {}
    with open(path, encoding='utf-8') as f:
        for st in json.load(f)['stations']:
            for l in st['lifts']:
                known[l['id']] = l['id']
                if l.get('ref'):
                    known[l['ref']] = l['id']
    return known


def load_json(src):
    if not src:
        return None
    try:
        if re.match(r'^https?://', src):
            req = urllib.request.Request(src, headers={'Cache-Control': 'no-cache'})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        with open(src, encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:  # first run, site down, ...: start fresh
        print(f'No previous state from {src} ({e}); starting fresh.', file=sys.stderr)
        return None


def lift_id(ref, known):
    """Feed reference -> our lift id. `known` maps feed ids (from EPIAP) and lift ids to lift ids."""
    if ref in known:
        return known[ref]
    m = LIFT_ID.search(ref)  # fallback: a ref that contains the public code itself
    return known.get(m.group(1).upper()) if m else None


def apply(state, conditions, known, now):
    """Apply conditions to state['lifts'] in place. Returns the refs we couldn't link to a lift.
    `known`: {feed id or lift id: lift id}, see load_known()."""
    lifts = state.setdefault('lifts', {})
    unmatched = set()
    for c in sorted(conditions, key=lambda c: c['at'] or datetime.min.replace(tzinfo=timezone.utc)):
        lid = lift_id(c['ref'], known)
        if not lid:
            unmatched.add(c['ref'])
            continue
        prev = lifts.get(lid, {})
        at = c['at'] or now
        if prev.get('upd') and parse_time(prev['upd']) > at:
            continue  # older than what we already have
        entry = dict(prev)
        entry['upd'] = iso(at)
        if c['start'] and c['start'] > now:
            # Announced for later (maintenance, replacement): keep the current status.
            entry['planned'] = {'s': c['status'], 'from': iso(c['start']), 'until': iso(c['end'])}
        else:
            if c['status'] != prev.get('s'):
                entry['since'] = iso(c['start'] or at)
            elif c['start']:
                entry['since'] = iso(c['start'])
            entry['s'] = c['status']
            entry.pop('inferred', None)  # we heard it from the source now
            entry['until'] = iso(c['end'])
            entry['desc'] = c['desc']
        lifts[lid] = {k: v for k, v in entry.items() if v is not None}
    # A planned outage whose end has passed is no longer interesting.
    for entry in lifts.values():
        p = entry.get('planned')
        if p and p.get('until') and parse_time(p['until']) < now:
            del entry['planned']
    return unmatched


# A listen window must cover at least two of DOVA's 5-minute rounds before we draw conclusions
# from what was *not* said.
MIN_WINDOW_S = 10 * 60
DOWN = ('notAvailable', 'partiallyAvailable')


def feed_lifts(path=STATIONS):
    """Lift ids that are in the live feed and monitored."""
    with open(path, encoding='utf-8') as f:
        return {l['id'] for st in json.load(f)['stations'] for l in st['lifts']
                if l.get('ref') and l.get('monitored', True)}


def infer_from_window(lifts, heard, monitored, window, now):
    """NOT USED BY DEFAULT (--infer). Observed 16 Nov 2026: one lift was re-sent every 5 min, but
    most broken lifts only came again in a larger batch (at 16:56; interval unknown). Only switch
    this on once we know every broken lift is re-sent within the listen window.

    Assumes DOVA re-sends every lift that is still out of service each 5-minute round. So after a
    long enough window, a monitored lift that wasn't reported as down isn't known to be down:
    - a lift we had as down (without a future end date) that wasn't repeated is working again;
    - a lift we know nothing about yet gets 'available', marked as inferred ("geen storing gemeld").
    `heard`: lift ids that had any message in this window. Returns the number of lifts changed."""
    start, end = parse_time(window.get('from')), parse_time(window.get('to'))
    if not start or not end or (end - start).total_seconds() < MIN_WINDOW_S or not window.get('messages'):
        return 0  # too short, or nothing at all came in (feed down?): don't guess
    changed = 0
    for lid in monitored - heard:
        e = lifts.setdefault(lid, {})
        s = e.get('s')
        if s in DOWN:
            until = parse_time(e.get('until'))
            if until and until > now:
                continue  # announced long outage: keep it until it's lifted explicitly
            e.update(s='available', since=iso(start), inferred=True)
            e.pop('until', None)
            e.pop('desc', None)
            changed += 1
        elif s is None:
            e.update(s='available', inferred=True)
            changed += 1
        if e.get('s') == 'available':
            e['upd'] = iso(end)  # confirmed: still no outage reported
    return changed


def new_state(prev, source):
    """Running state from a previous lifts.json (or nothing). Never mixes sources."""
    prev = prev or {}
    if prev.get('source') != source:
        prev = {}  # never mix example data with real data
    return {'lifts': prev.get('lifts', {}), 'heartbeat': prev.get('heartbeat'), 'feed': prev.get('feed'),
            'unmatched': set(), 'received': 0, 'heard': set()}


def ingest(state, raw, known, now):
    """Apply one SIRI-FM message (bytes, gzip is fine) to the state. Raises ET.ParseError."""
    if raw[:2] == b'\x1f\x8b':
        raw = gzip.decompress(raw)
    conditions, heartbeat = parse_message(raw)
    state['received'] += len(conditions)
    state['heard'] |= {lift_id(c['ref'], known) for c in conditions} - {None}
    state['unmatched'] |= apply(state, conditions, known, now)
    if heartbeat and (not state['heartbeat'] or heartbeat > parse_time(state['heartbeat'])):
        state['heartbeat'] = iso(heartbeat)
    for c in conditions:
        if c['at'] and (not state['feed'] or c['at'] > parse_time(state['feed'])):
            state['feed'] = iso(c['at'])
    return len(conditions)


def output(state, source, now, window=None):
    """The lifts.json content for this state."""
    out = {
        'generated': iso(now),
        'window': window,
        'source': source,
        'feed': state['feed'],          # newest message time
        'heartbeat': state['heartbeat'],
        'lifts': dict(sorted(state['lifts'].items())),
    }
    if state['unmatched']:
        out['unmatched'] = sorted(state['unmatched'])[:50]
    return {k: v for k, v in out.items() if v is not None}


def write_json(path, data):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
        f.write('\n')
    os.replace(tmp, path)  # never leave a half-written file behind


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('messages', nargs='*', help='SIRI-FM XML files (gzip is fine)')
    ap.add_argument('--previous', help='previous lifts.json (path or URL)')
    ap.add_argument('--source', default='siri-fm')
    ap.add_argument('--example', action='store_true', help='use the made-up test fixture')
    ap.add_argument('--window', help='window.json written by listen_lifts.py')
    ap.add_argument('--infer', action='store_true',
                    help='conclude from silence (see infer_from_window); off until the re-send rhythm is known')
    ap.add_argument('--out', default=OUT)
    args = ap.parse_args()

    if args.example:
        args.messages, args.source, args.previous = [EXAMPLE], 'example', None

    known = load_known()
    now = datetime.now(timezone.utc)
    state = new_state(load_json(args.previous), args.source)

    for path in sorted(args.messages):  # msg_000001.xml, ... in arrival order
        with open(path, 'rb') as f:
            raw = f.read()
        try:
            ingest(state, raw, known, now)
        except ET.ParseError as e:
            print(f'Skipping {path}: {e}', file=sys.stderr)

    window = load_json(args.window) if args.window else None
    inferred = infer_from_window(state['lifts'], state['heard'], feed_lifts(), window, now) if window and args.infer else 0

    write_json(args.out, output(state, args.source, now, window))
    down = sum(1 for l in state['lifts'].values() if l.get('s') == 'notAvailable')
    print(f'{state["received"]} conditions read, {len(state["lifts"])} lifts known, {down} not available, '
          f'{len(state["unmatched"])} unmatched refs, {inferred} inferred -> {os.path.relpath(args.out, ROOT)}')


if __name__ == '__main__':
    main()
