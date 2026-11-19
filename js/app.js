/* global m */
import { DATA_URL } from './config.js';
import {
  LEVEL_TEXT, formatAge, formatDistance, formatMoment, liftState, moveFav, nearestStations,
  searchStations, sortLifts, stationSummary, toggleFav,
} from './lib.js';
import * as MSG from './messages.js';
import { Mascot, randomCoat } from './mascot.js';
import { load, loadSync, requestPersistence, save } from './storage.js';

// --- storage: IndexedDB with a localStorage mirror, see storage.js (same as Clawback) -----
const KEYS = { settings: 'lift.settings', favs: 'lift.favs' };
const dirty = { settings: false, favs: false }; // changed since start-up (don't overwrite with older loaded data)

// When the status file is older than this, say so prominently. The feed service (nas/)
// publishes at least every 10 minutes.
const STATUS_OLD_MIN = 120;
const REFRESH_AFTER_MIN = 5;
// The feed sends a heartbeat every 10 minutes. Silence for longer means the feed (or our
// connection to it) is down, even when the file itself is fresh.
const FEED_QUIET_H = 1;

// Where the data is, see config.js. Falls back to the copy next to the app when the bucket
// doesn't have the file (404), e.g. before the feed service has run.
function dataUrls(file) {
  return DATA_URL ? [DATA_URL + file, `data/${file}`] : [`data/${file}`];
}

/** JSON from the first of `urls` that works. `onlyAfter404`: only move on after a 404 (for the
 * status, where the bundled example must not replace a live status after a network error);
 * otherwise after any failure (the station list: the bundled copy is a fine fallback). */
function fetchFirst(urls, { onlyAfter404 = false } = {}) {
  const next = (err) => { if (urls.length > 1) return fetchFirst(urls.slice(1), { onlyAfter404 }); throw err; };
  return fetch(urls[0], { cache: 'no-cache' }).then((r) => {
    if (r.status === 404 || (!r.ok && !onlyAfter404)) return next(new Error(r.status));
    if (!r.ok) throw new Error(r.status);
    // Remember where it came from (shown under Instellingen → Gegevens).
    return r.json().then((d) => Object.defineProperty(d, 'from', { value: r.url || urls[0] }));
  }, (err) => (onlyAfter404 ? Promise.reject(err) : next(err)));
}

// --- state -------------------------------------------------------------------

const settings = { theme: 'system', ...loadSync(KEYS.settings, {}) };

const S = {
  stations: [],
  byCode: new Map(),
  loadError: '',
  status: null, // data/lifts.json
  statusError: '',
  statusFetchedAt: 0,
  statusLoading: false,

  favs: loadSync(KEYS.favs, []),
  tab: null, // set once we know whether there are favourites
  tabChosen: false, // the user picked a tab (so don't switch it for them)
  updateReady: false, // a newer version is stored; show the reload banner
  reorder: false,

  locating: false,
  locError: '',
  nearby: null, // [{ station, metres }]
  query: '',
  open: new Set(), // expanded stations (both tabs)
  online: navigator.onLine,

  coat: randomCoat(),
  catKey: '', // situation the current line was picked for
  catLine: '',
};

const saveFavs = () => { dirty.favs = true; return save(KEYS.favs, S.favs); };
const saveSettings = () => { dirty.settings = true; return save(KEYS.settings, settings); };

function applyTheme() {
  const t = settings.theme || 'system';
  if (t === 'system') delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = t;
}

// --- data ------------------------------------------------------------------------

function loadStatus() {
  if (S.statusLoading) return;
  S.statusLoading = true;
  fetchFirst(dataUrls('lifts.json'), { onlyAfter404: true })
    .then((d) => { S.status = d; S.statusError = ''; S.statusFetchedAt = Date.now(); })
    .catch(() => {
      S.statusError = S.status
        ? 'Verversen lukte niet. Je ziet de laatst opgehaalde status.'
        : 'De liftstatus kon niet geladen worden. Controleer je verbinding.';
    })
    .finally(() => { S.statusLoading = false; m.redraw(); });
}

