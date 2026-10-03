#!/usr/bin/env python3
"""Build the emaki CLI reproducibly, or check a built binary for builder paths."""
import argparse
import os
from pathlib import Path
import subprocess

REPO = Path(__file__).resolve().parent.parent


def require(condition, message):
    if not condition:
        raise ValueError(message)


def build_paths():
    return {"source": REPO,
            "cargo": Path(os.environ.get("CARGO_HOME", REPO / ".cache/cargo-home")).absolute(),
            "target": Path(os.environ.get("CARGO_TARGET_DIR", REPO / ".cache/target")).absolute()}


def build():
    env = dict(os.environ)
    # Match Cargo's precedence and whitespace splitting; encoded flags also
    # preserve arguments and source/cache paths containing spaces.
    encoded = env.get("CARGO_ENCODED_RUSTFLAGS")
    if encoded is not None:
        flags = encoded.split("\x1f") if encoded else []
    else:
        flags = env.get("RUSTFLAGS", "").split()
    paths = build_paths()
    remaps = {str(path): f"/emaki-build/{name}"
              for name, original in paths.items() for path in (original, original.resolve())}
    # rustc uses the last matching prefix, so specific nested roots go last.
    for source, replacement in sorted(remaps.items(), key=lambda pair: len(pair[0])):
        require("\x1f" not in source, "build path contains Cargo flag separator")
        flags.append(f"--remap-path-prefix={source}={replacement}")
    env["CARGO_HOME"] = str(paths["cargo"])
    env["CARGO_TARGET_DIR"] = str(paths["target"])
    env["CARGO_ENCODED_RUSTFLAGS"] = "\x1f".join(flags)
    subprocess.run(["cargo", "build", "--release", "--locked", "--offline", "-p", "emaki-cli"],
                   cwd=REPO, env=env, check=True)


def verify_build_paths(binary):
    paths = build_paths()
    if os.environ.get("HOME"):
        paths["home"] = Path(os.environ["HOME"]).absolute()
    contents = binary.read_bytes()
    for name, original in paths.items():
        for path in {original, original.resolve()}:
            if path != Path("/"):
                require(os.fsencode(path) not in contents,
                        f"binary contains builder {name} path; rebuild with make build-core")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("build")
    verify_paths = commands.add_parser("verify-build-paths")
    verify_paths.add_argument("--binary", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "build":
            build()
        else:
            verify_build_paths(args.binary)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, f"core-package: {error}\n")


if __name__ == "__main__":
    main()
