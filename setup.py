"""Setup script for the jvagent package."""

from setuptools import find_packages, setup

with open("README.md", "r", encoding="utf-8") as f:
    long_description = f.read()

# Read version from version.py without importing the package
import re
from pathlib import Path


def get_version():
    """Read version from jvagent/version.py without importing the package."""
    version_file = Path(__file__).parent / "jvagent" / "version.py"
    with open(version_file, "r") as f:
        content = f.read()
        match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
        if match:
            return match.group(1)
        else:
            raise ValueError("Could not find __version__ in jvagent/version.py")


__version__ = get_version()

setup(
    name="jvagent",
    version=__version__,
    description="A modular, pluggable agentive platform built on jvspatial",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="TrueSelph Inc.",
    author_email="adminh@trueselph.com",
    url="https://github.com/your-org/jvagent",
    packages=find_packages(),
    include_package_data=True,
    install_requires=[
        "jvspatial>=0.0.6",
        "python-dotenv>=1.0.0",
        "pyyaml>=6.0.0",
        "httpx>=0.27.0",
        "jinja2>=3.1.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.0",
            "pytest-asyncio>=0.21.0",
            "httpx>=0.24.0",
            "pre-commit>=3.0.0",
            "black>=23.9.0",
            "ruff>=0.1.0",
            "mypy>=1.6.0",
        ],
        "test": [
            "pytest>=7.0",
            "pytest-asyncio>=0.21.0",
            "httpx>=0.24.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "jvagent=jvagent.cli:main",
        ],
    },
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Framework :: FastAPI",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
    ],
    python_requires=">=3.8",
)
