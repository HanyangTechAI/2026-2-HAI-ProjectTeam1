from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol

from agent.agent import Agent

from context.tokenizer import TokenCounter, provisional_token_count

from memory.baselines import Embedder
from memory.schema import MemoryItem

from pipeline.orchestrator import GoalChecker, Orchestrator, PipelineRunResult, SelectionPolicy, make_baseline_policy


AgentFactory = Callable[[], Agent]


class PipelineAdapterProtocol(Protocol):
    def __call__(
        self,
        *,
        query: str,
        session_id: str,
        memories: Sequence[MemoryItem],
        total_budget: int,
        current_turn: int,  
    ) -> PipelineRunResult:
        ...
        

class PipelineAdapter:
    """실행마다 새 Agent를 만들어 입력 스냅샷을 Orchestrator에 전달한다."""
    def __init__(
        self,
        *,
        policy: SelectionPolicy,
        system_prompt: str,
        tool_definitions: str,
        agent_factory: AgentFactory = Agent,
        token_counter: TokenCounter = provisional_token_count,
        max_steps: int = 8,
        goal_checker: GoalChecker | None = None,
    ) -> None:
        if not callable(policy):
            raise TypeError("policy must be callable")
        
        if not callable(agent_factory):
            raise TypeError("agent_factory must be callable")
        
        if not callable(token_counter):
            raise TypeError("token_counter must be callable")
        
        if not isinstance(system_prompt, str):
            raise TypeError("system_prompt must be a string")
        
        if not isinstance(tool_definitions, str):
            raise TypeError("tool_definitions must be a string")
        
        if type(max_steps) is not int or max_steps < 1:
            raise ValueError("max_steps must be a positive integer")
        
        if goal_checker is not None and not callable(goal_checker):
            raise TypeError("goal_checker must be callable")
        
        self.policy = policy
        self.agent_factory = agent_factory
        
        self.system_prompt = system_prompt
        self.tool_definitions = tool_definitions
        
        self.token_counter = token_counter
        self.max_steps = max_steps
        self.goal_checker = goal_checker
        
    def __call__(
        self, 
        *,
        query: str,
        session_id: str,
        memories: Sequence[MemoryItem],
        total_budget: int,
        current_turn: int,  
    ) -> PipelineRunResult:
        agent = self.agent_factory()
        
        if not isinstance(agent, Agent):
            raise TypeError("agent_factory must return an Agent instance")
        
        orchestrator = Orchestrator(
            agent=agent,
            policy=self.policy,
            system_prompt=self.system_prompt,
            tool_definitions=self.tool_definitions,
            token_counter=self.token_counter,
            max_steps=self.max_steps,
            goal_checker=self.goal_checker,
        )
        
        return orchestrator.run(
            query=query,
            session_id=session_id,
            memories=memories,
            total_budget=total_budget,
            current_turn=current_turn,
        )
        
        
def make_baseline_pipeline_adapter(
    strategy: str,
    *,
    system_prompt: str,
    tool_definitions: str,
    agent_factory: AgentFactory = Agent,
    embedder: Embedder | None = None,
    top_k: int = 20,
    utility_weights: tuple[float, float, float] = (0.6, 0.3, 0.1),
    token_counter: TokenCounter = provisional_token_count,
    max_steps: int = 8,
    goal_checker: GoalChecker | None = None,
) -> PipelineAdapter:
    """전략 이름을 공통 선택 정책으로 바꾸어 실행 adapter를 구성한다."""
    policy = make_baseline_policy(
        strategy,
        embedder=embedder,
        top_k=top_k,
        utility_weights=utility_weights,
    )
    
    return PipelineAdapter(
        policy=policy,
        system_prompt=system_prompt,
        tool_definitions=tool_definitions,
        agent_factory=agent_factory,
        token_counter=token_counter,
        max_steps=max_steps,
        goal_checker=goal_checker,
    )
