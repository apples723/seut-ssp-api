#!/usr/bin/env python3
"""Generate buildinfo.json and optionally build/push the API Docker image."""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.resolve()
REGISTRY = "ghcr.io/seut-org"
API_IMAGE = f"{REGISTRY}/ssp-api"


def run_cmd(cmd, check=True, capture=False):
    print(f"  → {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, capture_output=capture, text=True)


def get_version():
    version_file = PROJECT_ROOT / "releaseversion.txt"
    if version_file.exists():
        return version_file.read_text().strip()
    return "dev"


def get_git_info():
    def git(fmt):
        r = run_cmd(["git", "log", "-1", f"--format={fmt}"], check=False, capture=True)
        return r.stdout.strip() if r.returncode == 0 else ""

    hash_full = run_cmd(["git", "rev-parse", "HEAD"], check=False, capture=True).stdout.strip()
    return {
        "commitHash": hash_full,
        "commitHashShort": hash_full[:7],
        "commitMessage": git("%s"),
        "commitTime": git("%cI"),
        "author": git("%an"),
    }


def generate_buildinfo(version):
    git_info = get_git_info()
    build_time = datetime.now(timezone.utc).isoformat()

    buildinfo = {
        "version": version,
        "buildTime": build_time,
        "commitHash": git_info["commitHash"],
        "commitHashShort": git_info["commitHashShort"],
        "commitMessage": git_info["commitMessage"],
        "commitTime": git_info["commitTime"],
        "author": git_info["author"],
    }

    target = PROJECT_ROOT / "buildinfo.json"
    target.write_text(json.dumps(buildinfo, indent=2))
    print(f"   Generated {target} — v{version} @ {git_info['commitHashShort']}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="SSP API release tool")
    parser.add_argument("--version", help="Override version (default: from releaseversion.txt)")
    parser.add_argument("--build", action="store_true", help="Build Docker image after generating buildinfo")
    parser.add_argument("--push", action="store_true", help="Push image to registry")
    parser.add_argument("--tag", default="latest", help="Docker tag (default: latest)")
    args = parser.parse_args()

    version = args.version or get_version()

    if args.build:
        print(f"\n  Generating buildinfo (v{version})...")
        generate_buildinfo(version)

        print(f"\n  Building {API_IMAGE}:{args.tag}...")
        run_cmd([
            "docker", "build", "--no-cache", "--platform", "linux/amd64",
            "-t", f"{API_IMAGE}:{args.tag}",
            "."
        ])

    if args.push:
        image = f"{API_IMAGE}:{args.tag}"
        # Verify the image exists locally before pushing
        result = run_cmd(["docker", "image", "inspect", image], check=False, capture=True)
        if result.returncode != 0:
            print(f"\n  Error: image {image} not found locally.")
            print(f"   Run 'python release.py --build' first.")
            sys.exit(1)

        print(f"\n  Pushing {image}...")
        run_cmd(["docker", "push", image])

    if not args.build and not args.push:
        # Default: just generate buildinfo
        print(f"\n  Generating buildinfo (v{version})...")
        generate_buildinfo(version)

    print("\n  Done!")


if __name__ == "__main__":
    main()
