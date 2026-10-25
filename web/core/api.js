// HTTP client for the Tavern API. All calls return parsed JSON or throw ApiError.

export class ApiError extends Error {
  constructor(status, code, message, details) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

async function request(method, path, body) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch("/api" + path, options);
  } catch (e) {
    throw new ApiError(0, "network", "Нет связи с сервером");
  }
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch (e) {
    // non-JSON body (proxy error page): fall through with data = null
  }
  if (!response.ok) {
    const err = (data && data.error) || {};
    throw new ApiError(response.status, err.code || "http_" + response.status,
      err.message || response.statusText || "Ошибка запроса", err.details);
  }
  return data;
}

export const api = {
  get: (path) => request("GET", path),
  post: (path, body = {}) => request("POST", path, body),
  put: (path, body = {}) => request("PUT", path, body),
  del: (path) => request("DELETE", path),
};

// Subscribes to a generation run. Returns a function that closes the stream.
// EventSource reconnects by itself and sends Last-Event-ID, so the server resumes the stream.
export function followRun(runId, onEvent) {
  const source = new EventSource(`/api/runs/${runId}/events`);
  source.onmessage = (message) => {
    const event = JSON.parse(message.data);
    if (event.type === "ping") return;
    onEvent(event);
    if (["done", "stopped", "error"].includes(event.type)) source.close();
  };
  // A refused connection (run evicted, server restarted into a different state) is closed for
  // good by the browser; report it instead of leaving the UI waiting forever.
  source.onerror = () => {
    if (source.readyState === EventSource.CLOSED) {
      onEvent({ type: "error", message: null,
        error: { code: "connection_lost", message: "Потеряна связь с генерацией" } });
    }
  };
  return () => source.close();
}
