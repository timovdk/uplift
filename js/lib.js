// Pure helpers (no DOM), so they can be tested with node: `npm test`.

// --- places --------------------------------------------------------------------

/** Distance in metres (equirectangular; plenty accurate within the Netherlands). */
export function distance(lat1, lon1, lat2, lon2) {
  const rad = Math.PI / 180;
  const x = (lon2 - lon1) * rad * Math.cos(((lat1 + lat2) / 2) * rad);
  const y = (lat2 - lat1) * rad;
  return Math.sqrt(x * x + y * y) * 6371000;
}

/** Nearest stations with lifts. Stations are sparse, so no distance cut-off: just the closest few. */
export function nearestStations(stations, lat, lon, limit = 6) {
  return stations
    .map((station) => ({ station, metres: distance(lat, lon, station.lat, station.lon) }))
    .sort((a, b) => a.metres - b.metres)
    .slice(0, limit);
}

// --- station search ------------------------------------------------------------
//
// Dutch station names are full of punctuation and abbreviations ("'s-Hertogenbosch",
// "Alphen a/d Rijn", "Den Haag Laan v NOI"), and people use other names than the official ones
// ("Den Bosch", "Utrecht CS", "A'dam"). Names and queries go through the same normalisation,
// and every station gets a few extra searchable names.

/**
 * Lowercase, no accents, apostrophes and punctuation, abbreviations written out, and "ij" as "y"
 * (so Ypenburg/IJpenburg and IJssel/Yssel match). Returns words joined by single spaces.
 */
