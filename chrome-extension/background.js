const BRIDGE = "http://127.0.0.1:9232";
const POLL_ALARM = "market-radar-bridge-poll";
const LOCK_STORAGE_KEY = "market-radar-run-tab-locks-v1";
const sessions = new Map();
const platformTabLocks = new Map();
let locksLoadPromise = null;
let polling = false;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function ensureLocksLoaded() {
  if (!locksLoadPromise) {
    locksLoadPromise = (async () => {
      const stored = await chrome.storage.local.get(LOCK_STORAGE_KEY);
      const values = stored[LOCK_STORAGE_KEY] || {};
      for (const [lockKey, entry] of Object.entries(values)) {
        if (!entry || typeof entry.platform !== "string" || !Number.isInteger(entry.tabId)) continue;
        platformTabLocks.set(lockKey, {
          platform: entry.platform,
          tabId: entry.tabId,
          owner: typeof entry.owner === "string" ? entry.owner : ""
        });
      }
    })();
  }
  await locksLoadPromise;
}

async function persistLocks() {
  await chrome.storage.local.set({
    [LOCK_STORAGE_KEY]: Object.fromEntries(platformTabLocks.entries())
  });
}

async function setPlatformLock(lockKey, entry) {
  await ensureLocksLoaded();
  platformTabLocks.set(lockKey, entry);
  await persistLocks();
}

async function deletePlatformLock(lockKey) {
  await ensureLocksLoaded();
  if (!platformTabLocks.delete(lockKey)) return;
  await persistLocks();
}

async function deletePlatformLockIfOwner(platform, lockKey, lockOwner) {
  await ensureLocksLoaded();
  const entry = platformTabLocks.get(lockKey);
  if (!entry || entry.platform !== platform || !lockOwner || entry.owner !== lockOwner) {
    return false;
  }
  await deletePlatformLock(lockKey);
  return true;
}

function platformMarkers(platform) {
  if (platform === "shopee") return ["shopee.com.my", "xiapibuy.com"];
  if (platform === "lazada") return ["lazada.com.my"];
  return [];
}

function hostMatches(host, marker) {
  return host === marker || host.endsWith(`.${marker}`);
}

function tabScore(platform, tab) {
  let parsed;
  try {
    parsed = new URL(tab.url || "about:blank");
  } catch {
    return -1;
  }
  const path = parsed.pathname.toLowerCase();
  const verification = /\/(verify|verification|captcha|punish|challenge|security[-_]?check)/.test(path)
    || (platform === "shopee" && hostMatches(parsed.hostname, "xiapibuy.com"))
    || (platform === "lazada" && parsed.hostname.startsWith("acs-m."));
  const search = platform === "shopee" ? path.startsWith("/search") : path.startsWith("/catalog");
  if (search) return 300;
  if (verification) return 200;
  return tab.active ? 110 : 100;
}

function commandLockKey(platform, lockKey) {
  return lockKey || `legacy:${platform}`;
}

async function platformTab(platform, createUrl, requestedLockKey) {
  await ensureLocksLoaded();
  const lockKey = commandLockKey(platform, requestedLockKey);
  const markers = platformMarkers(platform);
  const lockedEntry = platformTabLocks.get(lockKey);
  if (lockedEntry !== undefined && lockedEntry.platform === platform) {
    try {
      const locked = await chrome.tabs.get(lockedEntry.tabId);
      const lockedHost = new URL(locked.url || "about:blank").hostname.toLowerCase();
      if (markers.some((marker) => hostMatches(lockedHost, marker))) return locked;
    } catch {}
    await deletePlatformLock(lockKey);
  }
  const reservedTabIds = new Set(
    [...platformTabLocks.entries()]
      .filter(([otherKey]) => otherKey !== lockKey)
      .map(([, entry]) => entry.tabId)
  );
  const tabs = await chrome.tabs.query({});
  const candidates = tabs.filter((tab) => {
    if (reservedTabIds.has(tab.id)) return false;
    try {
      const host = new URL(tab.url || "about:blank").hostname.toLowerCase();
      return markers.some((marker) => hostMatches(host, marker));
    } catch {
      return false;
    }
  });
  if (candidates.length) {
    candidates.sort((a, b) => tabScore(platform, b) - tabScore(platform, a));
    await setPlatformLock(lockKey, { platform, tabId: candidates[0].id });
    return candidates[0];
  }
  const created = await chrome.tabs.create({ url: createUrl, active: false });
  await setPlatformLock(lockKey, { platform, tabId: created.id });
  return created;
}

