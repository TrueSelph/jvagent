# Avatar Action

Allows an avatar image to be added to the agent.

## Configuration

This action is a singleton, meaning only one instance can be added per agent.

## Endpoints

- `POST /actions/{action_id}/avatar`: Set the agent's avatar image. Supports raw base64 data + mimetype, or a single data URI string (e.g., `data:image/png;base64,...`).
- `GET /actions/{action_id}/avatar`: Get the agent's avatar image.
- `DELETE /actions/{action_id}/avatar`: Delete the agent's avatar image.
- `GET /actions/{action_id}/avatar/health`: Check the health of the avatar action.

## Implementation Details

The avatar data is stored as a base64 encoded string with its corresponding MIME type in the action's attributes.
