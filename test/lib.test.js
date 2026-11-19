import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  LEVEL_TEXT, formatDistance, formatMoment, liftState, moveFav, nearestStations, normalise, searchStations, stationSummary,
  toggleFav,
} from '../js/lib.js';

const data = JSON.parse(readFileSync(new URL('../data/stations.json', import.meta.url), 'utf8'));
const byCode = new Map(data.stations.map((s) => [s.code, s]));
const now = new Date('2026-11-16T12:00:00Z');

test('station data looks sane', () => {
  assert.ok(data.stations.length > 100, 'expected 100+ stations with lifts');
  const ids = new Set();
  for (const s of data.stations) {
    assert.ok(s.code && s.name && s.lifts.length, s.code);
    // Everything should be inside the Netherlands (plus a margin).
    assert.ok(s.lat > 50.7 && s.lat < 53.6 && s.lon > 3.3 && s.lon < 7.3, `${s.code} at ${s.lat},${s.lon}`);
    for (const l of s.lifts) {
      assert.match(l.id, /^[A-Z]+-(LIF|INV)-\d+$/);
      assert.ok(!ids.has(l.id), `duplicate ${l.id}`);
      ids.add(l.id);
    }
  }
  const ut = byCode.get('UT');
  assert.equal(ut.name, 'Utrecht Centraal');
  assert.ok(Math.abs(ut.lat - 52.089) < 0.01 && Math.abs(ut.lon - 5.110) < 0.01, 'Utrecht Centraal in the right place');
});

test('search: codes, punctuation, abbreviations and other names', () => {
  const first = (q) => (searchStations(data.stations, q)[0] || {}).name;
  const cases = {
    // station codes
    ut: 'Utrecht Centraal', ASD: 'Amsterdam Centraal',
    // apostrophes, dashes, slashes
    "'s-Hertogenbosch": "'s-Hertogenbosch", 's hertogenbosch': "'s-Hertogenbosch", hertogenbosch: "'s-Hertogenbosch",
    'breda prinsenbeek': 'Breda-Prinsenbeek', bredaprinsenbeek: 'Breda-Prinsenbeek', zeist: 'Driebergen-Zeist',
    'alphen a/d rijn': 'Alphen a/d Rijn', 'alphen aan den rijn': 'Alphen a/d Rijn',
    'nieuwerkerk aan den ijssel': 'Nieuwerkerk a/d IJssel',
    // other names
    'den bosch': "'s-Hertogenbosch", 'Den Bosch Oost': "'s-Hertogenbosch Oost", denbosch: "'s-Hertogenbosch",
    's-gravenhage centraal': 'Den Haag Centraal', 'den haag hollands spoor': 'Den Haag HS',
    'laan van nieuw oost indie': 'Den Haag Laan v NOI', 'laan v noi': 'Den Haag Laan v NOI',
    'bijlmer arena': 'Amsterdam Bijlmer ArenA', 'zaanse schans': 'Zaandijk Zaanse Schans',
    // ij / y
    ijpenburg: 'Den Haag Ypenburg', ypenburg: 'Den Haag Ypenburg',
    // abbreviations people type
    'utrecht cs': 'Utrecht Centraal', 'utrecht c': 'Utrecht Centraal', "a'dam zuid": 'Amsterdam Zuid',
    // bigger stations first among equal matches
    utrecht: 'Utrecht Centraal', amsterdam: 'Amsterdam Centraal', zuid: 'Amsterdam Zuid',
    centraal: 'Utrecht Centraal', 'den haag': 'Den Haag Centraal', oost: "'s-Hertogenbosch Oost",
    // one typo
    utrect: 'Utrecht Centraal', groningn: 'Groningen', zwole: 'Zwolle', 'amsterdm centraal': 'Amsterdam Centraal',
  };
  for (const [q, want] of Object.entries(cases)) assert.equal(first(q), want, `"${q}"`);
  // a lone "c" is the start of a name, not "Centraal"
  assert.equal(first('c'), 'Castricum');
  // typo matches don't crowd out exact ones
  assert.deepEqual(searchStations(data.stations, 'adam zuid').map((s) => s.name), ['Amsterdam Zuid']);
  assert.deepEqual(searchStations(data.stations, '   '), []);
  assert.deepEqual(searchStations(data.stations, 'xyzzy'), []);
});

test('normalise', () => {
  assert.equal(normalise("'s-Hertogenbosch"), 's hertogenbosch');
  assert.equal(normalise('Alphen a/d Rijn'), 'alphen aan den ryn');
  assert.equal(normalise('Den Haag Laan v NOI'), 'den haag laan van noi');
  assert.equal(normalise('Café  Één'), 'cafe een');
});

