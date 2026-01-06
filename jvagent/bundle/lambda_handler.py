"""Lambda handler template generator."""


def generate_lambda_handler() -> str:
    """Generate Lambda handler entrypoint code.

    Returns:
        Lambda handler Python code as string
    """
    return '''"""AWS Lambda handler for jvagent application.

This handler initializes the jvagent app and exposes it via Mangum for AWS Lambda.
"""

import os
import sys
from pathlib import Path

# Add bundle directories to Python path
# Lambda unpacks the bundle to /var/task, so we need to adjust paths
bundle_dir = Path(__file__).parent

# Add packages and editable sources to path
sys.path.insert(0, str(bundle_dir / "packages"))
sys.path.insert(0, str(bundle_dir / "src" / "jvagent"))
sys.path.insert(0, str(bundle_dir / "src" / "jvspatial"))

# Set app root to bundle/app
app_root = str(bundle_dir / "app")
os.environ["JVAGENT_APP_ROOT"] = app_root

# Set Lambda-specific environment variables
os.environ.setdefault("JVSPATIAL_DB_TYPE", "dynamodb")
os.environ.setdefault("LAMBDA_TASK_ROOT", str(bundle_dir))

# Import jvagent components
from jvagent.cli import bootstrap_application_graph, create_server_from_config
from jvspatial.api.lambda_server import LambdaServer
import asyncio

# Global server instance (reused across invocations)
_server = None
_handler = None


async def _initialize_server():
    """Initialize the jvagent server."""
    global _server, _handler

    if _server is not None:
        return _server

    # Create Lambda server
    _server = LambdaServer(
        title="jvagent Lambda API",
        description="jvagent application deployed on AWS Lambda",
        serverless_lifespan="auto",
        # DynamoDB is default for Lambda
        dynamodb_table_name=os.getenv("JVSPATIAL_DYNAMODB_TABLE_NAME", "jvagent"),
        dynamodb_region=os.getenv("JVSPATIAL_DYNAMODB_REGION", "us-east-1"),
    )

    # Bootstrap application graph
    await bootstrap_application_graph(app_root=app_root)

    # Get Lambda handler
    _handler = _server.get_lambda_handler()

    return _server


def handler(event, context):
    """AWS Lambda handler function.

    Args:
        event: Lambda event object
        context: Lambda context object

    Returns:
        Response from Mangum handler
    """
    global _handler

    # Initialize server on first invocation
    if _handler is None:
        asyncio.run(_initialize_server())

    # Invoke Mangum handler
    return _handler(event, context)

