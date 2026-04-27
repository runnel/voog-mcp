"""Entry point: `voog-mcp` console script or `python3 -m voog_mcp`."""
import asyncio
import logging
import sys

from voog_mcp.server import run_server


def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    asyncio.run(run_server())


if __name__ == "__main__":
    main()
