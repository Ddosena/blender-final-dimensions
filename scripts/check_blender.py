"""Run numerical, package and optional viewport checks in isolated Blender processes."""
import argparse
import ast
import json
import os
import re
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def addon_version():
    manifest = tomllib.loads((ROOT / 'final_dimensions/blender_manifest.toml').read_text(encoding='utf-8'))
    tree = ast.parse((ROOT / 'final_dimensions/__init__.py').read_text(encoding='utf-8'))
    info = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == 'bl_info' for target in node.targets))
    assert tuple(map(int, manifest['version'].split('.'))) == tuple(info['version']), 'Version mismatch'
    return manifest['version']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--blender', required=True, help='Path to Blender executable')
    parser.add_argument('--gui', action='store_true', help='Also run mouse/GPU tests; needs a display')
    args = parser.parse_args()
    blender = str(Path(args.blender).resolve())
    version = addon_version()
    artifacts = ROOT / 'artifacts'
    artifacts.mkdir(exist_ok=True)
    (ROOT / 'dist').mkdir(exist_ok=True)
    output = subprocess.check_output([blender, '--version'], text=True, encoding='utf-8', errors='replace')
    match = re.search(r'Blender (\d+\.\d+\.\d+)(?: (LTS))?', output)
    if match is None:
        raise RuntimeError('Could not read Blender version')
    blender_version = match.group(1) + ('-LTS' if match.group(2) else '')
    report = {'blender': blender_version, 'addon': version, 'checks': []}
    environment = os.environ.copy()
    environment['BLENDER_USER_RESOURCES'] = str(artifacts / 'profiles' / ('checks-'+blender_version))

    def run(name, command):
        print('CHECK', name, flush=True)
        result = subprocess.run([blender, *command], cwd=ROOT, env=environment,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding='utf-8', errors='replace', timeout=240)
        (artifacts / f'{name}-{blender_version}.log').write_text(result.stdout, encoding='utf-8')
        if result.returncode:
            print(result.stdout, flush=True)
            raise RuntimeError(f'{name}: Blender exited with code {result.returncode}')
        report['checks'].append(name)

    for script in ('test_blender', 'test_extra', 'test_direction', 'test_section',
                   'test_cursor_diameter', 'test_surface'):
        run(script, ['--background', '--factory-startup', '--python-exit-code', '1',
                     '--python', str(ROOT / 'tests' / (script+'.py'))])
    run('build', ['--factory-startup', '--command', 'extension', 'build',
                  '--source-dir', str(ROOT / 'final_dimensions'), '--output-dir', str(ROOT / 'dist')])
    package = ROOT / 'dist' / f'final_dimensions-{version}.zip'
    run('validate', ['--factory-startup', '--command', 'extension', 'validate', str(package)])
    run('test_install', ['--background', '--factory-startup', '--python-exit-code', '1',
                         '--python', str(ROOT / 'tests/test_install.py')])
    if args.gui:
        for script, prefix in (('test_hover_live', 'hover-live'), ('test_ruler_live', 'ruler-live'),
                               ('test_multi_ruler_live', 'multi-ruler-live')):
            result_path = artifacts / f'{prefix}-{blender_version}.json'
            # An old report must never pass a failed new process.
            if result_path.exists():
                result_path.unlink()
            run(script, ['--factory-startup', '--window-geometry', '0', '0', '1920', '1080',
                         '--enable-event-simulate', '--python-exit-code', '1',
                         '--python', str(ROOT / 'tests' / (script+'.py'))])
            result = json.loads(result_path.read_text(encoding='utf-8'))
            assert result['status'] == 'PASS', result
    report['status'] = 'PASS'
    (artifacts / f'checks-{blender_version}.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('ALL CHECKS PASS', blender_version, 'Final Dimensions', version, flush=True)


if __name__ == '__main__':
    main()
