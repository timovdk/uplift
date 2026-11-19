#!/usr/bin/env python3
"""Listen to the live lift feed for a while and save every message as a file.

The lift status (SIRI-FM, from ProRail via DOVA) is pushed over ZeroMQ by the NDOV loket of
Stichting OpenGeo / GOVI (free, CC0, fair use: max. 1 connection per user per stream):

    tcp://pubsub.besteffort.ndovloket.nl:7666   envelope /DOVA/ServiceDelivery

What arrives (seen 16 Nov 2026): DOVA checks ProRail every 5 minutes and sends every lift whose
status changed, within ~5 minutes of the change, and once a day all lifts (time unknown; see
the overnight listen). Only ASD-LIF-013 (out of service since 2024) was repeated every 5 minutes;
other broken lifts were not repeated. A HeartbeatNotification arrives every 10 minutes.

Usage: python3 scripts/listen_lifts.py OUTPUT_DIR [--minutes 11]
Writes OUTPUT_DIR/msg_000001.xml ... and OUTPUT_DIR/window.json ({"from": ..., "to": ..., "messages": n}).
Needs pyzmq (pip install pyzmq).
"""
import argparse
import gzip
import json
import os
import sys
import time
from datetime import datetime, timezone

import zmq

ENDPOINT = os.environ.get('LIFTS_ZMQ', 'tcp://pubsub.besteffort.ndovloket.nl:7666')
ENVELOPE = b'/DOVA/'
# A heartbeat comes every 10 minutes. If nothing at all arrives for longer, the connection has
# gone stale (seen 17 Nov 2026 around 04:00: still "ESTABLISHED", but silent for hours), so
# reconnect. ZeroMQ only reconnects by itself when it notices the connection drop.
SILENCE_S = 12 * 60


def connect(ctx):
    sock = ctx.socket(zmq.SUB)
    sock.setsockopt(zmq.RCVTIMEO, 5000)
    sock.setsockopt(zmq.LINGER, 0)
    # ZeroMQ's own ping/pong (the server answers, tested 17 Nov 2026): a few bytes every 10 s
    # on the one connection we have. A silently dead connection is noticed within ~30 s and
    # ZeroMQ reconnects by itself (a closed one right away). Stricter since 19 Nov, when a full
    # status went missing around 03:02. Unlike universal-sub-pubsub's "reconnect after 60 s
    # without data", a healthy but quiet connection is never dropped. SILENCE_S is the backup.
    sock.setsockopt(zmq.HEARTBEAT_IVL, 10_000)
    sock.setsockopt(zmq.HEARTBEAT_TIMEOUT, 30_000)
    sock.setsockopt(zmq.TCP_KEEPALIVE, 1)
    sock.setsockopt(zmq.TCP_KEEPALIVE_IDLE, 60)
    sock.setsockopt(zmq.TCP_KEEPALIVE_INTVL, 30)
    sock.setsockopt(zmq.TCP_KEEPALIVE_CNT, 4)
    sock.connect(ENDPOINT)
    sock.setsockopt(zmq.SUBSCRIBE, ENVELOPE)
    return sock


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('out')
    ap.add_argument('--minutes', type=float, default=11)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    ctx = zmq.Context()
    sock = connect(ctx)

    started = now_iso()
    end = time.time() + args.minutes * 60
    n, reconnects, last = 0, 0, time.time()
    try:
        while time.time() < end:
            try:
                parts = sock.recv_multipart()
            except zmq.Again:
                if time.time() - last > SILENCE_S:
                    print(f'{now_iso()} nothing for {SILENCE_S // 60} min, reconnecting', file=sys.stderr)
                    sock.close()
                    sock = connect(ctx)
                    reconnects += 1
                    last = time.time()
                continue
            last = time.time()
            body = b''.join(parts[1:])
            if body[:2] == b'\x1f\x8b':
                body = gzip.decompress(body)
            if b'FacilityMonitoringDelivery' not in body and b'HeartbeatNotification' not in body:
                continue
            n += 1
            # Written straight away, so stopping early (Ctrl+C) keeps everything received so far.
            with open(os.path.join(args.out, f'msg_{n:06d}.xml'), 'wb') as f:
                f.write(body)
    except KeyboardInterrupt:
        print('Stopped early.', file=sys.stderr)
    sock.close()
    ctx.term()

    window = {'from': started, 'to': now_iso(), 'messages': n, 'reconnects': reconnects}
    with open(os.path.join(args.out, 'window.json'), 'w') as f:
        json.dump(window, f)
    print(f'{n} lift messages between {window["from"]} and {window["to"]}', file=sys.stderr)


if __name__ == '__main__':
    main()
