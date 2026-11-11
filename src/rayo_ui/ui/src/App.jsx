import { useCallback, useEffect, useState } from 'react';
import { appInfo, createGreeting, deleteGreeting, hello, listGreetings } from './api.js';

export default function App() {
  const [name, setName] = useState('');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [greetings, setGreetings] = useState([]);
  const [info, setInfo] = useState(null);

  const run = useCallback(async (action) => {
    setError('');
    try {
      await action();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  const refresh = useCallback(() => run(async () => setGreetings(await listGreetings())), [run]);

  useEffect(() => {
    let active = true;
    listGreetings()
      .then((items) => active && setGreetings(items))
      .catch((err) => active && setError(err.message));
    return () => {
      active = false;
    };
  }, []);

  return (
    <main>
      <h1>Rayo</h1>

      <form
        onSubmit={(event) => {
          event.preventDefault();
          run(async () => setMessage((await hello(name)).message));
        }}
      >
        <label htmlFor="name">Name</label>
        <input id="name" value={name} onChange={(event) => setName(event.target.value)} />
        <button type="submit">Greet</button>
        <button
          type="button"
          onClick={() =>
            run(async () => {
              await createGreeting(name);
              await refresh();
            })
          }
        >
          Save
        </button>
      </form>

      {message && <p role="status">{message}</p>}
      {error && <p role="alert">{error}</p>}

      <h2>Saved greetings</h2>
      {greetings.length === 0 ? (
        <p>Nothing saved yet.</p>
      ) : (
        <ul>
          {greetings.map((item) => (
            <li key={item.id}>
              {item.message}{' '}
              <button
                type="button"
                aria-label={`Delete ${item.name}`}
                onClick={() =>
                  run(async () => {
                    await deleteGreeting(item.id);
                    await refresh();
                  })
                }
              >
                Delete
              </button>
            </li>
          ))}
        </ul>
      )}

      <h2>Native action</h2>
      <button type="button" onClick={() => run(async () => setInfo(await appInfo()))}>
        About this app
      </button>
      {info && (
        <p role="status">
          {info.name} {info.version}
        </p>
      )}
    </main>
  );
}
