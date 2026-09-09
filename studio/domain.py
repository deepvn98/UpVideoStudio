from __future__ import annotations

import hashlib
import os
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from .catalog import validate_choices

VIDEO_EXTS = {'.mp4', '.mov', '.mkv', '.avi', '.webm', '.m4v'}
HEADER = re.compile(r'^\s*(Title|Tiêu đề|Tieu de|Video Description|Description|Desc|Giới thiệu|Mô tả|Mo ta|Tags?|Keywords?|Thẻ tag video|Thẻ|The)\s*:\s*(.*)$', re.I)


def parse_text(text: str, fallback: str) -> dict:
    parts = {'title': [], 'description': [], 'tags': []}
    mode = None
    for line in text.lstrip('\ufeff').splitlines():
        match = HEADER.match(line)
        if match:
            label, value = match.groups()
            label = label.casefold()
            mode = ('title' if label in ('title', 'tiêu đề', 'tieu de') else
                    'tags' if label in ('tag', 'tags', 'keyword', 'keywords', 'thẻ tag video', 'thẻ', 'the') else 'description')
            if value:
                parts[mode].append(value)
        elif mode:
            parts[mode].append(line.rstrip())
    return {'title': ' '.join(x.strip() for x in parts['title'] if x.strip()) or fallback,
            'description': '\n'.join(parts['description']).strip(),
            'tags': list(dict.fromkeys(x.strip() for x in re.split(r'[,\n]', '\n'.join(parts['tags'])) if x.strip()))}


def read_content(path: Path) -> str:
    if path.suffix.lower() == '.docx':
        with zipfile.ZipFile(path) as archive:
            info = archive.getinfo('word/document.xml')
            if info.file_size > 8_000_000:
                raise ValueError('DOCX quá lớn để đọc nội dung.')
            root = ET.fromstring(archive.read(info))
        ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        return '\n'.join(''.join(p.itertext()) for p in root.findall('.//w:p', ns))
    if path.stat().st_size > 2_000_000:
        raise ValueError('File nội dung vượt quá 2 MB.')
    return path.read_text(encoding='utf-8-sig')


def fingerprint(path: Path, checkpoint=None) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            if checkpoint:
                checkpoint()
            digest.update(chunk)
    return digest.hexdigest()


def folder_files(folder, checkpoint=None):
    """Strict recursive listing shared by imports and watching; never follow directory links."""
    if not Path(folder).is_dir():
        raise ValueError('Thư mục không tồn tại hoặc đang ngoại tuyến; giữ nguyên bản nháp.')
    def fail(error):
        raise error
    for directory, dirs, files in os.walk(folder, onerror=fail, followlinks=False):
        if checkpoint:
            checkpoint()
        dirs[:] = [d for d in dirs if not Path(directory, d).is_symlink() and not Path(directory, d).is_junction()]
        for name in files:
            path = Path(directory, name)
            if not path.is_symlink():
                yield path


