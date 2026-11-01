"""Example external MCP plugin: no third-party dependencies; can be copied directly to user mcp_plugins/."""


def register(host):
    @host.tool(
        name="example.hello",
        description="Say hello. Optional parameter: name. Used to verify whether external MCP plugins load successfully.",
        props=[{"name": "name", "type": "string", "default": "World"}],
    )
    async def hello(args):
        name = (args or {}).get("name") or "World"
        return f"Hello, {name}! (from external plugin com.example.hello)"

    # Optional: read-only configuration
    cfg = host.get("config_readonly")
    log = host.get("logger")
    if log and cfg is not None:
        log.debug("[example.hello] Acquired config_readonly")
