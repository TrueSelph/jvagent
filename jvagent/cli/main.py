"""jvagent CLI main entry point."""

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from jvspatial.env import env
from jvspatial.logging import configure_standard_logging

from jvagent.cli import app_commands
from jvagent.cli.commands import (
    bootstrap_only,
    handle_action_command,
    handle_agent_command,
    handle_bundle_command,
    handle_skill_command,
    load_app_env,
    print_usage,
    purge_app_data,
    run_server,
    run_validate,
    show_status,
)
from jvagent.cli.server_config import _set_db_env_from_config
from jvagent.core.config import is_development_mode
from jvagent.stress_seed_graph import STRESS_FLAG_NAMES, parse_stress_seed_for_run

configure_standard_logging(
    level=env("JVSPATIAL_LOG_LEVEL", default="INFO"),
    enable_colors=True,
    preserve_handler_class_names=["DBLogHandler", "StartupLogCounter"],
)
logger = logging.getLogger(__name__)

# Suppress noisy asyncio selector logs
logging.getLogger("asyncio").setLevel(logging.WARNING)

# Subcommands that are not the default (HTTP) server. ``run`` is optional and is stripped.
DISPATCH = frozenset(
    {
        "status",
        "agent",
        "skill",
        "action",
        "bootstrap",
        "bundle",
        "app",
        "validate",
        "stress-seed",
        "stress_seed",
    }
)


def _first_app_root_path(
    args_in: List[str], subcommands: frozenset
) -> Tuple[Optional[str], List[str]]:
    """Return (app_root, argv_without_path_tokens).

    Strips tokens that are existing directories (app root), but keeps subcommands, known
    flags, and :data:`STRESS_FLAG_NAMES` so ``--user-memory-nodes 700`` is not mis-parsed.
    """
    static_flags = frozenset(
        [
            "--debug",
            "--update",
            "--purge",
            "--source",
            "--merge",
            "--serverless",
            "-h",
            "--help",
        ]
    )
    static_flags = static_flags | frozenset(STRESS_FLAG_NAMES)
    app: Optional[str] = None
    out: list[str] = []
    i = 0
    n = len(args_in)
    while i < n:
        arg = args_in[i]
        if arg in subcommands or arg in static_flags:
            out.append(arg)
            i += 1
            continue
        if arg.startswith("-"):
            out.append(arg)
            i += 1
            if i < n and not args_in[i].startswith("-"):
                nxt = args_in[i]
                if nxt in subcommands or nxt in static_flags:
                    out.append(nxt)
                    i += 1
                else:
                    p2 = Path(nxt).expanduser().resolve()
                    if p2.is_dir() and p2.exists():
                        if app is None:
                            app = str(p2)
                        i += 1
                    else:
                        out.append(nxt)
                        i += 1
            continue
        p = Path(arg).expanduser().resolve()
        if p.is_dir() and p.exists():
            if app is None:
                app = str(p)
            i += 1
        else:
            out.append(arg)
            i += 1
    return app, out


def main() -> None:
    """Main entry point for jvagent application."""
    raw = list(sys.argv[1:])

    # Path segments + all CLI names that must not be treated as a directory
    subcommands = DISPATCH | frozenset({"run"})

    first_path, args = _first_app_root_path(raw, subcommands)
    app_root = first_path if first_path is not None else os.getcwd()

    logger.debug("Using app root: %s", app_root)

    load_app_env(app_root=app_root)

    from jvagent.core.app_context import set_app_root

    set_app_root(app_root)

    from jvagent.core.cache import reload_performance_config
    from jvagent.core.profiling import reload_profiling_config

    reload_performance_config()
    reload_profiling_config()

    if "--serverless" in args:
        args = [arg for arg in args if arg != "--serverless"]
        os.environ["SERVERLESS_MODE"] = "true"
        os.environ["AWS_LAMBDA_FUNCTION_NAME"] = "jvagent-serverless"
        logger.info(
            "Serverless mode enabled (single worker, jvspatial SERVERLESS_MODE=true)"
        )

    _set_db_env_from_config(app_root)

    debug_flag = "--debug" in args
    if debug_flag:
        args = [arg for arg in args if arg != "--debug"]
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG)
        for handler in root_logger.handlers:
            handler.setLevel(logging.DEBUG)
        logging.getLogger("jvagent").setLevel(logging.DEBUG)

    has_update = "--update" in args
    has_source = "--source" in args
    has_merge = "--merge" in args

    if has_update:
        if has_source:
            update_mode = "source"
        else:
            update_mode = "merge"
    else:
        update_mode = None
        if has_source or has_merge:
            logger.warning("--source/--merge flags have no effect without --update")

    args = [arg for arg in args if arg not in ["--update", "--source", "--merge"]]

    purge_flag = "--purge" in args
    if purge_flag:
        args = [arg for arg in args if arg != "--purge"]

        if not is_development_mode():
            logger.error("The --purge flag is only allowed in development mode.")
            logger.error(
                "Set JVSPATIAL_ENVIRONMENT=development or ensure you are not in production mode."
            )
            sys.exit(1)

        purge_app_data(app_root=app_root)

    # ``jvagent`` and ``jvagent run`` are the same
    if args and args[0] == "run":
        args = args[1:]

    if args and args[0] in ("-h", "--help"):
        print_usage()
        return

    # -------------------------------------------------------------------------
    # Exclusive subcommands (not the default server)
    # -------------------------------------------------------------------------
    if args and args[0] in DISPATCH:
        if args[0] == "status":
            asyncio.run(show_status(app_root=app_root))
        elif args[0] == "app":
            app_commands.handle_app_command(args[1:], default_cwd=app_root)
        elif args[0] == "agent":
            handle_agent_command(args[1:], app_root=app_root)
        elif args[0] == "action":
            handle_action_command(args[1:], app_root=app_root)
        elif args[0] == "skill":
            handle_skill_command(args[1:], app_root=app_root)
        elif args[0] == "bootstrap":
            asyncio.run(bootstrap_only(update_mode=update_mode, app_root=app_root))
        elif args[0] == "bundle":
            handle_bundle_command(args[1:], app_root=app_root)
        elif args[0] == "validate":
            sys.exit(run_validate(app_root))
        elif args[0] in ("stress-seed", "stress_seed"):
            from jvagent.stress_seed_graph import main as stress_seed_main

            stress_seed_main(args[1:], app_root=app_root)
        return

    # -------------------------------------------------------------------------
    # Default: start HTTP server (``jvagent``, ``jvagent --debug``, ``jvagent --stress-seed`` …)
    # -------------------------------------------------------------------------
    stress_seed, args_rest = parse_stress_seed_for_run(args, allow_env_defaults=True)
    if args_rest:
        bad = [a for a in args_rest if not str(a).startswith("-")]
        if bad:
            logger.error("Unknown argument(s): %s", " ".join(bad))
            print_usage()
            sys.exit(2)

    run_server(
        update_mode=update_mode,
        debug=debug_flag,
        app_root=app_root,
        stress_seed=stress_seed,
    )
