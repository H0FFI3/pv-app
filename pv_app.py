"""Einstiegspunkt der App (wird von build_exe.ps1 zur EXE gebündelt). Die Logik liegt im Paket pvapp/."""

import sys

from pvapp.app import main

if __name__ == "__main__":
    sys.exit(main())
