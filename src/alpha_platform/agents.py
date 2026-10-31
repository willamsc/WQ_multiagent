"""Runtime produces candidate data; it cannot schedule jobs or mutate the database."""

from typing import Protocol

from .domain import AlphaCandidate, ResearchConfig, Signal


class AgentRuntime(Protocol):
    async def generate_structured(
        self, task_type: str, context: dict, response_schema: type
    ) -> AlphaCandidate: ...


class MockAgentRuntime:
    async def generate_structured(self, task_type, context, response_schema):
        if task_type != "generate_candidate" or response_schema is not AlphaCandidate:
            raise ValueError("unsupported mock agent task")
        config = context["config"]
        return AlphaCandidate(
            config.expression,
            Signal("Synthetic offline hypothesis; no predictive claim", tuple(config.fields)),
        )


class Generator(Protocol):
    async def generate(self, config: ResearchConfig) -> AlphaCandidate: ...


class MockGenerator:
    def __init__(self, runtime: AgentRuntime | None = None):
        self.runtime = runtime or MockAgentRuntime()

    async def generate(self, config):
        return await self.runtime.generate_structured(
            "generate_candidate", {"config": config}, AlphaCandidate
        )
