"""Einstiegspunkt der App (wird von build_exe.ps1 zur EXE gebündelt). Die Logik liegt im Paket pvapp/.

  PV-Tagesbericht.exe            Daten aktualisieren, Bericht erzeugen und öffnen
  PV-Tagesbericht.exe serve      lokaler Server mit Einrichtungsseite und täglicher Aktualisierung
"""

import sys


def entry() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        from pvapp.server import main as serve_main  # schlank: ohne pandas/pvlib

        return serve_main(sys.argv[2:])
    from pvapp.app import main

    return main()


if __name__ == "__main__":
    sys.exit(entry())
