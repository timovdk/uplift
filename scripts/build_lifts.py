#!/usr/bin/env python3
"""Build data/lifts.json: the latest known status of every lift, from SIRI-FM messages.

How the feed works (BISON TMI9 Dutch SIRI-FM profile, and what we saw in Nov 2026): DOVA sends
some status changes live, mostly short blips, and now and then (seen 18 Nov 03:02 UTC, not the
night after) the status of *all* lifts in one message. That full status is the new baseline
(apply_snapshot); live changes build on it (apply). In between, a lift keeps its last known
status. Each lift keeps its changes of the last 24 hours, for the display rules in js/lib.js.

Feed ids ("NL:CHB:LiftEquipment:8400058_013") are mapped to lift codes ("ASD-LIF-013") via
data/stations.json, which build_stations.py makes from DOVA's NeTEx-EPIAP file.

Usage (the feed service in nas/ does this continuously; this is for trying things locally):
  python3 scripts/build_lifts.py --out /tmp/lifts.json path/to/raw/*/*.xml   # from archived messages
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
from datetime import datetime, timedelta, timezone

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


# A message with at least this many lifts is DOVA's full status ("snapshot", all ~440 lifts;
# seen 18 Nov 03:02 UTC, not daily). Ordinary messages carry one or a few lifts.
SNAPSHOT_MIN = 100
# How long each lift's changes are kept (for "a few short outages today", see lib.js).
HISTORY_H = 24


def record(entry, t, status, now):
    """Add a status change to the lift's recent history (entry['h'] = [[time, status], ...])."""
    keep_from = now - timedelta(hours=HISTORY_H)
    h = [x for x in entry.get('h', []) if parse_time(x[0]) >= keep_from]
    if t and t >= keep_from and not (h and h[-1] == [iso(t), status]):
        h.append([iso(t), status])
    if h:
        entry['h'] = h
    else:
        entry.pop('h', None)


def clean(entry):
    return {k: v for k, v in entry.items() if v is not None}


def apply(state, conditions, known, now):
    """Apply live conditions to state['lifts'] in place, on top of the last snapshot. Returns
    the refs we couldn't link to a lift. `known`: {feed id or lift id: lift id}, see load_known().

    A change replaces the lift's status. A repeat of the same status only confirms it: what we
    already know (end date, description, start) is kept when the repeat doesn't say. (ASD-LIF-013
    is repeated every 5 minutes without the "in gebruik genomen in juni 2027" the snapshot has.)"""
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
                start = c['start'] or at
                entry.update(s=c['status'], since=iso(start), until=iso(c['end']), desc=c['desc'])
                if prev.get('s') or c['status'] != 'available':
                    record(entry, start, c['status'], now)
            else:
                entry['since'] = prev.get('since') or iso(c['start'] or at)
                entry['until'] = iso(c['end']) or prev.get('until')
                entry['desc'] = c['desc'] or prev.get('desc')
            entry.pop('snap', None)      # ... and live, not just in last night's snapshot
        lifts[lid] = clean(entry)
    # A planned outage whose end has passed is no longer interesting.
    for entry in lifts.values():
        p = entry.get('planned')
        if p and p.get('until') and parse_time(p['until']) < now:
            del entry['planned']
    return unmatched


def apply_snapshot(state, conditions, known, now):
    """DOVA's full status is the new baseline: every lift gets the status from the
    snapshot (marked `snap`, so the app can say "gemeld vannacht"), and lifts that aren't in it
    lose their status. Only the recent history is carried over. Live messages then build on it.
    Returns the refs we couldn't link to a lift."""
    lifts = state.setdefault('lifts', {})
    unmatched, seen = set(), set()
    at = max((c['at'] for c in conditions if c['at']), default=now)
    for c in conditions:
        lid = lift_id(c['ref'], known)
        if not lid:
            unmatched.add(c['ref'])
            continue
        seen.add(lid)
        prev = lifts.get(lid, {})
        entry = {'upd': iso(at), 'snap': True}
        if prev.get('h'):
            entry['h'] = prev['h']
        if c['start'] and c['start'] > now:
            entry['planned'] = {'s': c['status'], 'from': iso(c['start']), 'until': iso(c['end'])}
            if prev.get('s'):
                entry.update(s=prev['s'], since=prev.get('since'))
        else:
            start = c['start'] or at
            entry.update(s=c['status'], since=iso(start), until=iso(c['end']), desc=c['desc'])
            # History is about changes: "working" for a lift we knew nothing about isn't one.
            if c['status'] != prev.get('s') and (prev.get('s') or c['status'] != 'available'):
                record(entry, start, c['status'], now)
        lifts[lid] = clean(entry)
    for lid in list(lifts):
        if lid not in seen:  # not in the full status: we no longer know anything about it
            if lifts[lid].get('h'):
                lifts[lid] = {'h': lifts[lid]['h']}
            else:
                del lifts[lid]
    state['snapshot'] = iso(at)
    return unmatched


def new_state(prev, source):
    """Running state from a previous lifts.json (or nothing). Never mixes sources."""
    prev = prev or {}
    if prev.get('source') != source:
        prev = {}  # never mix example data with real data
    return {'lifts': prev.get('lifts', {}), 'heartbeat': prev.get('heartbeat'), 'feed': prev.get('feed'),
            'snapshot': prev.get('snapshot'),
            'unmatched': set(), 'received': 0}


def ingest(state, raw, known, now):
    """Apply one SIRI-FM message (bytes, gzip is fine) to the state. Raises ET.ParseError."""
    if raw[:2] == b'\x1f\x8b':
        raw = gzip.decompress(raw)
    conditions, heartbeat = parse_message(raw)
    state['received'] += len(conditions)
    if len(conditions) >= SNAPSHOT_MIN:
        state['unmatched'] = apply_snapshot(state, conditions, known, now)  # fresh start, also for these
    else:
        state['unmatched'] |= apply(state, conditions, known, now)
    if heartbeat and (not state['heartbeat'] or heartbeat > parse_time(state['heartbeat'])):
        state['heartbeat'] = iso(heartbeat)
    for c in conditions:
        if c['at'] and (not state['feed'] or c['at'] > parse_time(state['feed'])):
            state['feed'] = iso(c['at'])
    return len(conditions)


def output(state, source, now):
    """The lifts.json content for this state."""
    out = {
        'generated': iso(now),
        'source': source,
        'feed': state['feed'],          # newest message time
        'heartbeat': state['heartbeat'],
        'snapshot': state.get('snapshot'),  # the full status this builds on
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


    write_json(args.out, output(state, args.source, now))
    down = sum(1 for l in state['lifts'].values() if l.get('s') == 'notAvailable')
    print(f'{state["received"]} conditions read, {len(state["lifts"])} lifts known, {down} not available, '
          f'{len(state["unmatched"])} unmatched refs -> {os.path.relpath(args.out, ROOT)}')


if __name__ == '__main__':
    main()
