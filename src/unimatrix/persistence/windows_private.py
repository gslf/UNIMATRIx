"""Create credential files with a protected, current-user-only Windows DACL.

The ACL is supplied to CreateFileW so there is no inherited-access window before
the secret is written. This module is imported only on Windows; no shell is used.
"""

import ctypes as ct
import os
import uuid
from ctypes import wintypes as wt
from pathlib import Path


class SecurityAttributes(ct.Structure):
    _fields_ = [("length", wt.DWORD), ("descriptor", ct.c_void_p), ("inherit", wt.BOOL)]


class SidAndAttributes(ct.Structure):
    _fields_ = [("sid", ct.c_void_p), ("attributes", wt.DWORD)]


def api(library, name, arguments, result):
    function = getattr(library, name)
    function.argtypes = arguments
    function.restype = result
    return function


def create_temporary(directory):
    """Return an open CRT descriptor and path, like mkstemp, with an explicit DACL."""
    import msvcrt

    kernel = ct.WinDLL("kernel32", use_last_error=True)
    security = ct.WinDLL("advapi32", use_last_error=True)
    close = api(kernel, "CloseHandle", [wt.HANDLE], wt.BOOL)
    free = api(kernel, "LocalFree", [ct.c_void_p], ct.c_void_p)
    process = api(kernel, "GetCurrentProcess", [], wt.HANDLE)
    open_token = api(security, "OpenProcessToken",
                     [wt.HANDLE, wt.DWORD, ct.POINTER(wt.HANDLE)], wt.BOOL)
    token_info = api(security, "GetTokenInformation",
                     [wt.HANDLE, ct.c_int, ct.c_void_p, wt.DWORD, ct.POINTER(wt.DWORD)], wt.BOOL)
    sid_string = api(security, "ConvertSidToStringSidW",
                     [ct.c_void_p, ct.POINTER(wt.LPWSTR)], wt.BOOL)
    parse_descriptor = api(security, "ConvertStringSecurityDescriptorToSecurityDescriptorW",
                           [wt.LPCWSTR, wt.DWORD, ct.POINTER(ct.c_void_p), ct.POINTER(wt.DWORD)], wt.BOOL)
    create_file = api(kernel, "CreateFileW",
                      [wt.LPCWSTR, wt.DWORD, wt.DWORD, ct.POINTER(SecurityAttributes),
                       wt.DWORD, wt.DWORD, wt.HANDLE], wt.HANDLE)
    token, sid, descriptor = wt.HANDLE(), wt.LPWSTR(), ct.c_void_p()
    try:
        if not open_token(process(), 0x0008, ct.byref(token)):  # TOKEN_QUERY
            raise ct.WinError(ct.get_last_error())
        size = wt.DWORD()
        token_info(token, 1, None, 0, ct.byref(size))  # TokenUser
        if ct.get_last_error() != 122:  # ERROR_INSUFFICIENT_BUFFER
            raise ct.WinError(ct.get_last_error())
        buffer = ct.create_string_buffer(size.value)
        if not token_info(token, 1, buffer, size, ct.byref(size)):
            raise ct.WinError(ct.get_last_error())
        user = ct.cast(buffer, ct.POINTER(SidAndAttributes)).contents
        if not sid_string(user.sid, ct.byref(sid)):
            raise ct.WinError(ct.get_last_error())
        # Protected DACL; only the current user receives file access, without inheritance.
        if not parse_descriptor(f"D:P(A;;FA;;;{sid.value})", 1, ct.byref(descriptor), None):
            raise ct.WinError(ct.get_last_error())
        attributes = SecurityAttributes(ct.sizeof(SecurityAttributes), descriptor, False)
        temporary = Path(directory) / f".write-{uuid.uuid4().hex}.tmp"
        # GENERIC_READ | GENERIC_WRITE; no sharing; CREATE_NEW; FILE_ATTRIBUTE_NORMAL.
        handle = create_file(str(temporary), 0xC0000000, 0, ct.byref(attributes), 1, 0x80, None)
        if handle == ct.c_void_p(-1).value:
            raise ct.WinError(ct.get_last_error())
        try:
            fd = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
        except BaseException:
            close(handle)
            temporary.unlink()
            raise
        return fd, str(temporary)
    finally:
        if descriptor:
            free(descriptor)
        if sid:
            free(sid)
        if token:
            close(token)
