"""fleet serve [--config PATH | --fixture PATH] | fleet snapshot [--config PATH]"""
import argparse
import json
import sys

from . import config as configuration
from . import hub as hubs
from . import server, sources
from .clients import Clients


def main(argv=None):
    parser = argparse.ArgumentParser(prog="fleet", description="Live view of every agent across the T3 fleet.")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="collect and serve the page")
    serve.add_argument("--config", help="TOML config (default ~/.config/fleet/config.toml)")
    serve.add_argument("--fixture", help="serve this snapshot JSON instead of collecting")
    serve.add_argument("--bind")
    serve.add_argument("--port", type=int)
    once = commands.add_parser("snapshot", help="collect every source once and print the snapshot")
    once.add_argument("--config")
    env = commands.add_parser("pass-env-args", help="print pass-env -e arguments (names only) for the configured tokens")
    env.add_argument("--config")
    args = parser.parse_args(argv)

    if args.command == "pass-env-args":
        config = configuration.load(args.config)
        print(" ".join(f"-e {s.token_env}={s.token_pass}" for s in config.servers if s.token_pass))
        return 0

    clients = None
    if args.command == "serve" and args.fixture:
        config = configuration.parse({})
        with open(args.fixture) as handle:
            hub = hubs.FixedHub(json.load(handle))
    else:
        config = configuration.load(args.config)
        clients = Clients(config.servers)
        hub = hubs.Hub(config, sources.build(config, clients))
    if args.command == "snapshot":
        for source in hub.sources:
            hub.run_once(source)
        json.dump(hub.current, sys.stdout, indent=2)
        print()
        return 0
    from . import actions
    server.serve(hub, args.bind or config.bind, args.port or config.port, config.base_path, actions.build(config, clients))
    return 0


if __name__ == "__main__":
    sys.exit(main())
