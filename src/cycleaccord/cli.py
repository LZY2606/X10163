"""命令行入口：python -m cycleaccord --host 127.0.0.1 --port 5236"""

from __future__ import annotations

import argparse
import os
import sys

from .demo import DEMO_GRAPH
from .server import make_server
from .service import Service
from .storage import Storage

DEFAULT_INCLUDE = ("hard", "runtime", "test")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="cycleaccord",
                                     description="依赖环协商器")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5236)
    parser.add_argument("--db", default=os.environ.get("CYCLEACCORD_DB", "cycleaccord.db"))
    parser.add_argument("--no-seed", action="store_true", help="空库时不导入演示图")
    args = parser.parse_args(argv)

    storage = Storage(args.db)
    service = Service(storage)

    if not args.no_seed and service.latest_version() is None:
        service.import_graph(DEMO_GRAPH)
        analysis = service.ensure_analysis(list(DEFAULT_INCLUDE))
        print("已导入演示图（版本 %d）并创建默认分析 %s"
              % (analysis["graph_version"], analysis["id"]), file=sys.stderr)

    # 重新用 make_server 打开同一数据库文件（WAL 允许共存）
    storage.close()
    httpd, _ = make_server(args.host, args.port, args.db)
    print("依赖环协商器运行中：http://%s:%d" % (args.host, args.port), file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