test('nearest stations from Utrecht Centraal', () => {
  const near = nearestStations(data.stations, 52.0894, 5.1101, 3);
  assert.equal(near[0].station.code, 'UT');
  assert.ok(near[0].metres < 300);
  assert.equal(near.length, 3);
  assert.ok(near[1].metres >= near[0].metres);
});

const lift = (id) => ({ id, name: id });
const status = {
  lifts: {
    'A-LIF-001': { s: 'available', since: '2026-11-10T08:00:00Z', upd: '2026-11-16T07:00:00Z' },
    'A-LIF-002': { s: 'notAvailable', since: '2026-11-16T06:42:00Z', upd: '2026-11-16T07:00:00Z' },
    'A-LIF-003': { s: 'notAvailable', since: '2026-11-02T09:00:00Z', until: '2026-11-15T16:00:00Z', upd: '2026-11-16T07:00:00Z' },
    'A-LIF-004': { s: 'available', upd: '2026-11-13T07:00:00Z' },
    'A-LIF-005': { s: 'available', upd: '2026-11-16T07:00:00Z', planned: { s: 'notAvailable', from: '2026-11-16T10:00:00Z', until: '2026-11-16T18:00:00Z' } },
    'A-LIF-006': { s: 'available', upd: '2026-11-16T07:00:00Z', planned: { s: 'notAvailable', from: '2027-01-12T05:00:00Z' } },
    'A-LIF-007': { s: 'unknown', desc: 'Object not monitored.', upd: '2026-11-16T07:00:00Z' },
  },
};

test('liftState', () => {
  assert.equal(liftState(lift('A-LIF-001'), status, now).level, 'ok');
  assert.equal(liftState(lift('A-LIF-002'), status, now).level, 'down');
  const overdue = liftState(lift('A-LIF-003'), status, now);
  assert.equal(overdue.level, 'down');
  assert.equal(overdue.overdue, true, 'end time passed but no "available" message yet');
  assert.equal(liftState(lift('A-LIF-004'), status, now).level, 'ok', 'no news for 3 days: keeps its last known status');
  const started = liftState(lift('A-LIF-005'), status, now);
  assert.equal(started.level, 'down', 'planned outage has begun');
  const later = liftState(lift('A-LIF-006'), status, now);
  assert.equal(later.level, 'ok');
  assert.equal(later.planned.level, 'down');
  assert.equal(liftState(lift('A-LIF-007'), status, now).level, 'unknown');
  const never = liftState(lift('A-LIF-999'), status, now);
  assert.deepEqual([never.level, never.noData], ['unknown', true], 'never heard of: unknown');
  assert.equal(liftState(lift('A-LIF-001'), null, now).level, 'unknown');
  assert.equal(liftState(lift('A-LIF-001'), { lifts: { 'A-LIF-001': { h: [] } } }, now).noData, true, 'only history left');
  assert.equal(liftState(lift('A-LIF-001'), { lifts: { 'A-LIF-001': { s: 'partiallyAvailable' } } }, now).level, 'down');
  assert.deepEqual(Object.keys(LEVEL_TEXT), ['ok', 'down', 'unknown', 'notyet'], 'four statuses');
});

test('stationSummary', () => {
  const st = (ids) => ({ code: 'A', name: 'A', lifts: ids.map(lift) });
  assert.equal(stationSummary(st(['A-LIF-001']), status, now).text, 'De lift werkt');
  const down = stationSummary(st(['A-LIF-001', 'A-LIF-002', 'A-LIF-007']), status, now);
  assert.equal(down.level, 'down');
  assert.equal(down.text, '1 van 3 liften buiten gebruik, 1 onbekend');
  assert.equal(stationSummary(st(['A-LIF-001', 'A-LIF-004']), status, now).text, 'Alle 2 liften werken',
    'old news is still the last known status');
  assert.equal(stationSummary(st(['A-LIF-001', 'A-LIF-999']), status, now).text, '1 van 2 liften werkt, 1 onbekend');
  assert.equal(stationSummary(st(['A-LIF-998', 'A-LIF-999']), status, now).text, 'Status van alle 2 liften onbekend');
});

