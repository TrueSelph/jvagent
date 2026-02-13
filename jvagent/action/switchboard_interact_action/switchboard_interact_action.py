"""Switchboard interact action for agent routing and selection."""

from typing import List
from jvagent.action.interact.base import InteractAction
from jvagent.action.interact.interact_walker import InteractWalker
from jvagent.core.agent import Agent
from jvagent.core.agents import Agents
from jvspatial.core.annotations import attribute


class SwitchboardInteractAction(InteractAction):
    """Switchboard action that presents available agents and routes users to selected agents.
    
    This action manages agent routing by:
    1. Presenting a list of available agents to the user
    2. Routing users to their selected agent via sub-walker spawning
    3. Maintaining agent selection state in conversation context
    
    The action runs with always_execute=True to handle routing on every interaction
    where a switchboard_agent is set in the conversation context.
    """

    # Configuration attributes
    switchboard_agents: List[dict] = attribute(
        default_factory=list,
        description="List of available switchboard agents with id, name, alias, and description"
    )
    
    available_switchboard_agents_directive: str = attribute(
        default="Present these agents to the user and ask them to choose a single agent from the list:\n\n{agents}",
        description="Directive template to present available switchboard agents to the user"
    )

    always_execute: bool = attribute(
        default=True,
        description="Always execute regardless of routing to handle agent selection and routing",
    )



    async def execute(self, visitor: InteractWalker) -> None:
        """Execute switchboard routing logic.
        
        This method handles two scenarios:
        1. If a switchboard_agent is set in conversation context, route to that agent
        2. Otherwise, present the list of available agents for user selection
        
        Args:
            visitor: InteractWalker instance containing interaction context
        """
        # Skip if switchboard interview is already running
        if "SwitchboardInterviewInteractAction" in visitor.interaction.actions:
            return

        # Check if agent routing is requested
        conversation = visitor.conversation
        switchboard_agent = conversation.context.get("switchboard_agent")

        if switchboard_agent:
            target_agent_id = switchboard_agent.get("id")
            if target_agent_id:
                target_agent = await Agent.get(target_agent_id)
                
                # If agent not found by ID, try to find by name
                if not target_agent:
                    switchboard_agents = await self.get_switchboard_agents()
                    for agent in switchboard_agents:
                        if agent.get("name") == switchboard_agent.get("name"):
                            target_agent = await Agent.get(agent.get("id"))
                            conversation.context["switchboard_agent"]["id"] = target_agent.id
                            break
                
                # Route to target agent if found
                if target_agent:
                    # Prepare data (copy from current visitor)
                    data = visitor.data.copy()
                    data['switchboard_agent_id'] = target_agent.id

                    # Create and spawn sub-walker for target agent
                    sub_walker = InteractWalker(
                        agent_id=target_agent_id,
                        utterance=visitor.utterance,
                        channel=visitor.channel,
                        data=data,
                        session_id=visitor.session_id,
                        user_id=visitor.user_id,
                        user_name=visitor.user_name,
                        stream=visitor.stream
                    )

                    # Spawn walker on the target agent
                    await sub_walker.spawn(target_agent)

                return

        # Present available agents for selection
        switchboard_agents = await self.get_switchboard_agents()
        agents_str = ", ".join(agent["alias"] for agent in switchboard_agents)
        
        await visitor.add_directives([self.available_switchboard_agents_directive.format(agents=agents_str)])
        await self.respond(visitor)



    async def get_switchboard_agents(self) -> list[dict]:
        """Retrieve list of available switchboard agents.
        
        If switchboard_agents is not configured, fetches all connected agents
        from the Agents node, excluding the current agent.
        
        Returns:
            List of agent dictionaries containing id, name, alias, and description
        """
        # If switchboard_agents is not configured or empty, fetch from Agents node
        if not self.switchboard_agents:
            agents_node = await Agents.get()
            if not agents_node:
                return []
            self.switchboard_agents = []

            connected_agents = await agents_node.get_connected_agents()

            agent = await self.get_agent()
            agent_name = agent.name
            
            # Add all agents except the current one
            for _agent in connected_agents:
                if _agent.name != agent_name:
                    self.switchboard_agents.append({
                        "id": _agent.id,
                        "name": _agent.name,
                        "alias": _agent.alias,
                        "description": _agent.description
                    })
            await self.save()
        return self.switchboard_agents

