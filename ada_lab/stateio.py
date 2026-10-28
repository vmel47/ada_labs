"""Bounded retries for transient Windows file scanners, without changing identities."""
import time
from .files import write as atomic_write


def write(path,value):
    deadline=time.monotonic()+3
    while True:
        try:return atomic_write(path,value)
        except PermissionError:
            if time.monotonic()>=deadline:raise
            time.sleep(.04)


def rename(source,target):
    deadline=time.monotonic()+3
    while True:
        try:return source.rename(target)
        except PermissionError:
            if time.monotonic()>=deadline:raise
            time.sleep(.04)
