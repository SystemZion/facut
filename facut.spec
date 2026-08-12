# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all
from pathlib import Path

root = Path(SPECPATH).resolve()
datas = [(str(root / 'src' / 'facut' / 'analysis' / 'asr_worker.py'), 'facut_worker')]
binaries = [(str(root / 'vendor' / 'ffmpeg' / 'ffmpeg.exe'), 'facut_bin'), (str(root / 'vendor' / 'ffmpeg' / 'ffprobe.exe'), 'facut_bin')]
hiddenimports = []
datas += collect_data_files('openpyxl')
datas += collect_data_files('opentimelineio')
datas += collect_data_files('facut')
hiddenimports += collect_submodules('opentimelineio.adapters')
tmp_ret = collect_all('typer')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('rich')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pydantic')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    [str(root / 'src' / 'facut' / '__main__.py')],
    pathex=[str(root / 'src')],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['faster_whisper', 'ctranslate2', 'av', 'torch', 'transformers', 'tensorflow', 'pandas', 'numpy', 'scipy', 'sklearn', 'matplotlib', 'gradio', 'pyarrow'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='facut',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
