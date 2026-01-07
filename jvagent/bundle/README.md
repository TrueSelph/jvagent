# jvagent Dockerfile Generator

The bundle module generates Dockerfiles for jvagent applications by extending a base template and including pip dependencies discovered from action info.yaml files.

## Overview

**Note:** The generated Dockerfile is currently tuned for AWS Lambda deployment with EFS support. It uses the AWS Lambda Python base image and includes Lambda Web Adapter for HTTP API support.

The bundler generates a ready-to-use Dockerfile directly in your jvagent app directory. The Dockerfile:
- Extends a base template optimized for AWS Lambda with EFS support
- Automatically discovers pip dependencies from all actions in your app
- Includes separate RUN commands per action for better Docker layer caching

## Usage

### Generate Dockerfile

The bundle command supports two execution methods:

**Method 1: Change to app directory first (recommended)**
```bash
cd /path/to/jvagent_app
jvagent bundle
```

**Method 2: Specify app path as argument**
```bash
jvagent bundle /path/to/jvagent_app
```

You can also specify the path before the command:
```bash
jvagent /path/to/jvagent_app bundle
```

**Examples:**

```bash
# Method 1: Change to app directory first
cd /path/to/my_app
jvagent bundle

# Method 2: Specify path after command
jvagent bundle /path/to/my_app

# Method 3: Specify path before command
jvagent /path/to/my_app bundle
```

## How It Works

### 1. App Validation
- Validates that `app.yaml` exists in the app root directory

### 2. Dependency Discovery
- Scans `agents/{namespace}/{agent_name}/actions/` directory structure
- For each action, reads `info.yaml` file
- Extracts `package.dependencies.pip` list from each action

### 3. Dockerfile Generation
- Loads base Dockerfile template (`Dockerfile.base`)
- Generates separate RUN commands per action for pip dependencies
- Extends the base template with action-specific dependencies
- Writes `Dockerfile` to the app directory

## Generated Dockerfile

**AWS Lambda Optimized:** The generated Dockerfile is specifically tuned for AWS Lambda deployment with EFS support.

The generated Dockerfile:
- Uses AWS Lambda Python 3.12 base image (`public.ecr.aws/lambda/python:3.12`)
- Includes Lambda Web Adapter for HTTP API Gateway integration
- Clones and installs jvagent and jvspatial as editable packages from GitHub
- Installs action-specific pip dependencies (one RUN command per action)
- Sets up EFS-compatible virtual environment (`/mnt/venv`)
- Configures Lambda-specific environment variables and paths
- Includes runtime script optimized for Lambda execution

## Base Template

The base Dockerfile template (`Dockerfile.base`) is optimized for AWS Lambda deployment and includes:
- AWS Lambda Python 3.12 base image
- Lambda Web Adapter setup for HTTP API Gateway integration
- Base Python environment with numpy, tiktoken, pydantic (installed in read-only `/opt/venv`)
- jvagent and jvspatial installation from GitHub (dev branch)
- EFS virtual environment setup (`/mnt/venv`) with system-site-packages inheritance
- Lambda-specific environment variables (port 8080, paths, readiness checks)
- Runtime script that creates EFS venv on first run and executes jvagent

Action-specific dependencies are inserted into the template at the `{{ACTION_DEPENDENCIES}}` placeholder.

## Action Dependency Discovery

The generator automatically discovers dependencies by:
1. Scanning `agents/` directory for all agents
2. For each agent, scanning `actions/{namespace}/{action_name}/` directories
3. Reading `info.yaml` from each action directory
4. Extracting `package.dependencies.pip` list

**Example info.yaml structure:**
```yaml
package:
  name: jvagent/my_action
  dependencies:
    pip:
      - openai>=1.0.0
      - httpx>=0.24.0
```

## Docker Build and Deployment

### AWS Lambda Deployment

The generated Dockerfile is optimized for AWS Lambda container-based deployments. After generating the Dockerfile:

```bash
# Build Docker image
docker build -t my-jvagent-app .

# Tag and push to Amazon ECR (for Lambda deployment)
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <account-id>.dkr.ecr.us-east-1.amazonaws.com
docker tag my-jvagent-app:latest <account-id>.dkr.ecr.us-east-1.amazonaws.com/my-jvagent-app:latest
docker push <account-id>.dkr.ecr.us-east-1.amazonaws.com/my-jvagent-app:latest
```

Then deploy as a container-based Lambda function with:
- Container image from ECR
- EFS mount point configured (for `/mnt/venv`)
- Environment variables set (see below)
- API Gateway trigger configured

### Local Testing

For local testing, you can run the container:

```bash
# Build Docker image
docker build -t my-jvagent-app .

# Run container locally
docker run -p 8080:8080 \
  -e JVAGENT_ADMIN_PASSWORD=your-password \
  -e JVSPATIAL_DB_TYPE=json \
  -e JVSPATIAL_DB_PATH=/app/data/jvagent_db \
  my-jvagent-app
```

**Note:** The Dockerfile is optimized for Lambda. For non-Lambda deployments, you may need to modify the base image and configuration.

### Environment Variables

Configure the following environment variables when running the container:

- `JVAGENT_ADMIN_PASSWORD`: Admin user password (required)
- `JVSPATIAL_DB_TYPE`: Database type (default: `json`)
- `JVSPATIAL_DB_PATH`: Database path (default: `/app/data/jvagent_db`)
- `JVSPATIAL_MONGODB_URI`: MongoDB connection string (if using MongoDB)
- Other variables as defined in your `app.yaml`

### AWS Lambda Deployment (Primary Use Case)

The generated Dockerfile is specifically optimized for AWS Lambda container-based deployments with EFS support:

- **Base Image**: AWS Lambda Python 3.12 (`public.ecr.aws/lambda/python:3.12`)
- **Lambda Web Adapter**: Included for HTTP API Gateway integration
- **EFS Virtual Environment**: Uses `/mnt/venv` for writable package installation
- **Read-Only Base Packages**: Heavy dependencies (numpy, pydantic) in `/opt/venv`
- **Lambda-Specific Configuration**: Port 8080, proper PATH setup, readiness checks

**Deployment Steps:**
1. Generate Dockerfile: `jvagent bundle`
2. Build image: `docker build -t my-jvagent-app .`
3. Push to ECR: Tag and push to your ECR repository
4. Create Lambda function: Use container image from ECR
5. Configure EFS: Mount EFS to `/mnt` for persistent venv
6. Set environment variables: Configure Lambda environment variables
7. Create API Gateway: Set up HTTP API trigger

**Note:** This Dockerfile is currently tuned for AWS Lambda. For other deployment targets, you may need to modify the base image and configuration.

## Implementation

### Bundler Class

```python
from jvagent.bundle import Bundler

bundler = Bundler(app_root="./my-app")
success = bundler.generate_dockerfile()
```

### Module Structure

- `bundler.py` - Main Bundler class
- `dockerfile_generator.py` - Dependency discovery and Dockerfile generation
- `Dockerfile.base` - Base Dockerfile template

## See Also

- [jvagent CLI Documentation](../cli.py)
- [Docker Deployment Guide](../../../examples/api/README.md)
