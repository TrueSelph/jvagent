"""Main bundling logic for creating deployment-ready jvagent app packages."""

import logging
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


class Bundler:
    """Bundles jvagent applications into deployment-ready packages."""

    def __init__(
        self,
        app_root: str,
        output_dir: str = "./bundle",
        generate_lambda: bool = False,
        generate_docker: bool = False,
    ):
        """Initialize the bundler.

        Args:
            app_root: Path to the jvagent app root directory
            output_dir: Output directory for the bundle
            generate_lambda: Whether to generate Lambda handler
            generate_docker: Whether to generate Dockerfile
        """
        self.app_root = Path(app_root).resolve()
        self.output_dir = Path(output_dir).resolve()
        self.generate_lambda = generate_lambda
        self.generate_docker = generate_docker

        # Bundle subdirectories
        self.bundle_app_dir = self.output_dir / "app"
        self.bundle_packages_dir = self.output_dir / "packages"
        self.bundle_src_dir = self.output_dir / "src"

    def bundle(self) -> bool:
        """Create the bundle.

        Returns:
            True if bundling succeeded, False otherwise
        """
        try:
            logger.info(f"Starting bundle creation for app: {self.app_root}")
            logger.info(f"Output directory: {self.output_dir}")

            # Validate app.yaml exists
            if not self._validate_app():
                return False

            # Create bundle directory structure
            self._create_bundle_structure()

            # Copy app source files
            self._copy_app_files()

            # Detect and install editable packages (jvagent, jvspatial)
            self._install_editable_packages()

            # Install other dependencies
            self._install_dependencies()

            # Bootstrap app to force dependency resolution
            self._bootstrap_app()

            # Generate optional files
            if self.generate_lambda:
                self._generate_lambda_handler()

            if self.generate_docker:
                self._generate_dockerfile()

            # Generate requirements.txt
            self._generate_requirements()

            # Generate README
            self._generate_readme()

            logger.info(f"Bundle created successfully: {self.output_dir}")
            return True

        except Exception as e:
            logger.error(f"Bundling failed: {e}", exc_info=True)
            return False

    def _validate_app(self) -> bool:
        """Validate that app.yaml exists in app root.

        Returns:
            True if valid, False otherwise
        """
        app_yaml = self.app_root / "app.yaml"
        if not app_yaml.exists():
            logger.error(f"app.yaml not found in {self.app_root}")
            return False
        logger.debug(f"Found app.yaml: {app_yaml}")
        return True

    def _create_bundle_structure(self) -> None:
        """Create bundle directory structure."""
        logger.info("Creating bundle directory structure...")

        # Remove existing bundle if it exists
        if self.output_dir.exists():
            logger.warning(f"Removing existing bundle directory: {self.output_dir}")
            shutil.rmtree(self.output_dir)

        # Create directories
        self.bundle_app_dir.mkdir(parents=True, exist_ok=True)
        self.bundle_packages_dir.mkdir(parents=True, exist_ok=True)
        self.bundle_src_dir.mkdir(parents=True, exist_ok=True)

        logger.debug(f"Created bundle structure at: {self.output_dir}")

    def _copy_app_files(self) -> None:
        """Copy app source files to bundle/app/."""
        logger.info("Copying app source files...")

        # Files/directories to copy
        items_to_copy = [
            "app.yaml",
            "agents",
            ".env",
        ]

        # Copy each item if it exists
        for item in items_to_copy:
            source = self.app_root / item
            if source.exists():
                dest = self.bundle_app_dir / item
                if source.is_dir():
                    shutil.copytree(source, dest, dirs_exist_ok=True)
                else:
                    shutil.copy2(source, dest)
                logger.debug(f"Copied {item} to bundle/app/")

        # Copy any other Python files or config files in root
        for item in self.app_root.iterdir():
            if item.is_file() and item.suffix in [".py", ".yaml", ".yml", ".json", ".txt"]:
                if item.name not in ["app.yaml", ".env"]:
                    dest = self.bundle_app_dir / item.name
                    shutil.copy2(item, dest)
                    logger.debug(f"Copied {item.name} to bundle/app/")

        logger.info("App source files copied")

    def _detect_package_source(self, package_name: str) -> Optional[Path]:
        """Detect source directory for a package.

        Checks:
        1. Workspace paths (from user_info)
        2. Environment variables (JVAGENT_SOURCE_DIR, JVSPATIAL_SOURCE_DIR)
        3. Parent directories of current jvagent installation
        4. Import-based detection

        Args:
            package_name: Name of the package (jvagent or jvspatial)

        Returns:
            Path to package source directory, or None if not found
        """
        # Check environment variables
        env_var = f"{package_name.upper()}_SOURCE_DIR"
        env_path = os.getenv(env_var)
        if env_path:
            path = Path(env_path).resolve()
            # Check if path points to package directory or parent
            if path.exists():
                if path.name == package_name and (path / package_name).exists():
                    # Path is parent directory containing package
                    logger.debug(f"Found {package_name} source via {env_var}: {path}")
                    return path / package_name
                elif (path / package_name).exists():
                    # Path is parent of package directory
                    logger.debug(f"Found {package_name} source via {env_var}: {path / package_name}")
                    return path / package_name

        # Check parent directories (common development layout)
        current_file = Path(__file__).resolve()
        # jvagent/bundle/bundler.py -> jvagent -> parent
        jvagent_install_dir = current_file.parent.parent.parent
        parent_dir = jvagent_install_dir.parent

        # Check if parent contains jvagent or jvspatial
        for check_dir in [parent_dir, parent_dir.parent]:
            package_dir = check_dir / package_name
            if package_dir.exists():
                # Check if it's the package directory itself (has pyproject.toml or setup.py)
                if (package_dir / "pyproject.toml").exists() or (package_dir / "setup.py").exists():
                    logger.debug(f"Found {package_name} source in parent: {package_dir}")
                    return package_dir
                # Or if it contains the package subdirectory
                elif (package_dir / package_name).exists():
                    logger.debug(f"Found {package_name} source in parent: {package_dir / package_name}")
                    return package_dir / package_name

        # Try import-based detection
        try:
            import importlib.util

            spec = importlib.util.find_spec(package_name)
            if spec and spec.origin:
                package_path = Path(spec.origin).parent
                # Check if this looks like a source directory (has setup.py or pyproject.toml)
                parent = package_path.parent
                if (parent / "setup.py").exists() or (parent / "pyproject.toml").exists():
                    logger.debug(f"Found {package_name} source via import: {parent}")
                    return parent
        except Exception as e:
            logger.debug(f"Could not detect {package_name} via import: {e}")

        logger.warning(f"Could not detect {package_name} source directory")
        return None

    def _install_editable_packages(self) -> None:
        """Install jvagent and jvspatial as editable packages."""
        logger.info("Installing editable packages (jvagent, jvspatial)...")

        packages = ["jvagent", "jvspatial"]
        for package_name in packages:
            source_dir = self._detect_package_source(package_name)
            if not source_dir:
                logger.warning(
                    f"Could not find {package_name} source directory. "
                    f"Set {package_name.upper()}_SOURCE_DIR environment variable."
                )
                continue

            # Determine the root directory (contains setup.py or pyproject.toml)
            # source_dir might be the package directory or the parent
            if (source_dir / "pyproject.toml").exists() or (source_dir / "setup.py").exists():
                package_root = source_dir
            elif (source_dir.parent / "pyproject.toml").exists() or (
                source_dir.parent / "setup.py"
            ).exists():
                package_root = source_dir.parent
            else:
                logger.warning(f"Could not find setup.py or pyproject.toml for {package_name}")
                continue

            # Copy entire package root to bundle/src/
            bundle_package_dir = self.bundle_src_dir / package_name
            if bundle_package_dir.exists():
                shutil.rmtree(bundle_package_dir)

            # Copy all files from package root
            shutil.copytree(package_root, bundle_package_dir)

            logger.debug(f"Copied {package_name} source to {bundle_package_dir}")

            # Install as editable package (point to directory with setup.py/pyproject.toml)
            try:
                logger.info(f"Installing {package_name} as editable package...")
                result = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-e", str(bundle_package_dir)],
                    cwd=str(self.output_dir),
                    capture_output=True,
                    text=True,
                    check=True,
                )
                logger.debug(f"Installed {package_name}: {result.stdout}")
            except subprocess.CalledProcessError as e:
                logger.error(f"Failed to install {package_name} as editable: {e}")
                logger.error(f"stdout: {e.stdout}")
                logger.error(f"stderr: {e.stderr}")
                raise

        logger.info("Editable packages installed")

    def _install_dependencies(self) -> None:
        """Install all other dependencies to bundle/packages/."""
        logger.info("Installing dependencies...")

        # Collect requirements from app
        requirements = self._collect_requirements()

        if not requirements:
            logger.warning("No requirements found")
            return

        # Write temporary requirements file
        temp_req_file = self.output_dir / "temp_requirements.txt"
        with open(temp_req_file, "w") as f:
            f.write("\n".join(requirements))

        try:
            # Install to bundle/packages/
            logger.info(f"Installing {len(requirements)} dependencies...")
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--target",
                    str(self.bundle_packages_dir),
                    "-r",
                    str(temp_req_file),
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            logger.debug(f"Dependencies installed: {result.stdout}")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to install dependencies: {e}")
            logger.error(f"stdout: {e.stdout}")
            logger.error(f"stderr: {e.stderr}")
            raise
        finally:
            # Clean up temp file
            if temp_req_file.exists():
                temp_req_file.unlink()

        logger.info("Dependencies installed")

    def _collect_requirements(self) -> List[str]:
        """Collect requirements from app and dependencies.

        Returns:
            List of requirement strings
        """
        requirements = set()

        # Check for requirements.txt in app root
        app_requirements = self.app_root / "requirements.txt"
        if app_requirements.exists():
            with open(app_requirements, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        requirements.add(line)

        # Add jvagent and jvspatial dependencies from their pyproject.toml
        for package_name in ["jvagent", "jvspatial"]:
            source_dir = self._detect_package_source(package_name)
            if source_dir:
                # Determine the root directory (contains setup.py or pyproject.toml)
                if (source_dir / "pyproject.toml").exists():
                    pyproject = source_dir / "pyproject.toml"
                elif (source_dir.parent / "pyproject.toml").exists():
                    pyproject = source_dir.parent / "pyproject.toml"
                else:
                    continue

                if pyproject.exists():
                    try:
                        import tomli

                        with open(pyproject, "rb") as f:
                            data = tomli.load(f)
                            deps = data.get("project", {}).get("dependencies", [])
                            requirements.update(deps)
                    except ImportError:
                        # Fallback to basic parsing if tomli not available
                        logger.debug("tomli not available, using basic parsing")
                        with open(pyproject, "r") as f:
                            in_deps = False
                            for line in f:
                                line = line.strip()
                                if line.startswith("dependencies") or line.startswith('dependencies ='):
                                    in_deps = True
                                elif in_deps and line.startswith("[") and not line.startswith("[project"):
                                    break
                                elif in_deps and line and not line.startswith("#"):
                                    # Remove quotes and brackets, handle list format
                                    dep = line.strip('",[]')
                                    if dep and ("=" in dep or ">=" in dep or "~" in dep):
                                        requirements.add(dep)

        return sorted(requirements)

    def _bootstrap_app(self) -> None:
        """Bootstrap app in isolated environment to force dependency resolution."""
        logger.info("Bootstrapping app to validate dependencies...")

        # Create a temporary bootstrap script
        bootstrap_script = self.output_dir / "temp_bootstrap.py"
        bootstrap_script.write_text(
            """
import sys
import os
from pathlib import Path

# Add bundle directories to Python path
bundle_dir = Path(__file__).parent
sys.path.insert(0, str(bundle_dir / "packages"))
sys.path.insert(0, str(bundle_dir / "src" / "jvagent"))
sys.path.insert(0, str(bundle_dir / "src" / "jvspatial"))

# Set app root
app_root = str(bundle_dir / "app")
os.environ["JVAGENT_APP_ROOT"] = app_root

# Set database paths to avoid conflicts
os.environ.setdefault("JVSPATIAL_DB_TYPE", "json")
os.environ.setdefault("JVSPATIAL_DB_PATH", str(bundle_dir / "app" / "jvagent_db"))
os.environ.setdefault("JVSPATIAL_JSONDB_PATH", str(bundle_dir / "app" / "jvagent_db"))

# Try to import and bootstrap
try:
    # Import jvspatial first to initialize context
    from jvspatial.db import set_current_db_path, set_current_db_type
    set_current_db_type("json")
    set_current_db_path(str(bundle_dir / "app" / "jvagent_db"))
    
    # Now import jvagent
    from jvagent.cli import bootstrap_application_graph
    import asyncio
    asyncio.run(bootstrap_application_graph(app_root=app_root))
    print("Bootstrap successful")
except Exception as e:
    print(f"Bootstrap error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
"""
        )

        try:
            # Run bootstrap script
            result = subprocess.run(
                [sys.executable, str(bootstrap_script)],
                cwd=str(self.output_dir),
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout
            )

            if result.returncode != 0:
                logger.warning(f"Bootstrap validation had issues: {result.stderr}")
                if result.stdout:
                    logger.debug(f"Bootstrap stdout: {result.stdout}")
            else:
                logger.info("Bootstrap validation successful")
        except subprocess.TimeoutExpired:
            logger.warning("Bootstrap validation timed out")
        except Exception as e:
            logger.warning(f"Bootstrap validation failed: {e}")
        finally:
            # Clean up
            if bootstrap_script.exists():
                bootstrap_script.unlink()

    def _generate_lambda_handler(self) -> None:
        """Generate Lambda handler entrypoint."""
        logger.info("Generating Lambda handler...")

        from jvagent.bundle.lambda_handler import generate_lambda_handler

        handler_content = generate_lambda_handler()
        handler_file = self.output_dir / "lambda_handler.py"
        handler_file.write_text(handler_content)

        logger.info(f"Lambda handler generated: {handler_file}")

    def _generate_dockerfile(self) -> None:
        """Generate Dockerfile."""
        logger.info("Generating Dockerfile...")

        from jvagent.bundle.dockerfile import generate_dockerfile

        dockerfile_content = generate_dockerfile()
        dockerfile = self.output_dir / "Dockerfile"
        dockerfile.write_text(dockerfile_content)

        logger.info(f"Dockerfile generated: {dockerfile}")

    def _generate_requirements(self) -> None:
        """Generate requirements.txt with locked dependencies."""
        logger.info("Generating requirements.txt...")

        requirements = self._collect_requirements()
        requirements_file = self.output_dir / "requirements.txt"
        with open(requirements_file, "w") as f:
            f.write("\n".join(requirements))
            f.write("\n")

        logger.info(f"Requirements file generated: {requirements_file}")

    def _generate_readme(self) -> None:
        """Generate README.md with deployment instructions."""
        logger.info("Generating README.md...")

        lambda_section = ""
        if self.generate_lambda:
            lambda_section = """### AWS Lambda Deployment

1. **Upload to Lambda**:
   - Upload this bundle (or the ZIP file) to AWS Lambda
   - Set handler to: `lambda_handler.handler`
   - Configure environment variables (see below)

2. **Configure API Gateway**:
   - Set up API Gateway trigger for your Lambda function
   - Configure CORS if needed

3. **Lambda Environment Variables**:
   - `JVAGENT_ADMIN_PASSWORD`: Admin user password (required)
   - `JVSPATIAL_DB_TYPE`: Database type (default: `dynamodb` for Lambda)
   - `JVSPATIAL_DYNAMODB_TABLE_NAME`: DynamoDB table name (default: `jvagent`)
   - `JVSPATIAL_DYNAMODB_REGION`: AWS region (default: `us-east-1`)
   - Other variables as defined in your app.yaml

**Note**: The Lambda handler (`lambda_handler.py`) is already configured and ready to use.
"""
        else:
            lambda_section = """### AWS Lambda Deployment

To deploy to AWS Lambda, you'll need to create a Lambda handler. See the jvagent bundling documentation for details.

**Lambda Environment Variables**:
- `JVAGENT_ADMIN_PASSWORD`: Admin user password (required)
- `JVSPATIAL_DB_TYPE`: Database type (default: `dynamodb` for Lambda)
- `JVSPATIAL_DYNAMODB_TABLE_NAME`: DynamoDB table name
- `JVSPATIAL_DYNAMODB_REGION`: AWS region
- Other variables as defined in your app.yaml
"""

        docker_section = ""
        if self.generate_docker:
            docker_section = """### Docker Deployment

1. **Build Docker image**:
   ```bash
   docker build -t my-jvagent-app .
   ```

2. **Run container**:
   ```bash
   docker run -p 8000:8000 \\
     -e JVAGENT_ADMIN_PASSWORD=your-password \\
     -e JVSPATIAL_DB_TYPE=json \\
     -e JVSPATIAL_DB_PATH=/app/data/jvagent_db \\
     my-jvagent-app
   ```

3. **Docker Environment Variables**:
   - `JVAGENT_ADMIN_PASSWORD`: Admin user password (required)
   - `JVSPATIAL_DB_TYPE`: Database type (default: `json`)
   - `JVSPATIAL_DB_PATH`: Database path (default: `/app/data/jvagent_db`)
   - `JVSPATIAL_MONGODB_URI`: MongoDB connection string (if using MongoDB)
   - Other variables as defined in your app.yaml

**Note**: The Dockerfile is already configured and ready to use.
"""
        else:
            docker_section = """### Docker Deployment

To deploy using Docker, you'll need to create a Dockerfile. See the jvagent bundling documentation for details.

**Docker Environment Variables**:
- `JVAGENT_ADMIN_PASSWORD`: Admin user password (required)
- `JVSPATIAL_DB_TYPE`: Database type (default: `json`)
- `JVSPATIAL_DB_PATH`: Database path (default: `/app/data/jvagent_db`)
- `JVSPATIAL_MONGODB_URI`: MongoDB connection string (if using MongoDB)
- Other variables as defined in your app.yaml
"""

        readme_content = f"""# jvagent App Bundle

This bundle contains a ready-to-deploy jvagent application created using the jvagent bundling service.

## Bundle Contents

- `app/`: Application source files (app.yaml, agents/, etc.)
- `packages/`: Python dependencies (installed via pip)
- `src/`: Editable packages (jvagent, jvspatial)
{"- `lambda_handler.py`: AWS Lambda handler entrypoint" if self.generate_lambda else ""}
{"- `Dockerfile`: Docker container configuration" if self.generate_docker else ""}
- `requirements.txt`: Locked dependency list
- `README.md`: This file

## Deployment Options

{lambda_section}

{docker_section}

## Running Locally

To test the bundled app locally before deployment:

```bash
# Set Python path to include bundled packages
export PYTHONPATH="$PWD/packages:$PWD/src/jvagent:$PWD/src/jvspatial:$PYTHONPATH"

# Navigate to app directory
cd app

# Run jvagent
python -m jvagent
```

Or run from the bundle root:

```bash
export PYTHONPATH="$PWD/packages:$PWD/src/jvagent:$PWD/src/jvspatial:$PYTHONPATH"
cd app
python -m jvagent
```

## Bundle Information

- **App Root**: `{self.app_root}`
- **Bundle Created**: Using jvagent bundling service
- **Lambda Handler**: {"Generated" if self.generate_lambda else "Not generated"}
- **Dockerfile**: {"Generated" if self.generate_docker else "Not generated"}

## Troubleshooting

### Import Errors

If you encounter import errors when running the bundle:
1. Ensure PYTHONPATH includes `packages`, `src/jvagent`, and `src/jvspatial`
2. Verify all dependencies are installed in `packages/`
3. Check that editable packages are correctly installed

### Database Configuration

- For **JSON/SQLite**: Set `JVSPATIAL_DB_TYPE=json` and `JVSPATIAL_DB_PATH`
- For **MongoDB**: Set `JVSPATIAL_DB_TYPE=mongodb` and `JVSPATIAL_MONGODB_URI`
- For **DynamoDB** (Lambda): Set `JVSPATIAL_DB_TYPE=dynamodb` and DynamoDB configuration

### Missing Dependencies

If dependencies are missing:
1. Check `requirements.txt` for the full dependency list
2. Re-run the bundling process to ensure all dependencies are included
3. Verify that your app's `requirements.txt` (if exists) is up to date

## Next Steps

1. **Test locally** using the instructions above
2. **Configure environment variables** for your deployment target
3. **Deploy** using your chosen method (Lambda, Docker, etc.)
4. **Monitor** your deployment and check logs for any issues

For more information, see the jvagent bundling service documentation.
"""

        readme_file = self.output_dir / "README.md"
        readme_file.write_text(readme_content)

        logger.info(f"README generated: {readme_file}")

    def create_zip(self) -> Optional[Path]:
        """Create ZIP archive of the bundle.

        Returns:
            Path to created ZIP file, or None if creation failed
        """
        logger.info("Creating ZIP archive...")

        zip_path = self.output_dir.parent / f"{self.output_dir.name}.zip"

        try:
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
                # Add all files in bundle directory
                for root, dirs, files in os.walk(self.output_dir):
                    # Skip __pycache__ directories
                    dirs[:] = [d for d in dirs if d != "__pycache__"]

                    for file in files:
                        file_path = Path(root) / file
                        # Skip .pyc files
                        if file_path.suffix == ".pyc":
                            continue

                        # Calculate relative path for archive
                        arcname = file_path.relative_to(self.output_dir.parent)
                        zipf.write(file_path, arcname)
                        logger.debug(f"Added to ZIP: {arcname}")

            logger.info(f"ZIP archive created: {zip_path}")
            return zip_path

        except Exception as e:
            logger.error(f"Failed to create ZIP archive: {e}", exc_info=True)
            return None

