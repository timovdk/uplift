import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

// Minimal in-memory stand-ins for localStorage and the bits of IndexedDB that storage.js uses.
function fakeLocalStorage() {
  const data = new Map();
  return {
    data,
    getItem: (k) => (data.has(k) ? data.get(k) : null),
    setItem: (k, v) => data.set(k, String(v)),
  };
}

function fakeIndexedDB() {
  const stores = new Map();
  const later = (fn) => setTimeout(fn, 0);
  const db = {
    createObjectStore: (name) => stores.set(name, new Map()),
    transaction: (name) => {
      const tx = {};
      tx.objectStore = () => ({
        get: (k) => { const req = {}; later(() => { req.result = stores.get(name).get(k); req.onsuccess(); }); return req; },
        put: (v, k) => { later(() => { stores.get(name).set(k, structuredClone(v)); later(() => tx.oncomplete()); }); },
      });
      return tx;
    },
  };
  return {
    stores,
    open: () => {
      const req = { result: db };
      later(() => { if (!stores.size) req.onupgradeneeded(); req.onsuccess(); });
      return req;
    },
  };
}

let storage;
beforeEach(async () => {
  globalThis.localStorage = fakeLocalStorage();
  globalThis.indexedDB = fakeIndexedDB();
  // fresh module per test (it caches the open database)
  storage = await import(`../js/storage.js?t=${Math.random()}`);
});

test('save writes to IndexedDB and the localStorage mirror', async () => {
  const favs = ['UT', 'ASD'];
  assert.equal(await storage.save('lift.favs', favs), true);
  assert.deepEqual(await storage.load('lift.favs', []), favs);
  assert.deepEqual(storage.loadSync('lift.favs', []), favs);
});

test('an un-star is durable once save resolves', async () => {
  await storage.save('lift.favs', ['UT', 'ASD']);
  await storage.save('lift.favs', ['ASD']);
  // simulate the app being killed: the localStorage mirror is lost, IndexedDB remains
  globalThis.localStorage = fakeLocalStorage();
  assert.deepEqual(await storage.load('lift.favs', []), ['ASD']);
});

test('existing localStorage data is used and moved to IndexedDB', async () => {
  localStorage.setItem('lift.settings', JSON.stringify({ theme: 'dark' }));
  assert.deepEqual(await storage.load('lift.settings', {}), { theme: 'dark' });
  await new Promise((r) => setTimeout(r, 10));
  assert.deepEqual(indexedDB.stores.get('kv').get('lift.settings'), { theme: 'dark' });
});

test('without IndexedDB it falls back to localStorage', async () => {
  delete globalThis.indexedDB;
  storage = await import(`../js/storage.js?t=${Math.random()}`);
  assert.equal(await storage.save('lift.favs', ['HT']), false);
  assert.deepEqual(await storage.load('lift.favs', []), ['HT']);
});

test('missing keys give the fallback', async () => {
  assert.deepEqual(await storage.load('nope', ['fallback']), ['fallback']);
  assert.equal(storage.loadSync('nope', 7), 7);
});