function locate() {
  if (!('geolocation' in navigator)) {
    S.locError = 'Locatie wordt niet ondersteund door deze browser. Zoek het station op naam.';
    return;
  }
  S.locating = true;
  S.locError = '';
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      S.locating = false;
      S.nearby = nearestStations(S.stations, pos.coords.latitude, pos.coords.longitude);
      m.redraw();
    },
    (err) => {
      S.locating = false;
      S.locError = err.code === err.PERMISSION_DENIED
        ? 'Geen toegang tot je locatie. Zoek het station op naam, of sta locatie toe in je browser.'
        : 'Je locatie bepalen lukte niet. Probeer opnieuw of zoek op naam.';
      m.redraw();
    },
    { enableHighAccuracy: false, timeout: 15000, maximumAge: 60000 },
  );
}

function star(code) {
  S.favs = toggleFav(S.favs, code);
  saveFavs();
}

function openTab(id) {
  S.tab = id;
  S.reorder = false;
  window.scrollTo(0, 0);
  if (id === 'find' && !S.nearby && !S.locating && !S.locError) locate();
}

// --- small components ------------------------------------------------------------

const ICON = { ok: '✓', down: '✕', unknown: '?', notyet: '–' };

function StatusPill(level, text) {
  return m(`span.pill.${level}`, [m('span.pill-icon', { 'aria-hidden': 'true' }, ICON[level]), text]);
}

function describeUnknown(desc) {
  if (/not monitored/i.test(desc)) return 'Deze lift wordt niet op afstand bewaakt.';
  if (/no connection/i.test(desc)) return 'Er is tijdelijk geen verbinding met de lift.';
  return desc;
}

function LiftRow(lift) {
  const st = liftState(lift, S.status);
  const lines = [];
  if (st.level === 'down') {
    if (st.since) lines.push(`Sinds ${formatMoment(st.since)}`);
    if (st.until) {
      lines.push(st.overdue
        ? `Zou tot ${formatMoment(st.until)} duren, maar is nog niet als hersteld gemeld`
        : `Verwacht tot ${formatMoment(st.until)}`);
    }
    if (st.snapshot && !st.until) {
      lines.push(`Gemeld in de volledige update van ${formatMoment(st.updated)}; sindsdien geen nieuws over deze lift.`);
    }
  } else if (st.level === 'notyet') {
    lines.push(st.desc);
  } else if (st.level === 'ok' && st.shortOutage) {
    lines.push(`Storing gemeld om ${formatMoment(st.shortOutage).replace(/^vandaag /, '')}. Meestal werkt de lift binnen een half uur weer.`);
  } else if (st.level === 'unknown' && st.desc) {
    lines.push(describeUnknown(st.desc));
  } else if (st.noData && lift.monitored === false) {
    lines.push('Deze lift wordt niet op afstand bewaakt, dus de status is nooit bekend.');
  } else if (st.noData) {
    lines.push(lift.manager && lift.manager !== '?'
      ? `Beheer: ${lift.manager}. Van deze lift komt (nog) geen status binnen.`
      : 'Van deze lift komt (nog) geen status binnen.');
  }
  if (st.planned) {
    const until = st.planned.until ? ` tot ${formatMoment(st.planned.until)}` : '';
    lines.push(`Gepland buiten gebruik: ${formatMoment(st.planned.from)}${until}`);
  }
  if (st.flaky) lines.push(`Had het afgelopen etmaal ${st.flaky} korte storingen, maar werkt meestal.`);

  const map = lift.lat
    ? `https://www.openstreetmap.org/?mlat=${lift.lat}&mlon=${lift.lon}#map=19/${lift.lat}/${lift.lon}`
    : null;
  return m('li.lift', { class: st.level }, [
    m('div.lift-head', [
      m('span.lift-name', [lift.name, lift.tracks && !/spoor/i.test(lift.name) && m('span.muted', ` (spoor ${lift.tracks})`)]),
      StatusPill(st.level, LEVEL_TEXT[st.level]),
    ]),
    lines.map((l) => m('p.lift-note', l)),
    m('p.lift-meta', [
      m('span', lift.id),
      map && [' · ', m('a', { href: map, target: '_blank', rel: 'noopener' }, 'waar is deze lift?')],
    ]),
  ]);
}

