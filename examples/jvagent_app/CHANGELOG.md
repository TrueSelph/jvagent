# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Initial boilerplate structure
- Example action implementation
- Example agent configuration
- Documentation
- **MCPAction** (core action `jvagent/mcp`): Gateway action that pairs with a named MCP server and exposes `fulfill(natural_language_command)` for use by other actions (e.g. InteractActions). Requires a LanguageModelAction on the agent for NL→tool mapping. See [jvagent/action/mcp/README.md](../../jvagent/action/mcp/README.md).

