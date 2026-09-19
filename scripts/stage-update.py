#!/usr/bin/env python3
"""Verify and stage a macOS app; never install it or control a running process."""

import argparse
import ctypes
import datetime
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from xml.parsers.expat import ExpatError


REPOSITORY = Path(__file__).resolve().parents[1]
BUNDLE_ID = 'dev.napier.ModelHarbor'


class StageError(Exception):
    pass


def inventory(bundle):
    """Hash bytes, modes and link targets without following links during traversal."""
    entries = {}

    def visit(directory):
        for path in sorted(directory.iterdir()):
            relative = path.relative_to(bundle).as_posix()
            mode = path.lstat().st_mode
            entry = {'mode': stat.S_IMODE(mode)}
            if stat.S_ISLNK(mode):
                target = os.readlink(path)
                try:
                    resolved = path.resolve(strict=True)
                except (OSError, RuntimeError) as error:
                    raise StageError(f'Invalid bundle symlink: {relative}') from error
                if os.path.isabs(target) or not resolved.is_relative_to(bundle):
                    raise StageError(f'Bundle symlink escapes its relative boundary: {relative}')
                entry.update(type='symlink', target=target)
            elif stat.S_ISDIR(mode):
                entry['type'] = 'directory'
                visit(path)
            elif stat.S_ISREG(mode):
                digest = hashlib.sha256()
                with path.open('rb') as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                        digest.update(chunk)
                entry.update(type='file', size=path.stat().st_size, sha256=digest.hexdigest())
            else:
                raise StageError(f'Unsupported bundle file type: {relative}')
            entries[relative] = entry

    visit(bundle)
    return dict(sorted(entries.items()))


def bundle_metadata(bundle):
    try:
        with (bundle / 'Contents/Info.plist').open('rb') as handle:
            info = plistlib.load(handle)
    except (OSError, ValueError, ExpatError, plistlib.InvalidFileException) as error:
        raise StageError('The app needs a valid Contents/Info.plist.') from error
    if not isinstance(info, dict) or info.get('CFBundleIdentifier') != BUNDLE_ID:
        raise StageError('The app is not a Model Harbor bundle.')
    executable = info.get('CFBundleExecutable')
    if not isinstance(executable, str) or executable in ('', '.', '..') or '/' in executable:
        raise StageError('Invalid bundle executable name.')
    binary = bundle / 'Contents/MacOS' / executable
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise StageError('The bundle executable is missing or not executable.')
    fields = ('CFBundleIdentifier', 'CFBundleExecutable', 'CFBundleShortVersionString', 'CFBundleVersion')
    if any(not isinstance(info.get(key), str) or not info[key] for key in fields):
        raise StageError('The bundle is missing string version metadata.')
    return {key: info[key] for key in fields}


def verify_signature(bundle):
    if sys.platform != 'darwin':
        raise StageError('Staging signed app bundles requires macOS.')
    try:
        subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(bundle)],
                       check=True, capture_output=True, timeout=60)
        result = subprocess.run(['/usr/bin/codesign', '-d', '--verbose=4', str(bundle)],
                                check=True, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as error:
        raise StageError('Code signature verification failed; no release was staged.') from error
    fields = {}
    for line in (result.stdout + '\n' + result.stderr).splitlines():
        key, separator, value = line.partition('=')
        if separator and key in ('Identifier', 'TeamIdentifier', 'CDHash', 'Signature'):
            fields[key] = value
        elif separator and key == 'Authority':
            fields.setdefault('authorities', []).append(value)
    if fields.get('Identifier') != BUNDLE_ID or not fields.get('CDHash'):
        raise StageError('The signature does not identify a Model Harbor build.')
    return {'verified': True, **fields}


def source_context(repository):
    try:
        commit = subprocess.run(['git', '-C', str(repository), 'rev-parse', '--verify', 'HEAD'],
                                check=True, capture_output=True, text=True, timeout=10).stdout.strip()
        dirty = bool(subprocess.run(['git', '-C', str(repository), 'status', '--porcelain',
                                     '--untracked-files=normal'], check=True, capture_output=True,
                                    text=True, timeout=10).stdout.strip())
    except (OSError, subprocess.SubprocessError) as error:
        raise StageError('Cannot record the staging checkout revision.') from error
    return {'checkout_commit_at_staging': commit, 'checkout_dirty_at_staging': dirty,
            'artifact_source_binding': 'unverified',
            'note': 'Checkout context only. An existing bundle is not proven to originate from this commit.'}


def publish_exclusive(source, destination):
    # Darwin stdio.h: RENAME_EXCL (0x4) atomically fails if destination exists.
    if sys.platform != 'darwin':
        raise StageError('Atomic app staging requires macOS.')
    rename = ctypes.CDLL(None, use_errno=True).renamex_np
    rename.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(os.fsencode(source), os.fsencode(destination), 0x4):
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number), str(destination))


