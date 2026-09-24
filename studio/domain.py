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
    """List source files for imports and linked-folder updates without following directory links."""
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


def scan_folder(root: str, hash_cache=None, checkpoint=None, channel_name='') -> tuple[list[dict], list[str]]:
    folder = Path(root).expanduser().resolve()
    directories = {}
    leaf_directories = set()
    for current, subdirs, _ in os.walk(folder, followlinks=False):
        subdirs[:] = [name for name in subdirs
                      if not Path(current, name).is_symlink()
                      and not (hasattr(Path(current, name), 'is_junction') and Path(current, name).is_junction())]
        directory = Path(current)
        directories.setdefault(directory, {})
        if not subdirs:
            leaf_directories.add(directory)
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
    for directory, files in directories.items():
        if counts.get(directory, 0):
            continue
        has_content = any(Path(name).suffix.lower() in ('.txt', '.docx') for name in files)
        has_thumbnail = any(Path(name).suffix.lower() in ('.jpg', '.jpeg', '.png') for name in files)
        if has_content or has_thumbnail or (directory in leaf_directories and not files):
            issues = ['Thiếu file video MP4']
            if not has_content:
                issues.append('Thiếu file TXT/DOCX nội dung')
            if not has_thumbnail:
                issues.append('Thiếu ảnh thumbnail JPG/PNG')
            metadata_files = [p for p in files.values() if p.suffix.lower() in ('.txt', '.docx')]
            metadata = next((p for p in metadata_files if p.stem.casefold() == 'info'), None)
            if metadata is None and len(metadata_files) == 1:
                metadata = metadata_files[0]
            if metadata is None and has_content:
                issues.append('Không xác định được một file TXT/DOCX nội dung duy nhất')
            try:
                info = parse_text(read_content(metadata), directory.name) if metadata else parse_text('', directory.name)
            except (OSError, ValueError, KeyError, zipfile.BadZipFile, ET.ParseError) as exc:
                issues.append(f'{metadata}: Không đọc được file nội dung: {exc}')
                info = parse_text('', directory.name)
            images = [p for p in files.values() if p.suffix.lower() in ('.jpg', '.jpeg', '.png')]
            thumbnail = next((p for p in images if p.stem.casefold() in ('thumb', 'thumbnail')), None)
            if thumbnail is None and len(images) == 1:
                thumbnail = images[0]
            if thumbnail is None and has_thumbnail:
                issues.append('Không xác định được một ảnh thumbnail duy nhất')
            prefix = f'Kênh {channel_name} · ' if channel_name else ''
            expected = directory / f'{directory.name}.mp4'
            digest = hashlib.sha256(('missing-video:' + str(directory)).encode('utf-8')).hexdigest()
            items.append(dict(info, path=str(expected), content_source=str(metadata) if metadata else '',
                              fingerprint=digest, size=0, thumbnail=str(thumbnail) if thumbnail else '',
                              category='22', language='vi', privacy='private', publish_at='',
                              made_for_kids=None, synthetic=None, playlists=[],
                              asset_errors=[prefix + str(directory) + ': ' + issue for issue in issues]))
    for path in videos:
        if checkpoint:
            checkpoint()
        asset_errors = []
        prefix = f'Kênh {channel_name} · ' if channel_name else ''
        if path.suffix.lower() != '.mp4':
            asset_errors.append(f'{prefix}{path.parent} · {path.name}: Video phải có định dạng MP4')
        files = directories[path.parent]
        meta = next((files.get((path.stem + ext).casefold()) for ext in ('.txt', '.docx')
                     if files.get((path.stem + ext).casefold())), None)
        if meta is None and counts[path.parent] == 1:
            meta = files.get('info.txt') or files.get('info.docx')
            if meta is None:
                candidates = [p for p in files.values() if p.suffix.lower() in ('.txt', '.docx')]
                if len(candidates) == 1:
                    meta = candidates[0]
        thumb = next((files.get((path.stem + ext).casefold()) for ext in ('.jpg', '.png', '.jpeg')
                      if files.get((path.stem + ext).casefold())), None)
        if thumb is None and counts[path.parent] == 1:
            images = [p for p in files.values() if p.suffix.lower() in ('.jpg', '.png', '.jpeg')]
            if len(images) == 1:
                thumb = images[0]
        if meta is None:
            asset_errors.append(f'{prefix}{path.parent}: Thiếu file TXT/DOCX nội dung cho {path.name}')
        if thumb is None:
            asset_errors.append(f'{prefix}{path.parent}: Thiếu ảnh thumbnail JPG/PNG cho {path.name}')
        try:
            info = parse_text(read_content(meta), path.stem) if meta else parse_text('', path.stem)
        except (OSError, ValueError, KeyError, zipfile.BadZipFile, ET.ParseError) as exc:
            asset_errors.append(f'{prefix}{path.parent} · {meta}: Không đọc được file nội dung: {exc}')
            info = parse_text('', path.stem)
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
        row = dict(info, path=str(path), content_source=str(meta) if meta else '',
                          fingerprint=digest, size=stat.st_size,
                          thumbnail=str(thumb) if thumb else '',
                          category='22', language='vi', privacy='private', publish_at='',
                          made_for_kids=None, synthetic=None, playlists=[])
        # Avoid adding a generic size error when the specific root cause is
        # already known (for example, a zero-byte or non-MP4 source file).
        if stat.st_size <= 0:
            asset_errors.append(f'{prefix}{path}: File video đang trống')
        elif path.suffix.lower() == '.mp4':
            try:
                validate_source(dict(row, made_for_kids=False, synthetic=False), check_time=False)
            except ValueError as exc:
                asset_errors.append(f'{prefix}{path.parent} · {exc}')
        row['asset_errors'] = list(dict.fromkeys(asset_errors))
        items.append(row)
    return items, warnings