function StationCard(station, { metres, expanded, toggle, reorder } = {}) {
  const sum = stationSummary(station, S.status);
  const fav = S.favs.includes(station.code);
  const headInner = [
    m('span.station-name', station.name),
    m('span.station-sub', [
      StatusPill(sum.level, sum.text),
      metres != null && m('span.muted', formatDistance(metres)),
    ]),
  ];
  return m('section.card.station', { key: station.code, class: sum.level }, [
    m('div.station-head', [
      toggle
        ? m('button.station-toggle', {
          type: 'button', 'aria-expanded': String(!!expanded), onclick: toggle,
        }, [m('span.station-main', headInner), m('span.chev', { 'aria-hidden': 'true' }, expanded ? '▴' : '▾')])
        : m('div.station-main', headInner),
      reorder
        ? m('span.reorder', [
          m('button.icon-btn', { type: 'button', 'aria-label': `${station.name} omhoog`, onclick: () => { S.favs = moveFav(S.favs, station.code, -1); saveFavs(); } }, '↑'),
          m('button.icon-btn', { type: 'button', 'aria-label': `${station.name} omlaag`, onclick: () => { S.favs = moveFav(S.favs, station.code, 1); saveFavs(); } }, '↓'),
        ])
        : m('button.icon-btn.star', {
          type: 'button', 'aria-pressed': String(fav),
          'aria-label': fav ? `${station.name} uit Mijn stations halen` : `${station.name} bewaren in Mijn stations`,
          title: fav ? 'Uit Mijn stations' : 'Bewaar in Mijn stations',
          onclick: () => star(station.code),
        }, fav ? '★' : '☆'),
    ]),
    expanded === 'all' && m('ul.lifts', sortLifts(station.lifts).map(LiftRow)),
  ]);
}

function statusAge() {
  const st = S.status;
  if (!st || !st.generated) return {};
  const fileOld = (Date.now() - new Date(st.generated)) / 60000 > STATUS_OLD_MIN;
  // The feed sends a heartbeat every 10 minutes. If even that stopped, our file is fresh but
  // its contents aren't.
  const heard = [st.feed, st.heartbeat].filter(Boolean).map((s) => new Date(s)).sort((a, b) => b - a)[0];
  const feedQuiet = st.source !== 'example' && (!heard || Date.now() - heard > FEED_QUIET_H * 3600e3);
  return { fileOld, feedQuiet, heard };
}

/** Which list of lines fits the current situation. */
function catSituation() {
  if (!S.online) return 'OFFLINE';
  const age = statusAge();
  if (age.fileOld || age.feedQuiet) return 'STALE';
  if (S.tab === 'find') return 'FIND';
  if (S.tab === 'settings') return 'SETTINGS';
  const favs = S.favs.map((c) => S.byCode.get(c)).filter(Boolean);
  if (!favs.length) return 'MINE_EMPTY';
  if (!S.status) return 'LOADING';
  const levels = favs.map((s) => stationSummary(s, S.status).level);
  if (levels.includes('down')) return 'MINE_DOWN';
  if (levels.includes('unknown')) return 'MINE_UNKNOWN';
  return 'MINE_OK';
}

/** The cat's line: picked anew only when the situation (or tab) changes. */
function catLine() {
  const key = catSituation();
  if (key !== S.catKey) {
    S.catKey = key;
    S.catLine = MSG.pick(MSG[key]);
  }
  return S.catLine;
}

