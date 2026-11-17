#!/usr/bin/env python3
"""Build data/stations.json: every station with lifts, and those lifts.

Two CC0 sources on NDOV Loket, combined:

1. NeTEx-EPIAP from DOVA (https://data.ndovloket.nl/netex/epiap/), the main source. It is the
   static data the live feed belongs to: each lift's id there ("NL:CHB:LiftEquipment:8400058_013")
   is the FacilityRef in the SIRI-FM status messages. It also has the station name, station code,
   location, the lift's public code ("ASD-LIF-013") and whether it is monitored at all.
2. ProRail's lift register (https://data.ndovloket.nl/prorail/, "Liften juni 2026.csv"), for what
   EPIAP lacks: the tracks a lift serves, its exact position, and who manages it. Lifts that are
   only in the register are kept too (they get no live status).
3. ProRail's station classes from the network statement ("Lijst stations uit NV2019 v1.0.xlsx",
   same folder): kathedraal / mega / plus / basis / halte, by passenger numbers. Stored as `size`
   (5..1) so the search can put Amsterdam Zuid before Kampen Zuid. Classes rarely change; the
   list is from 2019, so a few renamed stations are mapped by hand (CLASS_NAMES).

Usage: python3 scripts/build_stations.py [--epiap FILE.xml(.gz)] [--register FILE.csv] [--out FILE]

The feed service (nas/) runs build() and check() monthly and uploads the result to R2;
data/stations.json in git is the fallback the app ships with.
"""
import argparse
import gzip
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
import csv
import io
import json
import math
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from statistics import median

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data', 'stations.json')
CACHE = os.path.join(ROOT, '.cache')
INDEX = 'https://data.ndovloket.nl/prorail/'
EPIAP_INDEX = 'https://data.ndovloket.nl/netex/epiap/'
CLASSES_URL = 'https://data.ndovloket.nl/prorail/Lijst%20stations%20uit%20NV2019%20v1.0.xlsx'
SIZES = {'kathedraal': 5, 'mega': 4, 'plus': 3, 'basis': 2, 'halte': 1}
DEFAULT_SIZE = 2
# Our name -> name in the 2019 class list, for stations renamed or spelled differently since.
CLASS_NAMES = {
    "'s-Hertogenbosch": 'Hertogenbosch (‘s)',
    "'s-Hertogenbosch Oost": 'Hertogenbosch Oost (‘s)',
    'Amersfoort Centraal': 'Amersfoort',
    'Eindhoven Centraal': 'Eindhoven',
    'Delft Campus': 'Delft Zuid',
    'Maastricht Brightlands': 'Maastricht Randwyck',
}
USER_AGENT = os.environ.get('LIFT_USER_AGENT', 'uplift/1.0 (open data app)')

MONTHS = {
    'jan': 1, 'januari': 1, 'feb': 2, 'februari': 2, 'mrt': 3, 'maart': 3, 'apr': 4, 'april': 4,
    'mei': 5, 'jun': 6, 'juni': 6, 'jul': 7, 'juli': 7, 'aug': 8, 'augustus': 8,
    'sep': 9, 'sept': 9, 'september': 9, 'okt': 10, 'oktober': 10, 'nov': 11, 'november': 11,
    'dec': 12, 'december': 12,
}


def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def file_date(name):
    """'Liften juni 2026.csv' -> (2026, 6); 'Lift 2024-12-19.csv' -> (2024, 12). None if unknown."""
    base = urllib.request.unquote(name).lower()
    m = re.search(r'(\d{4})-(\d{2})-\d{2}', base)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r'([a-z]+)\s+(\d{4})', base)
    if m and m.group(1) in MONTHS:
        return int(m.group(2)), MONTHS[m.group(1)]
    return None


def newest_lift_file():
    html = fetch(INDEX).decode('utf-8', 'replace')
    names = set(re.findall(r'href="(lift[^"]*\.csv)"', html, flags=re.I))
    dated = [(file_date(n), n) for n in names]
    dated = [x for x in dated if x[0]]
    if not dated:
        raise SystemExit(f'No lift CSV found on {INDEX}')
    return max(dated)[1]


