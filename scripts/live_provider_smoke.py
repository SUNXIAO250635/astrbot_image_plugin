from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import adapters  # noqa: E402
from imagegen_core.http_client import close_all_sessions  # noqa: E402
from media import extract_all_media  # noqa: E402


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"Missing environment variable: {name}")
    return value


async def _run(args):
    config = {
        "base_url": _required_env("ASTRBOT_IMAGEGEN_BASE_URL"),
        "api_key": _required_env("ASTRBOT_IMAGEGEN_API_KEY"),
        "model": _required_env("ASTRBOT_IMAGEGEN_MODEL"),
        "size": os.getenv("ASTRBOT_IMAGEGEN_SIZE", "1024x1024"),
        "seconds": int(os.getenv("ASTRBOT_IMAGEGEN_SECONDS", "5")),
    }
    try:
        if args.capability == "text_to_image":
            response = await adapters.image_generation(
                config, args.prompt, args.timeout
            )
        else:
            response = await adapters.openai_video(
                config, args.prompt, timeout=args.timeout
            )
        media = extract_all_media(response)
        if not media:
            raise SystemExit("Provider responded without parseable media")
        for kind, value in media:
            display = value if value.startswith("http") else f"{value[:32]}..."
            print(f"{kind}: {display}")
    finally:
        await close_all_sessions()


def main():
    parser = argparse.ArgumentParser(description="Live provider smoke test")
    parser.add_argument("capability", choices=("text_to_image", "text_to_video"))
    parser.add_argument("prompt")
    parser.add_argument("--timeout", type=int, default=300)
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
