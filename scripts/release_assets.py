"""Prepare versioned + stable installer assets from an already built extension ZIP."""
import argparse
import hashlib
import re
import shutil
import tomllib
import zipfile
from pathlib import Path

from check_blender import addon_version

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=ROOT / 'dist')
    parser.add_argument('--output', type=Path, default=ROOT / 'release-assets')
    args = parser.parse_args()
    version = addon_version()
    package = args.input / f'final_dimensions-{version}.zip'
    source = ROOT / 'final_dimensions'
    expected = {p.name for p in source.iterdir() if p.is_file() and
                (p.suffix == '.py' or p.name in {'blender_manifest.toml', 'LICENSE.txt'})}
    with zipfile.ZipFile(package) as archive:
        assert set(archive.namelist()) == expected, 'Unexpected or missing files in installer'
        for name in expected:
            assert archive.read(name) == (source / name).read_bytes(), f'Stale installer: {name}'
        manifest = tomllib.loads(archive.read('blender_manifest.toml').decode())
        assert manifest['version'] == version
    notes = (ROOT / 'CHANGELOG.md').read_text(encoding='utf-8')
    section = re.search(r'^## '+re.escape(version)+r'[^\n]*\n(.*?)(?=^## |\Z)', notes, re.M | re.S)
    if section is None:
        raise RuntimeError('Add a CHANGELOG section for '+version)
    args.output.mkdir(parents=True, exist_ok=True)
    assets = [args.output / package.name, args.output / 'final_dimensions.zip']
    for asset in assets:
        shutil.copyfile(package, asset)
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    (args.output / 'SHA256SUMS.txt').write_text(''.join(f'{digest}  {p.name}\n' for p in assets), encoding='utf-8')
    release_notes = (f'## Final Dimensions {version}\n\n'
                     '**Для установки скачайте `final_dimensions.zip` из Assets. Не распаковывайте его.**\n\n'
                     '[Установка с нуля](https://github.com/Ddosena/blender-final-dimensions/blob/main/docs/INSTALL_RU.md) · '
                     '[Как пользоваться](https://github.com/Ddosena/blender-final-dimensions/blob/main/docs/USAGE_RU.md)\n\n'
                     +section.group(1).strip()+'\n\n'
                     f'Минимальная версия Blender: {manifest["blender_version_min"]}. '
                     '[Проверенные версии и результаты](https://github.com/Ddosena/blender-final-dimensions/blob/main/TESTING.md). '
                     'Линейки временные и не сохраняются в `.blend`.\n\n'
                     'Файлы `final_dimensions.zip` и архив с номером версии содержат один и тот же установщик. '
                     '`Source code (zip)` — исходники, не установочный пакет.\n')
    (args.output / 'RELEASE_NOTES.md').write_text(release_notes, encoding='utf-8')
    (args.output / 'VERSION').write_text(version, encoding='utf-8')
    print('RELEASE ASSETS READY', version, digest)


if __name__ == '__main__':
    main()
