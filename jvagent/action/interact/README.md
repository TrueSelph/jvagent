# InteractAction API Guide

This guide documents the API for InteractAction and related methods for adding directives, parameters, and generating responses.

## Overview

InteractAction provides a simplified API for:
- Adding directives and parameters to interactions
- Generating responses via PersonaAction
- Managing interaction state efficiently

## respond() Method

The `respond()` method is the primary way to generate responses via PersonaAction. It supports passing directives and parameters directly, eliminating the need for separate method calls.

### Signature

```python
async def respond(
    self,
    visitor: "InteractWalker",
    directives: Optional[List[str]] = None,
    parameters: Optional[List[Dict[str, Any]]] = None,
    *,
    # History configuration
    use_utterance: bool = True,
    use_history: bool = True,
    history_limit: int = 3,
    with_interpretation: bool = False,
    with_event: bool = False,
    with_response: bool = True,
    max_statement_length: Optional[int] = None
    
) -> Optional[str]
```

### Parameters

#### History Configuration
- `use_utterance`: Include user utterance in prompt (default: True)
- `use_history`: Include conversation history (default: True)
- `history_limit`: Number of past interactions to include (default: 3)
- `with_interpretation`: Include interpretations in history (default: False)
- `with_event`: Include events in history (default: False)
- `with_response`: Include AI responses in history (default: True)
- `max_statement_length`: Truncate utterances/responses to this length (default: None)

#### Simplified API Parameters
- `directives`: Optional list of directive strings to add before generating response
- `parameters`: Optional list of parameter dictionaries (each should have 'condition' and 'response' keys)

### Examples

#### Basic Usage

```python
# Simple response generation
response = await self.respond(visitor)
```

#### With Directives

```python
# Add directive and generate response in one call
response = await self.respond(
    visitor,
    directives=["Use the provided context to answer the question"]
)
```

#### With Parameters

```python
# Add parameters and generate response
response = await self.respond(
    visitor,
    parameters=[{
        "condition": "No relevant context found",
        "response": "Inform the user that no relevant information was found"
    }]
)
```

#### With Both Directives and Parameters

```python
# Add both directives and parameters
response = await self.respond(
    visitor,
    directives=["Use the provided context to answer"],
    parameters=[{
        "condition": "No relevant context found",
        "response": "Inform the user that no relevant information was found"
    }]
)
```

#### With Conversation History

```python
# Include conversation history
response = await self.respond(
    visitor,
    use_history=True,
    history_limit=5,
    directives=["Answer based on the conversation history"]
)
```

#### Complete Example

```python
response = await self.respond(
    visitor,
    use_history=True,
    history_limit=10,
    with_interpretation=True,
    with_event=True,
    directives=[
        "Use the provided context to answer the question",
        "Be concise and accurate"
    ],
    parameters=[{
        "condition": "No relevant context found",
        "response": "Inform the user that no relevant information was found"
    }]
)
```

## Bulk Methods

For adding multiple directives or parameters efficiently, use the bulk methods on the InteractWalker:

### add_directives()

Add multiple directives with a single save operation:

```python
await visitor.add_directives([
    "Directive 1",
    "Directive 2",
    "Directive 3"
])
```

### add_parameters()

Add multiple parameters with a single save operation:

```python
await visitor.add_parameters([
    {
        "condition": "Condition 1",
        "response": "Response 1"
    },
    {
        "condition": "Condition 2",
        "response": "Response 2"
    }
])
```

## Single-Item Methods

For adding single items, use these convenience methods (they delegate to bulk methods internally):

### add_directive()

```python
await visitor.add_directive("Single directive")
```

### add_parameter()

```python
await visitor.add_parameter({
    "condition": "Some condition",
    "response": "Some response"
})
```

## Best Practices

### 1. Use respond() for Simplified API

**Preferred:**
```python
await self.respond(
    visitor,
    directives=[directive],
    parameters=self.parameters if self.parameters else None
)
```

**Avoid:**
```python
await visitor.add_directive(directive)
if self.parameters:
    for param in self.parameters:
        await visitor.add_parameter(param)
await self.respond(visitor)
```

### 2. Use Bulk Methods for Multiple Items

**Preferred:**
```python
await visitor.add_directives([directive1, directive2, directive3])
```

**Avoid:**
```python
await visitor.add_directive(directive1)
await visitor.add_directive(directive2)
await visitor.add_directive(directive3)
```

### 3. Pass Parameters Correctly

**Correct:**
```python
# self.parameters is already a List[Dict[str, Any]]
await self.respond(visitor, parameters=self.parameters if self.parameters else None)
```

**Incorrect:**
```python
# Don't wrap in another list!
await self.respond(visitor, parameters=[self.parameters])  # ❌ Creates nested list
```

## Benefits

1. **Simplified API**: Fewer method calls, cleaner code
2. **Automatic Persistence**: Interaction is automatically saved
3. **Efficient**: Bulk operations use single save operations
4. **Type Safe**: Proper type hints for all parameters

## See Also

- [InteractAction Base Class](../interact/base.py)
- [InteractWalker](../interact/interact_walker.py)
- [IntroInteractAction README](../intro/README.md)
- [RetrievalInteractAction README](../retrieval/README.md)
