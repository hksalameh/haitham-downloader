"use strict";
const ENDPOINT = "http://127.0.0.1:18767";
// Chrome capture for the Classic 4.1 desktop app only.
const STATUS_KEY = "autoCapture";
let redirecting = new Set();

const validUrl = url => typeof url === "string" && /^(https?|ftp):\/\//i.test(url);
const safeDownload = item => (
  item && typeof item.id === "number" && item.state === "in_progress"
  && !item.byExtensionId
  && validUrl(item.finalUrl || item.url)
  && (!item.danger || item.danger === "safe")
);

async function send(url, kind = "file", filename = "") {
  if (!validUrl(url)) throw Error("unsupported_url");
  const controller = new AbortController();
  const deadline = setTimeout(() => controller.abort(), 100000);
  try {
    const response = await fetch(ENDPOINT + "/browser/add", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Haitham-Downloader": "1"
      },
      body: JSON.stringify({ url, kind, filename }),
      signal: controller.signal
    });
    if (!response.ok) throw Error("downloader_http_" + response.status);
    const result = await response.json();
    if (result.ok !== true) throw Error("not_accepted");
    return true;
  } finally {
    clearTimeout(deadline);
  }
}

async function health() {
  const controller = new AbortController();
  const deadline = setTimeout(() => controller.abort(), 2000);
  try {
    const response = await fetch(ENDPOINT + "/health", { signal: controller.signal });
    if (!response.ok) return false;
    const result = await response.json();
    return result.ok === true && String(result.version||"").startsWith("4.1-");
  } catch {
    return false;
  } finally {
    clearTimeout(deadline);
  }
}

function notify(message) {
  chrome.notifications.create({
    type: "basic", title: "Haitham Downloader",
    iconUrl: "icon.png", message
  }).catch(() => {});
}

chrome.runtime.onInstalled.addListener(details => {
  chrome.contextMenus.removeAll().then(() => {
    chrome.contextMenus.create({ id: "hdm-link", title: "تحميل الرابط بواسطة هيثم", contexts: ["link"] });
    // Video-page capture will be enabled only after quality selection is implemented.
  });
  // User asked to test Chrome integration now. They can turn it off in popup.
  if (details?.reason === "update" || details?.reason === "install") {
    chrome.storage.local.set({ [STATUS_KEY]: true });
  } else {
    chrome.storage.local.get(STATUS_KEY).then(settings => {
      if (typeof settings[STATUS_KEY] !== "boolean") chrome.storage.local.set({ [STATUS_KEY]: true });
    });
  }
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  const url = info.linkUrl;
  send(url, "file")
    .then(() => notify("أُرسل الرابط بنجاح إلى مدير تحميل هيثم"))
    .catch(() => notify("مدير التحميل غير متاح أو لم يقبل الرابط. شغّل البرنامج وأعد المحاولة."));
});

// Automatic interception here happens TOO LATE: when Chrome has already started
// Save As, this event can produce both Chrome's dialog and our own dialog.
// Direct link clicks are intercepted before navigation by content.js instead.
// Downloads triggered by scripts, blobs, or unsupported sites remain with Chrome.
// Do not register downloads.onCreated in Classic 4.1.3.

async function intercept(item) {
  if (!safeDownload(item) || redirecting.has(item.id)) return;
  const { autoCapture = false } = await chrome.storage.local.get({ autoCapture: false });
  if (!autoCapture) return;
  // Never pause Chrome when the correct desktop application is closed.
  if (!(await health())) return;
  const id = item.id;
  redirecting.add(id);
  let paused = false;
  try {
    try {
      await chrome.downloads.pause(id);
      paused = true;
    } catch {
      // Chrome may have completed a very small download already.
      return;
    }
    const matches = await chrome.downloads.search({ id });
    const current = matches?.[0];
    if (!current || current.state !== "in_progress" || !current.paused) return;
    // If the desktop app is closed or refuses, Chrome resumes the original.
    await send(current.finalUrl || current.url, "file", (current.filename || "").split(/[\\/]/).pop());
    await chrome.downloads.cancel(id);
    paused = false;
    await chrome.downloads.erase({ id }).catch(() => {});
  } catch (err) {
    console.warn("Haitham Downloader fallback to Chrome", String(err));
  } finally {
    if (paused) {
      try {
        const matches = await chrome.downloads.search({ id });
        if (matches?.[0]?.state === "in_progress" && matches[0].paused) {
          await chrome.downloads.resume(id);
        }
      } catch (err) {
        console.warn("Could not resume Chrome download", err);
        notify("تعذر استكمال تنزيل في Chrome. افتح صفحة التحميلات وتحقق من حالته.");
      }
    }
    redirecting.delete(id);
  }
}

chrome.runtime.onMessage.addListener((req, sender, respond) => {
  if (!req || typeof req !== "object") return;
  if (req.action === "smartLink") {
    send(req.url, "file", req.filename || "")
      .then(() => respond({ ok: true }))
      .catch(async err => {
        try {
          await chrome.downloads.download({ url: req.url, saveAs: false });
          respond({ ok: false, fallback: true });
        } catch (fallbackError) {
          respond({ ok: false, fallback: false, error: String(fallbackError) });
        }
      });
    return true;
  }
  if (req.action === "send") {
    send(req.url, req.kind === "video" ? "video" : "file")
      .then(() => respond({ ok: true }))
      .catch(err => respond({ ok: false, error: String(err) }));
    return true;
  }
  if (req.action === "status") {
    Promise.all([health(), chrome.storage.local.get({ autoCapture: false })])
      .then(([online, settings]) => respond({ online, autoCapture: settings.autoCapture }))
      .catch(() => respond({ online: false, autoCapture: false }));
    return true;
  }
  if (req.action === "autoCapture") {
    chrome.storage.local.set({ autoCapture: req.enabled === true })
      .then(() => respond({ ok: true }))
      .catch(err => respond({ ok: false, error: String(err) }));
    return true;
  }
});