test('display rules from the real data: short outages, flaky lifts, snapshot, new lifts', () => {
  // now = 2026-11-16T12:00:00Z
  const rules = {
    snapshot: '2026-11-16T03:02:00Z',
    lifts: {
      // Live outage of 20 minutes, no end date: still counts as working.
      'B-LIF-001': { s: 'notAvailable', since: '2026-11-16T11:40:00Z', upd: '2026-11-16T11:41:00Z' },
      // Live outage of 45 minutes: out of service.
      'B-LIF-002': { s: 'notAvailable', since: '2026-11-16T11:15:00Z', upd: '2026-11-16T11:16:00Z' },
      // Fresh outage with an expected end date: out of service straight away.
      'B-LIF-003': { s: 'notAvailable', since: '2026-11-16T11:50:00Z', until: '2026-11-20T16:00:00Z', upd: '2026-11-16T11:51:00Z' },
      // From last night's full status, no end date: out of service, reported at 03:02.
      'B-LIF-004': { s: 'notAvailable', since: '2026-11-16T02:58:00Z', upd: '2026-11-16T03:02:00Z', snap: true },
      // Flaky: three short outages today, working now.
      'B-LIF-005': {
        s: 'available', since: '2026-11-16T11:00:00Z', upd: '2026-11-16T11:01:00Z',
        h: [['2026-11-16T08:00:00Z', 'notAvailable'], ['2026-11-16T08:05:00Z', 'available'],
          ['2026-11-16T09:00:00Z', 'notAvailable'], ['2026-11-16T09:10:00Z', 'available'],
          ['2026-11-16T10:50:00Z', 'notAvailable'], ['2026-11-16T11:00:00Z', 'available'],
          ['2026-11-15T09:00:00Z', 'notAvailable']], // older than 24 hours: doesn't count
      },
      // Two short outages: not flaky yet.
      'B-LIF-006': { s: 'available', upd: '2026-11-16T11:01:00Z', h: [['2026-11-16T08:00:00Z', 'notAvailable'], ['2026-11-16T09:00:00Z', 'notAvailable']] },
      // A new lift that isn't in use yet.
      'B-LIF-007': { s: 'notAvailable', since: '2026-11-16T03:02:00Z', until: '2027-06-30T00:00:00Z', desc: 'Lift wordt in juni 2027 in gebruik genomen', upd: '2026-11-16T11:56:00Z' },
    },
  };
  const at = (id) => liftState(lift(id), rules, now);
  const short = at('B-LIF-001');
  assert.equal(short.level, 'ok', 'a 20-minute outage still counts as working');
  assert.equal(short.shortOutage.toISOString(), '2026-11-16T11:40:00.000Z');
  assert.equal(at('B-LIF-002').level, 'down', 'after 30 minutes it is out of service');
  assert.equal(at('B-LIF-003').level, 'down', 'an announced end date means a real outage');
  const snap = at('B-LIF-004');
  assert.equal(snap.level, 'down');
  assert.equal(snap.snapshot, true);
  assert.equal(at('B-LIF-005').level, 'ok');
  assert.equal(at('B-LIF-005').flaky, 3);
  assert.equal(at('B-LIF-006').flaky, undefined);
  assert.equal(at('B-LIF-007').level, 'notyet');
  assert.equal(LEVEL_TEXT.notyet, 'Nog niet in gebruik');

  const st = (ids) => ({ code: 'B', name: 'B', lifts: ids.map(lift) });
  assert.equal(stationSummary(st(['B-LIF-001', 'B-LIF-005', 'B-LIF-007']), rules, now).text, 'Alle 2 liften werken',
    'a lift not in use yet is left out; short outages and flaky lifts count as working');
  assert.equal(stationSummary(st(['B-LIF-002', 'B-LIF-005']), rules, now).text, '1 van 2 liften buiten gebruik');
  assert.equal(stationSummary(st(['B-LIF-007']), rules, now).text, 'Liften nog niet in gebruik');
});

test('favourites', () => {
  assert.deepEqual(toggleFav([], 'UT'), ['UT']);
  assert.deepEqual(toggleFav(['UT', 'ASD'], 'UT'), ['ASD']);
  assert.deepEqual(moveFav(['UT', 'ASD', 'HT'], 'HT', -1), ['UT', 'HT', 'ASD']);
  assert.deepEqual(moveFav(['UT', 'ASD'], 'UT', -1), ['UT', 'ASD']);
});

test('formatting', () => {
  assert.equal(formatDistance(234), '230 m');
  assert.equal(formatDistance(4321), '4,3 km');
  assert.equal(formatDistance(23456), '23 km');
  const local = new Date(2026, 10, 16, 14, 5);
  assert.equal(formatMoment(local, new Date(2026, 10, 16, 18, 0)), 'vandaag 14:05');
  assert.equal(formatMoment(local, new Date(2026, 10, 17, 9, 0)), 'gisteren 14:05');
});

test('mascot lines fit the top bar (≤ 60 characters) and every situation has lines', async () => {
  const MSG = await import('../js/messages.js');
  const situations = ['MINE_EMPTY', 'MINE_OK', 'MINE_DOWN', 'MINE_UNKNOWN', 'LOADING', 'FIND', 'SETTINGS', 'STALE', 'OFFLINE'];
  for (const key of situations) {
    assert.ok(Array.isArray(MSG[key]) && MSG[key].length, `no lines for ${key}`);
    for (const line of MSG[key]) assert.ok(line.length <= 60, `${line.length} chars: ${line}`);
  }
});
