# jvagent

A modular, pluggable agentive platform built on jvspatial that provides a production-ready framework for AI agent development with enterprise-grade observability and scalability.

## Installation

### Prerequisites

- Python 3.8 or higher
- pip

### Install from Source

1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd jvagent
   ```

2. Install in development mode:
   ```bash
   pip install -e .
   ```

   Or install with development dependencies:
   ```bash
   pip install -e ".[dev]"
   ```

### Install from Distribution

If you have a built distribution:
```bash
pip install dist/jvagent-*.whl
```

## Quick Start

### 1. Configure Environment

Copy the example environment file and update with your values:
```bash
cp .env.example .env
```

Edit `.env` and set at minimum:
- `JVAGENT_ADMIN_PASSWORD` - Password for the initial admin user
- `JVSPATIAL_JWT_SECRET` - Secret key for JWT authentication (change from default in production)

### 2. Run jvagent

After installation, you can run jvagent in several ways:

**Option 1: Using the console command**
```bash
jvagent
```

**Option 2: Using Python module**
```bash
python -m jvagent
```

The server will start on `http://127.0.0.1:8000` by default (configurable via `.env`).

### 3. Access the API

- **API Documentation**: http://localhost:8000/docs
- **Alternative Docs**: http://localhost:8000/redoc

### 4. Login

Use the admin credentials from your `.env` file:
```bash
curl -X POST "http://localhost:8000/auth/login" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "admin@jvagent.example",
    "password": "your-admin-password"
  }'
```

## Configuration

Key environment variables (see `.env.example` for full list):

### Server Configuration
- `JVAGENT_HOST` - Server host (default: `127.0.0.1`)
- `JVAGENT_PORT` - Server port (default: `8000`)
- `JVAGENT_TITLE` - API title
- `JVAGENT_VERSION` - Application version

### Database Configuration
- `JVSPATIAL_DB_TYPE` - Database type: `json` or `mongodb` (default: `json`)
- `JVSPATIAL_DB_PATH` - Database path (default: `./jvdb`)

### Authentication
- `JVAGENT_AUTH_ENABLED` - Enable authentication (default: `true`)
- `JVSPATIAL_JWT_SECRET` - JWT secret key (change in production!)
- `JVSPATIAL_JWT_EXPIRE_MINUTES` - JWT expiration (default: `60`)

### Admin User
- `JVAGENT_ADMIN_USERNAME` - Admin username (default: `admin`)
- `JVAGENT_ADMIN_PASSWORD` - Admin password (required)
- `JVAGENT_ADMIN_EMAIL` - Admin email (default: `admin@jvagent.example`)

## What Happens on Startup

When jvagent starts, it automatically:

1. **Bootstraps the application graph**:
   - Creates an `App` node (if it doesn't exist)
   - Creates an `Agents` node (if it doesn't exist)
   - Connects `App` to the Root node
   - Connects `Agents` to `App`

2. **Creates admin user** (if it doesn't exist):
   - Uses credentials from `.env` file
   - Hashed password stored securely

## Development

### Install Development Dependencies

```bash
pip install -e ".[dev]"
```

### Run Tests

```bash
pytest
```

### Code Formatting

```bash
black jvagent/
ruff check jvagent/
```

## Project Structure

```
jvagent/
├── jvagent/              # Main package
│   ├── cli.py            # CLI entry point
│   ├── core/             # Core entities
│   │   ├── app.py        # App node
│   │   └── agents.py     # Agents node
│   └── version.py        # Version info
├── .env.example          # Environment template
├── pyproject.toml        # Package configuration
└── README.md             # This file
```

## Next Steps

- Review the [Implementation Plan](IMPLEMENTATION_PLAN.md) for development roadmap
- Check the [Architecture Documentation](REF/README.md) for detailed specifications
- Start building agents and actions!

## License

MIT License - see [LICENSE](LICENSE) file for details.

