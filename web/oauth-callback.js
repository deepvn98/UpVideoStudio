'use strict';
const resultNode = document.getElementById('oauth-result');
if (resultNode) {
  try {
    const result = JSON.parse(resultNode.textContent);
    try { localStorage.setItem('upvideo.oauth.callback', JSON.stringify(result)); } catch { /* postMessage can still notify the opener. */ }
    if (window.opener) window.opener.postMessage(result, location.origin);
  } catch {
    // The result page remains readable if browser storage is unavailable.
  }
}
