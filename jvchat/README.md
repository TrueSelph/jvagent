# jvchat

React-based chat interface for interacting with jvagent agents. A ChatGPT-like experience with authentication, agent selection, and conversation management.

> **Just want to run it?** The built UI ships inside the `jvagent` package — run
> `jvagent chat` (optionally `jvagent chat --url <agent-url>`) and skip the Node
> setup entirely. See [`docs/jvchat.md`](../docs/jvchat.md). The instructions
> below are for **developing jvchat itself** (hot-reload dev server, building the
> bundle).

## Features

- 🔐 **Authentication**: Login with jvagent admin credentials
- 🤖 **Agent Selection**: Browse and select from available agents
- 💬 **Chat Interface**: ChatGPT-like conversation experience
- 📱 **Responsive Design**: Works on desktop and mobile devices
- 🔄 **Real-time Streaming**: SSE-based streaming responses
- 🧠 **Thinking Panel**: Reasoning/tool thoughts grouped per interaction in a collapsible panel
- 💾 **Conversation Management**: Switch between multiple conversations
- 🎨 **Modern UI**: Clean, intuitive interface built with React and Tailwind CSS

## Prerequisites

- Node.js 18+ and npm
- A running jvagent server
- jvagent admin credentials

## Installation

1. Navigate to the jvchat directory:
```bash
cd jvagent/jvchat
```

2. Install dependencies:
```bash
npm install
```

## Configuration

Configuration can be provided in multiple ways (in order of priority):

1. **Login Screen** (recommended): Enter the server URL directly in the login form. The URL is saved to localStorage and persists across sessions.

2. **Environment variable**: Set `VITE_JVAGENT_URL` (only affects the default URL):
```bash
export VITE_JVAGENT_URL=http://localhost:8000
```

3. **localStorage**: Configuration is automatically saved to browser localStorage when you log in. You can also set it programmatically if needed.

**Note**: The `config.yaml` file in the project root is for reference only and shows the configuration structure. Configuration is managed through the login screen and localStorage.

## CORS Configuration

For jvchat to work with jvagent, ensure CORS is properly configured on the jvagent server. The jvagent server should allow requests from the jvchat development server origin (typically `http://localhost:5173`).

By default, jvagent includes common development origins in its CORS configuration. If you need to customize CORS origins, set the `JVSPATIAL_CORS_ORIGINS` environment variable:

```bash
export JVSPATIAL_CORS_ORIGINS="http://localhost:5173,http://localhost:3000"
```

**Important**: When using credentials (JWT tokens), wildcard origins (`*`) are not allowed by browsers. You must specify explicit origins.

## Development

Start the development server:

```bash
npm run dev
```

The app will open at `http://localhost:5173` (or the next available port).

## Building for Production

Build the production bundle:

```bash
npm run build
```

The built files will be in the `dist/` directory. You can serve them with any static file server:

```bash
npm run preview
```

## Usage

1. **Start the app**: Run `npm run dev` or open the built app
2. **Login**: Enter your jvagent admin email and password
3. **Select Agent**: Choose an agent from the list
4. **Start Chatting**: Type your first message to create a new conversation
5. **Manage Conversations**: Use the sidebar to switch between conversations or create new ones

### Thinking stream behavior

- Thought messages (`category: "thought"`) are persisted in the same `messages` array as user/assistant messages.
- The transcript renders one collapsible **Thoughts** panel per interaction (`interactionId`), with one compact line per thought entry.
- Panels auto-expand while thought chunks are streaming, then can be reopened later from persisted history.

## Project Structure

```
jvchat/
├── src/
│   ├── components/      # React components
│   ├── hooks/          # Custom React hooks
│   ├── config/          # Configuration and API client
│   ├── types/           # TypeScript type definitions
│   ├── utils/           # Utility functions
│   └── styles/          # Global styles
└── dist/                # Build output
```

## API Integration

jvchat communicates with jvagent via REST API:

- **POST /auth/login**: Authentication
- **GET /agents**: List available agents
- **POST /agents/{agent_id}/interact**: Send messages (with SSE streaming)

**Authentication:**

- **Admin REST APIs** (agents, graph, PageIndex, memory admin, logs, etc.) send `Authorization: Bearer <access_token>`. Tokens are stored in `localStorage` after login.
- **Interact (chat)** uses jvagent’s **anonymous** interact endpoint: no Bearer header. The client sends `user_id` (from login or JWT `sub`) and optional `session_id` in the JSON body. See the parent [jvagent README](../README.md) for server-side interact rate limits and access policy.

### App Graph (memory / structure viewer)

The **App Graph** modal loads a **bounded subgraph** first, then loads more on demand (no full-graph DOT download by default):

- **GET /api/graph/subgraph** — initial view from root `n.Root.root` (`max_depth`, `max_nodes`, `max_edges_per_node`).
- **GET /api/graph/expand** — neighbors for a single node (`node_id`, `limit`, `cursor` for pagination).

These routes are provided by **jvspatial** on the jvagent server and use the **same JWT** as other protected API routes (including **GET /api/graph**). If they are missing (404), jvchat **falls back** to **GET /api/graph** (Graphviz DOT) so older deployments still work.

Graph **repair** still uses **POST /api/graph/repair** and then refreshes the viewer.

## Development Workflow

1. Make sure your jvagent server is running
2. Set the server URL on the login screen (saved to `localStorage`) or via `VITE_JVAGENT_URL` for the default
3. Run `npm run dev` to start the development server
4. Open `http://localhost:5173` in your browser
5. Login and start chatting!

### Quality checks

```bash
cd jvagent/jvchat
npm run lint
npm test
npm run build
```

## Troubleshooting

### Cannot connect to jvagent server

- Verify the server URL in your config file
- Ensure the jvagent server is running
- Check CORS settings if accessing from a different origin

### Authentication fails

- Verify your admin credentials
- Check that JWT authentication is enabled on the jvagent server
- Ensure the token is being stored correctly (check browser localStorage)

### Streaming not working

- Verify that the `/agents/{agent_id}/interact` endpoint supports streaming
- Check browser console for SSE connection errors
- Ensure the server supports Server-Sent Events (SSE)

## License

Part of the jvagent project.

