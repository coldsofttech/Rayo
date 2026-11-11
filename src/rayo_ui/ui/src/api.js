// Calls to Python. Cornea serves the page and the API from one address, so paths are relative and
// the session cookie is sent automatically.

/**
 * @param {string} path
 * @param {RequestInit} [options]
 */
async function request(path, options) {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (response.status === 204) return null;
  const body = await response.json();
  if (!response.ok) {
    throw new Error(body.detail || body.error || `Request failed (${response.status})`);
  }
  return body;
}

/** @param {string} name */
export const hello = (name) => request(`/api/v1/hello/${encodeURIComponent(name)}`);

export const listGreetings = () => request('/api/v1/greetings');

/** @param {string} name */
export const createGreeting = (name) =>
  request('/api/v1/greetings', { method: 'POST', body: JSON.stringify({ name }) });

/** @param {number} id */
export const deleteGreeting = (id) => request(`/api/v1/greetings/${id}`, { method: 'DELETE' });

/** Native action registered in Python: returns the app name and version. */
export async function appInfo() {
  const body = await request('/api/bridge/app_info', { method: 'POST', body: '{}' });
  if (!body.available) throw new Error(body.reason || 'Not available here');
  return body.result;
}