def staging_root(repository, app):
    repository = repository.resolve(strict=True)
    protected = (Path('/Applications'), Path('/System/Applications'), Path.home() / 'Applications')
    if any(repository.is_relative_to(path.resolve()) for path in protected):
        raise StageError('Release staging cannot write inside Applications.')
    if (repository / 'build/staged-updates').is_relative_to(app):
        raise StageError('The input app cannot contain the staging directory.')
    current = repository
    for component in ('build', 'staged-updates'):
        current = current / component
        if current.is_symlink():
            raise StageError('The staging directory must not use symlinks.')
        current.mkdir(exist_ok=True)
        if not current.is_dir():
            raise StageError('The staging path is not a directory.')
    return current


def stage(app, repository=REPOSITORY, name=None):
    if name is None:
        name = (datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
                + '-' + uuid.uuid4().hex[:8])
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}', name):
        raise StageError('Stage names must be 1–100 letters, digits, dots, underscores or hyphens.')
    app = Path(app).resolve(strict=True)
    if not app.is_dir() or app.suffix != '.app':
        raise StageError('Supply an existing .app directory.')
    root = staging_root(Path(repository), app)
    destination = root / name
    if os.path.lexists(destination):
        raise StageError('That stage already exists; choose a new name.')
    before = inventory(app)
    metadata = bundle_metadata(app)
    signature = verify_signature(app)
    context = source_context(Path(repository))
    temporary = Path(tempfile.mkdtemp(prefix='.partial-', dir=root))
    try:
        copied = temporary / 'Model Harbor.app'
        shutil.copytree(app, copied, symlinks=True)
        after = inventory(copied)
        if before != after or before != inventory(app):
            raise StageError('The source changed or the staged copy does not match it.')
        if bundle_metadata(copied) != metadata or verify_signature(copied) != signature:
            raise StageError('The staged metadata or signature does not match the source.')
        manifest = {
            'schema_version': 1,
            'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'state': 'staged',
            'installed': False,
            'provider_verified': False,
            'live_handoff_verified': False,
            'source_context': context,
            'bundle': {'path': 'Model Harbor.app', 'metadata': metadata, 'signature': signature,
                       'inventory_sha256': hashlib.sha256(json.dumps(after, sort_keys=True,
                                                                   separators=(',', ':')).encode()).hexdigest(),
                       'entries': after},
        }
        manifest_path = temporary / 'manifest.json'
        with manifest_path.open('x', encoding='utf-8') as handle:
            json.dump(manifest, handle, indent=2, sort_keys=True)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        manifest_path.chmod(0o600)
        publish_exclusive(temporary, destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, required=True, help='Already built and signed Model Harbor .app')
    parser.add_argument('--name', help='Unique stage name (default: UTC timestamp plus random suffix)')
    args = parser.parse_args()
    try:
        destination = stage(args.app, name=args.name)
    except (StageError, OSError) as error:
        parser.exit(1, f'Staging failed: {error}\n')
    print(f'Staged: {destination}\nManifest: {destination / "manifest.json"}')
    print('Installed: no. Provider verified: no. Live handoff verified: no.')


if __name__ == '__main__':
    main()
