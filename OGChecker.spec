# PyInstaller recipe for OGChecker.exe (used by the GitHub build and build_exe.bat)
a = Analysis(
    ["app.py"],
    datas=[("static", "static")],
    hiddenimports=["server", "checker_core"],
    excludes=["tkinter", "unittest", "pydoc_data"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="OGChecker",
    icon="app.ico",
    console=False,      # a real app window, no console
    upx=False,          # UPX-packed files get flagged by antivirus more often
)
