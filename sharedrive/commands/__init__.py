from sharedrive.commands.auth import register_auth_commands
from sharedrive.commands.descriptor import register_descriptor_commands
from sharedrive.commands.diagram import register_diagram_command

__all__ = [
    "register_auth_commands",
    "register_descriptor_commands",
    "register_diagram_command",
]