function StatusBanner() {
  const st = S.status;
  const out = [];
  if (st && st.source === 'example') {
    out.push(m('div.banner.example', { role: 'note' }, [
      m('strong', 'Voorbeelddata. '),
      'De echte liftstatus van ProRail is nog niet gekoppeld. Wat je hieronder over storingen ziet, is verzonnen om de app te testen.',
    ]));
  }
  if (S.statusError) out.push(m('div.banner.warn', S.statusError));
  const age = statusAge();
  if (age.fileOld) {
    out.push(m('div.banner.warn', `Let op: de liftstatus is ${formatAge(new Date(st.generated))} voor het laatst bijgewerkt.`));
  }
  if (age.feedQuiet) {
    out.push(m('div.banner.warn', age.heard
      ? `Let op: ProRail heeft sinds ${formatMoment(age.heard)} niets meer doorgegeven. De status kan verouderd zijn.`
      : 'Let op: er is nog geen liftstatus van ProRail ontvangen.'));
  }
  if (!S.online) out.push(m('div.banner', 'Je bent offline. Je ziet de laatst opgehaalde status.'));
  return out;
}

function UpdatedLine() {
  const st = S.status;
  if (!st) return null;
  return m('p.updated', [
    `Status bijgewerkt ${formatAge(new Date(st.generated))}. `,
    m('button.link', { type: 'button', onclick: loadStatus, disabled: S.statusLoading }, S.statusLoading ? 'Verversen…' : 'Ververs'),
  ]);
}

// --- tabs --------------------------------------------------------------------------

/** Tap a station to show or hide its lifts; same in both lists (and remembered between them). */
function toggleOpen(code) {
  return () => { if (!S.open.delete(code)) S.open.add(code); };
}

function MineTab() {
  const list = S.favs.map((c) => S.byCode.get(c)).filter(Boolean);
  if (!list.length) {
    return m('section.card', [
      m('h2', 'Nog geen stations bewaard'),
      m('p', ['Tik op de ster ', m('span', { 'aria-hidden': 'true' }, '☆'), ' bij een station om het hier te bewaren. Dan zie je in één oogopslag of de liften werken.']),
      m('button.primary', { type: 'button', onclick: () => openTab('find') }, 'Zoek een station'),
    ]);
  }
  return [
    UpdatedLine(),
    // Always in the user's own order (not sorted by outages), so each station keeps its place.
    list.map((s) => StationCard(s, { expanded: S.open.has(s.code) && 'all', toggle: toggleOpen(s.code), reorder: S.reorder })),
    list.length > 1 && m('button.link', { type: 'button', onclick: () => { S.reorder = !S.reorder; } },
      S.reorder ? 'Klaar met volgorde' : 'Volgorde wijzigen'),
  ];
}

function FindTab() {
  const results = S.query.trim() ? searchStations(S.stations, S.query) : null;
  const card = (station, metres) => StationCard(station, {
    metres,
    expanded: S.open.has(station.code) && 'all',
    toggle: toggleOpen(station.code),
  });
  return [
    m('section.card', [
      m('label.field', [
        m('span', 'Zoek een station'),
        m('input[type=search]', {
          value: S.query, placeholder: 'naam of code, bijv. Utrecht of UT', autocomplete: 'off',
          oninput: (e) => { S.query = e.target.value; },
        }),
      ]),
      !results && m('button.secondary', { type: 'button', onclick: locate, disabled: S.locating },
        S.locating ? 'Locatie zoeken…' : S.nearby ? 'Opnieuw zoeken in de buurt' : 'Stations in de buurt'),
      S.locError && m('p.warn', S.locError),
    ]),
    results
      ? (results.length
        ? results.map((s) => card(s))
        : m('section.card', [
          m('p', 'Geen station gevonden.'),
          m('p.hint', 'Staat je station er niet bij? Dan heeft het volgens ProRail geen liften, bijvoorbeeld omdat de perrons gelijkvloers of via een hellingbaan bereikbaar zijn.'),
        ]))
      : S.nearby && [
        m('h2.section-title', 'In de buurt'),
        S.nearby.map(({ station, metres }) => card(station, metres)),
      ],
    UpdatedLine(),
  ];
}

/** "uplift-data.timovdk.nl" for the bucket, "deze website" for the copy next to the app. */
function sourceName(url) {
  if (!url) return 'onbekend';
  const u = new URL(url, location.href);
  return u.origin === location.origin ? 'deze website (meegeleverde kopie)' : u.host;
}

