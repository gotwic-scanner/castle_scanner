#!/usr/bin/env python3
# GOTWiC Scanner GUI package release: 1.0.3
"""Apply a fully staged changed-file update after the GUI exits."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import tkinter as tk
from tkinter import messagebox

def alert(message):
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("GOTWiC Scanner update", message, parent=root)
        root.destroy()
    except Exception:
        print(message, file=sys.stderr)

def wait_for_pid(pid):
    if os.name == "nt":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)
        if handle:
            try:
                return ctypes.windll.kernel32.WaitForSingleObject(handle, 30000) == 0
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        return True
    for _ in range(300):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except OSError:
            return True
        time.sleep(.1)
    return False

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--pid", type=int, required=True)
    p.add_argument("--root", required=True)
    p.add_argument("--stage", required=True)
    args = p.parse_args()
    root = Path(args.root).resolve()
    stage = Path(args.stage).resolve()
    if not (root/"gotwic_gui.py").is_file() or not (stage/"plan.json").is_file():
        alert("The staged update or application folder is missing.")
        return 2
    plan = json.loads((stage/"plan.json").read_text(encoding="utf-8"))
    changes = plan["files"]
    # Never accept path traversal, symlinks or absolute destinations.
    for item in changes:
        name = item["path"]
        if (not isinstance(name, str) or not name or
            "/" in name or "\\" in name or name in (".", "..") or
            Path(name).name != name or
            (root/name).is_symlink() or (stage/name).is_symlink()):
            alert("Unsafe file path in update plan.")
            return 2
    if not wait_for_pid(args.pid):
        alert("The application did not close. Update cancelled; select OK to continue.")
        return 1

    backup = stage/"backup"
    backup.mkdir(exist_ok=True)
    installed = []
    try:
        for item in changes:
            name = item["path"]
            target = root/name
            staged = stage/name
            if not staged.is_file():
                raise OSError("Missing staged file: " + name)
            if target.exists():
                shutil.copy2(target, backup/name)
            os.replace(staged, target)
            installed.append(name)
    except Exception as exc:
        failures = []
        for name in reversed(installed):
            try:
                if (backup/name).exists():
                    os.replace(backup/name, root/name)
                else:
                    (root/name).unlink(missing_ok=True)
            except Exception as rollback_error:
                failures.append(str(rollback_error))
        alert("Installation failed. Existing files were restored where possible.\n\n" +
              str(exc) + ("\nRollback errors: " + "; ".join(failures) if failures else "") +
              "\n\nSelect OK to start the installed application.")
    finally:
        shutil.rmtree(stage, ignore_errors=True)

    try:
        subprocess.Popen([sys.executable, str(root/"gotwic_gui.py")], cwd=str(root))
    except Exception as exc:
        alert("Could not restart GOTWiC Scanner: " + str(exc))
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
