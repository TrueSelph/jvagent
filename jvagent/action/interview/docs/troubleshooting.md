# Troubleshooting

## Session Not Found

- Ensure conversation exists before creating session
- Check that `interview_type` matches the class name
- Verify session is attached to conversation

## State Not Transitioning

- Verify `session.save()` is called after `transition_to()`
- Check that all required questions are answered before REVIEW

## Question Nodes Not Rebuilding

- Ensure `on_reload()` is called when `question_graph` changes
- Check that question names in `question_graph` match expected format

## Custom Handlers Not Working

- Verify handler is callable (for Python code) or importable (for agent.yaml)
- Check that handler signature matches: `(raw_input: str, session: InterviewSession) -> Any`
- For validators: `(value: Any, session: InterviewSession) -> Tuple[ValidationStatus, Optional[str]]` or similar
- Ensure handler is registered via decorator or string reference in `question_graph` constraints
- Check that the question name in the decorator matches the question `name` in `question_graph`
