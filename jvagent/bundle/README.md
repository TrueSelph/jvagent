# jvagent Bundling Service

The bundling service creates deployment-ready packages for jvagent applications, suitable for AWS Lambda and Docker deployments.

## Overview

The bundling service packages jvagent applications into self-contained bundles that include:
- Application source files (app.yaml, agents/, etc.)
- All Python dependencies
- jvagent and jvspatial as editable packages
- Optional Lambda handler and Dockerfile

## Usage

### Basic Bundling

Create a bundle from your jvagent app:

```bash
jvagent [<app_root>] bundle
```

This creates a `./bundle` directory with all necessary files.

### Command Options

```bash
jvagent [<app_root>] bundle [OPTIONS]
```

**Options:**
- `--output-dir <dir>`: Specify output directory (default: `./bundle`)
- `--lambda`: Generate Lambda handler entrypoint
- `--docker`: Generate Dockerfile
- `--zip`: Create ZIP archive after bundling

**Examples:**

```bash
# Basic bundle
jvagent bundle

# Bundle with Lambda handler
jvagent bundle --lambda

# Bundle with Lambda handler and ZIP
jvagent bundle --lambda --zip

# Bundle with Docker support
jvagent bundle --docker

# Full bundle with all options
jvagent bundle --lambda --docker --zip --output-dir ./my-bundle
```

## Bundle Structure

The bundling service creates the following structure:

```
bundle/
├── app/                    # Application source
│   ├── app.yaml
│   ├── agents/
│   ├── .env (optional)
│   └── ... (other app files)
├── packages/               # All Python packages (pip install --target)
│   ├── dspy/
│   ├── fastapi/
│   └── ... (other dependencies)
├── src/                    # Editable package sources
│   ├── jvagent/           # Installed as editable (pip install -e)
│   └── jvspatial/         # Installed as editable (pip install -e)
├── lambda_handler.py       # Lambda entrypoint (optional, generated with --lambda)
├── Dockerfile              # Docker support (optional, generated with --docker)
├── requirements.txt        # Locked dependencies
└── README.md               # Deployment instructions
```

## How It Works

### 1. App Validation
- Validates that `app.yaml` exists in the app root directory

### 2. Bundle Directory Creation
- Creates the bundle directory structure
- Removes existing bundle if it exists

### 3. App Source Copying
- Copies `app.yaml`, `agents/`, `.env`, and other app files to `bundle/app/`

### 4. Editable Package Installation
- Detects jvagent and jvspatial source directories via:
  - Environment variables (`JVAGENT_SOURCE_DIR`, `JVSPATIAL_SOURCE_DIR`)
  - Parent directory traversal (common development layout)
  - Import-based detection
- Copies source to `bundle/src/` and installs as editable packages using `pip install -e`

### 5. Dependency Installation
- Collects requirements from:
  - `requirements.txt` in app root (if exists)
  - Dependencies from jvagent and jvspatial `pyproject.toml` files
- Installs all dependencies to `bundle/packages/` using `pip install --target`

### 6. Bootstrap Validation
- Bootstraps the app in an isolated environment to:
  - Force dependency resolution
  - Validate that all dependencies are available
  - Ensure the app can initialize correctly

### 7. Optional File Generation
- **Lambda Handler** (`--lambda`): Generates `lambda_handler.py` for AWS Lambda deployment
- **Dockerfile** (`--docker`): Generates `Dockerfile` for containerized deployment
- **ZIP Archive** (`--zip`): Creates a ZIP file of the entire bundle

## Package Source Detection

The bundler automatically detects jvagent and jvspatial source directories using the following methods (in order):

1. **Environment Variables**:
   ```bash
   export JVAGENT_SOURCE_DIR=/path/to/jvagent
   export JVSPATIAL_SOURCE_DIR=/path/to/jvspatial
   ```

2. **Parent Directory Traversal**: Checks parent directories of the current jvagent installation

3. **Import-based Detection**: Uses Python's import system to locate installed packages

If source directories cannot be detected, the bundler will log a warning and skip editable package installation. You can set the environment variables to explicitly specify the source directories.

## Deployment

### AWS Lambda Deployment

1. **Create bundle with Lambda handler**:
   ```bash
   jvagent bundle --lambda --zip
   ```