async function releaseSessionsForTab(tabId) {
  let hadSession = false;
  for (const [sessionId, session] of sessions.entries()) {
    if (session.target.tabId !== tabId) continue;
    sessions.delete(sessionId);
    hadSession = true;
  }
  if (hadSession) {
    try { await chrome.debugger.detach({ tabId }); } catch {}
  }
}

async function attach(params) {
  const lockKey = commandLockKey(params.platform, params.lock_key);
  const lockOwner = crypto.randomUUID();
  const tab = await platformTab(params.platform, params.url, lockKey);
  // Claim the run lock before touching the debugger. A recovering worker can reuse the same
  // run key, but its new owner token prevents the superseded worker's late cleanup from
  // deleting this tab association.
  await setPlatformLock(lockKey, {
    platform: params.platform,
    tabId: tab.id,
    owner: lockOwner
  });
  try {
    const activeTab = await chrome.tabs.update(tab.id, { active: true });
    if (activeTab.windowId !== undefined) {
      await chrome.windows.update(activeTab.windowId, { focused: true });
    }
    const target = { tabId: tab.id };
    // A timed-out backend request can finish in Chrome after the caller has already retried.
    // Drop that tab's stale logical session before reattaching so one platform always has one
    // debugger owner and the retry remains pinned to the same signed-in tab.
    await releaseSessionsForTab(tab.id);
    try {
      await chrome.debugger.attach(target, "1.3");
    } catch (error) {
      try { await chrome.debugger.detach(target); } catch {}
      await chrome.debugger.attach(target, "1.3");
    }
    const sessionId = crypto.randomUUID();
    sessions.set(sessionId, { target, platform: params.platform, lockKey, lockOwner });
    return {
      session_id: sessionId,
      lock_owner: lockOwner,
      tab_id: String(tab.id),
      url: tab.url || params.url
    };
  } catch (error) {
    // An attach that never produced a session must not reserve this tab forever. The owner
    // comparison also protects a concurrent recovery attach that already reclaimed the key.
    try {
      await deletePlatformLockIfOwner(params.platform, lockKey, lockOwner);
    } catch {}
    throw error;
  }
}

async function cdp(params) {
  const session = sessions.get(params.session_id);
  if (!session) throw new Error("Chrome bridge session expired");
  return chrome.debugger.sendCommand(session.target, params.method, params.command_params || {});
}

async function detach(params) {
  const session = sessions.get(params.session_id);
  sessions.delete(params.session_id);
  if (session) {
    try { await chrome.debugger.detach(session.target); } catch {}
  }
  return true;
}

async function listTabs() {
  const tabs = await chrome.tabs.query({});
  return tabs.map((tab) => ({
    id: String(tab.id),
    type: "page",
    url: tab.url || "",
    title: tab.title || "",
    active: Boolean(tab.active)
  }));
}

async function newTab(params) {
  const tab = await chrome.tabs.create({ url: params.url, active: false });
  return { id: String(tab.id), type: "page", url: tab.url || params.url, title: tab.title || "" };
}

async function activateTab(params) {
  const tabId = Number(params.tab_id);
  const tab = await chrome.tabs.update(tabId, { active: true });
  if (tab.windowId !== undefined) await chrome.windows.update(tab.windowId, { focused: true });
  return true;
}

