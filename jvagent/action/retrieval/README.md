# RetrievalInteractAction

RetrievalInteractAction is a core InteractAction that retrieves relevant context from a vector store using the interaction's interpretation (or utterance as fallback) and composes a structured directive for PersonaAction to use when generating responses.

## Overview

RetrievalInteractAction bridges the gap between intent understanding (from InteractRouter) and response generation (by PersonaAction) by:

1. **Using the interpretation** (or utterance as fallback) as a search query
2. **Retrieving relevant context** from a configured vector store collection
3. **Formatting retrieved results** into a structured directive
4. **Adding the directive** to the interaction for PersonaAction to consume

## Architecture

### Execution Flow

```
InteractRouter (weight: -100)
    ↓
RetrievalInteractAction (weight: -50)
    ↓
    - Get interpretation or utterance
    - Search vector store
    - Format directive
    - Add to interaction
    ↓
PersonaAction (weight: 0+)
    ↓
    - Consumes directive via interaction.get_directives()
    - Includes in prompt
    - Generates response
```

### Integration Points

- **InteractRouter**: Uses `interaction.interpretation` if available (preferred)
- **PersonaAction**: Consumes directives via `interaction.get_directives()`
- **VectorStore**: Uses `search()` method with collection, query, and k parameters

## Configuration

### Attributes

- `vectorstore_action_type: str` - Entity type of VectorStore action (e.g., "TypesenseVectorStore"). If empty, uses first available VectorStore action.
- `collection: str` - Collection name to search in (default: "default")
- `k: int` - Number of search results to retrieve (default: 10, minimum: 1)
- `weight: int` - Execution weight (default: -50, runs after InteractRouter but before PersonaAction)
- `directive_template: Optional[str]` - Optional custom template for formatting the directive. Uses default structured format if not provided. Template should use `{results}` placeholder.
- `min_score_threshold: Optional[float]` - Optional minimum similarity score (0.0-1.0) to include results. Results below this threshold are filtered out.

### Example Configuration

```yaml
actions:
  - action: jvagent/retrieval_interact_action
    context:
      enabled: true
      vectorstore_action_type: "TypesenseVectorStore"
      collection: "knowledge_base"
      k: 5
      weight: -50
      min_score_threshold: 0.7
```

## Usage

### Basic Setup

1. **Register a VectorStore action** (e.g., TypesenseVectorStore)
2. **Register RetrievalInteractAction** with appropriate configuration
3. **Ensure InteractRouter runs first** (weight: -100) to generate interpretations
4. **PersonaAction will automatically consume** the generated directive

### Query Selection

RetrievalInteractAction uses the following priority for the search query:

1. **`interaction.interpretation`** - LLM-generated interpretation from InteractRouter (preferred)
2. **`interaction.utterance`** - Original user utterance (fallback)

If neither is available, the action skips retrieval.

### Directive Format

By default, RetrievalInteractAction uses a structured format:

```
Context retrieved from knowledge base:

1. [Document content/excerpt] (Relevance score: 0.850)
2. [Document content/excerpt] (Relevance score: 0.782)
3. [Document content/excerpt] (Relevance score: 0.745)

Use this context to inform your response to the user's query.
```

### Custom Directive Template

You can provide a custom template using the `directive_template` attribute:

```yaml
directive_template: |
  Relevant information from knowledge base:
  
  {results}
  
  Please incorporate this information into your response.
```

Note: The template should use `{results}` as a placeholder. The results will be formatted as a list of documents with scores.

## Examples

### Example 1: Basic Configuration

```yaml
actions:
  - action: jvagent/retrieval_interact_action
    context:
      enabled: true
      vectorstore_action_type: "TypesenseVectorStore"
      collection: "docs"
      k: 10
```

### Example 2: With Score Threshold

```yaml
actions:
  - action: jvagent/retrieval_interact_action
    context:
      enabled: true
      vectorstore_action_type: "TypesenseVectorStore"
      collection: "knowledge_base"
      k: 5
      min_score_threshold: 0.75  # Only include highly relevant results
```

### Example 3: Custom Template

```yaml
actions:
  - action: jvagent/retrieval_interact_action
    context:
      enabled: true
      vectorstore_action_type: "TypesenseVectorStore"
      collection: "faq"
      k: 3
      directive_template: |
        FAQ Context:
        {results}
        Use these FAQ entries to answer the user's question accurately.
```

## Dependencies

- **VectorStore Action**: Requires a VectorStore action to be registered (e.g., TypesenseVectorStore)
- **InteractRouter** (optional): If InteractRouter runs first, RetrievalInteractAction will use the interpretation. Otherwise, it falls back to the utterance.
- **PersonaAction**: Consumes the generated directive when composing prompts

## Error Handling

RetrievalInteractAction is designed to be non-blocking:

- **Missing VectorStore**: Logs warning and skips retrieval
- **No query available**: Logs debug message and skips retrieval
- **Search failures**: Logs error but doesn't raise (allows other actions to continue)
- **Empty results**: Logs debug message, no directive added

## Best Practices

1. **Collection Selection**: Use specific collections for different knowledge domains (e.g., "faq", "docs", "policies")
2. **Score Thresholds**: Set `min_score_threshold` to filter out low-relevance results and improve response quality
3. **Result Count**: Adjust `k` based on context window size and desired detail level
4. **Template Customization**: Use custom templates to match your agent's prompt style
5. **Weight Configuration**: Ensure weight is between InteractRouter (-100) and PersonaAction (0+) for proper execution order

## Troubleshooting

### No directive added

- Check that VectorStore action is registered and enabled
- Verify collection name exists in vector store
- Check logs for search errors
- Ensure interpretation or utterance is available

### Low-quality results

- Increase `min_score_threshold` to filter low-relevance results
- Verify vector store has relevant documents indexed
- Check that interpretation/utterance is clear and specific

### Directive not consumed by PersonaAction

- Verify PersonaAction is configured to use directives
- Check that PersonaAction runs after RetrievalInteractAction (weight ordering)
- Review PersonaAction prompt template for directive inclusion