def rd_to_wgs84(x, y):
    """Rijksdriehoek (EPSG:28992) -> (lat, lon). Standard polynomial approximation, ~1 m."""
    dx = (x - 155000) * 1e-5
    dy = (y - 463000) * 1e-5
    lat = (3235.65389 * dy - 32.58297 * dx ** 2 - 0.2475 * dy ** 2 - 0.84978 * dx ** 2 * dy
           - 0.0655 * dy ** 3 - 0.01709 * dx ** 2 * dy ** 2 - 0.00738 * dx + 0.0053 * dx ** 4
           - 0.00039 * dx ** 2 * dy ** 3 + 0.00033 * dx ** 4 * dy - 0.00012 * dx * dy)
    lon = (5260.52916 * dx + 105.94684 * dx * dy + 2.45656 * dx * dy ** 2 - 0.81885 * dx ** 3
           + 0.05594 * dx * dy ** 3 - 0.05607 * dx ** 3 * dy + 0.01199 * dy - 0.00256 * dx ** 3 * dy ** 2
           + 0.00128 * dx * dy ** 4 + 0.00022 * dy ** 2 - 0.00022 * dx ** 2 + 0.00026 * dx ** 5)
    return round(52.15517440 + lat / 3600, 6), round(5.38720621 + lon / 3600, 6)


def num(s):
    """'91.284,497' (Dutch notation) -> 91284.497"""
    s = (s or '').strip().replace('.', '').replace(',', '.')
    try:
        return float(s)
    except ValueError:
        return None


def clean(s):
    return re.sub(r'\s+', ' ', (s or '').strip())


def parse(text):
    rows = list(csv.reader(io.StringIO(text), delimiter=';'))
    # Row 0 holds Dutch column names, row 1 the SAP field codes; we use the codes.
    header = rows[1] if rows[1] and rows[1][0] == 'MESSAGE' else rows[0]
    start = 2 if header is rows[1] else 1
    stations = {}
    for r in rows[start:]:
        if len(r) < len(header):
            continue
        d = dict(zip(header, r))
        if 'INAC' in d.get('STTXS', ''):  # equipment marked inactive
            continue
        label = clean(d.get('AL_LABEL')).upper()
        code = clean(d.get('INR_VERKORTING')).upper()
        if not re.match(r'^[A-Z]+-LIF-\d+$', label) or not code:
            continue
        x, y = num(d.get('ZZ_GPS_VAN_X')), num(d.get('ZZ_GPS_VAN_Y'))
        lat, lon = rd_to_wgs84(x, y) if x and y else (None, None)
        if lat is not None and not (50.7 < lat < 53.6 and 3.3 < lon < 7.3):
            lat = lon = None  # a few third-party lifts have nonsense coordinates in the register
        # "UT-LIF-007 Spoor 20/21" -> "Spoor 20/21"
        name = clean(d.get('EQKTX'))
        name = clean(name[len(label):]) if name.upper().startswith(label) else name
        tracks = clean(d.get('TR_LOCATIE_PERRON'))
        tracks = re.sub(r'^SP\s*', '', tracks) if tracks.upper().startswith('SP') else ''
        owner = clean(d.get('AL_BEHEERDER'))
        st = stations.setdefault(code, {'code': code, 'name': clean(d.get('HEQUI_EQKTX')), 'lifts': []})
        lift = {'id': label, 'name': name or label}
        if tracks:
            lift['tracks'] = tracks
        if lat is not None:
            lift['lat'], lift['lon'] = lat, lon
        if owner and owner != 'ProRail':
            lift['manager'] = owner  # e.g. "NS Stations", "Derden", "?"
        st['lifts'].append(lift)
    return stations


def newest_epiap_file():
    html = fetch(EPIAP_INDEX).decode('utf-8', 'replace')
    names = sorted(set(re.findall(r'href="(NeTEx_DOVA_epiap_[\d-]+\.xml\.gz)"', html)))
    if not names:
        raise SystemExit(f'No EPIAP file found on {EPIAP_INDEX}')
    return names[-1]


