"""Credentials encrypted with Windows DPAPI, bound to the current Windows user."""
import ctypes
import json
import os
from ctypes import wintypes


class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def _convert(data, encrypt):
    if os.name != 'nt':
        raise ValueError('Kết nối Google hiện hỗ trợ Windows (mã hóa DPAPI).')
    buf = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    lib = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if encrypt:
        fn = lib.CryptProtectData
        fn.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p,
                       ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        args = (ctypes.byref(source), 'UpVideo Studio', None, None, None, 1, ctypes.byref(target))
    else:
        fn = lib.CryptUnprotectData
        fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                       ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        args = (ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    fn.restype = wintypes.BOOL
    if not fn(*args):
        raise ValueError('Không mở được thông tin đăng nhập bằng tài khoản Windows hiện tại. Hãy kết nối lại kênh.')
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(target.data)


def seal(data):
    return _convert(json.dumps(data).encode(), True)


def unseal(data):
    return json.loads(_convert(data, False))