def scan_channel_variants(root: str, channel_count: int, *, hash_files=False, channel_names=None) -> list[dict]:
    """Read one shared video plus one metadata/thumbnail folder per selected channel."""
    folder = Path(root).expanduser().resolve()
    if not folder.is_dir():
        raise ValueError('Thư mục gốc không tồn tại hoặc không truy cập được.')
    if type(channel_count) is not int or channel_count < 1:
        raise ValueError('Chọn ít nhất một kênh để nhập video.')
    channel_names = channel_names or []

    def channels_text():
        return ' · '.join(channel_names) if channel_names else 'các kênh đã chọn'

    def natural_key(path):
        parts = tuple((1, int(part)) if part.isdigit() else (0, part.casefold())
                      for part in re.split(r'(\d+)', path.name))
        return parts, path.name.casefold(), path.name

    def safe_children(path):
        try:
            children = list(path.iterdir())
        except OSError as exc:
            raise ValueError('Không đọc được thư mục: ' + str(path)) from exc
        return [child for child in children if not child.is_symlink()
                and not (hasattr(child, 'is_junction') and child.is_junction())]

    video_folders = sorted((item for item in safe_children(folder) if item.is_dir()), key=natural_key)
    if not video_folders:
        raise ValueError('Thư mục gốc cần chứa các thư mục video con.')
    result = []
    for video_folder in video_folders:
        children = safe_children(video_folder)
        videos = sorted((item for item in children if item.is_file()
                         and item.suffix.lower() in VIDEO_EXTS), key=natural_key)
        if len(videos) > 1:
            raise ValueError(f'{video_folder} · kênh {channels_text()}: chỉ được có một file video MP4.')
        video = videos[0] if videos else video_folder / f'{video_folder.name}.mp4'
        video_issues = []
        if not videos:
            video_issues.append(f'{video_folder}: Thiếu file video MP4')
        elif video.suffix.lower() != '.mp4':
            video_issues.append(f'{video_folder} · {video.name}: Video phải có định dạng MP4')
        variant_folders = sorted((item for item in children if item.is_dir()), key=natural_key)
        if video.is_file():
            stat = video.stat()
            size = stat.st_size
            if size <= 0:
                video_issues.append(f'{video_folder} · {video.name}: File video đang trống')
            digest = fingerprint(video) if hash_files else ''
            after = video.stat()
            if (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                video_issues.append(f'{video_folder} · {video.name}: File đang thay đổi; chờ sao chép hoàn tất rồi thử lại')
        else:
            size = 0
            digest = hashlib.sha256(('missing-video:' + str(video_folder)).encode('utf-8')).hexdigest()
        variants = []
        assigned_folders = variant_folders[:channel_count]
        while len(assigned_folders) < channel_count:
            missing_index = len(assigned_folders)
            assigned_folders.append(video_folder / f'__missing_channel_{missing_index + 1}__')
        for variant_index, variant_folder in enumerate(assigned_folders):
            channel_label = channel_names[variant_index] if variant_index < len(channel_names) else f'kênh thứ {variant_index + 1}'
            files = [item for item in safe_children(variant_folder) if item.is_file()] if variant_folder.is_dir() else []
            texts = [item for item in files if item.suffix.lower() in ('.txt', '.docx')]
            images = [item for item in files if item.suffix.lower() in ('.jpg', '.jpeg', '.png')]
            meta = next((item for item in texts if item.stem.casefold() == 'info'), None)
            if meta is None and len(texts) == 1:
                meta = texts[0]
            thumb = next((item for item in images if item.stem.casefold() in ('thumb', 'thumbnail')), None)
            if thumb is None and len(images) == 1:
                thumb = images[0]
            asset_errors = [f'Kênh {channel_label} · {issue}' for issue in video_issues]
            if meta is None:
                asset_errors.append(f'Kênh {channel_label} · {video_folder} · {variant_folder}: Thiếu file TXT/DOCX nội dung')
            if thumb is None:
                asset_errors.append(f'Kênh {channel_label} · {video_folder} · {variant_folder}: Thiếu ảnh thumbnail JPG/PNG')
            try:
                info = parse_text(read_content(meta), video_folder.name) if meta else parse_text('', video_folder.name)
            except (OSError, ValueError, KeyError, zipfile.BadZipFile, ET.ParseError) as exc:
                asset_errors.append(f'Kênh {channel_label} · {video_folder} · {variant_folder} · {meta}: Không đọc được file nội dung: {exc}')
                info = parse_text('', video_folder.name)
            if not info['title']:
                asset_errors.append(f'Kênh {channel_label} · {video_folder} · {variant_folder} · {meta or variant_folder}: Thiếu tiêu đề trong file nội dung')
                info['title'] = video_folder.name
            row = dict(info, path=str(video), content_source=str(meta) if meta else '', fingerprint=digest, size=size,
                       thumbnail=str(thumb) if thumb else '', category='22', language='vi', privacy='private',
                       publish_at='', made_for_kids=None, synthetic=None, playlists=[], asset_errors=asset_errors)
            if size > 0 and video.suffix.lower() == '.mp4' and not video_issues:
                try:
                    validate_source(dict(row, made_for_kids=False, synthetic=False), check_time=False)
                except ValueError as exc:
                    row['asset_errors'].append(f'Kênh {channel_label} · {video_folder} · {variant_folder} · {exc}')
            row['asset_errors'] = list(dict.fromkeys(row['asset_errors']))
            variants.append({'folder': str(variant_folder), 'metadata': str(meta),
                             'thumbnail': str(thumb) if thumb else '', 'row': row})
        result.append({'folder': str(video_folder), 'video': str(video),
                       'fingerprint': digest, 'size': size, 'variants': variants})
    return result


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
    if thumb:
        thumb_path = Path(thumb)
        try:
            valid_thumb = (thumb_path.is_file() and thumb_path.suffix.lower() in ('.jpg', '.jpeg', '.png')
                           and thumb_path.stat().st_size <= 2 * 1024 * 1024)
        except OSError as exc:
            raise ValueError(f'{thumb_path}: Không đọc được file thumbnail: {exc}') from None
        if not valid_thumb:
            raise ValueError(f'{thumb_path}: Thumbnail phải là JPG/PNG tồn tại và không quá 2 MB.')


def validate_source(item: dict, *, check_time=True) -> None:
    """Validate a job and identify the source file for text/thumbnail errors."""
    try:
        validate(item, check_time=check_time)
    except ValueError as exc:
        message = str(exc)
        if message.startswith('Thumbnail') and item.get('thumbnail'):
            raise ValueError(f'{item["thumbnail"]}: {message}') from None
        if message.startswith(('Tiêu đề', 'Mô tả', 'Danh sách tag', 'Tổng tag')):
            source = item.get('content_source')
            if not source and item.get('thumbnail'):
                thumb = Path(item['thumbnail'])
                candidates = [thumb.parent / ('info' + suffix) for suffix in ('.txt', '.docx')]
                source = next((str(path) for path in candidates if path.is_file()), '')
                if not source:
                    try:
                        texts = [path for path in thumb.parent.iterdir()
                                 if path.is_file() and path.suffix.lower() in ('.txt', '.docx')]
                        if len(texts) == 1:
                            source = str(texts[0])
                    except OSError:
                        pass
            if not source and item.get('path'):
                video = Path(item['path'])
                candidates = [video.with_suffix(suffix) for suffix in ('.txt', '.docx')]
                source = next((str(path) for path in candidates if path.is_file()), '')
                if not source:
                    try:
                        videos = [path for path in video.parent.iterdir()
                                  if path.is_file() and path.suffix.lower() in VIDEO_EXTS]
                        if len(videos) == 1:
                            candidates = [video.parent / ('info' + suffix) for suffix in ('.txt', '.docx')]
                            source = next((str(path) for path in candidates if path.is_file()), '')
                    except OSError:
                        pass
                if not source:
                    source = str(video)
            if source:
                raise ValueError(f'{source}: {message}') from None
        raise


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
