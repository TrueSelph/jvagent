# UserModelAction

UserModelAction is an InteractAction that automatically maintains a user profile by analyzing conversation history and extracting facts and preferences using an LLM.

## Overview

UserModelAction passively observes conversations and builds a structured profile of the user in markdown format. It:

1. Runs periodically based on configurable update frequency
2. Analyzes recent conversation history
3. Uses an LLM to extract facts and preferences from USER messages only
4. Intelligently consolidates related information
5. Stores the profile in markdown format on the User object

## How It Works

### Execution Flow

1. **Triggered on Interaction**: Runs every N interactions (configurable via `update_frequency`)
2. **Gathers Context**: Retrieves recent conversation history (configurable via `history_limit`)
3. **LLM Analysis**: Sends conversation history to LLM with system prompt
4. **Profile Update**: Parses markdown response and updates `user.user_model`
5. **Change Detection**: Only saves if profile has meaningfully changed

### Key Features

- **Intelligent Consolidation**: Groups related items (e.g., "likes Caribbean cuisine", "enjoys Caribbean music" → "interested in Caribbean culture (cuisine, music, festivals)")
- **USER-Only Extraction**: Only extracts from user's messages, not AI recommendations
- **Update Throttling**: Configurable frequency to reduce LLM costs
- **Markdown Format**: Human-readable profile format
- **Change Detection**: Only updates when new information is found

## Configuration

### Properties

- `model_action_type`: Type of LanguageModelAction to use (default: "OpenAILanguageModelAction")
- `model`: LLM model to use (default: "gpt-4o")
- `model_temperature`: Temperature for generation (default: 0.1 for consistency)
- `model_max_tokens`: Max tokens for response (default: 1000)
- `update_frequency`: Update every N interactions (default: 2)
- `history_limit`: Number of recent interactions to analyze (default: 6)
- `weight`: Execution weight (default: 150 to run after routing)
- `always_execute`: Always run regardless of routing (default: true)

### Example Configuration

```yaml
actions:
  - action: jvagent/user_model_action
    context:
      enabled: true
      model: "gpt-4o"
      update_frequency: 2  # Update every 2 interactions
      history_limit: 6     # Analyze last 6 interactions
```

## Profile Format

The user profile is stored as markdown with the following structure:

```markdown
## Facts
- fact 1
- fact 2

## Preferences
### Topics
- topic 1
- topic 2

### Style
Brief description of communication style
```

### Example Profile

```markdown
## Facts
- Lives in Georgetown, Guyana
- Interested in Caribbean culture (cuisine, music, festivals)
- Works in technology sector

## Preferences
### Topics
- Guyanese history and culture
- Technology and software development
- Caribbean travel

### Style
Prefers casual, conversational tone with detailed explanations
```

## Usage

### Accessing User Profile

The profile is stored on the User object and can be accessed by other actions:

```python
async def execute(self, visitor):
    interaction = visitor.interaction
    user = await interaction.get_user()

    if user.user_model:
        # user.user_model contains markdown-formatted profile
        print(f"User profile:\n{user.user_model}")
```

### Searching User Profile

Use the `search_profile()` method to query the user profile for specific information:

```python
from jvagent.action.user_model.user_model_action import UserModelAction

async def execute(self, visitor):
    interaction = visitor.interaction
    user = await interaction.get_user()

    # Get UserModelAction
    user_model_action = await self.get_action(UserModelAction)
    if not user_model_action:
        return

    # Search for specific information
    food_prefs = await user_model_action.search_profile(
        user_id=user.id,
        query="What foods does the user want to try?"
    )

    if food_prefs:
        # Use in response
        response = f"I see you want to try {food_prefs}!"
```

**Search Examples**:
- `"What is the user's name?"` → Returns: "Marcia"
- `"What foods does the user want to try?"` → Returns: "Black cake, pepperpot"
- `"What are the user's travel plans?"` → Returns: "Visiting Guyana next week"
- `"What cuisines is the user interested in?"` → Returns: "Guyanese cuisine, Caribbean culture"

**Benefits**:
- Returns only relevant information (token efficient)
- Returns `None` if information not found
- Preserves markdown formatting
- Fast LLM-based extraction

### Integration with PersonaAction

