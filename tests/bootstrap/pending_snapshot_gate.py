#!/usr/bin/env python3
"""Precommit fixed-point gate over two snapshots of the current pending source.

Unlike test_bootstrap_smoke's clean-clone gate, this gate copies the present
working tree, including staged, unstaged, and untracked source, while excluding
root Git metadata and only the repository's known Git-ignored generated output.
It creates no commits.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
STAGE0_COMMIT = "9cc060d0a408ea88bd5a0c42cbf8ad5bf61cf9a9"
IGNORED_GENERATED_DIRS = {"out", "__pycache__"}
IGNORED_GENERATED_SUFFIXES = {".pyc", ".class"}
DECLARED_ARTIFACTS = {
    "compiler.py", "latent_host.py", "LtJavaDaemon.java", "JReflect.java",
    "LtRt.java",
}

sys.path.insert(0, str(ROOT))
from tests._safe_test_env import clean_test_environment  # noqa: E402


def git_tracked_paths(root: Path) -> set[str]:
    """Return indexed paths only when root itself is the Git worktree."""
    top = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
        capture_output=True, check=False)
    if top.returncode or Path(os.fsdecode(top.stdout.strip())).resolve() != root.resolve():
        return set()
    listing = subprocess.run(["git", "-C", str(root), "ls-files", "-z"],
                             capture_output=True, check=False)
    if listing.returncode:
        raise RuntimeError(
            f"cannot inventory tracked paths in {root}: "
            f"{os.fsdecode(listing.stderr).strip()}")
    return {os.fsdecode(item) for item in listing.stdout.split(b"\0") if item}


def _tracked_below(relative: str, tracked: set[str]) -> bool:
    prefix = relative.rstrip("/") + "/"
    return relative in tracked or any(item.startswith(prefix) for item in tracked)


def _ignored_generated(relative: str, *, is_dir: bool,
                       tracked: set[str]) -> bool:
    """Match only .gitignore's output/cache patterns; tracked paths always win."""
    path = PurePosixPath(relative)
    if relative == ".git":
        return True
    if relative in tracked:
        return False
    if is_dir:
        generated_dir = any(part in IGNORED_GENERATED_DIRS for part in path.parts)
        return generated_dir and not _tracked_below(relative, tracked)
    if any(part in IGNORED_GENERATED_DIRS for part in path.parts[:-1]):
        return True
    return path.suffix in IGNORED_GENERATED_SUFFIXES


def source_entries(root: Path, tracked_paths: set[str] | None = None) -> dict[str, str]:
    """Hash source contents, entry types, and modes; omit ignored generated trees."""
    tracked = git_tracked_paths(root) if tracked_paths is None else tracked_paths
    entries: dict[str, str] = {}
    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        relative_dir = current_path.relative_to(root)
        kept_dirs = []
        for name in dirs:
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            if _ignored_generated(relative, is_dir=True, tracked=tracked):
                continue
            if path.is_symlink():
                payload = (b"symlink\0" + oct(stat.S_IMODE(path.lstat().st_mode)).encode()
                           + b"\0" + os.readlink(path).encode("utf-8"))
                entries[relative] = hashlib.sha256(payload).hexdigest()
                continue
            kept_dirs.append(name)
        dirs[:] = sorted(kept_dirs)
        if relative_dir.parts:
            relative = relative_dir.as_posix()
            metadata = current_path.lstat()
            payload = b"directory\0" + oct(stat.S_IMODE(metadata.st_mode)).encode()
            entries[relative] = hashlib.sha256(payload).hexdigest()
        for name in sorted(files):
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            if _ignored_generated(relative, is_dir=False, tracked=tracked):
                continue
            metadata = path.lstat()
            mode = oct(stat.S_IMODE(metadata.st_mode)).encode()
            if stat.S_ISLNK(metadata.st_mode):
                kind = b"symlink"
                content = os.readlink(path).encode("utf-8")
            elif stat.S_ISREG(metadata.st_mode):
                kind = b"file"
                content = path.read_bytes()
            else:
                kind = f"special-{stat.S_IFMT(metadata.st_mode)}".encode()
                content = b""
            entries[relative] = hashlib.sha256(kind + b"\0" + mode + b"\0" + content).hexdigest()
    return entries


