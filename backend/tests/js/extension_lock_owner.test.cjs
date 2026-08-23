const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const LOCK_STORAGE_KEY = "market-radar-run-tab-locks-v1";

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function loadWorker(chrome) {
  const sourcePath = path.resolve(
    __dirname,
    "../../../chrome-extension/background.js"
  );
  const source = fs.readFileSync(sourcePath, "utf8").replace(
    /\nschedulePolling\(\);\s*$/,
    "\n"
  );
  const context = {
    chrome,
    console,
    crypto: crypto.webcrypto,
    URL,
    setTimeout,
    clearTimeout
  };
  vm.createContext(context);
  vm.runInContext(source, context, { filename: sourcePath });
  return context;
}

test("a stale owner cannot delete a recovered worker's run lock", async () => {
  const writes = [];
  const stored = {
    [LOCK_STORAGE_KEY]: {
      "run:42:shopee": {
        platform: "shopee",
        tabId: 17,
        owner: "owner-new"
      }
    }
  };
  const listener = { addListener() {} };
  const chrome = {
    storage: {
      local: {
        async get() { return clone(stored); },
        async set(value) {
          writes.push(clone(value));
          Object.assign(stored, clone(value));
        }
      }
    },
    runtime: { onInstalled: listener, onStartup: listener },
    alarms: { create() {}, onAlarm: listener },
    tabs: { onRemoved: listener },
    debugger: { onDetach: listener }
  };
  const context = loadWorker(chrome);

  await context.releasePlatformLock({
    platform: "shopee",
    lock_key: "run:42:shopee",
    lock_owner: "owner-old"
  });
  assert.equal(writes.length, 0);

  await context.releasePlatformLock({
    platform: "shopee",
    lock_key: "run:42:shopee",
    lock_owner: "owner-new"
  });
  assert.equal(writes.length, 1);
  assert.deepEqual(writes[0][LOCK_STORAGE_KEY], {});
});

test("a failed debugger attach releases only its claimed owner lock", async () => {
  const writes = [];
  const stored = { [LOCK_STORAGE_KEY]: {} };
  const listener = { addListener() {} };
  const chrome = {
    storage: {
      local: {
        async get() { return clone(stored); },
        async set(value) {
          writes.push(clone(value));
          Object.assign(stored, clone(value));
        }
      }
    },
    runtime: { onInstalled: listener, onStartup: listener },
    alarms: { create() {}, onAlarm: listener },
    tabs: {
      onRemoved: listener,
      async query() {
        return [{ id: 17, url: "https://shopee.com.my/search?keyword=nail" }];
      },
      async update(id) {
        return { id, windowId: 3, url: "https://shopee.com.my/search?keyword=nail" };
      }
    },
    windows: { async update() {} },
    debugger: {
      onDetach: listener,
      async attach() { throw new Error("debugger unavailable"); },
      async detach() {}
    }
  };
  const context = loadWorker(chrome);

  await assert.rejects(
    context.attach({
      platform: "shopee",
      lock_key: "run:43:shopee",
      url: "https://shopee.com.my/search?keyword=nail"
    }),
    /debugger unavailable/
  );

  assert.ok(writes.length >= 3);
  assert.deepEqual(stored[LOCK_STORAGE_KEY], {});
  assert.deepEqual(writes.at(-1)[LOCK_STORAGE_KEY], {});
});
