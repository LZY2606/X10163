"""CLI entry point: ``python -m cycleaccord --host ... --port ...``."""
import argparse
import os
import sys

from .persistence import Store
from .sample import SAMPLE_GRAPH
from .server import create_server
from .service import AccordService


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="cycleaccord", description="依赖环协商器 (dependency cycle negotiator)"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5236)
    parser.add_argument(
        "--db",
        default=os.environ.get("CYCLEACCORD_DB", "cycleaccord.db"),
        help="SQLite file for persistence (default: cycleaccord.db)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="delete the database file before starting (fresh demo)",
    )
    args = parser.parse_args(argv)

    if args.reset and os.path.exists(args.db):
        os.remove(args.db)

    store = Store(args.db)
    service = AccordService(store, bootstrap=SAMPLE_GRAPH)
    server = create_server(args.host, args.port, service)
    print("依赖环协商器 listening on http://%s:%d (db=%s)" % (args.host, args.port, args.db))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
