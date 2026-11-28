const HOST = "app.talos.native";
let sessionId = null;
let pollTimer = null;

async function native(payload) {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendNativeMessage(HOST, payload, (response) => {
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
        return;
      }
      resolve(response);
    });
  });
}

async function pageContext() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || tab.id === undefined) {
    throw new Error("No active tab.");
  }
  const [{ result }] = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: () => ({
      url: location.href,
      title: document.title,
      text: document.body ? document.body.innerText.slice(0, 30000) : "",
      selection: String(window.getSelection() || "")
    })
  });
  return result;
}

function setStatus(text, isError = false) {
  const element = document.getElementById("status");
  element.textContent = text;
  element.className = isError ? "status error" : "status";
}

function setOutput(text) {
  document.getElementById("output").textContent = text || "—";
}

async function ping() {
  try {
    const response = await native({ action: "ping" });
    setStatus(response.ok
      ? `Host ready · app server ${response.serverRunning ? "connected" : "not running"}`
      : `Host error: ${response.error}`, !response.ok);
    return response;
  } catch (error) {
    setStatus(`Native host unavailable: ${error.message}`, true);
    return null;
  }
}

async function savePage() {
  try {
    const page = await pageContext();
    const response = await native({ action: "page", params: page });
    setStatus(response.ok ? "Page context saved." : `Save failed: ${response.error}`, !response.ok);
  } catch (error) {
    setStatus(error.message, true);
  }
}

async function ask() {
  try {
    const page = await pageContext();
    const prompt = document.getElementById("prompt").value.trim() || "Summarize this page.";
    const response = await native({ action: "ask", params: { ...page, prompt } });
    if (!response.ok) {
      setStatus(response.error || "Talos rejected the request.", true);
      return;
    }
    sessionId = response.sessionId;
    await chrome.storage.local.set({ talosSessionId: sessionId });
    setStatus("Sent to Talos. Waiting for a reply…");
    setOutput("Working…");
    startPolling();
  } catch (error) {
    setStatus(error.message, true);
  }
}

function startPolling() {
  stopPolling();
  pollTimer = setInterval(refreshStatus, 1000);
  refreshStatus();
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

async function refreshStatus() {
  if (!sessionId) return;
  try {
    const response = await native({ action: "status", params: { sessionId } });
    if (!response.ok) {
      setStatus(response.error || "Status failed.", true);
      stopPolling();
      return;
    }
    setOutput(response.lastAssistant || (response.streaming ? "Working…" : "No reply yet."));
    setStatus(response.streaming ? "Talos is working…" : "Reply received.");
    if (!response.streaming) {
      stopPolling();
    }
  } catch (error) {
    setStatus(error.message, true);
    stopPolling();
  }
}

async function cancel() {
  if (!sessionId) {
    setStatus("No Talos session to cancel.");
    return;
  }
  try {
    const response = await native({ action: "cancel", params: { sessionId } });
    setStatus(response.ok ? "Cancelled." : `Cancel failed: ${response.error}`, !response.ok);
    stopPolling();
  } catch (error) {
    setStatus(error.message, true);
  }
}

async function openApp() {
  try {
    const response = await native({ action: "open" });
    setStatus(response.ok ? "Opening Talos." : `Open failed: ${response.error}`, !response.ok);
  } catch (error) {
    setStatus(error.message, true);
  }
}

document.getElementById("ping").addEventListener("click", ping);
document.getElementById("save").addEventListener("click", savePage);
document.getElementById("ask").addEventListener("click", ask);
document.getElementById("cancel").addEventListener("click", cancel);
document.getElementById("open").addEventListener("click", openApp);

chrome.storage.local.get("talosSessionId").then((value) => {
  if (value.talosSessionId) {
    sessionId = value.talosSessionId;
    refreshStatus();
  }
});
ping();
