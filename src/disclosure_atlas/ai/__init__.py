"""AI layer: a read-only SQL guard, an MCP server for agents and "Ask the Atlas" (see docs/ai.md).

Install with the `ai` extra. Everything here reads the gold views only, through a sandboxed DuckDB
connection, and never writes to the lake.
"""
