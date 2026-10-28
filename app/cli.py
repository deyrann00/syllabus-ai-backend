"""Admin commands: `python -m app.cli import-rubric rubric/2026.1.yaml`, `python -m app.cli seed`."""

import argparse
import asyncio
import logging
from pathlib import Path

from app.db import pg
from app.db.mongo import close_mongo, init_mongo


async def _import_rubric(path: Path, activate: bool) -> None:
    from app.services.rubric import import_rubric

    async with pg.session_scope() as session:
        rubric = await import_rubric(session, path, activate=activate)
        print(f"imported rubric {rubric.code} (active={rubric.is_active})")


async def _seed() -> None:
    from seed.demo import run

    await run()


async def _main(args: argparse.Namespace) -> None:
    await init_mongo()
    try:
        if args.command == "import-rubric":
            await _import_rubric(Path(args.path), not args.no_activate)
        elif args.command == "seed":
            await _seed()
    finally:
        await close_mongo()
        await pg.get_engine().dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    rubric = sub.add_parser("import-rubric", help="import a rubric YAML as a new version")
    rubric.add_argument("path")
    rubric.add_argument(
        "--no-activate", action="store_true", help="import without making it the active rubric"
    )
    sub.add_parser("seed", help="load the prototype demo data and run its checks")
    asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    main()
