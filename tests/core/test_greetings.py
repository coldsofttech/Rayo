import pytest

from example_package import GreetingNotFoundError, GreetingStore


def test_create_and_list() -> None:
    store = GreetingStore()

    item = store.create("  Ada ")

    assert (item.id, item.name, item.message) == (1, "Ada", "Hello, Ada!")
    assert store.list() == [item]


def test_ids_are_not_reused_after_delete() -> None:
    store = GreetingStore()
    first = store.create("Ada")
    store.delete(first.id)

    assert store.create("Grace").id == 2


def test_update_replaces_name_and_message() -> None:
    store = GreetingStore()
    item = store.create("Ada")

    updated = store.update(item.id, "Grace")

    assert (updated.name, updated.message) == ("Grace", "Hello, Grace!")
    assert store.get(item.id) == updated


def test_empty_name_is_rejected() -> None:
    store = GreetingStore()
    item = store.create("Ada")

    with pytest.raises(ValueError, match="empty"):
        store.create("  ")
    with pytest.raises(ValueError, match="empty"):
        store.update(item.id, "")


@pytest.mark.parametrize("action", ["get", "delete"])
def test_unknown_id_raises(action: str) -> None:
    store = GreetingStore()

    with pytest.raises(GreetingNotFoundError):
        getattr(store, action)(99)


def test_update_unknown_id_raises() -> None:
    with pytest.raises(GreetingNotFoundError):
        GreetingStore().update(99, "Ada")
