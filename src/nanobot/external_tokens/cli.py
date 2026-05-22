from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from nanobot.external_tokens.reddit import bootstrap_reddit

logger = logging.getLogger(__name__)


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def _bootstrap_reddit(args: argparse.Namespace) -> None:
    asyncio.run(
        bootstrap_reddit(
            client_id=args.client_id,
            client_secret=args.client_secret,
            redirect_port=args.redirect_port,
            scopes=args.scopes,
            user_agent=args.user_agent,
            config_path=args.config,
        )
    )


def main() -> None:
    _setup_logging()

    parser = argparse.ArgumentParser(
        description="Bootstrap external service credentials",
    )
    parser.add_argument("--config", default="config.yaml", help="Path to config yaml")

    sub = parser.add_subparsers(dest="service", required=True)

    reddit_parser = sub.add_parser("reddit", help="Bootstrap Reddit OAuth refresh token")
    reddit_parser.add_argument("--client-id", required=True, help="Reddit app client_id")
    reddit_parser.add_argument("--client-secret", required=True, help="Reddit app client_secret")
    reddit_parser.add_argument(
        "--redirect-port",
        type=int,
        default=8080,
        help="Local port for OAuth redirect (default: 8080)",
    )
    reddit_parser.add_argument(
        "--scopes",
        default="identity,read,submit,edit,privatemessages,history",
        help="OAuth scopes (comma-separated)",
    )
    reddit_parser.add_argument(
        "--user-agent",
        default="nanobot/1.0 by u/YOUR_USERNAME",
        help="Reddit API user agent",
    )

    args = parser.parse_args()

    if args.service == "reddit":
        try:
            _bootstrap_reddit(args)
        except KeyboardInterrupt:
            print("\nAborted by user.")
            sys.exit(1)
        except Exception as exc:
            logger.exception("Reddit OAuth bootstrap failed: %s", exc)
            print(f"\nError: {exc}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
