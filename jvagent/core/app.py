"""App node - Root application node for jvagent."""

from jvspatial.core import Node


class App(Node):
    """Root application node representing the jvagent application.
    
    This node serves as the root of the application graph and manages
    the overall system state.
    
    Attributes:
        name: Application name
        version: Application version
        description: Application description
    """
    name: str = "jvAgent"
    version: str = "0.0.1"
    description: str = "jvagent Application"
    status: str = "active"  # active, inactive, maintenance

