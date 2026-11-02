# PyInstaller recipe for OGChecker.exe (used by the GitHub build and build_exe.bat)
a = Analysis(
    ["server.py"],
    datas=[("static", "static")],
    hiddenimports=[],
    excludes=["tkinter", "unittest", "pydoc_data"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="OGChecker",
    icon="app.ico",
    console=False,      # no black window - the app lives in the browser
    upx=False,          # UPX-packed files get flagged by antivirus more often
)
