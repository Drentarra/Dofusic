from __future__ import annotations

import ctypes
import os
from ctypes import wintypes


_TH32CS_SNAPPROCESS = 0x00000002
_MAX_PATH = 260


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ('dwSize', wintypes.DWORD),
        ('cntUsage', wintypes.DWORD),
        ('th32ProcessID', wintypes.DWORD),
        ('th32DefaultHeapID', ctypes.c_size_t),
        ('th32ModuleID', wintypes.DWORD),
        ('cntThreads', wintypes.DWORD),
        ('th32ParentProcessID', wintypes.DWORD),
        ('pcPriClassBase', wintypes.LONG),
        ('dwFlags', wintypes.DWORD),
        ('szExeFile', wintypes.WCHAR * _MAX_PATH),
    ]


def is_process_running(process_name: str) -> bool:
    """Return whether *process_name* currently exists on Windows.

    The probe uses Toolhelp32 directly so the portable build does not need
    psutil, WMI, PowerShell or a console-spawning ``tasklist`` subprocess.
    """
    target = str(process_name or '').strip().casefold()
    if not target or os.name != 'nt':
        return False

    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    create_snapshot = kernel32.CreateToolhelp32Snapshot
    create_snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    create_snapshot.restype = wintypes.HANDLE
    process_first = kernel32.Process32FirstW
    process_first.argtypes = (wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W))
    process_first.restype = wintypes.BOOL
    process_next = kernel32.Process32NextW
    process_next.argtypes = (wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W))
    process_next.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    snapshot = create_snapshot(_TH32CS_SNAPPROCESS, 0)
    invalid_handle = ctypes.c_void_p(-1).value
    if snapshot in (None, 0, invalid_handle):
        return False

    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        if not process_first(snapshot, ctypes.byref(entry)):
            return False
        while True:
            if str(entry.szExeFile or '').casefold() == target:
                return True
            if not process_next(snapshot, ctypes.byref(entry)):
                return False
    finally:
        close_handle(snapshot)