You can include the user profile in conversation context to personalize responses:

```python
# In your system prompt
if user.user_model:
    prompt += f"\n\nUser Profile:\n{user.user_model}\n"
```

## Prompt Engineering

The action uses a carefully crafted prompt that:

1. **Extracts ONLY from USER messages** (not AI recommendations)
2. **Consolidates similar items** to keep the profile concise
3. **Returns "No updates"** when nothing new is learned
4. **Validates output** to ensure markdown format

### Key Prompt Rules

- Extract only explicit information from USER's messages
- Keep facts concise (max 15 words each)
- Intelligently consolidate related items
- Compact topics list to ~10 items
- Remove outdated information
- Return plain markdown (no code blocks)

## Best Practices

1. **Set Appropriate Update Frequency**: Balance between freshness and LLM costs
   - High frequency (1-2): More responsive but higher costs
   - Low frequency (5-10): Lower costs but slower updates

2. **Use Adequate History**: More context helps LLM make better decisions
   - Minimum: 3-5 interactions
   - Recommended: 6-10 interactions
   - Maximum: Depends on token limits

3. **Monitor Profile Quality**: Regularly review user profiles to ensure:
   - Facts are accurate and recent
   - Consolidation is working properly
   - No AI recommendations leaked into profile

4. **Privacy Considerations**:
   - User profiles contain personal information
   - Implement data retention policies
   - Provide user access to their profile
   - Allow users to update or delete their profile

## Troubleshooting

### Profile Not Updating

**Symptoms**: User model stays empty or doesn't update

**Solutions**:
1. Check `interaction_count` reaches `update_frequency` threshold
2. Verify conversation history is available
3. Check LLM response in logs (debug level)
4. Ensure `always_execute=True` so action runs

### Poor Consolidation

**Symptoms**: Many similar entries not grouped together

**Solutions**:
1. Review prompt wording for consolidation rules
2. Increase `model_temperature` slightly (e.g., 0.2)
3. Use more capable model (e.g., gpt-4o instead of gpt-4o-mini)
4. Provide more conversation context (increase `history_limit`)

### AI Recommendations in Profile

**Symptoms**: Profile contains information AI suggested, not user stated

**Solutions**:
1. Review prompt emphasizes USER messages only
2. Check conversation history format clearly marks USER vs ASSISTANT
3. Add more examples to prompt showing correct vs incorrect extraction

### "No updates" Always Returned

**Symptoms**: LLM always returns "No updates" even with new information

**Solutions**:
1. Check if conversation history includes user utterances
2. Verify system prompt is properly formatted
3. Review LLM response for errors or refusals
4. Try different model or adjust temperature

## Example Implementation

### Basic Setup

```yaml
# agent.yaml
actions:
  - action: jvagent/user_model_action
    context:
      enabled: true
      update_frequency: 2
      history_limit: 6
```

### Using Profile in Responses

```python
from jvagent.action.persona.prompts import USER_MODEL_PROFILE_PROMPT

class MyPersonaAction(PersonaAction):
    async def _compose_prompt(self, interaction):
        # ... base prompt composition ...

        # Add user profile context
        user = await interaction.get_user()
        if user and user.user_model:
            profile_section = USER_MODEL_PROFILE_PROMPT.format(
                user_model_profile=user.user_model
            )
            composed += f"\n\n{profile_section}"

        return composed
```

## Dependencies

- Requires a LanguageModelAction (e.g., OpenAILanguageModelAction)
- Stores profile on User object (`user.user_model`)
- Uses conversation history from Interaction chain

## Performance Considerations

- **LLM Costs**: Each update makes an LLM call. Adjust `update_frequency` to balance freshness vs cost.
- **Token Usage**: Profile updates typically use 500-1500 tokens depending on history length.
- **Database Writes**: Only writes when profile changes, minimizing database load.

## Future Enhancements

Potential improvements for future versions:

1. **Structured Data**: Support for typed fields (birthday, location, etc.)
2. **Profile Sections**: Configurable sections beyond facts/preferences
3. **Diff Tracking**: Track what changed in each update
4. **User Feedback**: Allow users to correct or approve profile updates
5. **Privacy Controls**: User-configurable update frequency and data retention
