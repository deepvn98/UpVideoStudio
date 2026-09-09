def folder_windows():
    import ctypes
    from ctypes import wintypes

    class BrowseInfo(ctypes.Structure):
        _fields_ = [('owner', wintypes.HWND), ('root', ctypes.c_void_p),
                    ('display', wintypes.LPWSTR), ('title', wintypes.LPCWSTR),
                    ('flags', wintypes.UINT), ('callback', ctypes.c_void_p),
                    ('param', wintypes.LPARAM), ('image', ctypes.c_int)]

    shell = ctypes.WinDLL('shell32', use_last_error=True)
    ole = ctypes.WinDLL('ole32', use_last_error=True)
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole.CoInitializeEx.restype = ctypes.c_long
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    shell.SHBrowseForFolderW.argtypes = [ctypes.POINTER(BrowseInfo)]
    shell.SHBrowseForFolderW.restype = ctypes.c_void_p
    shell.SHGetPathFromIDListEx.argtypes = [ctypes.c_void_p, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    shell.SHGetPathFromIDListEx.restype = wintypes.BOOL
    result = ole.CoInitializeEx(None, 2)
    display = ctypes.create_unicode_buffer(32768)
    info = BrowseInfo(None, None, ctypes.cast(display, wintypes.LPWSTR),
                      'Chọn thư mục video — UpVideo Studio', 0x41, None, 0, 0)
    pointer = None
    try:
        pointer = shell.SHBrowseForFolderW(ctypes.byref(info))
        if not pointer:
            return ''
        path = ctypes.create_unicode_buffer(32768)
        if not shell.SHGetPathFromIDListEx(pointer, path, len(path), 0):
            raise ValueError('Thư mục được chọn không có đường dẫn cục bộ.')
        return path.value
    finally:
        if pointer:
            ole.CoTaskMemFree(pointer)
        if result >= 0:
            ole.CoUninitialize()


def main():
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    if sys.platform != 'win32':
        raise ValueError('Hãy nhập đường dẫn thư mục trực tiếp trên hệ điều hành này.')
    print(folder_windows())


if __name__ == '__main__':
    main()