def parse_epiap(fileobj):
    """-> {station code: station}. Lifts carry 'ref' (the id used by the live feed)."""
    stations = {}
    place = []  # stack of open StopPlaces: [code, name, (x, y)]
    for event, el in ET.iterparse(fileobj, events=('start', 'end')):
        tag = el.tag.rsplit('}', 1)[-1]
        if event == 'start':
            if tag == 'StopPlace':
                place.append([None, None, None])
            continue
        if not place:
            el.clear()
            continue
        cur = place[-1]
        if tag == 'PrivateCode' and el.get('type') == 'StopPlaceCode' and cur[0] is None:
            cur[0] = (el.text or '').strip().rsplit(':', 1)[-1].upper()  # "NL:S:asd" -> "ASD"
        elif tag == 'Name' and cur[1] is None and len(place) and el is not None:
            # The first Name inside a StopPlace is its own (levels/places come later).
            cur[1] = clean(el.text)
        elif tag == 'pos' and cur[2] is None:
            parts = (el.text or '').split()
            if len(parts) == 2:
                cur[2] = (float(parts[0]), float(parts[1]))
        elif tag == 'LiftEquipment' and el.get('id'):
            kids = {c.tag.rsplit('}', 1)[-1]: clean(c.text) for c in el}
            code = (kids.get('PublicCode') or '').upper()
            if cur[0] and code:
                st = stations.setdefault(cur[0], {'code': cur[0], 'name': cur[1], 'lifts': [], '_pos': cur[2]})
                desc = kids.get('Description') or ''
                name = clean(desc[len(code):]) if desc.upper().startswith(code) else desc
                lift = {'id': code, 'name': name or code, 'ref': el.get('id')}
                if kids.get('Monitored') == 'false':
                    lift['monitored'] = False
                st['lifts'].append(lift)
            el.clear()
        elif tag == 'StopPlace':
            place.pop()
            el.clear()
    return stations


def name_key(name):
    """Loose key for matching names between files: 'Alphen a/d Rijn' == 'Alphen aan den Rijn'."""
    s = unicodedata.normalize('NFD', name).encode('ascii', 'ignore').decode().lower()
    s = re.sub(r'\ba\s*/\s*d\b', 'aan den', s)
    s = re.sub(r'\bv\b', 'van', s)
    return re.sub(r'[^a-z0-9]', '', s)


def parse_classes(xlsx_bytes):
    """-> {name_key: size} from the network statement's station table (first sheet, columns A/B)."""
    m = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
    with zipfile.ZipFile(io.BytesIO(xlsx_bytes)) as z:
        shared = [''.join(t.text or '' for t in si.iter(f'{m}t'))
                  for si in ET.fromstring(z.read('xl/sharedStrings.xml'))]
        sheet = ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
    out = {}
    for row in sheet.iter(f'{m}row'):
        vals = []
        for c in row:
            v = c.find(f'{m}v')
            vals.append(shared[int(v.text)] if v is not None and c.get('t') == 's' else (v.text if v is not None else ''))
        if len(vals) >= 2 and vals[1].strip().lower() in SIZES:
            out[name_key(vals[0])] = SIZES[vals[1].strip().lower()]
    return out


def add_sizes(stations, classes):
    for st in stations:
        st['size'] = classes.get(name_key(CLASS_NAMES.get(st['name'], st['name'])), DEFAULT_SIZE)


def merge(epiap, register):
    """EPIAP first; the register adds tracks, positions and managers, and lifts EPIAP lacks."""
    by_id = {l['id']: l for st in register.values() for l in st['lifts']}
    out = {}
    for code, st in epiap.items():
        for lift in st['lifts']:
            extra = by_id.get(lift['id'], {})
            for k in ('tracks', 'lat', 'lon', 'manager'):
                if k in extra:
                    lift[k] = extra[k]
        out[code] = st
    known = {l['id'] for st in out.values() for l in st['lifts']}
    for code, st in register.items():
        missing = [l for l in st['lifts'] if l['id'] not in known]
        if not missing:
            continue
        target = out.setdefault(code, {'code': code, 'name': st['name'], 'lifts': []})
        target['lifts'].extend(missing)
    return out


def finish(stations):
    out = []
    for st in stations.values():
        pos = st.pop('_pos', None)
        pts = [(l['lat'], l['lon']) for l in st['lifts'] if 'lat' in l]
        if pts:
            # Median, so one misplaced lift can't drag the station away.
            st['lat'] = round(median(p[0] for p in pts), 6)
            st['lon'] = round(median(p[1] for p in pts), 6)
        elif pos:
            st['lat'], st['lon'] = rd_to_wgs84(*pos)
        else:
            continue
        st['lifts'].sort(key=lambda l: (l['id'].split('-')[1] != 'LIF', int(re.sub(r'\D', '', l['id'].rsplit('-', 1)[1]) or 0)))
        out.append(st)
    out.sort(key=lambda s: s['name'])
    return out