function SettingsTab() {
  const st = S.status;
  const ext = (href, text) => m('a', { href, target: '_blank', rel: 'noopener' }, text);
  const when = (iso) => (iso ? formatMoment(new Date(iso)) : 'onbekend');
  const counted = st && st.lifts ? Object.keys(st.lifts).length : 0;
  const lifts = S.stations.reduce((n, s) => n + s.lifts.length, 0);
  return [
    m('section.card', [
      m('h2#theme-label', 'Weergave'),
      m('div.segmented', { role: 'radiogroup', 'aria-labelledby': 'theme-label' },
        [['system', 'Systeem'], ['light', 'Licht'], ['dark', 'Donker']].map(([value, label]) => {
          const on = (settings.theme || 'system') === value;
          return m('button', {
            type: 'button', role: 'radio', 'aria-checked': String(on), class: on ? 'active' : '',
            onclick: () => { settings.theme = value; saveSettings(); applyTheme(); },
          }, label);
        })),
    ]),
    m('section.card', [
      m('h2', 'Hoe betrouwbaar is dit?'),
      m('p', 'De app toont wat ProRail via open data over de liften meldt. Dat is niet altijd wat de NS-app of NS Reisassistentie zegt: die kunnen andere of nieuwere informatie hebben.'),
      m('p', 'Een lift die hier als werkend staat, kan toch stuk zijn, en andersom. Ben je afhankelijk van de lift, boek dan ook NS Reisassistentie of check het ter plekke.'),
      m('p.hint', 'Niet alle liften worden op afstand bewaakt. Liften die NS of de gemeente beheert, missen vaak in de data.'),
    ]),
    m('section.card', [
      m('h2', 'Gegevens'),
      m('p', `${S.stations.length} stations met samen ${lifts} liften.`),
      st && [
        m('p', [`Liftstatus bijgewerkt: ${when(st.generated)}`, st.source === 'example' && m('strong', ' (voorbeelddata)')]),
        m('p.hint', `Laatste bericht van ProRail: ${when(st.feed)}. Status bekend van ${counted} van de ${lifts} liften.`),
        m('p.hint', `Opgehaald van: ${sourceName(st.from)}.`),
      ],
    ]),
    m('section.card.about', [
      m('h2', 'Over deze app'),
      m('p', [m('strong', 'Geen app van NS of ProRail.'), ' Dit is een onafhankelijk hulpmiddel, niet van of namens NS, ProRail of DOVA. Het laat de open data van ProRail over liften overzichtelijk zien.']),
      m('p', 'Gebruik op eigen risico: er is geen garantie dat de informatie klopt of actueel is. Zie ook “Hoe betrouwbaar is dit?” hierboven.'),
      m('h3', 'Privacy'),
      m('p', 'De app verstuurt zelf niets over jou. Je bewaarde stations en instellingen blijven op dit apparaat. Je locatie wordt alleen op dit apparaat gebruikt, om stations in de buurt te vinden.'),
      m('p.hint', 'De app en de liftgegevens worden geleverd door GitHub Pages en Cloudflare. Zoals bij elke website zien die je IP-adres. Er zijn geen cookies, analytics of advertenties.'),
      m('h3', 'Bronnen'),
      m('p', ['Stations, liften en liftstatus: open data (CC0) van ProRail en DOVA, via het NDOV Loket van ', ext('https://govi.nu', 'GOVI'), ' en Stichting OpenGeo. Zonder garantie dat alles klopt.']),
      m('p', ['De links “waar is deze lift?” openen ', ext('https://www.openstreetmap.org/copyright', 'OpenStreetMap'), ' (© OpenStreetMap-bijdragers).']),
      m('h3', 'Software'),
      m('p', ['Uplift is open source onder de ', ext('LICENSE', 'EUPL-1.2'), ': je mag de code gebruiken en aanpassen, zolang aangepaste versies ook open source blijven. Gemaakt met ', ext('https://mithril.js.org/', 'Mithril.js'), ' (© Leo Horie, ', ext('vendor/mithril.LICENSE', 'MIT-licentie'), '). De kat komt uit ', ext('https://timovdk.nl/clawback/', 'Clawback'), '.']),
    ]),
  ];
}

