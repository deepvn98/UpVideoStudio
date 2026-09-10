"""Start with: python run.py. No third-party runtime dependencies."""
import argparse
import json
import os
import sys
import threading
import traceback
import webbrowser
import urllib.request
from pathlib import Path
from studio.address import ui_url


def console(message):
    """Windowed PyInstaller builds have no stdout; source runs still get diagnostics."""
    stream = getattr(sys, 'stdout', None)
    if stream is not None:
        print(message, file=stream, flush=True)


def message_box(message, title='UpVideo Studio'):
    if os.name == 'nt':
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)
            return
        except (AttributeError, OSError):
            pass
    console(f'{title}: {message}')


def report_startup_error(exc):
    """Never fail silently in the no-console release build."""
    data = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'UpVideoStudio'
    try:
        data.mkdir(parents=True, exist_ok=True)
        log = data / 'startup-error.log'
        log.write_text(traceback.format_exc(), encoding='utf-8')
        detail = f'\n\nChi tiết đã được lưu tại:\n{log}'
    except OSError:
        detail = ''
    message_box('Không thể khởi động chương trình.' + detail + '\n\nLỗi: ' + str(exc))


def existing_url(data):
    """Validate a saved local address before reopening an existing instance."""
    try:
        port = json.loads((data / 'instance.json').read_text(encoding='utf-8'))['port']
        if type(port) is not int or not 1 <= port <= 65535:
            return None
        url = f'http://127.0.0.1:{port}'
        with urllib.request.urlopen(url + '/health', timeout=2) as response:
            info = json.load(response)
            if info.get('app') == 'UpVideoStudio':
                return url
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def main():
    parser = argparse.ArgumentParser(description='UpVideo Studio')
    parser.add_argument('--port', type=int, default=None)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--data-dir', type=Path)
    args = parser.parse_args()
    from studio.server import App, make_server
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    data = args.data_dir or Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'UpVideoStudio'
    data.mkdir(parents=True, exist_ok=True)
    # Hold a per-data-directory process lock; a second server must not recover active jobs.
    lock = (data / 'instance.lock').open('a+b')
    lock.seek(0)
    if os.fstat(lock.fileno()).st_size == 0:
        lock.write(b'0')
        lock.flush()
    lock.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        url = existing_url(data)
        if url:
            console(f'UpVideo Studio is already running: {url}')
            if not args.no_browser:
                webbrowser.open(url)
            lock.close()
            return 0
        message_box('Chương trình đang khởi động hoặc đã chạy nền. Hãy dùng tab trình duyệt hiện có hoặc thử lại sau vài giây.')
        lock.close()
        return 1
    app = App(data, root / 'web')
    try:
        server = make_server(app, 8765 if args.port is None else args.port)
    except OSError:
        if args.port is not None:
            app.close()
            lock.close()
            raise
        server = make_server(app, 0)
    (data / 'instance.json').write_text(json.dumps({'port': server.server_port}), encoding='utf-8')
    url = ui_url(server.server_port)
    console(f'UpVideo Studio: {url}')
    console('Press Ctrl+C to stop. Upload sessions are saved automatically.')
    console('Keep this window open. Closing it disconnects the browser interface.')
    if not args.no_browser:
        threading.Timer(.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=.25)
    except KeyboardInterrupt:
        console('Saving upload progress…')
    finally:
        server.server_close()
        app.close()
        lock.close()
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:
        report_startup_error(error)
        sys.exit(1)
