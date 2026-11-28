const HOST = "app.talos.native";

function sendNative(payload) {
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

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message && message.type === "native") {
    sendNative(message.payload)
      .then((response) => sendResponse({ ok: true, response }))
      .catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }
});

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "talos-save-page",
    title: "Save page context for Talos",
    contexts: ["page", "selection"]
  });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId !== "talos-save-page" || !tab || tab.id === undefined) {
    return;
  }
  try {
    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => ({
        url: location.href,
        title: document.title,
        text: document.body ? document.body.innerText.slice(0, 30000) : "",
        selection: String(window.getSelection() || "")
      })
    });
    await sendNative({ action: "page", params: result });
  } catch (error) {
    console.error("Talos context save failed", error);
  }
});
