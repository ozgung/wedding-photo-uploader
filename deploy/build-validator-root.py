"""Build a small root filesystem using trusted installed code and library aliases."""
import os
import shutil
import tempfile
from pathlib import Path


def build():
    if os.geteuid() != 0:
        raise SystemExit('Run as root')
    base = Path('/var/lib/wedding-validator')
    base.mkdir(mode=0o755, parents=True, exist_ok=True)
    base.chmod(0o755)
    # This known, readable host file MUST be invisible inside the sandbox.
    sentinel = base / 'host-only-sentinel'
    sentinel.write_text('Generated isolation test marker, not guest data.\n')
    sentinel.chmod(0o644)
    root = Path(tempfile.mkdtemp(prefix='root.new-', dir=base))
    root.chmod(0o755)
    try:
        for directory in ('app', 'usr', 'opt/wedding/.venv', 'etc/alternatives', 'proc', 'dev', 'tmp', 'run'):
            (root / directory).mkdir(parents=True, exist_ok=True)
        for name in ('bin', 'sbin', 'lib', 'lib64'):
            (root / name).symlink_to('usr/' + name)
        (root / 'app/__init__.py').write_text('')
        for name in ('validator_service.py', 'validation_protocol.py', 'validate.py'):
            shutil.copyfile(Path('/opt/wedding/app') / name, root / 'app' / name)
        # Debian BLAS/LAPACK symlinks pass through /etc/alternatives. Expose only
        # shared-library aliases whose resolved targets live in the mounted /usr.
        for alias in Path('/etc/alternatives').glob('lib*.so*'):
            if not alias.is_symlink():
                continue
            target = alias.resolve(strict=True)
            if not target.is_relative_to('/usr') or not target.is_file():
                raise RuntimeError('Unexpected shared-library alias: ' + alias.name)
            (root / 'etc/alternatives' / alias.name).symlink_to(target)
        # Some dependencies live outside the loader's default directories.
        shutil.copyfile('/etc/ld.so.cache', root / 'etc/ld.so.cache')
        for path in root.rglob('*'):
            if not path.is_symlink():
                path.chmod(0o755 if path.is_dir() else 0o644)
        destination = base / 'root'
        if destination.exists():
            previous = base / ('root.previous-' + root.name.removeprefix('root.new-'))
            destination.rename(previous)
        root.rename(destination)
    finally:
        if root.exists():
            shutil.rmtree(root)


if __name__ == '__main__':
    build()
