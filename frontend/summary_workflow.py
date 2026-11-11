"""In-memory summary orchestration without checkpointing or external graph tracing."""

from collections.abc import AsyncIterator, Callable
from contextlib import aclosing
from typing import TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context


class _State(TypedDict):
    result: dict | None


async def run(generate: Callable[[], AsyncIterator[dict]]) -> AsyncIterator[dict]:
    """Preserve progress, tokens, warnings and the first result from existing engines."""
    async def prepare(state):
        return {"result": None}

    async def generate_result(state):
        writer = get_stream_writer()
        async with aclosing(generate()) as events:
            async for event in events:
                if event["type"] == "ferdig":
                    return {"result": event}
                writer(event)
        return {"result": None}

    async def finish(state):
        if state["result"] is None:
            raise RuntimeError("Generation ended without a result")
        get_stream_writer()(state["result"])
        return {}

    builder = StateGraph(_State)
    builder.add_node("prepare", prepare)
    builder.add_node("generate", generate_result)
    builder.add_node("finish", finish)
    builder.add_edge(START, "prepare")
    builder.add_edge("prepare", "generate")
    builder.add_edge("generate", "finish")
    builder.add_edge("finish", END)
    graph = builder.compile()  # No checkpointer: meeting state must not be persisted here.
    # Suppress even ambient LangSmith tracing; Langfuse uses our separate metadata exporter.
    with tracing_context(enabled=False):
        async with aclosing(graph.astream({"result": None}, stream_mode="custom", config={"callbacks": []})) as events:
            async for event in events:
                yield event