def build(epiap_path=None, register_path=None, cache=CACHE):
    """Download (or read) the sources and return the stations.json content."""
    os.makedirs(cache, exist_ok=True)
    if register_path:
        reg_source = os.path.basename(register_path)
        with open(register_path, 'rb') as f:
            raw = f.read()
    else:
        reg_source = newest_lift_file()
        print(f'Downloading {urllib.request.unquote(reg_source)}')
        raw = fetch(INDEX + reg_source)
        with open(os.path.join(cache, 'liften.csv'), 'wb') as f:
            f.write(raw)
    register = parse(raw.decode('utf-8-sig'))

    if epiap_path:
        epi_source, path = os.path.basename(epiap_path), epiap_path
    else:
        epi_source = newest_epiap_file()
        print(f'Downloading {epi_source}')
        path = os.path.join(cache, 'epiap.xml.gz')
        with open(path, 'wb') as f:
            f.write(fetch(EPIAP_INDEX + epi_source))
    opener = gzip.open if path.endswith('.gz') else open
    with opener(path, 'rb') as f:
        epiap = parse_epiap(f)

    stations = finish(merge(epiap, register))
    try:
        add_sizes(stations, parse_classes(fetch(CLASSES_URL)))
    except Exception as e:  # nice to have: the search just ranks a bit worse without it
        print(f'Station classes not available ({e}); everyone gets size {DEFAULT_SIZE}.', file=sys.stderr)
        add_sizes(stations, {})
    return {
        'generated': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'source': [epi_source, urllib.request.unquote(reg_source)],
        'stations': stations,
    }


LIFT_CODE = re.compile(r'^[A-Z]+-(LIF|INV)-\d+$')


def check(data, previous=None):
    """Problems with a freshly built station list (empty: fine to publish). The list is used
    without review (the feed service rebuilds it monthly), so be strict: a source that changed
    its format should keep the old list, not break the app."""
    stations = data.get('stations') or []
    problems = []
    if len(stations) < 150:
        problems.append(f'only {len(stations)} stations')
    lifts = [l for st in stations for l in st.get('lifts', [])]
    if len(lifts) < 400:
        problems.append(f'only {len(lifts)} lifts')
    if sum(1 for l in lifts if l.get('ref')) < 0.8 * len(lifts):
        problems.append('fewer than 80% of the lifts are linked to the live feed')
    for st in stations:
        if not (st.get('code') and st.get('name') and st.get('lifts')):
            problems.append(f'incomplete station {st.get("code") or st.get("name")}')
        elif not (50.7 < st.get('lat', 0) < 53.6 and 3.3 < st.get('lon', 0) < 7.3):
            problems.append(f'{st["code"]} is outside the Netherlands')
        for l in st.get('lifts', []):
            if not LIFT_CODE.match(l.get('id', '')):
                problems.append(f'odd lift code {l.get("id")!r} at {st.get("code")}')
    if previous and len(stations) < 0.9 * len(previous.get('stations') or []):
        problems.append(f'{len(stations)} stations, down from {len(previous["stations"])}')
    return problems[:10]


def summary(data):
    lifts = [l for s in data['stations'] for l in s['lifts']]
    return (f'{len(data["stations"])} stations, {len(lifts)} lifts ({sum(1 for l in lifts if "ref" in l)} in the '
            f'live feed, {sum(1 for l in lifts if l.get("monitored") is False)} not monitored)')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--epiap', help='local NeTEx-EPIAP file (default: download the newest)')
    ap.add_argument('--register', help='local ProRail lift register CSV (default: download the newest)')
    ap.add_argument('--out', default=OUT)
    args = ap.parse_args()
    data = build(args.epiap, args.register)
    previous = None
    if os.path.exists(args.out):
        with open(args.out, encoding='utf-8') as f:
            previous = json.load(f)
    problems = check(data, previous)
    if problems:
        raise SystemExit('Not written, the new list looks wrong: ' + '; '.join(problems))
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
        f.write('\n')
    print(f'{summary(data)} -> {args.out}')


if __name__ == '__main__':
    main()
