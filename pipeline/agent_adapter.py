from __future__ import annotations

from agent.agent import Agent, AgentRunResult


class SingleStepAgentAdapter:
    def __init__(self, agent: Agent) -> None:
        if not isinstance(agent, Agent):
            raise TypeError("agent must be an Agent instance")
        
        self.agent = agent
        
    def __call__(
        self, 
        context: str, 
        *,
        session_id: str,
    ) -> AgentRunResult:
        if not isinstance(context, str) or not context.strip():
            raise ValueError("context must be a non-empty string")
        
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must be a non-empty string")
        
        return self.agent.run_task(
            context,
            session_id=session_id,
        )
        
def make_agent_adapter() -> SingleStepAgentAdapter:
    return SingleStepAgentAdapter(Agent())