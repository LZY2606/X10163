"""CLI: python -m cycleaccord --host 127.0.0.1 --port 5236"""
from __future__ import annotations

import argparse

from .demo import demo_graph
from .server import serve
from .store import Store


def main(argv=None):
    parser = argparse.ArgumentParser(prog="cycleaccord",
                                     description="依赖环协商器")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5236)
    parser.add_argument("--state", default=".cycleaccord-state.json",
                        help="persistence file")
    args = parser.parse_args(argv)

    store = Store(args.state)
    if store.data["graph"] is None:
        store.load_graph(demo_graph())
        store.set_view(["hard", "runtime"])
    serve(args.host, args.port, store)


if __name__ == "__main__":
    main()