export function normalise(s) {
  return ` ${s} `
    .normalize('NFD').replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/['’`´]/g, '')               // 's-Hertogenbosch -> s-hertogenbosch, A'dam -> adam
    .replace(/\ba\s*\/\s*d\b/g, ' aan den ') // a/d -> aan den
    .replace(/[^a-z0-9]+/g, ' ')         // dashes, slashes, dots, brackets -> space
    .replace(/ij/g, 'y')
    .replace(/ v (?=\S)/g, ' van ')      // "Laan v NOI" -> "laan van noi"
    .trim()
    .replace(/\s+/g, ' ');
}

// Words people type that mean something else in the official names.
// Only whole words are replaced; a word that's still being typed ("st" for Steenwijk) is left alone.
const QUERY_WORDS = {
  cs: 'centraal', adam: 'amsterdam', rdam: 'rotterdam', dh: 'den haag',
  sgravenhage: 'den haag', hague: 'den haag', denbosch: 's hertogenbosch', station: '',
};
// Only after the first word: "Utrecht C" means Centraal, but "C" alone is the start of Castricum.
const LATER_WORDS = { c: 'centraal', the: '' };

// Other names for (parts of) station names: [start of official name, alternative].
const NAME_ALIASES = [
  ['s hertogenbosch', 'den bosch'],
  ['s hertogenbosch', 'hertogenbosch'],
  ['s hertogenbosch', 'bosch'],
  ['den haag', 's gravenhage'],
  ['den haag', 'gravenhage'],
  ['den haag', 'the hague'],
  ['den haag hs', 'den haag hollands spoor'],
  ['den haag laan van noi', 'den haag laan van nieuw oost indie'],
  ['amsterdam bijlmer arena', 'amsterdam arena'],
  ['amsterdam bijlmer arena', 'bijlmer'],
];

const index = new WeakMap();

/** All searchable forms of a station: official name, aliases, compact (no spaces) versions. */
function searchForms(station) {
  let f = index.get(station);
  if (f) return f;
  const name = normalise(station.name);
  const names = [name];
  for (const [start, alt] of NAME_ALIASES) {
    const n = normalise(start);
    if (name === n || name.startsWith(`${n} `)) names.push(normalise(alt + name.slice(n.length)));
  }
  f = {
    names,
    words: [...new Set(names.flatMap((n) => n.split(' ')))],
    nameWords: name.split(' '), // official name only: these matches beat alias matches
    compact: names.map((n) => n.replace(/ /g, '')),
    code: station.code.toLowerCase(),
  };
  index.set(station, f);
  return f;
}

function queryWords(query) {
  const typed = normalise(query).split(' ').filter(Boolean);
  const out = typed.flatMap((w, i) => {
    let r = w;
    if (Object.hasOwn(QUERY_WORDS, w)) r = QUERY_WORDS[w];
    else if (i > 0 && Object.hasOwn(LATER_WORDS, w)) r = LATER_WORDS[w];
    return r ? r.split(' ') : [];
  });
  // "the hague": "the" first is dropped by the alias anyway; keep the query non-empty.
  return out.length ? out : typed;
}

/** At most one letter added, dropped, changed, or two letters swapped. */
function nearlyEqual(a, b) {
  if (a === b) return true;
  if (Math.abs(a.length - b.length) > 1) return false;
  let i = 0;
  while (i < a.length && a[i] === b[i]) i++;
  if (a.length === b.length) {
    if (a.slice(i + 1) === b.slice(i + 1)) return true;                 // changed
    return a[i] === b[i + 1] && a[i + 1] === b[i] && a.slice(i + 2) === b.slice(i + 2); // swapped
  }
  const [long, short] = a.length > b.length ? [a, b] : [b, a];
  return long.slice(i + 1) === short.slice(i);                          // added / dropped
}

/** A typed word matches a name word when it's a prefix of it, or (4+ letters) one typo away from the start of it. */
function wordMatches(typed, word, fuzzy) {
  if (word.startsWith(typed)) return true;
  if (!fuzzy || typed.length < 4) return false;
  return nearlyEqual(typed, word.slice(0, typed.length)) || nearlyEqual(typed, word.slice(0, typed.length + 1))
    || nearlyEqual(typed, word.slice(0, typed.length - 1));
}

/**
 * Search by name, other name or station code. Best matches first:
 * 0 the station code, 1 a name starting with the query, 2 every typed word starts a word in the
 * official name (2.5: in another name),
 * 3 the query without spaces is in a name without spaces ("bijlmerarena"), 4 same as 2 with one typo per word.
 */
export function searchStations(stations, query, limit = 25) {
  const words = queryWords(query);
  if (!words.length) return [];
  const q = words.join(' ');
  const qCompact = words.join('');
  const raw = query.trim().toLowerCase();
  const scored = [];
  for (const s of stations) {
    const f = searchForms(s);
    let score = -1;
    if (f.code === raw) score = 0;
    else if (f.names.some((n) => n.startsWith(q))) score = 1;
    else if (words.every((w) => f.nameWords.some((x) => wordMatches(w, x, false)))) score = 2;
    else if (words.every((w) => f.words.some((x) => wordMatches(w, x, false)))) score = 2.5;
    else if (qCompact.length >= 3 && f.compact.some((c) => c.includes(qCompact))) score = 3;
    else if (words.every((w) => f.words.some((x) => wordMatches(w, x, true)))) score = 4;
    if (score >= 0) scored.push({ s, score });
  }
  // Typo matches only when nothing matched properly ("adam zuid" shouldn't also give Muiderpoort).
  const best = scored.some((x) => x.score < 4) ? scored.filter((x) => x.score < 4) : scored;
  // Equally good: busier stations first (ProRail's station class, then number of lifts), so
  // "zuid" starts with Amsterdam Zuid and "utrecht" with Utrecht Centraal.
  const size = (st) => st.size || 2;
  return best
    .sort((a, b) => a.score - b.score || size(b.s) - size(a.s) || b.s.lifts.length - a.s.lifts.length
      || a.s.name.localeCompare(b.s.name, 'nl'))
    .slice(0, limit)
    .map((x) => x.s);
}

export function formatDistance(metres) {
  if (metres < 1000) return `${Math.round(metres / 10) * 10} m`;
  return `${(metres / 1000).toFixed(metres < 10000 ? 1 : 0).replace('.', ',')} km`;
}

// --- lift status -----------------------------------------------------------------

/** A lift's own status counts as stale when nothing was heard about it for this long. */
export const STALE_HOURS = 36;

/**
 * What we can say about one lift right now.
 * -> { level: 'ok' | 'down' | 'partial' | 'unknown' | 'none',
 *      since?, until?, overdue?, desc?, planned?, stale?, updated?, inferred? }
 * 'none' = no status received at all for this lift.
 * inferred = 'ok' only because no outage was reported (see build_lifts.py), not an explicit "available".
 */
export function liftState(lift, status, now = new Date()) {
  const e = status && status.lifts && status.lifts[lift.id];
  if (!e) return { level: 'none' };
  const t = (s) => (s ? new Date(s) : null);
  const out = { updated: t(e.upd), desc: e.desc || '' };
  let s = e.s;
  let since = t(e.since);
  let until = t(e.until);
  const p = e.planned;
  if (p) {
    const from = t(p.from);
    const to = t(p.until);
    if (from <= now && (!to || to > now)) {
      // The announced outage has started since the data was built.
      s = p.s; since = from; until = to;
    } else if (from > now) {
      out.planned = { level: levelOf(p.s), from, until: to };
    }
  }
  out.level = levelOf(s);
  if (e.inferred && s === e.s && out.level === 'ok') out.inferred = true;
  if (since) out.since = since;
  if (until) {
    out.until = until;
    if (out.level === 'down' && until < now) out.overdue = true;
  }
  if (out.updated && now - out.updated > STALE_HOURS * 3600e3) out.stale = true;
  return out;
}

function levelOf(s) {
  switch (s) {
    case 'available': case 'added': return 'ok';
    case 'notAvailable': case 'removed': return 'down';
    case 'partiallyAvailable': return 'partial';
    case 'unknown': case 'neverMonitored': return 'unknown';
    default: return 'none';
  }
}

export const LEVEL_TEXT = {
  ok: 'Werkt',
  down: 'Buiten gebruik',
  partial: 'Werkt beperkt',
  unknown: 'Status onbekend',
  none: 'Geen gegevens',
};

/**
 * One line about a whole station, worst news first.
 * -> { level, text, down, unknown, total }
 */
export function stationSummary(station, status, now = new Date()) {
  const states = station.lifts.map((l) => liftState(l, status, now));
  const total = states.length;
  const down = states.filter((s) => s.level === 'down' || s.level === 'partial').length;
  const unknown = states.filter((s) => s.level === 'unknown' || s.level === 'none' || s.stale).length;
  const liften = (n) => (n === 1 ? 'lift' : 'liften');
  if (down) {
    const extra = unknown ? `, ${unknown} onbekend` : '';
    return { level: 'down', down, unknown, total, text: `${down} van ${total} ${liften(total)} buiten gebruik${extra}` };
  }
  if (unknown === total) {
    return { level: 'unknown', down, unknown, total, text: total === 1 ? 'Status van de lift onbekend' : `Status van alle ${total} liften onbekend` };
  }
  const quiet = states.some((s) => s.inferred); // "no outage reported" rather than "confirmed working"
  if (unknown) {
    const ok = quiet ? 'zonder storing' : (total - unknown === 1 ? 'werkt' : 'werken');
    return { level: 'unknown', down, unknown, total, text: `${total - unknown} van ${total} liften ${ok}, ${unknown} onbekend` };
  }
  if (quiet) {
    return { level: 'ok', down, unknown, total, text: total === 1 ? 'Geen storing gemeld' : `Geen storingen gemeld (${total} liften)` };
  }
  return { level: 'ok', down, unknown, total, text: total === 1 ? 'De lift werkt' : `Alle ${total} liften werken` };
}

// --- dates -------------------------------------------------------------------

const pad = (n) => String(n).padStart(2, '0');

function sameDay(a, b) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

/** 'vandaag 14:05', 'gisteren 09:30', 'ma 9 nov 14:05', '12 jan 2027 06:00' */
export function formatMoment(d, now = new Date()) {
  const time = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  if (sameDay(d, now)) return `vandaag ${time}`;
  const y = new Date(now); y.setDate(y.getDate() - 1);
  if (sameDay(d, y)) return `gisteren ${time}`;
  const tm = new Date(now); tm.setDate(tm.getDate() + 1);
  if (sameDay(d, tm)) return `morgen ${time}`;
  const opts = { day: 'numeric', month: 'short' };
  if (Math.abs(d - now) < 6 * 864e5) opts.weekday = 'short';
  if (d.getFullYear() !== now.getFullYear()) opts.year = 'numeric';
  return `${d.toLocaleDateString('nl-NL', opts)} ${time}`;
}

/** '3 min geleden', '2 uur geleden', '4 dagen geleden' */
export function formatAge(d, now = new Date()) {
  const min = Math.max(0, Math.round((now - d) / 60000));
  if (min < 1) return 'zojuist';
  if (min < 60) return `${min} min geleden`;
  const h = Math.round(min / 60);
  if (h < 48) return `${h} uur geleden`;
  return `${Math.round(h / 24)} dagen geleden`;
}

// --- favourites ---------------------------------------------------------------

/** Toggle a station code in the list (kept in the order the user starred them). */
export function toggleFav(favs, code) {
  return favs.includes(code) ? favs.filter((c) => c !== code) : [...favs, code];
}

/** Move a code one place up (-1) or down (+1). */
export function moveFav(favs, code, dir) {
  const i = favs.indexOf(code);
  const j = i + dir;
  if (i < 0 || j < 0 || j >= favs.length) return favs;
  const out = [...favs];
  [out[i], out[j]] = [out[j], out[i]];
  return out;
}
