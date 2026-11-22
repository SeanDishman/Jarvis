import hashlib
import os
from pathlib import Path
import shutil
import stat
import uuid


def revision(data):
    return hashlib.sha256(data).hexdigest()


def local_path(path, roots, *, existing=False):
    candidate = Path(path).absolute()
    if str(candidate).startswith(('\\\\', '//')):
        raise ValueError('Choose a local path, not a network share')
    for part in (candidate, *candidate.parents):
        if part.exists() or part.is_symlink():
            info = part.lstat()
            if part.is_symlink() or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError('Linked folders and files are not supported')
    resolved = candidate.resolve(strict=existing)
    if not any(resolved.is_relative_to(Path(root).resolve()) for root in roots):
        raise ValueError('That path is outside the folders you selected')
    if any(part.startswith('.') for part in resolved.relative_to(next(Path(r).resolve() for r in roots if resolved.is_relative_to(Path(r).resolve()))).parts):
        raise ValueError('Hidden files and configuration folders are not supported')
    return resolved


def write_file(path, data, *, expected=None, backup_root=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if expected is None:
        with path.open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    else:
        original = path.read_bytes()
        if revision(original) != expected:
            raise ValueError('The file changed since it was read. Read it again before editing.')
        if backup_root:
            backup = Path(backup_root) / (uuid.uuid4().hex + path.suffix)
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup)
        temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            with temporary.open('xb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if revision(path.read_bytes()) != expected:
                raise ValueError('The file changed while the edit was prepared. Read it again.')
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return {'path': str(path), 'revision': revision(data), 'backup': str(backup) if backup else None}
