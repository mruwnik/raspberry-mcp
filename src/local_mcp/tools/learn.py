"""Spaced-repetition tools for learning things in conversation."""

from fastmcp import FastMCP

from local_mcp.lib import learn

mcp = FastMCP(name="learn")


@mcp.tool()
def learn_log(deck: str, entries: list[learn.Entry]) -> dict:
    """
    Record what happened to items in conversation. Send one batched call per turn.

    Unknown items are created, so this also imports words.

    Args:
        deck: e.g. "japanese"
        entries: list of {front, event, reading?, back?, note?}
            - front: the item itself (word, term, question)
            - event: "seen" (came up, no reaction; doesn't affect scheduling),
                     "asked" (user asked what it meant), "used" (user used it correctly),
                     "misused" (user used it wrongly)
            - reading/back/note: set or overwrite these fields

    Returns {"items": [...]} with each item's updated state.
    """
    try:
        return {"items": learn.log(deck, entries)}
    except ValueError as e:
        return {"error": str(e)}


@mcp.tool()
def learn_due(deck: str, limit: int = 20) -> dict:
    """
    Items due for review, hardest (lowest ease) first. Work these back into conversation.

    Returns {"items": [...]}.
    """
    return {"items": learn.due(deck, limit)}


@mcp.tool()
def learn_get(deck: str, front: str) -> dict:
    """
    Look up one item, with its full event history. Use when unsure whether the user knows it.
    """
    return learn.get(deck, front) or {"error": f"No item {front!r} in deck {deck!r}"}


@mcp.tool()
def learn_edit(
    deck: str,
    front: str,
    reading: str | None = None,
    back: str | None = None,
    note: str | None = None,
    retired: bool | None = None,
) -> dict:
    """
    Fix an item's fields, or set retired=true to stop scheduling it ("easy, stop showing me").

    Only the fields passed are changed.
    """
    fields = {"reading": reading, "back": back, "note": note, "retired": retired}
    try:
        return learn.edit(deck, front, **{k: v for k, v in fields.items() if v is not None})
    except KeyError as e:
        return {"error": str(e)}


@mcp.tool()
def learn_recent(deck: str, session: int | None = None) -> dict:
    """
    Items touched in a session, in order, with that session's events.

    Sessions are assigned automatically: a new one starts after 30 minutes of no events.

    Args:
        session: session number; defaults to the deck's latest

    Returns {"session": n, "items": [...]}.
    """
    return learn.recent(deck, session)
