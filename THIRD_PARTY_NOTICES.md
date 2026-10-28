# Third-party notices

The portable release bundles the following software. The source repository does not bundle game files or Windows fonts.

## CPython 3.13.17, Windows x64 embeddable distribution

- Source and release: https://www.python.org/downloads/release/python-31317/
- Download: https://www.python.org/ftp/python/3.13.17/python-3.13.17-embed-amd64.zip
- SHA-256: `9e35319bffedab8b36ad44d8f377cb4f1733bcec257a0b6d04d94f01486ddb38`
- License: Python Software Foundation License and licenses of its bundled components. The original `runtime/LICENSE.txt` is retained unmodified in the portable release.

## Mono.Cecil 0.11.6

- Project: https://github.com/jbevain/cecil
- Package: https://www.nuget.org/packages/Mono.Cecil/0.11.6
- Package SHA-256: `d2a23832aaa948ba9a01acc42b5726e34c5f995958f1b30d45c0e7c70b3a72d5`
- License: MIT/X11, reproduced in `licenses/Mono.Cecil.txt`.

Only `Mono.Cecil.dll` from the .NET Framework 4.0 distribution is included. `AssemblyPatcher.exe` is built from this repository's own source, not copied from the game.

## Windows fonts

No font binaries are bundled. The installer reads the player's existing Consolas, Courier New, and Arial files locally. Those files retain their original Microsoft licenses.
