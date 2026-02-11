"""Onboarding interact action."""
from jvspatial.core import Node
from typing import List, Dict, Any
from jvagent.action.interact.base import InteractAction
from jvagent.action.interact.interact_walker import InteractWalker
from jvspatial.core.annotations import attribute
import logging
logger = logging.getLogger(__name__)

class OnboardingInteractAction(InteractAction):
    """This action only allows verified ranks to interact with the agent."""
    
    intro_directive: str = attribute(
        default="Introduce yourself by giving your name and mention that you are an AI assistant here at the GDF and that you can help them with base protocols, general info, and short pass requests.",
        description="Welcome message template"
    )

    unverified_directive: str = attribute(
        default="Tell the user: 'Sorry, you do not have access to ARC, the AI assistant at the GDF'. Please contact support to onboard your number.",
        description="Directive to show when the user is not verified"
    )

    always_execute: bool = attribute(
        default=True,
        description="Always execute regardless of routing (first-time user intro handler).",
    )


    async def execute(self, visitor: InteractWalker) -> None:
        """Execute onboarding process for new users."""

        
        # CHECK IF TO EXECUTE ACTION
        interaction = visitor.interaction
        user = await interaction.get_user()
        subscriber_display_name = user.get_display_name() if user else "user"
        subscriber_phone = user.user_id
        rank_profile = getattr(user, 'rank_profile', {})
        channel = getattr(interaction, 'channel', '')
        is_new_user = visitor.new_user
        api = await self.get_action("ArcAPIAction")

        if not api or channel != 'whatsapp' or not is_new_user:
            return

        # EXECUTE ACTION

        # # grab basic profile data based on registered phone
        # result = await api.verify_phone(phone = subscriber_phone)
        # profile_data = await api.retrieve_rank_info(pin="", session_id=subscriber_phone)
        result = {'first_name': 'Tharick', 'last_name': 'Jairam', 'is_first_time': False, 'is_security_question_set': True, 'is_pin_set': True, 'rank': {'name': 'Lt Col', 'full_name': 'Lieutenant Colonel'}}
        profile_data = {'ident_code': 'MiPWJFWbxqPccfusEygn', 'regimental_number': '15264', 'unit': {'id': 4, 'name': 'Artillery'}, 'sub_unit': None, 'supervisor': {'first_name': 'John', 'last_name': 'Brown', 'regimental_number': '34342', 'phone': '5926415808', 'is_unit_supervisor': True}}

        
        if(result and profile_data):
            # set the user name
            rank_name = f"{result.get("rank", {}).get("full_name", "")} {result.get('first_name', "")} {result.get('last_name', "")}"
            if rank_name:
                user.display_name = rank_name
                await user.save()


            # save rank profile to the conversation 
            rank_profile = await RankProfile.create(
                ident_code=profile_data.get("ident_code", ""),
                regimental_number=profile_data.get("regimental_number", ""),
                unit=profile_data.get("unit", {}),
                sub_unit=profile_data.get("sub_unit", {}),
                supervisor=profile_data.get("supervisor", {}),
                first_name=result.get("first_name", ""),
                last_name=result.get("last_name", ""),
                is_first_time=result.get("is_first_time", False),
                is_security_question_set=result.get("is_security_question_set", False),
                is_pin_set=result.get("is_pin_set", False),
                rank=result.get("rank", {}),
            )
            await user.connect(rank_profile)


            await visitor.add_directives([self.intro_directive])
        else:
            await visitor.add_directives([self.unverified_directive])



class RankProfile(Node):
    """Rank profile node."""
    
    ident_code: str = attribute(
        default="",
        description="Ident code of the rank",
    )
    regimental_number: str = attribute(
        default="",
        description="Regimental number of the rank",
    )
    unit: Dict[str, Any] = attribute(
        default_factory=dict,
        description="Unit of the rank",
    )
    sub_unit: Dict[str, Any] = attribute(
        default_factory=dict,
        description="Sub unit of the rank",
    )
    supervisor: Dict[str, Any] = attribute(
        default_factory=dict,
        description="Supervisor of the rank",
    )
    first_name: str = attribute(
        default="",
        description="First name of the rank",
    )
    last_name: str = attribute(
        default="",
        description="Last name of the rank",
    )
    is_first_time: bool = attribute(
        default=False,
        description="Whether the rank is a first time user",
    )
    is_security_question_set: bool = attribute(
        default=False,
        description="Whether the rank has set a security question",
    )
    is_pin_set: bool = attribute(
        default=False,
        description="Whether the rank has set a PIN",
    )
    rank: Dict[str, Any] = attribute(
        default_factory=dict,
        description="Rank of the rank",
    )