2. **Upload to Lambda**:
   - Upload the generated ZIP file to AWS Lambda
   - Set handler to: `lambda_handler.handler`
   - Configure environment variables (see below)

3. **Configure API Gateway**:
   - Set up API Gateway trigger for your Lambda function
   - Configure CORS if needed

**Lambda Environment Variables:**
- `JVAGENT_ADMIN_PASSWORD`: Admin user password (required)
- `JVSPATIAL_DB_TYPE`: Database type (default: `dynamodb` for Lambda)
- `JVSPATIAL_DYNAMODB_TABLE_NAME`: DynamoDB table name
- `JVSPATIAL_DYNAMODB_REGION`: AWS region
- Other variables as defined in your app.yaml

### Docker Deployment

1. **Create bundle with Dockerfile**:
   ```bash
   jvagent bundle --docker
   ```

2. **Build Docker image**:
   ```bash
   cd bundle
   docker build -t my-jvagent-app .
   ```

3. **Run container**:
   ```bash
   docker run -p 8000:8000 \
     -e JVAGENT_ADMIN_PASSWORD=your-password \
     -e JVSPATIAL_DB_TYPE=json \
     -e JVSPATIAL_DB_PATH=/app/data/jvagent_db \
     my-jvagent-app
   ```

**Docker Environment Variables:**
- `JVAGENT_ADMIN_PASSWORD`: Admin user password (required)
- `JVSPATIAL_DB_TYPE`: Database type (default: `json`)
- `JVSPATIAL_DB_PATH`: Database path (default: `/app/data/jvagent_db`)
- `JVSPATIAL_MONGODB_URI`: MongoDB connection string (if using MongoDB)
- Other variables as defined in your app.yaml

### Local Testing

To test the bundled app locally:

```bash
cd bundle/app
export PYTHONPATH="../packages:../src/jvagent:../src/jvspatial:$PYTHONPATH"
python -m jvagent
```

Or use the generated README in the bundle directory for specific instructions.

## Requirements

The bundling service requires:
- Python 3.8+
- pip
- Access to jvagent and jvspatial source directories (for editable package installation)
- All dependencies listed in your app's `requirements.txt` (if exists)

## Troubleshooting

### Package Source Not Found

If you see warnings about jvagent/jvspatial source directories not being found:

1. Set environment variables:
   ```bash
   export JVAGENT_SOURCE_DIR=/path/to/jvagent
   export JVSPATIAL_SOURCE_DIR=/path/to/jvspatial
   ```

2. Or ensure the source directories are in parent directories of the jvagent installation

### Bootstrap Validation Fails

If bootstrap validation fails:
- Check that all dependencies are available
- Verify that `app.yaml` is valid
- Ensure database paths are correctly configured
- Check logs for specific error messages

### Large Bundle Size

Bundles can be large due to all dependencies being included:
- Consider using Lambda layers for dependencies
- Exclude unnecessary files from the bundle
- Use dependency analysis to minimize included packages

## Implementation Details

### Bundler Class

The `Bundler` class in `bundler.py` handles all bundling operations:

```python
from jvagent.bundle import Bundler

bundler = Bundler(
    app_root="./my-app",
    output_dir="./bundle",
    generate_lambda=True,
    generate_docker=False,
)

success = bundler.bundle()
if success:
    zip_path = bundler.create_zip()
```

### Lambda Handler

The Lambda handler (`lambda_handler.py`) provides:
- Automatic Python path setup
- Server initialization using `LambdaServer`
- Application graph bootstrapping
- Mangum handler for AWS Lambda compatibility

### Dockerfile

The generated Dockerfile:
- Uses Python 3.11 slim base image
- Installs editable packages (jvagent, jvspatial)
- Sets up proper working directories
- Configures environment variables
- Exposes port 8000

## Best Practices

1. **Always test bundles locally** before deploying
2. **Use environment variables** for configuration, not hardcoded values
3. **Keep bundle size manageable** by excluding unnecessary files
4. **Version your bundles** for easier rollback
5. **Use Lambda layers** for large dependencies to reduce bundle size
6. **Monitor bundle creation time** - large apps may take several minutes

## See Also

- [jvagent CLI Documentation](../cli.py)
- [AWS Lambda Deployment Guide](../../../examples/api/lambda_example.py)
- [Docker Deployment Guide](../../../examples/api/README.md)

