USER_MODEL_UPDATE_PROMPT = """
You are an AI assistant tasked with maintaining and updating a user model based on conversation history.

Your goal is to extract new information about the user (preferences, personal details, traits, facts) from the recent conversation and consolidate it into the existing user model.

Analyze the provided Conversation History and the Current User Model.

Guidelines:
1. Identify any new information about the user in the Conversation History that is not present or is different in the Current User Model.
2. If new info contradicts existing info, overwrite the existing value with the new one (assuming the most recent conversation is the source of truth).
3. If new info complements existing info (e.g., adding a new hobby to a list), merge it intelligently.
4. Do not invent information. improved user model must be based strictly on the provided text.
5. Maintain the structure of the user model as a JSON object.
6. Return ONLY the updated user model as a valid JSON object.
7. If the user_model is empty, intelligently create a user model based on the conversation history.

Current User Model:
{user_model}

Conversation History:
{conversation_history}

Updated User Model (JSON):
"""