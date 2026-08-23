const BRIDGE = "http://127.0.0.1:9232";
const POLL_ALARM = "market-radar-bridge-poll";
const sessions = new Map();
let polling = false;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

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

async function platformTab(platform, createUrl) {
  const markers = platformMarkers(platform);
  const tabs = await chrome.tabs.query({});
  const candidates = tabs.filter((tab) => {
    try {
      const host = new URL(tab.url || "about:blank").hostname.toLowerCase();
      return markers.some((marker) => hostMatches(host, marker));
    } catch {
      return false;
    }
  });
  if (candidates.length) {
    candidates.sort((a, b) => tabScore(platform, b) - tabScore(platform, a));
    return candidates[0];
  }
  return chrome.tabs.create({ url: createUrl, active: false });
}

async function attach(params) {
  const tab = await platformTab(params.platform, params.url);
  const activeTab = await chrome.tabs.update(tab.id, { active: true });
  if (activeTab.windowId !== undefined) {
    await chrome.windows.update(activeTab.windowId, { focused: true });
  }
  const target = { tabId: tab.id };
  try {
    await chrome.debugger.attach(target, "1.3");
  } catch (error) {
    try { await chrome.debugger.detach(target); } catch {}
    await chrome.debugger.attach(target, "1.3");
  }
  const sessionId = crypto.randomUUID();
  sessions.set(sessionId, target);
  return { session_id: sessionId, tab_id: String(tab.id), url: tab.url || params.url };
}

async function cdp(params) {
  const target = sessions.get(params.session_id);
  if (!target) throw new Error("Chrome bridge session expired");
  return chrome.debugger.sendCommand(target, params.method, params.command_params || {});
}

async function detach(params) {
  const target = sessions.get(params.session_id);
  sessions.delete(params.session_id);
  if (target) {
    try { await chrome.debugger.detach(target); } catch {}
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

async function execute(command) {
  const params = command.params || {};
  switch (command.action) {
    case "attach": return attach(params);
    case "cdp": return cdp(params);
    case "detach": return detach(params);
    case "tabs": return listTabs();
    case "new_tab": return newTab(params);
    case "activate_tab": return activateTab(params);
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
schedulePolling();