async function activateLockedPlatform(params) {
  await ensureLocksLoaded();
  const lockKey = commandLockKey(params.platform, params.lock_key);
  const entry = platformTabLocks.get(lockKey);
  if (entry === undefined || entry.platform !== params.platform) return null;
  let tab;
  try {
    tab = await chrome.tabs.get(entry.tabId);
    const host = new URL(tab.url || "about:blank").hostname.toLowerCase();
    if (!platformMarkers(params.platform).some((marker) => hostMatches(host, marker))) {
      await deletePlatformLock(lockKey);
      return null;
    }
  } catch {
    await deletePlatformLock(lockKey);
    return null;
  }
  const activeTab = await chrome.tabs.update(tab.id, { active: true });
  if (activeTab.windowId !== undefined) {
    await chrome.windows.update(activeTab.windowId, { focused: true });
  }
  return {
    id: String(activeTab.id),
    type: "page",
    url: activeTab.url || tab.url || "",
    title: activeTab.title || tab.title || ""
  };
}

async function releasePlatformLock(params) {
  await ensureLocksLoaded();
  const lockKey = commandLockKey(params.platform, params.lock_key);
  const lockOwner = typeof params.lock_owner === "string" ? params.lock_owner : "";
  // Compare-and-delete: a stale worker may finish after a recovery worker has already
  // reclaimed the same run key. Only the current owner is allowed to release the lock.
  await deletePlatformLockIfOwner(params.platform, lockKey, lockOwner);
  return true;
}

async function execute(command) {
  const params = command.params || {};
  switch (command.action) {
    case "attach": return attach(params);
    case "cdp": return cdp(params);
    case "detach": return detach(params);
    case "tabs": return listTabs();
    case "new_tab": return newTab(params);
    case "activate_tab": return activateTab(params);
    case "activate_locked_platform": return activateLockedPlatform(params);
    case "release_platform_lock": return releasePlatformLock(params);
    default: throw new Error(`Unknown bridge action: ${command.action}`);
  }
}

async function sendResult(id, result, error) {
  await fetch(`${BRIDGE}/result`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id, result, error })
  });
}

async function pollBurst() {
  if (polling) return;
  polling = true;
  let consecutiveFailures = 0;
  try {
    while (true) {
      try {
        // Keep one low-cost localhost long poll open while the app is available.  The old
        // one-second burst stopped whenever there was no active debugger session, leaving
        // ordinary actions such as "open verification" waiting for the next 30s alarm.
        const response = await fetch(`${BRIDGE}/command?wait=20`, { cache: "no-store" });
        const payload = await response.json();
        consecutiveFailures = 0;
        const command = payload.command;
        if (!command) continue;
        try {
          const result = await execute(command);
          await sendResult(command.id, result, null);
        } catch (error) {
          await sendResult(command.id, null, String(error?.message || error));
        }
      } catch {
        consecutiveFailures += 1;
        if (sessions.size === 0 && consecutiveFailures >= 3) break;
        await sleep(Math.min(500 * consecutiveFailures, 3000));
      }
    }
  } finally {
    polling = false;
  }
}

function schedulePolling() {
  chrome.alarms.create(POLL_ALARM, { periodInMinutes: 0.5 });
  pollBurst();
}

chrome.runtime.onInstalled.addListener(schedulePolling);
chrome.runtime.onStartup.addListener(schedulePolling);
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === POLL_ALARM) pollBurst();
});
chrome.tabs.onRemoved.addListener((tabId) => {
  void (async () => {
    await ensureLocksLoaded();
    let changed = false;
    for (const [lockKey, entry] of platformTabLocks.entries()) {
      if (entry.tabId !== tabId) continue;
      platformTabLocks.delete(lockKey);
      changed = true;
    }
    if (changed) await persistLocks();
  })();
  for (const [sessionId, session] of sessions.entries()) {
    if (session.target.tabId === tabId) sessions.delete(sessionId);
  }
});
chrome.debugger.onDetach.addListener((source) => {
  for (const [sessionId, session] of sessions.entries()) {
    if (session.target.tabId === source.tabId) sessions.delete(sessionId);
  }
});
schedulePolling();
