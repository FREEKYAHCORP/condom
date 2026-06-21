from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import uvicorn

from condom_core.db import connect, init_db


def main() -> None:
    conn = connect()
    init_db(conn)
    uvicorn.run("condom_core.api.app:app", host="127.0.0.1", port=8765, reload=False)


if __name__ == "__main__":
    main()