def copy_pending_source(source: Path, destination: Path,
                        tracked_paths: set[str] | None = None) -> None:
    tracked = git_tracked_paths(source) if tracked_paths is None else tracked_paths

    def ignore(directory: str, names: list[str]) -> set[str]:
        current = Path(directory)
        ignored = set()
        for name in names:
            path = current / name
            relative = path.relative_to(source).as_posix()
            if path.is_dir() and not path.is_symlink():
                if _ignored_generated(relative, is_dir=True, tracked=tracked):
                    ignored.add(name)
            elif _ignored_generated(relative, is_dir=False, tracked=tracked):
                ignored.add(name)
        return ignored

    shutil.copytree(source, destination, symlinks=True, ignore=ignore,
                    copy_function=shutil.copy2)


def run(command: list[str], *, cwd: Path, env: dict[str, str], timeout: int = 120) -> None:
    result = subprocess.run(command, cwd=cwd, env=env, text=True,
                            encoding="utf-8", capture_output=True,
                            timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(
            f"command failed ({result.returncode}): {command!r}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}")


def stage0_archive(destination: Path, cwd: Path, env: dict[str, str]) -> None:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "archive", STAGE0_COMMIT], cwd=cwd,
        env=env, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(
            f"fixed Stage0 archive unavailable ({STAGE0_COMMIT}): "
            f"{result.stderr.decode('utf-8', errors='replace')}")
    import io
    import tarfile

    destination.mkdir()
    with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:") as archive:
        for member in archive.getmembers():
            relative = PurePosixPath(member.name)
            if relative.is_absolute() or ".." in relative.parts:
                raise RuntimeError(f"unsafe fixed Stage0 archive path: {member.name}")
            target = destination.joinpath(*relative.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                stream = archive.extractfile(member)
                if stream is None:
                    raise RuntimeError(f"cannot read Stage0 file: {member.name}")
                target.write_bytes(stream.read())
            elif member.issym() or member.islnk():
                raise RuntimeError(f"unexpected link in fixed Stage0 archive: {member.name}")


def artifact_snapshot(directory: Path) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    unexpected: list[str] = []
    if not directory.is_dir():
        raise RuntimeError(f"build output directory missing: {directory}")
    for path in sorted(directory.rglob("*"), key=lambda item: item.relative_to(directory).as_posix()):
        relative = path.relative_to(directory).as_posix()
        if path.is_symlink() or not path.is_file():
            unexpected.append(relative)
        else:
            result[relative] = path.read_bytes()
    if unexpected:
        raise RuntimeError(f"non-file build artifacts in {directory}: {unexpected}")
    missing = sorted(DECLARED_ARTIFACTS - set(result))
    extra = sorted(set(result) - DECLARED_ARTIFACTS)
    if missing or extra:
        raise RuntimeError(f"artifact inventory differs in {directory}: missing={missing}, extra={extra}")
    return result


def content_manifest(files: dict[str, bytes]) -> list[dict[str, str]]:
    return [{"path": name, "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in sorted(files.items())]


def build_chain(label: str, source: Path, work: Path,
                env: dict[str, str], stage0_env: dict[str, str]) -> dict[str, dict[str, bytes]]:
    stage0 = work / f"{label}-stage0"
    stage0_archive(stage0, work, env)
    source_lt = source / "bootstrap" / "compiler.lt"
    adapter = (source / "bootstrap" / "latent_host.py").read_bytes()

    stage1 = work / f"{label}-stage1"
    stage1.mkdir()
    run([sys.executable, str(stage0 / "latent.py"), str(source_lt), "-t", "py",
         "-o", str(stage1)], cwd=work, env=stage0_env)
    (stage1 / "latent_host.py").write_bytes(adapter)
    shutil.rmtree(stage0)

    stage2 = work / f"{label}-stage2"
    run([sys.executable, str(stage1 / "compiler.py"), str(source_lt),
         "-o", str(stage2)], cwd=work, env=env)
    (stage2 / "latent_host.py").write_bytes(adapter)

    stage3 = work / f"{label}-stage3"
    run([sys.executable, str(stage2 / "compiler.py"), str(source_lt),
         "-o", str(stage3)], cwd=work, env=env)
    (stage3 / "latent_host.py").write_bytes(adapter)
    return {"stage2": artifact_snapshot(stage2), "stage3": artifact_snapshot(stage3)}


def equal(left: dict[str, bytes], right: dict[str, bytes]) -> bool:
    return left == right


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=ROOT,
                        help="working tree to snapshot (default: repository root)")
    parser.add_argument("--keep-snapshots", type=Path,
                        help="retain the two source snapshots under this new/existing directory")
    args = parser.parse_args()
    source = args.source_root.resolve()
    if not (source / "bootstrap" / "compiler.lt").is_file():
        parser.error(f"not a project root: {source}")

    env = clean_test_environment()
    if args.keep_snapshots:
        parent = args.keep_snapshots.resolve()
        if parent == source or parent.is_relative_to(source):
            parser.error("--keep-snapshots must be outside the source tree")
        parent.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="pending-fixed-point-"))
    else:
        holder = tempfile.TemporaryDirectory(prefix="pending-fixed-point-")
        work = Path(holder.name)
        parent = work / "snapshots"
        parent.mkdir()
    try:
        snapshot_a, snapshot_b = parent / "snapshot-a", parent / "snapshot-b"
        if snapshot_a.exists() or snapshot_b.exists():
            raise RuntimeError("snapshot-a/snapshot-b already exist; refusing to overwrite")
        tracked = git_tracked_paths(source)
        source_before = source_entries(source, tracked)
        copy_pending_source(source, snapshot_a, tracked)
        copy_pending_source(source, snapshot_b, tracked)
        input_a = source_entries(snapshot_a, tracked)
        input_b = source_entries(snapshot_b, tracked)
        if input_a != input_b:
            differing = sorted(key for key in set(input_a) | set(input_b)
                               if input_a.get(key) != input_b.get(key))
            raise RuntimeError(
                f"two pending-source snapshot inputs differ: {differing[:30]}")
        if input_a != source_before:
            differing = sorted(key for key in set(input_a) | set(source_before)
                               if input_a.get(key) != source_before.get(key))
            raise RuntimeError(
                f"snapshot inputs do not exactly cover the current source tree: {differing[:30]}")

        no_javac = work / "no-javac"
        no_javac.mkdir()
        stage0_env = dict(env)
        stage0_env["PATH"] = str(no_javac)
        first = build_chain("root-a", snapshot_a, work, env, stage0_env)
        second = build_chain("root-b", snapshot_b, work, env, stage0_env)
        if source_entries(snapshot_a, tracked) != input_a or source_entries(snapshot_b, tracked) != input_b:
            raise RuntimeError("a bootstrap build mutated its pending-source snapshot")

        checks = {
            "snapshot_inputs_equal": input_a == input_b,
            "root_a_stage2_stage3_equal": equal(first["stage2"], first["stage3"]),
            "root_b_stage2_stage3_equal": equal(second["stage2"], second["stage3"]),
            "stage2_roots_equal": equal(first["stage2"], second["stage2"]),
            "stage3_roots_equal": equal(first["stage3"], second["stage3"]),
        }
        report: dict[str, Any] = {
            "result": "PASS" if all(checks.values()) else "FAIL",
            "source_root": str(source),
            "stage0_commit": STAGE0_COMMIT,
            "excluded": {
                "root_git_metadata": True,
                "gitignored_generated_directories": sorted(IGNORED_GENERATED_DIRS),
                "gitignored_generated_suffixes": sorted(IGNORED_GENERATED_SUFFIXES),
                "tracked_paths_override_ignored_patterns": True,
            },
            "snapshot_entry_count": len(input_a),
            "snapshot_sha256": hashlib.sha256(json.dumps(
                input_a, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "checks": checks,
            "root_a_stage2": content_manifest(first["stage2"]),
            "root_a_stage3": content_manifest(first["stage3"]),
            "root_b_stage2": content_manifest(second["stage2"]),
            "root_b_stage3": content_manifest(second["stage3"]),
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if all(checks.values()) else 1
    except Exception as exc:
        print(f"pending-source dual-root fixed-point gate: FAIL: {exc}", file=sys.stderr)
        return 1
    finally:
        if args.keep_snapshots:
            print(f"snapshot parent retained at {parent}", file=sys.stderr)
        elif 'holder' in locals():
            holder.cleanup()
        if args.keep_snapshots and 'work' in locals():
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
