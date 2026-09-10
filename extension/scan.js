const base = "http://127.0.0.1:5000";
const status = document.querySelector("#status");
const start = document.querySelector("#start");
const stop = document.querySelector("#stop");
const token = document.querySelector("#token");
const scope = document.querySelector("#scope");
let cancelled = false;
let scanTab;
let paused = false;
let skipRequested = false;
const pause = document.querySelector("#pause");
const skip = document.querySelector("#skip");
const progress = document.querySelector("#progress");
pause.onclick = () => {paused = !paused; pause.textContent = paused ? "Resume" : "Pause";};
skip.onclick = () => {skipRequested = true; paused = false;};
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function waitWhilePaused() {
  const began = Date.now();
  while (paused && !cancelled && !skipRequested) await delay(250);
  return Date.now() - began;
}
chrome.storage.session.get("token").then(saved => {token.value = saved.token || "";});
stop.onclick = () => {cancelled = true; status.textContent = "Stopping…";};

async function api(path, body) {
  const response = await fetch(base + path, {
    method: body ? "POST" : "GET",
    headers: {Authorization: "Bearer " + token.value.trim(), ...(body ? {"Content-Type": "application/json"} : {})},
    ...(body ? {body: JSON.stringify(body)} : {}),
    signal: AbortSignal.timeout(15000)
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `BookTracker returned ${response.status}`);
  return data;
}

async function readPage(job) {
  let deadline = Date.now() + 10 * 60 * 1000;
  while (!cancelled && Date.now() < deadline) {
    deadline += await waitWhilePaused();
    if (cancelled || skipRequested) throw new Error("Scan interrupted.");
    const tab = await chrome.tabs.get(scanTab);
    if (tab.status === "complete") {
      let results;
      try {
        results = await chrome.scripting.executeScript({target: {tabId: scanTab}, func: () => {
          const challenge = /challenged|just a moment|access denied/i.test(document.title) ||
            /enable javascript and cookies to continue/i.test(document.body?.innerText || "");
          return {url: location.href, challenge, html: challenge ? "" : document.documentElement.outerHTML};
        }});
      } catch (error) {
        status.textContent = "Waiting for the Kobo tab. Keep it open and complete any verification there.";
      }
      const page = results?.[0]?.result;
      if (page && !page.challenge && page.url === job.url) {
        // Give an apparently empty list time to finish rendering, then read
        // it again before the server decides this is the end of pagination.
        if (job.kind === "deals" && job.page > 1 && !/\/ebook\//.test(page.html)) {
          await delay(3000);
          const refreshed = await chrome.scripting.executeScript({target: {tabId: scanTab}, func: () => ({
            url: location.href, html: document.documentElement.outerHTML,
            challenge: /challenged|just a moment|access denied/i.test(document.title)
          })});
          const current = refreshed?.[0]?.result;
          if (current && !current.challenge && current.url === job.url) return current;
        } else return page;
      }
      status.textContent = `Waiting for ${job.label}. Complete any verification in the Kobo tab; collection resumes automatically. Stop if verification keeps repeating.`;
    }
    await new Promise(resolve => setTimeout(resolve, 2000));
  }
  throw new Error(cancelled ? "Scan stopped." : "Timed out waiting for Kobo. No data was collected from this page.");
}

start.onclick = async () => {
  const wishlistOnly = scope.value === "wishlist";
  scope.disabled = true;
  start.disabled = true; stop.disabled = false; pause.disabled = false;
  token.disabled = true; cancelled = false; paused = false;
  document.querySelector("#results").replaceChildren();
  const signatures = {};
  let completed = 0, skipped = 0;
  function log(message) {
    const item = document.createElement("li"); item.textContent = message;
    document.querySelector("#results").append(item);
  }
  try {
    await chrome.storage.session.set({token: token.value.trim()});
    const plan = await api("/api/extension/plan");
    const jobs = wishlistOnly ? plan.jobs.filter(job => job.kind === "wishlist") : plan.jobs;
    if (!jobs.length) {
      status.textContent = "Your wishlist is empty. Add books in BookTracker before scanning your wishlist.";
      progress.textContent = "No pages opened.";
      return;
    }
    for (let index = 0; index < jobs.length && !cancelled; index++) {
      const job = jobs[index];
      let done = false;
      skipRequested = false;
      while (!done && !cancelled) {
        await waitWhilePaused();
        if (cancelled) break;
        progress.textContent = `${completed} pages saved/processed, ${skipped} skipped; ${jobs.length - index} queued${wishlistOnly ? "." : " (more deal pages may be added)."}`;
        try {
          status.textContent = `Opening ${job.label}${job.page ? ", page " + job.page : ""}?`;
          // Recreate a scan tab if the user closed it between pages or retries.
          if (scanTab) {
            try {await chrome.tabs.get(scanTab);} catch {scanTab = undefined;}
          }
          if (scanTab) await chrome.tabs.update(scanTab, {url: job.url, active: false});
          else scanTab = (await chrome.tabs.create({url: job.url, active: true})).id;
          await delay(2000);
          const page = await readPage(job);
          await waitWhilePaused();
          if (cancelled) break;
          const result = await api("/api/extension/page", {...page, seen_signatures: signatures[job.label] || []});
          if (result.signature) (signatures[job.label] ||= []).push(result.signature);
          if (!wishlistOnly && result.next_job) jobs.splice(index + 1, 0, result.next_job);
          log(result.message);
          completed++; done = true;
        } catch (error) {
          if (cancelled) break;
          status.textContent = `${job.label}: ${error.message}\nPaused. Complete verification in Kobo if needed, then Resume to retry this page, or Skip to continue with the next list/book.`;
          paused = true; pause.textContent = "Resume"; skip.disabled = false;
          await waitWhilePaused();
          skip.disabled = true; pause.textContent = "Pause";
          if (skipRequested) {skipped++; log(`Skipped ${job.label}${job.page ? ", page " + job.page : ""}.`); done = true;}
        }
      }
    }
    status.textContent = `${cancelled ? "Scan stopped" : "Scan finished"}. ${completed} pages processed; ${skipped} skipped. Refresh BookTracker to see prices.`;
    progress.textContent = "Saved results are kept. Books not seen in this scan are not marked ended.";
  } catch (error) {
    status.textContent = error.message + "\nCheck that BookTracker is running and the connection code is current.";
  } finally {
    scope.disabled = false;
    scanTab = undefined;
    start.disabled = false; stop.disabled = true; pause.disabled = true; skip.disabled = true;
    pause.textContent = "Pause"; token.disabled = false;
  }
};
