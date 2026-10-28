"""Tie a Windows CLI worker and descendants to controller lifetime."""
import ctypes
import os


class ProcessJob:
    def __init__(self, process):
        self.handle=None
        if os.name!='nt':
            raise RuntimeError('AI worker lifetime isolation has only been verified on Windows')
        from ctypes import wintypes as w
        class Basic(ctypes.Structure):
            _fields_=[('ProcessTime',ctypes.c_int64),('JobTime',ctypes.c_int64),('Flags',w.DWORD),
                      ('Min',ctypes.c_size_t),('Max',ctypes.c_size_t),('Active',w.DWORD),
                      ('Affinity',ctypes.c_size_t),('Priority',w.DWORD),('Scheduling',w.DWORD)]
        class IO(ctypes.Structure):
            _fields_=[(name,ctypes.c_uint64) for name in ('read','write','other','read_bytes','write_bytes','other_bytes')]
        class Limits(ctypes.Structure):
            _fields_=[('Basic',Basic),('IO',IO),('ProcessMemory',ctypes.c_size_t),('JobMemory',ctypes.c_size_t),
                      ('PeakProcessMemory',ctypes.c_size_t),('PeakJobMemory',ctypes.c_size_t)]
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.CreateJobObjectW.restype=w.HANDLE
        kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,w.LPCWSTR]
        kernel.SetInformationJobObject.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD]
        kernel.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE]
        kernel.CloseHandle.argtypes=[w.HANDLE]
        handle=kernel.CreateJobObjectW(None,None)
        if not handle:raise ctypes.WinError(ctypes.get_last_error())
        limits=Limits();limits.Basic.Flags=0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(handle,9,ctypes.byref(limits),ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(handle,w.HANDLE(int(process._handle))):
            error=ctypes.get_last_error();kernel.CloseHandle(handle);raise ctypes.WinError(error)
        self.handle=handle;self.kernel=kernel

    def close(self):
        if self.handle:self.kernel.CloseHandle(self.handle);self.handle=None
