import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { axe } from 'vitest-axe';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App from './App.jsx';

/** Fake the Python side: each key is "METHOD path" and holds the JSON that is returned. */
function mockApi(table) {
  const routes = new Map(Object.entries(table));
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path, options = {}) => {
      const key = `${options.method ?? 'GET'} ${path}`;
      if (!routes.has(key)) return { ok: false, status: 404, json: async () => ({ detail: key }) };
      return { ok: true, status: 200, json: async () => routes.get(key) };
    }),
  );
}

describe('App', () => {
  beforeEach(() => {
    mockApi({
      'GET /api/v1/greetings': [],
      'GET /api/v1/hello/Ada': { message: 'Hello, Ada!' },
      'POST /api/bridge/app_info': {
        available: true,
        result: { name: 'RayoHX', version: '1.2.3' },
      },
    });
  });

  afterEach(() => vi.unstubAllGlobals());

  it('shows the greeting returned by the API', async () => {
    render(<App />);
    await userEvent.type(screen.getByLabelText('Name'), 'Ada');
    await userEvent.click(screen.getByRole('button', { name: 'Greet' }));
    expect(await screen.findByText('Hello, Ada!')).toBeInTheDocument();
  });

  it('shows the native action result', async () => {
    render(<App />);
    await userEvent.click(screen.getByRole('button', { name: 'About this app' }));
    expect(await screen.findByText('RayoHX 1.2.3')).toBeInTheDocument();
  });

  it('shows an error when a call fails', async () => {
    render(<App />);
    await userEvent.click(screen.getByRole('button', { name: 'Greet' }));
    expect(await screen.findByRole('alert')).toBeInTheDocument();
  });

  it('has no accessibility violations', async () => {
    const { container } = render(<App />);
    expect(await screen.findByText('Nothing saved yet.')).toBeInTheDocument();
    expect(await axe(container)).toHaveNoViolations();
  });
});
