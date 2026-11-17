// Durable key-value storage for starred stations and settings (same as Clawback's storage.js).
//
// localStorage alone isn't enough: Safari (iOS home-screen apps in particular) keeps writes in
// memory and flushes them to disk later, so closing the app right after an action can lose it.
// IndexedDB commits each transaction to disk, so that's the primary store. Every write is also
// mirrored to localStorage, which gives a synchronous value at start-up (no empty flash) and a
// fallback where IndexedDB isn't available (some private modes).

const DB_NAME = 'uplift';
const STORE = 'kv';

let dbPromise = null;

function openDb() {
  if (!dbPromise) {
    dbPromise = new Promise((resolve, reject) => {
      if (!('indexedDB' in globalThis)) { reject(new Error('no IndexedDB')); return; }
      const req = indexedDB.open(DB_NAME, 1);
      req.onupgradeneeded = () => req.result.createObjectStore(STORE);
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
    dbPromise.catch(() => {}); // callers handle failure; avoid unhandled-rejection noise
  }
  return dbPromise;
}

function idbGet(key) {
  return openDb().then((db) => new Promise((resolve, reject) => {
    const req = db.transaction(STORE, 'readonly').objectStore(STORE).get(key);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  }));
}

function idbPut(key, value) {
  return openDb().then((db) => new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readwrite', { durability: 'strict' });
    tx.objectStore(STORE).put(value, key);
    tx.oncomplete = () => resolve(true);
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error);
  }));
}

/** Synchronous read from the localStorage mirror (may be slightly behind IndexedDB). */
export function loadSync(key, fallback) {
  try {
    const v = localStorage.getItem(key);
    return v ? JSON.parse(v) : fallback;
  } catch {
    return fallback;
  }
}

/** Read the durable value; falls back to (and migrates) the localStorage copy. */
export async function load(key, fallback) {
  try {
    const v = await idbGet(key);
    if (v !== undefined) return v;
  } catch { /* IndexedDB unavailable: use the mirror */ }
  const mirror = loadSync(key, undefined);
  if (mirror !== undefined) idbPut(key, mirror).catch(() => {}); // move older data over
  return mirror !== undefined ? mirror : fallback;
}

/** Write to both stores. Resolves to true once IndexedDB has committed. */
export function save(key, value) {
  const plain = JSON.parse(JSON.stringify(value)); // plain data only
  try { localStorage.setItem(key, JSON.stringify(plain)); } catch { /* full or blocked */ }
  return idbPut(key, plain).catch(() => false);
}

/** Ask the browser not to clear our storage under storage pressure (best effort). */
export function requestPersistence() {
  try {
    if (navigator.storage && navigator.storage.persist) navigator.storage.persist().catch(() => {});
  } catch { /* not supported */ }
}
