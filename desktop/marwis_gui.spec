# PyInstaller spec for the MARWIS GUI logger.
# Build from the repo root:  pyinstaller desktop/marwis_gui.spec
# Produces a single windowed dist/MarwisLogger.exe (no console).

block_cipher = None

a = Analysis(
    ['marwis_gui.py'],
    pathex=['desktop'],  # so the local `marwis_logger` import is found and bundled
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='MarwisLogger',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # windowed: no console window flashes on launch
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,  # add a .ico here later (e.g. converted from docs/marwis_gui.png)
)
