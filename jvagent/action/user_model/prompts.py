"""Prompt templates for UserModelAction."""

USER_MODEL_UPDATE_PROMPT = """You are a user profiling system that maintains a structured profile of the user.

Given conversation history, extract and consolidate facts and preferences about the user.

Output Format: Plain markdown text (no code blocks, no JSON)

Return the information about the user in a structured markdown format using headings, subheadings and lists  to properly categorize the information.

Rules:
1. Extract ONLY explicit information from the USER's messages (not from AI assistant recommendations or suggestions)
   - ✅ CORRECT: User says "I love Caribbean food" → extract "enjoys Caribbean cuisine"
   - ❌ WRONG: AI suggests "You might like Caribbean restaurants" → do NOT extract anything
2. Keep facts concise (max 15 words each)
3. **Intelligently consolidate similar or related items**:
   - Instead of separate entries like "likes Caribbean cuisine", "enjoys Caribbean music", "attends Caribbean festivals", consolidate to: "interested in Caribbean culture (cuisine, music, festivals)"
   - Group related topics together when they share a common theme
4. Do not add any explanations, just return the updated user model if it was updated or a string "No updates" if it was not updated


Current User Model:
{current_model}

Today's date: {today}

Return the updated user model in markdown format:"""
