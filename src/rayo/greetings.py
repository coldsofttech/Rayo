"""An in-memory greeting collection. It never prints or exits, so any front end can reuse it."""

from dataclasses import dataclass
from itertools import count

from rayo.core import greet


class GreetingNotFoundError(LookupError):
    """Raised when a greeting id does not exist."""


@dataclass(frozen=True)
class Greeting:
    """A stored greeting."""

    id: int
    name: str
    message: str


class GreetingStore:
    """Create, read, update and delete greetings. Replace it with your own storage."""

    def __init__(self) -> None:
        self._items: dict[int, Greeting] = {}
        self._ids = count(1)

    def list(self) -> list[Greeting]:
        """Return all greetings, oldest first."""
        return list(self._items.values())

    def get(self, greeting_id: int) -> Greeting:
        """Return one greeting.

        Raises:
            GreetingNotFoundError: If ``greeting_id`` does not exist.
        """
        try:
            return self._items[greeting_id]
        except KeyError:
            raise GreetingNotFoundError(f"greeting {greeting_id} not found") from None

    def create(self, name: str) -> Greeting:
        """Add a greeting for ``name``.

        Raises:
            ValueError: If ``name`` is empty or only whitespace.
        """
        item = Greeting(id=next(self._ids), name=name.strip(), message=greet(name))
        self._items[item.id] = item
        return item

    def update(self, greeting_id: int, name: str) -> Greeting:
        """Change the name of a greeting.

        Raises:
            GreetingNotFoundError: If ``greeting_id`` does not exist.
            ValueError: If ``name`` is empty or only whitespace.
        """
        self.get(greeting_id)
        item = Greeting(id=greeting_id, name=name.strip(), message=greet(name))
        self._items[greeting_id] = item
        return item

    def delete(self, greeting_id: int) -> None:
        """Remove a greeting.

        Raises:
            GreetingNotFoundError: If ``greeting_id`` does not exist.
        """
        self.get(greeting_id)
        del self._items[greeting_id]