def scan_folder(root: str, hash_cache=None, checkpoint=None) -> tuple[list[dict], list[str]]:
    folder = Path(root).expanduser().resolve()
    directories = {}
    videos = []
    for path in folder_files(folder, checkpoint):
        directories.setdefault(path.parent, {})[path.name.casefold()] = path
        if path.suffix.lower() in VIDEO_EXTS:
            videos.append(path)
    videos.sort(key=lambda p: str(p).casefold())
    items, warnings = [], []
    counts = {}
    for p in videos:
        counts[p.parent] = counts.get(p.parent, 0) + 1
    for path in videos:
        if checkpoint:
            checkpoint()
        files = directories[path.parent]
        meta = next((files.get((path.stem + ext).casefold()) for ext in ('.txt', '.docx')
                     if files.get((path.stem + ext).casefold())), None)
        if meta is None and counts[path.parent] == 1:
            meta = files.get('info.txt') or files.get('info.docx')
            if meta is None:
                candidates = [p for p in files.values() if p.suffix.lower() in ('.txt', '.docx')]
                if len(candidates) == 1:
                    meta = candidates[0]
        info = parse_text(read_content(meta), path.stem) if meta else parse_text('', path.stem)
        thumb = next((files.get((path.stem + ext).casefold()) for ext in ('.jpg', '.png', '.jpeg')
                      if files.get((path.stem + ext).casefold())), None)
        if thumb is None and counts[path.parent] == 1:
            images = [p for p in files.values() if p.suffix.lower() in ('.jpg', '.png', '.jpeg')]
            if len(images) == 1:
                thumb = images[0]
        if not meta:
            warnings.append(f'{path.name}: chưa có file nội dung riêng; dùng tên video làm tiêu đề.')
        stat = path.stat()
        key = (str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        digest = hash_cache.get(key) if hash_cache is not None else None
        if digest is None:
            digest = fingerprint(path, checkpoint)
            after = path.stat()
            if (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError('File đang thay đổi; chờ sao chép hoàn tất.')
            if hash_cache is not None:
                hash_cache[key] = digest
        items.append(dict(info, path=str(path), fingerprint=digest, size=stat.st_size,
                          thumbnail=str(thumb) if thumb else '',
                          category='22', language='vi', privacy='private', publish_at='',
                          made_for_kids=None, synthetic=None, playlists=[]))
    return items, warnings


def utc_now():
    return datetime.now(timezone.utc)


def parse_date(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('Lịch đăng phải có múi giờ.')
    return dt.astimezone(timezone.utc)


def validate(item: dict, *, check_time=True) -> None:
    if not isinstance(item.get('size'), int) or item['size'] <= 0:
        raise ValueError('File video trống hoặc kích thước không hợp lệ.')
    title = item.get('title', '').strip()
    if not title or len(title) > 100 or any(c in title for c in '<>'):
        raise ValueError('Tiêu đề phải có 1–100 ký tự và không chứa < >.')
    desc = item.get('description', '')
    if len(desc.encode('utf-8')) > 5000 or any(c in desc for c in '<>'):
        raise ValueError('Mô tả tối đa 5.000 byte UTF-8 và không chứa < >.')
    tags = item.get('tags', [])
    if not isinstance(tags, list) or any(not isinstance(t, str) or not t.strip() for t in tags):
        raise ValueError('Danh sách tag không hợp lệ.')
    if sum(len(t) + (2 if ' ' in t else 0) for t in tags) + max(0, len(tags)-1) > 500:
        raise ValueError('Tổng tag vượt giới hạn 500 ký tự (gồm dấu phân cách/dấu ngoặc).')
    if type(item.get('made_for_kids')) is not bool:
        raise ValueError('Hãy xác nhận video có dành cho trẻ em hay không.')
    if type(item.get('synthetic')) is not bool:
        raise ValueError('Hãy xác nhận nội dung chỉnh sửa/tổng hợp cần khai báo.')
    if item.get('privacy') not in ('private', 'unlisted', 'public'):
        raise ValueError('Chế độ hiển thị không hợp lệ.')
    validate_choices({'category': item.get('category'), 'language': item.get('language')})
    playlists = item.get('playlists', [])
    if not isinstance(playlists, list) or any(not isinstance(p, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', p) for p in playlists):
        raise ValueError('Playlist không hợp lệ.')
    when = item.get('publish_at')
    if when:
        if item['privacy'] != 'private':
            raise ValueError('Video có lịch đăng phải được tải lên ở chế độ riêng tư.')
        if check_time and parse_date(when) < utc_now() + timedelta(minutes=15):
            raise ValueError('Chọn lịch đăng cách hiện tại ít nhất 15 phút.')
    thumb = item.get('thumbnail')
    if thumb and (not Path(thumb).is_file() or Path(thumb).suffix.lower() not in ('.jpg', '.jpeg', '.png')
                  or Path(thumb).stat().st_size > 2 * 1024 * 1024):
        raise ValueError('Thumbnail phải là JPG/PNG tồn tại và không quá 2 MB.')


def schedule(count: int, start: str, slots: str, interval: int, offset: int, occupied: list[str]) -> list[str]:
    if count < 1:
        raise ValueError('Chọn ít nhất một video để xếp lịch.')
    if not 1 <= interval <= 365:
        raise ValueError('Khoảng cách ngày phải từ 1 đến 365; 1 nghĩa là mỗi ngày.')
    if not -720 <= offset <= 840:
        raise ValueError('Múi giờ không hợp lệ.')
    zone = timezone(timedelta(minutes=offset))
    try:
        times = sorted(set(datetime.strptime(s.strip(), '%H:%M').time() for s in slots.split(',') if s.strip()))
        base = datetime.strptime(start, '%Y-%m-%d').date()
    except ValueError:
        raise ValueError('Nhập ngày hợp lệ và các khung giờ HH:MM, phân cách bằng dấu phẩy.') from None
    if not times:
        raise ValueError('Nhập ít nhất một khung giờ HH:MM.')
    floor = utc_now() + timedelta(minutes=15)
    if occupied:
        floor = max(floor, max(parse_date(s) for s in occupied))
    output = []
    for n in range(3660 // interval + 1):
        day = base + timedelta(days=n * interval)
        for slot in times:
            dt = datetime.combine(day, slot, zone).astimezone(timezone.utc)
            if dt > floor:
                output.append(dt.isoformat())
                if len(output) == count:
                    return output
    raise ValueError('Không tìm được lịch phù hợp trong 10 năm.')
