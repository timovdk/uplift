# Uplift

A small web app (PWA) that shows whether the lifts at Dutch train stations work, so you know
before you leave whether you can get to the platform. Star the stations you use, and "Mijn
stations" shows at a glance which lifts there are out of service. Or find stations near you, or
search by name: "den bosch", "adam zuid" and "s-hertogenbosch" all work.

The status comes from ProRail's live lift feed, through the open data of the NDOV loket.
Everything you save stays on your phone. A cat keeps you company.

> Uplift is an independent tool. It is not made by or on behalf of NS or ProRail.

**Status:** the live feed works (tested Nov 2026). The feed service that publishes it (`nas/`)
isn't running yet; until it is, the app runs on a made-up example status and says so in a banner
on every screen. See "Going live" below.

## How reliable is it?

Uplift shows what ProRail publishes as open data. That is not always what the NS app or NS
Reisassistentie says: NS seems to have other or newer information. Some lifts aren't monitored
at all. The app says this in Instellingen and shows "Onbekend" instead of guessing.

## Where the data comes from

All CC0 open data from the NDOV loket of Stichting OpenGeo / [GOVI](https://govi.nu):

| File | What | Source | Refreshed |
|---|---|---|---|
| `data/stations.json` | 174 stations, 464 lifts, location, tracks, monitored or not, station size | DOVA's [NeTEx-EPIAP](https://data.ndovloket.nl/netex/epiap/) + ProRail's [lift register and station classes](https://data.ndovloket.nl/prorail/) | monthly by the feed service, in the R2 bucket (checked first); the copy in git is the fallback |
| `data/lifts.json` | latest status per lift | SIRI-FM (ProRail → DOVA), ZeroMQ `tcp://pubsub.besteffort.ndovloket.nl:7666`, envelope `/DOVA/ServiceDelivery` | every minute (when changed) by the feed service, in a Cloudflare R2 bucket |

The feed calls lifts `NL:CHB:LiftEquipment:8400058_013`; EPIAP links that id to the public code
`ASD-LIF-013` and the station. (The other loket, Reisinformatiegroep/9292, also has SIRI FM, but
charges €1000 a year and pushes to your own server with a fixed IP. Not needed.)

### How the status stays complete

What we saw on the stream (Nov 2026, BISON TMI9 Dutch SIRI-FM profile), and what the NAS data
of 17–19 Nov showed:

- **Now and then, DOVA sends the status of all lifts** (~440) in one message: on 18 Nov at
  03:02 UTC, but not the night after (asked GOVI why). It's the only complete picture: 53 of the
  61 outages in it had never come by as a change.
- **In between, it sends changes live**: mostly short blips (of the 58 outages that ended, 42
  were over within 30 minutes), but also repairs of outages we only knew from the full status
  (4 of them within a day).
- A heartbeat every 10 minutes; ASD-LIF-013 is repeated every 5 minutes. The statuses seen are
  `available`, `notAvailable` and `unknown` (never `partiallyAvailable`).

So the feed service (`nas/`, a small Docker container on a home server) keeps one connection
open and treats **a full status as the new baseline**: every lift gets its status from it,
lifts that aren't in it lose theirs. Live changes then build on it until the next one
(`scripts/build_lifts.py`). In between, **a lift keeps its last known status**: changes come
live, so silence mostly means nothing changed. It keeps each lift's changes of the last 24 hours, archives every
raw message, and uploads `lifts.json` to a public Cloudflare R2 bucket on our own domain: at
most every minute when something changed, at least every 10 minutes. At start-up it replays the
archive from the latest full status, so a restart or an update starts from the same baseline.
Once a month it also rebuilds the station list (`scripts/build_stations.py`), checks it, and
uploads it next to the status. The app reads both from the bucket (`js/config.js`). If the
heartbeat has been missing for over an hour, or the status file is over 2 hours old, the app
shows a warning.

### How the status is shown

Four statuses, **Werkt**, **Buiten gebruik**, **Onbekend** and **Nog niet in gebruik**; the rest
goes in a note under the lift. Rules in `js/lib.js` (`liftState`), chosen from the data:

| Situation | Shown as |
|---|---|
| Out of service, "wordt … in gebruik genomen" | grey **Nog niet in gebruik**; not counted for the station |
| Out of service with an expected end date | red **Buiten gebruik**, "verwacht tot …" |
| Out of service according to a full status, no end date | red, "gemeld in de volledige update van 18 nov 04:02; sindsdien geen nieuws" |
| Live outage, less than 30 minutes old (no end date) | **Werkt**, "storing gemeld om …" |
| Live outage of 30 minutes or more | red **Buiten gebruik** |
| 3+ outages in the last 24 hours, working now | **Werkt**, "had het afgelopen etmaal … korte storingen" |
| `unknown`, or never heard of | grey **Onbekend**, with the reason if known |
| No news for a long time | the last known status (no automatic "unknown") |

## Going live

1. Register (free) at [govi.nu/aanmelden](https://govi.nu/aanmelden) and sign the user agreement.
2. Set up the R2 bucket and run the feed service, see [nas/README.md](nas/README.md).
3. Put the bucket's address in `js/config.js` (`DATA_URL`) and push.

The example banner disappears as soon as the app finds the status in the bucket.

## Develop

```sh
npm run lifts:example   # data/lifts.json from the made-up fixture (needed once, locally)
npm run serve           # http://localhost:8000
npm test                # JS (node --test) and Python (unittest) tests
npm run stations        # rebuild data/stations.json (downloads EPIAP ~3 MB and the register)
npm run venv            # once: .venv with pyzmq (uv)
npm run listen          # listen to the live feed for 11 min (.cache/feed)
npm run listen:night    # 24 h, keeps the Mac awake (lid open, on power)
```

Like Clawback: no build step, Mithril from `vendor/`, everything the user saves (starred stations,
theme) stays in `localStorage` on the device. The service worker serves the app from cache, but
fetches `lifts.json` network-first. Bump `VERSION` in `sw.js` when the list of cached files changes.

| Path | What |
|---|---|
| `index.html`, `style.css` | page and styles (light/dark via `data-theme`) |
| `js/app.js` | the Mithril UI |
| `js/config.js` | where the live data is (`DATA_URL`, the R2 bucket) |
| `js/messages.js` | what the cat mascot says, per situation (≤ 60 characters, checked by `npm test`) |
| `js/mascot.js` | the mascot drawing and its coats (same cats as Clawback) |
| `js/lib.js` | pure logic (status per lift/station, search, distances), tested in `test/` |
| `scripts/build_stations.py` | NeTEx-EPIAP + ProRail register → `data/stations.json` |
| `scripts/build_lifts.py` | SIRI-FM XML (+ previous state) → `data/lifts.json` |
| `scripts/listen_lifts.py` | listens to the live lift stream and saves the messages |
| `nas/` | the feed service (Docker): listens around the clock, rebuilds the station list monthly, uploads both to R2 |
| `.github/workflows/deploy.yml` | on push: test + deploy the app |

### Publishing

Settings → Pages → Source: **GitHub Actions**. There are no scheduled workflows: all data jobs
run in the feed service. Refresh the fallback station list in git now and then with
`npm run stations` (it refuses to write a list that fails the check).

## Licence

Uplift is licensed under the [EUPL-1.2](LICENSE) (European Union Public Licence), an official EU
open-source licence, available in all EU languages
([Dutch text](https://interoperable-europe.ec.europa.eu/collection/eupl/eupl-text-eupl-12)).
You may use, change and share it; changed versions you share or offer as a service must stay
open source under the EUPL (or a compatible licence listed in its appendix).

Copyright (c) 2026 Timo van der Kuil.
Licensed under the EUPL.

Third-party parts keep their own licences:

- `vendor/mithril.min.js`: MIT, see `vendor/mithril.LICENSE`
- `data/stations.json`, `data/lifts.json` and the test fixtures: derived from CC0 open data
  (ProRail and DOVA, via the NDOV Loket of GOVI and Stichting OpenGeo)
- The feed service image installs [pyzmq](https://github.com/zeromq/pyzmq) (BSD-3-Clause, with
  libzmq under MPL-2.0) and [boto3](https://github.com/boto/boto3) (Apache-2.0); they are not
  part of this repository.
- Map links go to [OpenStreetMap](https://www.openstreetmap.org/copyright) (© OpenStreetMap
  contributors); no map data is included.

NS, ProRail and DOVA are named only to say where the data comes from; Uplift has no connection
with them. The data comes without any guarantee that it is correct or up to date (see "How
reliable is it?").