function UpdateBanner() {
  if (!S.updateReady) return null;
  return m('div.update-banner', { role: 'status' }, [
    m('p', m('strong', 'Er is een nieuwe versie van Uplift.')),
    m('div.update-actions', [
      m('button.primary.small', { type: 'button', onclick: () => window.location.reload() }, 'Herladen'),
      m('button.link', { type: 'button', onclick: () => { S.updateReady = false; } }, 'Later'),
    ]),
  ]);
}

const TABS = [['mine', 'Mijn stations'], ['find', 'Zoeken'], ['settings', 'Instellingen']];

const App = {
  view() {
    let body;
    if (S.loadError) body = m('section.card', m('p.warn', S.loadError));
    else if (!S.stations.length) body = m('section.card', m('p', 'Laden…'));
    else body = [StatusBanner(), { mine: MineTab, find: FindTab, settings: SettingsTab }[S.tab]()];
    const downFavs = S.favs.filter((c) => {
      const s = S.byCode.get(c);
      return s && S.status && stationSummary(s, S.status).level === 'down';
    }).length;
    return [
      m('header', [
        S.stations.length
          ? [m('h1.visually-hidden', 'Uplift'), m('div', { 'aria-live': 'polite' }, Mascot(catLine(), S.coat))]
          : m('h1', 'Uplift'),
        m('nav', { 'aria-label': 'Onderdelen' }, TABS.map(([id, label]) => m('button', {
          type: 'button', class: S.tab === id ? 'active' : '', 'aria-current': S.tab === id ? 'page' : null,
          onclick: () => { S.tabChosen = true; openTab(id); },
        }, [label, id === 'mine' && downFavs > 0 && m('span.count', { 'aria-label': ` (${downFavs} met storing)` }, downFavs)]))),
      ]),
      m('main', body),
      UpdateBanner(),
    ];
  },
};

// --- start ---------------------------------------------------------------------------

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible' && Date.now() - S.statusFetchedAt > REFRESH_AFTER_MIN * 60000) loadStatus();
});
for (const ev of ['online', 'offline']) {
  window.addEventListener(ev, () => { S.online = navigator.onLine; if (S.online) loadStatus(); m.redraw(); });
}
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('sw.js').catch(() => {});
  // Update banner: the service worker found newer files, or a new service worker took over.
  const hadController = !!navigator.serviceWorker.controller;
  navigator.serviceWorker.addEventListener('message', (e) => {
    if (e.data && e.data.type === 'update-available') { S.updateReady = true; m.redraw(); }
  });
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (hadController) { S.updateReady = true; m.redraw(); }
  });
  navigator.serviceWorker.startMessages();
}

applyTheme();
m.mount(document.getElementById('app'), App);

// The synchronous mirror is shown first; then switch to the durable copy (unless already edited).
requestPersistence();
Promise.all([load(KEYS.settings, null), load(KEYS.favs, null)]).then(([saved, favs]) => {
  if (saved && !dirty.settings) { Object.assign(settings, saved); applyTheme(); }
  if (Array.isArray(favs) && !dirty.favs) {
    S.favs = favs;
    // The mirror was empty but there are starred stations after all: show them.
    if (favs.length && S.stations.length && S.tab === 'find' && !S.tabChosen) openTab('mine');
  }
  m.redraw();
});

fetchFirst(dataUrls('stations.json'))
  .then((d) => {
    S.stations = d.stations;
    S.byCode = new Map(d.stations.map((s) => [s.code, s]));
    // Stations that disappeared from the data stay starred but aren't shown (see MineTab).
    openTab(S.favs.some((c) => S.byCode.has(c)) ? 'mine' : 'find');
  })
  .catch(() => { S.loadError = 'De stations konden niet geladen worden. Controleer je verbinding en herlaad de pagina.'; })
  .finally(() => m.redraw());
loadStatus();
