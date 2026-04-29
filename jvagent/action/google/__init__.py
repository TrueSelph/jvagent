"""Google action package: Modularized namespace for Google Workspace actions."""

# OAuth routes in ``google.endpoints`` are imported from server bootstrap
# (see ``jvagent.cli.server_config._import_core_endpoint_modules``) so that
# importing subpackages (e.g. drive ingest helpers) does not require google-api-client.
