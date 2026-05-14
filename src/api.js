async function request(url, options = {}) {
  const response = await fetch(url, {
    headers: options.body instanceof FormData ? {} : { "Content-Type": "application/json" },
    ...options
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail || (await response.text()) || `HTTP ${response.status}`);
  }
  return response.json();
}

export function getConfig() {
  return request("/api/config");
}

export function createSession(payload) {
  return request("/api/sessions", { method: "POST", body: JSON.stringify(payload) });
}

export function submitDat(sessionId, payload) {
  return request(`/api/sessions/${sessionId}/dat`, { method: "POST", body: JSON.stringify(payload) });
}

export function getState(sessionId) {
  return request(`/api/sessions/${sessionId}/state`);
}

export function startCalibration(type, sessionId) {
  return request(`/api/calibration/${type}/start`, {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId })
  });
}

export function getNextTrial(sessionId) {
  return request(`/api/sessions/${sessionId}/next-trial`);
}

export function postTrialEvents(trialId, payload) {
  return request(`/api/trials/${trialId}/events`, { method: "POST", body: JSON.stringify(payload) });
}

export function controllerDecision(trialId, payload = {}) {
  return request(`/api/trials/${trialId}/controller-decision`, {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export function completeTrial(trialId, payload) {
  return request(`/api/trials/${trialId}/complete`, { method: "POST", body: JSON.stringify(payload) });
}

export function saveClosingRatings(sessionId, ratings) {
  return request(`/api/sessions/${sessionId}/closing-ratings`, {
    method: "POST",
    body: JSON.stringify({ ratings })
  });
}
