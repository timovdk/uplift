# Feed service

Listens to the lift feed around the clock and publishes the status for the app. Runs in one
small container (Python). Only makes outgoing connections, so no ports to open:

- to `pubsub.besteffort.ndovloket.nl:7666` (the feed, one connection: GOVI's fair use),
- to Cloudflare R2 (upload `lifts.json` at most once a minute, `stations.json` monthly),
- to `data.ndovloket.nl` (download the station sources once a month).

## What ends up in the data folder

| Path | What |
|---|---|
| `raw/YYYY-MM-DD/HHMMSS_*.xml` | every message as received (UTC date), ~0.5 MB a day |
| `lifts.json` | the running status; a restart carries on from here |
| `stations.json` | the current station list (rebuilt monthly; until then the one in the image is used) |
| `cache/` | the downloaded station sources (~3 MB) |

Rebuild a status from the archive, e.g. to try new rules:
`python3 scripts/build_lifts.py --out /tmp/lifts.json /path/to/raw/2026-11-*/*.xml`

## Setup

### 1. Domain on Cloudflare (once)

R2 can only serve a bucket publicly for production on a domain whose DNS is on Cloudflare.

1. Create a Cloudflare account → **Add a domain** → your domain → **Free** plan.
2. Cloudflare copies the existing DNS records. Compare them with the ones at your registrar,
   especially **MX** and **TXT** (email) and anything pointing to your home server.
3. If **DNSSEC** is on at the registrar, switch it off there first (switch it back on via
   Cloudflare afterwards).
4. At the registrar, replace the nameservers with the two Cloudflare gives you. Cloudflare
   emails you when it's active (minutes to a day).

### 2. Bucket

1. Cloudflare → **R2** → enable it (asks for a payment method, even for the free tier).
2. **Create bucket** `uplift` (location: automatic or Western Europe).
3. Bucket → Settings → **Custom Domains** → add `uplift-data.timovdk.nl`.
4. Bucket → Settings → **CORS policy**, so the app may read it:
   ```json
   [{ "AllowedOrigins": ["https://timovdk.nl", "http://localhost:8000"],
      "AllowedMethods": ["GET", "HEAD"], "AllowedHeaders": ["*"], "MaxAgeSeconds": 3600 }]
   ```
5. R2 → **Manage API tokens** → Create: permission **Object Read & Write**, only bucket
   `uplift`. Note the *Access Key ID* and *Secret Access Key* (shown once), and the account id.

### 3. On the server

The NAS builds the image itself from the repository:

```sh
cd appdata/uplift                      # or wherever your compose projects live
git clone https://github.com/<you>/uplift src
cp src/nas/docker-compose.yml .
cp src/nas/.env.example .env           # fill it in; UPLIFT_DATA = a folder on the HDDs
docker compose up -d --build
docker logs -f uplift-feed             # "Station list rebuilt" within a minute,
                                       # "Published" after the first lift message
```

Check `https://uplift-data.timovdk.nl/lifts.json` and `…/stations.json` in a browser, then put
`https://uplift-data.timovdk.nl/` in `js/config.js` (`DATA_URL`) and push.

Update: `git -C src pull && docker compose up -d --build`.

## Good to know

- On 17 Nov 2026 a connection went silently dead at 03:56 CET, probably at a
  restart. Now ZeroMQ pings the server every 10 s and reconnects after 30 s without an answer
  (a closed connection right away);
  as a backup, the service reconnects after 12 minutes without any message.
- The container is *unhealthy* when no message came in for 15 minutes.
- After a fresh start (empty archive) most lifts are "unknown" until the next full status.
  That comes now and then, not daily (18 Nov 03:02 UTC yes, 19 Nov no). After a restart the
  archive is replayed from the latest full status, however old.
- Without the `R2_*` settings it still listens and archives, but publishes nothing.
- A station list that fails the check (too few stations or lifts, not linked to the feed,
  outside the Netherlands) is not used; the log says why, and it tries again after 6 hours.
- Free tier (Nov 2026, check Cloudflare's pricing page): 10 GB storage, 1 million writes and
  10 million reads a month. This uses at most ~45,000 writes a month.
