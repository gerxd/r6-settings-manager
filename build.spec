# PyInstaller build. Leaves out Qt files the app never loads and a duplicate copy of NumPy's OpenBLAS.
# uv run --no-project --with pyinstaller --with PySide6-Essentials --with numpy pyinstaller --noconfirm build.spec

DROP = ("opengl32sw.dll", "/translations/", "/imageformats/", "qdirect2d")


def keep(entry):
    name = entry[0].replace("\\", "/")
    return not name.startswith("libscipy_openblas") and not any(d in name for d in DROP)


a = Analysis(["r6_siege_settings_manager.py"], excludes=["tkinter"])
a.binaries = [b for b in a.binaries if keep(b)]
a.datas = [d for d in a.datas if keep(d)]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="R6 Siege Settings Manager", console=False, upx=False)
