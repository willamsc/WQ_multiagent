"""Role contracts sit above Runtime; neither role schedules simulation or approves admission."""

from typing import Protocol

from .contracts import ContextPacket, ExecutionOutcome, Role, TaskEnvelope


class MainSignalGenerator(Protocol):
    async def generate(self, task: TaskEnvelope, context: ContextPacket) -> ExecutionOutcome: ...


class IndependentCritic(Protocol):
    async def review(self, task: TaskEnvelope, context: ContextPacket) -> ExecutionOutcome: ...


class GeneratorRole:
    def __init__(self, runtime):
        self.runtime = runtime

    async def generate(self, task, context, **options):
        if task.role != Role.GENERATOR:
            raise ValueError("GENERATOR_ROLE_REQUIRED")
        return await self.runtime.execute(task, context, **options)


class CriticRole:
    def __init__(self, runtime):
        self.runtime = runtime

    async def review(self, task, context, **options):
        if task.role != Role.CRITIC:
            raise ValueError("CRITIC_ROLE_REQUIRED")
        return await self.runtime.execute(task, context, **options)
