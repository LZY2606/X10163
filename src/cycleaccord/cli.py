"""命令行入口：python -m cycleaccord / cycleaccord。"""
from __future__ import annotations

import argparse
import os
import sys

from .demo import load_demo_graph
from .storage import Store
from .web.app import create_server


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="cycleaccord", description="依赖环协商器"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5236)
    parser.add_argument(
        "--db",
        default=os.environ.get(
            "CYCLEACCORD_DB",
            os.path.join(os.getcwd(), "cycleaccord_data", "cycleaccord.db"),
        ),
        help="SQLite 数据库路径（默认 ./cycleaccord_data/cycleaccord.db）",
    )
    parser.add_argument(
        "--reset", action="store_true", help="启动时清空数据库并重新播种演示图"
    )
    args = parser.parse_args(argv)

    if args.reset and os.path.exists(args.db):
        for suffix in ("", "-wal", "-shm"):
            path = args.db + suffix
            if os.path.exists(path):
                os.remove(path)

    store = Store(args.db)
    if store.current_version() is None:
        store.put_graph(load_demo_graph(), note="内置演示图")

    httpd = create_server(store, host=args.host, port=args.port)
    print(f"依赖环协商器已启动：http://{args.host}:{args.port}", flush=True)
    print(f"数据库：{args.db}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
