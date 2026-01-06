"""Dockerfile generator for jvagent bundles."""


def generate_dockerfile() -> str:
    """Generate Dockerfile for containerized deployment.

    Returns:
        Dockerfile content as string
    """
    return '''# Dockerfile for jvagent application bundle
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy bundle contents
COPY app/ /app/app/
COPY packages/ /app/packages/
COPY src/ /app/src/

# Install editable packages
RUN pip install --no-cache-dir -e /app/src/jvagent
RUN pip install --no-cache-dir -e /app/src/jvspatial

# Install other dependencies from packages
ENV PYTHONPATH=/app/packages:$PYTHONPATH

# Set app root
ENV JVAGENT_APP_ROOT=/app/app

# Expose port
EXPOSE 8000

# Set default environment variables
ENV JVSPATIAL_DB_TYPE=json
ENV JVSPATIAL_DB_PATH=/app/data/jvagent_db
ENV JVSPATIAL_FILES_ROOT_PATH=/app/data/files

# Create data directory
RUN mkdir -p /app/data

# Run jvagent
WORKDIR /app/app
CMD ["python", "-m", "jvagent"]
'''

