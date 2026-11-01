"""Example external plugin: python-subprocess runtime."""


def register(host):
    @host.tool(
        name="example.hello_sub",
        description="Say hello from a subprocess plugin. Optional parameter: name.",
        props=[{"name": "name", "type": "string", "default": "World"}],
    )
    async def hello(args):
        name = (args or {}).get("name") or "World"
        return f"Hello, {name}! (subprocess plugin com.example.hello_sub)"
