"""Move selected draft folders outside watched roots without overwriting files."""
import os
import stat
from pathlib import Path

from .domain import VIDEO_EXTS


def inside(path, root):
    return path == root or root in path.parents


def check_plain_path(path):
    for part in (path, *path.parents):
        if part.exists() and (part.is_symlink() or getattr(part.lstat(), 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError('Không di chuyển thư mục qua liên kết/junction: ' + str(part))


BUSY_STATES = {'queued', 'uploading', 'finishing'}


def plan_removal(store, ids, allow_busy=False):
    jobs = [store.job(jid) for jid in dict.fromkeys(ids)]
    if not jobs:
        raise ValueError('Chọn ít nhất một video để xóa.')
    if not allow_busy and any(j['state'] in BUSY_STATES for j in jobs):
        raise ValueError('Video vẫn đang chạy. Hãy chờ chương trình dừng tác vụ trước khi xóa.')
    watches = store.watches()
    all_jobs = store.jobs()
    groups = {}
    missing = []
    for job in jobs:
        path = Path(job['path']).absolute()
        check_plain_path(path)
        path = path.resolve()
        if not path.is_file():
            missing.append(job)
            continue
        roots = [Path(w['path']).resolve() for w in watches if w['account'] == job['account'] and inside(path, Path(w['path']).resolve())]
        saved = job.get('watch_root') or job.get('import_root')
        if saved and inside(path, Path(saved).resolve()):
            roots.append(Path(saved).resolve())
        if not roots:
            roots = list({Path(value).resolve() for other in all_jobs if other['account'] == job['account']
                          for value in (other.get('watch_root'), other.get('import_root'))
                          if value and inside(path, Path(value).resolve())})
            if len(roots) > 1:
                roots = [max(roots, key=lambda p: len(p.parts))]
        if not roots:
            # Legacy jobs can predate the current watch or live outside its root.
            # The confirmation always shows the actual source and archive destination.
            linked = [Path(w['path']).resolve() for w in watches if w['account'] == job['account']]
            roots = linked or [path.parent.parent]
        root = max(roots, key=lambda p: len(p.parts))
        source = path.parent
        if inside(root, source) or any(inside(Path(w['path']).resolve(), source) for w in watches):
            raise ValueError('Video nằm trực tiếp trong thư mục gốc. Hãy đặt mỗi video cùng nội dung và ảnh vào một thư mục con trước khi xóa.')
        if not source.is_dir():
            raise ValueError('Không tìm thấy thư mục video để chuyển: ' + str(source))
        archive = root.with_name(root.name + '-remove video')
        check_plain_path(archive)
        if any(inside(archive, Path(w['path']).resolve()) or inside(Path(w['path']).resolve(), archive) for w in watches):
            raise ValueError('Thư mục lưu video đã xóa nằm trong một thư mục khác đang theo dõi. Dừng liên kết đó trước khi chuyển.')
        groups[source] = archive
    sources = list(groups)
    if any(a != b and inside(a, b) for a in sources for b in sources):
        raise ValueError('Các thư mục được chọn lồng nhau. Hãy xử lý riêng từng thư mục.')
    selected_paths = {Path(j['path']).resolve() for j in jobs}
    selected_ids = {j['id'] for j in jobs}
    plans = [{'source': str(Path(job['path']).absolute().parent), 'destination': '', 'missing': True}
             for job in missing]
    reserved = set()
    for source, archive in groups.items():
        check_plain_path(source)
        def fail(error):
            raise error
        for directory, dirs, files in os.walk(source, onerror=fail, followlinks=False):
            for name in dirs + files:
                entry = Path(directory, name)
                check_plain_path(entry)
                if entry.suffix.lower() in VIDEO_EXTS and entry.is_file() and entry.resolve() not in selected_paths:
                    raise ValueError('Thư mục có video chưa chọn. Chọn tất cả video trong thư mục: ' + str(source))
        if any(j['id'] not in selected_ids and (inside(Path(j['path']).resolve(), source)
                   or (j.get('thumbnail') and inside(Path(j['thumbnail']).resolve(), source))) for j in all_jobs):
            raise ValueError('Thư mục còn được một công việc khác sử dụng. Không chuyển để tránh ảnh hưởng video đó: ' + str(source))
        destination = archive/source.name
        suffix = 2
        while destination.exists() or destination in reserved:
            destination = archive/f'{source.name} ({suffix})'
            suffix += 1
        if not inside(destination, archive) or inside(archive, source):
            raise ValueError('Đường dẫn đích không hợp lệ.')
        reserved.add(destination)
        plans.append({'source': str(source), 'destination': str(destination)})
    return jobs, plans


def remove_jobs(store, ids):
    jobs, plans = plan_removal(store, ids)
    moved = []
    try:
        for plan in plans:
            if plan.get('missing'):
                continue
            source, destination = Path(plan['source']), Path(plan['destination'])
            check_plain_path(source)
            check_plain_path(destination)
            if destination.exists():
                raise ValueError('Thư mục đích vừa xuất hiện; hãy thử lại.')
            destination.parent.mkdir(exist_ok=True)
            source.rename(destination)
            moved.append((source, destination))
        store.remove([j['id'] for j in jobs])
    except Exception as exc:
        failures = []
        for source, destination in reversed(moved):
            try:
                if source.exists():
                    raise OSError('Source already exists')
                destination.rename(source)
            except OSError:
                failures.append(str(destination))
        if failures:
            raise ValueError('Không hoàn tất thao tác; file đã chuyển được giữ tại: ' + '; '.join(failures)) from exc
        raise ValueError('Chưa xóa video; không chuyển được thư mục. Đóng file đang mở và kiểm tra quyền truy cập. ' + str(exc)) from exc
    return {'ok': True, 'moved': plans, 'count': len(jobs)}
