# Classification

## Unified Classification System

The interview system uses a single LLM call for classification and extraction. In one API call it: (1) detects intent, (2) extracts field values, (3) resolves conversational references, (4) composes multi-turn values, and (5) applies field-type-aware extraction modes. The prompt is built from modular sections via `build_classification_rules()` in `prompts.py`, with configuration toggles for token budget management.

## Prompt Architecture

The classification prompt is composed from 8 sections:

| Section | Purpose |
|---------|---------|
| **Reasoning Instructions** | 6-step structured process: identify user/state → check references → check composition → determine intent → extract → verify |
| **Intent Rules** | Defines CANCELLATION, CONFIRMATION, UPDATE, DECLINE, SUBMISSION, NONE with expanded pattern matching |
| **Extraction Rules** | Three modes: [verbatim] (preserve full response), [normalized] (trim/casing), [select] (match to options) |
| **Reference Resolution** | Ordinal ("the second option"), temporal ("Wednesday afternoon"), anaphoric ("that one") |
| **Composition Rules** | Multi-turn value composition (e.g., "John" + "Smith" → "John Smith") |
| **Verification** | Chain-of-Verification checklist before final output |
| **Output Format** | Required JSON with structured `reasoning` object |
| **Examples** | Five few-shot examples for edge cases |

## Intent Detection

- **CANCELLATION**: "cancel", "abort", "stop", "never mind" — any state
- **CONFIRMATION** (REVIEW only): Pure affirmation with no new values. Expanded patterns: "yes", "correct", "looks good", "yep", "all good", "looks fine to me", "that's fine", "confirmed", "perfect"
- **SUBMISSION**: Answering unanswered questions. Includes "yes"/"no" when answering yes/no questions in active state.
- **UPDATE**: Changing an already-answered field. Requires explicit change language ("change to", "actually I prefer", "make it"). Bare "yes"/"no" is never UPDATE.
- **DECLINE**: Explicit refusal or "no" to optional content (photos, attachments). Not "no" as answer to yes/no question.
- **NONE**: Last resort for meta-requests, greetings, or off-topic content.

**Critical**: "yes"/"no" in active state = SUBMISSION (answer). In review state = CONFIRMATION or refusal.

## Extraction Modes

Extraction mode is auto-detected or set via `extraction_mode` constraint:

- **[verbatim]**: For description/narrative fields. Preserves full user response; no summarization. Keywords: "description", "narrative", "details", "incident", "explain"
- **[normalized]**: For structured fields (email, phone, name). Trim whitespace, fix casing
- **[select]**: For fields with `options` or `input_context_provider`. Match to closest valid option; handles references like "the second option"

## Context Enhancement

- **Entity format**: Each unanswered field includes mode marker, e.g. `incident_description [REQUIRED] [verbatim] — Expected: "..." | Constraints: ...`
- **Inline options**: Select fields include `| Options: Monday 9AM, Monday 2PM, Wednesday 9AM` for reference resolution
- **Conversation history**: Passed via the model API's `history` parameter as separate messages (not embedded in the prompt). The prompt instructs the LLM to use the preceding messages for identifying the current question, resolving "yes"/"no" and references, and multi-turn composition. Requires `use_history: true` and `history_limit` in context.

## Classification Configuration

| Option | Default | Description |
|--------|---------|-------------|
| `require_structured_reasoning` | true | Require structured reasoning object in LLM response |
| `include_few_shot_examples` | true | Include few-shot examples in prompt |
| `max_examples` | 5 | Cap on example count |
| `enable_reference_resolution` | true | Include reference resolution section |
| `enable_composition` | true | Include multi-turn composition section |

**Token budget**: Full config ~700 tokens; minimal (examples+reasoning disabled) ~200 tokens. For GPT-4o/Claude 3.5+ use full; for smaller models or token limits, disable examples and reasoning.

### Validation Scenarios

The system addresses five failure modes:

1. **Long-form extraction**: [verbatim] mode preserves multi-sentence incident descriptions verbatim
2. **Multi-turn composition**: "John" (earlier message) + "Smith" (current) → "John Smith" for full_name
3. **Reference resolution**: "the second option" resolved to specific value from Options list
4. **CONFIRMATION in review**: "yep that all looks good", "everything is correct" correctly classified
5. **"No" disambiguation**: "no" to yes/no question → SUBMISSION; "no" to optional content → DECLINE

### Input Processing

The prompt accepts both **utterance** and **interpretation** (when available). Interpretation helps distinguish "no" as rejection vs answer. Conversation history is passed as separate messages via the model API's `history` parameter (controlled by `use_history` and `history_limit` in context).

### Classification Result

`ClassificationResult` contains: `intent`, `confidence`, `extracted_data`, `field`, `value`, `from_data_input_field`.

### UPDATE Intent Handling

- **With field**: Processes update via QuestionNode validation
- **Without field**: Shows summary and prompts which field to change
- **Field normalization**: Handles string "null" from JSON

### State Transitions

- ACTIVE → REVIEW when all answered
- REVIEW → COMPLETED on CONFIRMATION
- REVIEW → ACTIVE on UPDATE
- Any → CANCELLED on CANCELLATION

## Troubleshooting Classification

- **Token limit exceeded**: Set `include_few_shot_examples: false` or `max_examples: 2`
- **Composition not working**: Ensure `enable_composition: true`, `use_history: true`, and `history_limit` is sufficient; history is passed via the API
- **Reference resolution failing**: Ensure `enable_reference_resolution: true`, Options in entities, field marked [select]
- **Verbatim truncated**: Add explicit `extraction_mode: verbatim` to constraints; check `model_max_tokens`
