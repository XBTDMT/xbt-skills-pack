"""The clock the speed tests read: CPU seconds this process and its finished children have used. A hook's speed test
guards the work it does on every prompt or stop; wall-clock time also counts the wait for a free core, so on a busy
machine (a virtual machine holding most cores, two suites at once) a test failed with the code no slower. CPU time
counts only the work, a whole-file re-read included. On Windows `os.times` reports no children, so a test there counts
only this process's own work."""
import os


def cpu_seconds():
    t = os.times()
    return t.user + t.system + t.children_user + t.children_system
