# jvchat

React-based chat interface for interacting with jvagent agents. A ChatGPT-like experience with authentication, agent selection, and conversation management.

## Features

- 🔐 **Authentication**: Login with jvagent admin credentials
- 🤖 **Agent Selection**: Browse and select from available agents
- 💬 **Chat Interface**: ChatGPT-like conversation experience
- 📱 **Responsive Design**: Works on desktop and mobile devices
- 🔄 **Real-time Streaming**: SSE-based streaming responses
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

1. **`public/config.json`** (recommended for development):
```json
{
  "jvagent": {
    "url": "http://localhost:8000",
    "timeout": 30000
  },
  "ui": {
    "theme": "light",
    "messages_per_page": 50,
    "auto_scroll": true
  }
}
```

2. **Environment variable**: Set `VITE_JVAGENT_URL` (only affects the URL):
```bash
export VITE_JVAGENT_URL=http://localhost:8000
```

3. **localStorage**: The app can store configuration in browser localStorage (set programmatically)

**Note**: The `config.yaml` file in the project root is for reference only. Browsers cannot read YAML files directly. Use `public/config.json` instead, or set the environment variable.

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
├── public/              # Static assets
└── dist/                # Build output
```

## API Integration

jvchat communicates with jvagent via REST API:

- **POST /auth/login**: Authentication
- **GET /agents**: List available agents
- **POST /agents/{agent_id}/interact**: Send messages (with SSE streaming)

All requests require JWT authentication (token stored in localStorage).

## Development Workflow

1. Make sure your jvagent server is running
2. Configure the server URL in `~/.jvchat/config.yaml`
3. Run `npm run dev` to start the development server
4. Open `http://localhost:5173` in your browser
5. Login and start chatting!

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

