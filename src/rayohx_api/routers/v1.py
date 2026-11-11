"""Version 1 of the API, served under ``/api/v1``."""

from functools import lru_cache
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Response, status

from rayohx import Greeting, GreetingNotFoundError, GreetingStore, greet
from rayohx_api.auth import Principal, authenticate
from rayohx_api.schemas import (
    GreetingCreate,
    GreetingResponse,
    GreetingUpdate,
    HelloResponse,
    WhoAmIResponse,
)

router = APIRouter(tags=["v1"])

GreetingId = Annotated[int, Path(ge=1, description="Greeting id.")]
NOT_FOUND: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {"description": "Greeting not found."}
}
INVALID: dict[int | str, dict[str, Any]] = {
    status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Invalid name."}
}


@lru_cache
def get_store() -> GreetingStore:
    """Return the greeting store, created once per process. Swap it for a real storage layer."""
    return GreetingStore()


Store = Annotated[GreetingStore, Depends(get_store)]


def _response(item: Greeting) -> GreetingResponse:
    return GreetingResponse(id=item.id, name=item.name, message=item.message)


def _invalid(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))


def _not_found(exc: GreetingNotFoundError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


# A plain ``def`` handler runs in a worker thread, so blocking library calls are safe.
# Use ``async def`` only when everything inside is awaited.
@router.get("/hello/{name}", summary="Greet someone", responses=INVALID)
def hello(name: Annotated[str, Path(description="Who to greet.")]) -> HelloResponse:
    """Return a greeting without storing it."""
    try:
        return HelloResponse(message=greet(name))
    except ValueError as exc:
        raise _invalid(exc) from exc


@router.get(
    "/whoami",
    summary="Show the caller",
    responses={status.HTTP_401_UNAUTHORIZED: {"description": "Authentication failed."}},
)
def whoami(principal: Annotated[Principal, Depends(authenticate)]) -> WhoAmIResponse:
    """Return the authenticated caller. Protected unless authentication is switched off."""
    return WhoAmIResponse(subject=principal.subject, method=principal.method)


@router.get("/greetings", summary="List greetings")
def list_greetings(store: Store) -> list[GreetingResponse]:
    """Return every stored greeting."""
    return [_response(item) for item in store.list()]


@router.post(
    "/greetings",
    summary="Create a greeting",
    status_code=status.HTTP_201_CREATED,
    responses=INVALID,
)
def create_greeting(body: GreetingCreate, store: Store) -> GreetingResponse:
    """Store a greeting for ``name``."""
    try:
        return _response(store.create(body.name))
    except ValueError as exc:
        raise _invalid(exc) from exc


@router.get("/greetings/{greeting_id}", summary="Get a greeting", responses=NOT_FOUND)
def get_greeting(greeting_id: GreetingId, store: Store) -> GreetingResponse:
    """Return one greeting."""
    try:
        return _response(store.get(greeting_id))
    except GreetingNotFoundError as exc:
        raise _not_found(exc) from exc


@router.put("/greetings/{greeting_id}", summary="Replace a greeting", responses=NOT_FOUND | INVALID)
def replace_greeting(
    greeting_id: GreetingId, body: GreetingCreate, store: Store
) -> GreetingResponse:
    """Replace the name, and so the message, of a greeting."""
    try:
        return _response(store.update(greeting_id, body.name))
    except GreetingNotFoundError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise _invalid(exc) from exc


@router.patch(
    "/greetings/{greeting_id}", summary="Update a greeting", responses=NOT_FOUND | INVALID
)
def update_greeting(
    greeting_id: GreetingId, body: GreetingUpdate, store: Store
) -> GreetingResponse:
    """Change only the fields that are sent."""
    try:
        current = store.get(greeting_id)
        return _response(store.update(greeting_id, body.name or current.name))
    except GreetingNotFoundError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise _invalid(exc) from exc


@router.delete(
    "/greetings/{greeting_id}",
    summary="Delete a greeting",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=NOT_FOUND,
)
def delete_greeting(greeting_id: GreetingId, store: Store) -> Response:
    """Remove a greeting."""
    try:
        store.delete(greeting_id)
    except GreetingNotFoundError as exc:
        raise _not_found(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
