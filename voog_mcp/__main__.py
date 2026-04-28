"""Entry point: `voog-mcp` console script or `python3 -m voog_mcp`."""
import asyncio
import logging
import sys

from voog_mcp.server import run_server


def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    try:
        asyncio.run(run_server())
    except RuntimeError as exc:
        sys.stderr.write(f"❌ {exc}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
