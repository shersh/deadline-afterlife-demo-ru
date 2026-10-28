"""Offline, version-locked Russian localization installer (standard library only)."""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
BACKUP_NAME = 'DEADLINE_RU_Backup'


class PatchError(Exception):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + '.new')
    pending.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    os.replace(pending, path)


def inside(root: Path, relative: str) -> Path:
    result = (root / relative).resolve()
    if not result.is_relative_to(root.resolve()) or result == root.resolve():
        raise PatchError('Недопустимый путь в описании патча: ' + relative)
    return result


def is_game(folder: Path) -> bool:
    return (folder / 'DEADLINE.exe').is_file() and (folder / 'DEADLINE_Data/Managed/Assembly-CSharp.dll').is_file()


def detect_game() -> Path | None:
    candidates = [PACKAGE, PACKAGE.parent, Path.cwd()]
    steam_roots = []
    if os.name == 'nt':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam') as key:
                steam_roots.append(Path(winreg.QueryValueEx(key, 'SteamPath')[0]))
        except OSError:
            pass
        steam_roots.append(Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) / 'Steam')
    for steam in list(steam_roots):
        libraries = steam / 'steamapps/libraryfolders.vdf'
        if libraries.is_file():
            for value in re.findall(r'"path"\s+"([^"]+)"', libraries.read_text(encoding='utf8')):
                steam_roots.append(Path(value.replace('\\\\', '\\')))
    for steam in steam_roots:
        candidates.append(steam / 'steamapps/common/DEADLINE Afterlife Processing Demo')
    return next((p.resolve() for p in candidates if is_game(p)), None)


def ensure_closed(game: Path):
    if os.name == 'nt':
        result = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq DEADLINE.exe', '/FO', 'CSV', '/NH'], capture_output=True, text=True, errors='replace', creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise PatchError('Не удалось проверить, закрыта ли игра. Закройте игру и повторите запуск.')
        if '"deadline.exe"' in result.stdout.lower():
            command = "[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false); @(Get-CimInstance Win32_Process -Filter \"Name = 'DEADLINE.exe'\" | Select-Object ProcessId,ExecutablePath) | ConvertTo-Json -Compress"
            details = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command], capture_output=True, text=True, encoding='utf8', errors='replace', creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                processes = json.loads(details.stdout.strip()) if details.returncode == 0 else None
            except ValueError:
                processes = None
            if isinstance(processes, dict):
                processes = [processes]
            if not isinstance(processes, list) or any(not p.get('ExecutablePath') or Path(p['ExecutablePath']).resolve() == (game / 'DEADLINE.exe').resolve() for p in processes):
                raise PatchError('Сначала закройте DEADLINE из этой папки, затем повторите запуск патча.')


def byte_patch(source: bytes, edits: list) -> bytes:
    output = bytearray()
    cursor = 0
    for offset, remove, encoded in edits:
        if not isinstance(offset, int) or not isinstance(remove, int) or offset < cursor or remove < 0 or offset + remove > len(source):
            raise PatchError('Повреждён список изменений объекта.')
        output.extend(source[cursor:offset])
        output.extend(base64.b64decode(encoded, validate=True))
        cursor = offset + remove
    output.extend(source[cursor:])
    return bytes(output)


def build_asset(source_path: Path, spec: dict, fonts: Path, expected_hash: str) -> bytes:
    source = source_path.read_bytes()
    if digest(source) != expected_hash:
        raise PatchError('Файл игры изменился во время подготовки: ' + spec['path'])
    if struct.unpack_from('>I', source, 8)[0] != 22 or source[16] != 0:
        raise PatchError('Неподдерживаемый формат Unity assets.')
    data_offset = struct.unpack_from('>Q', source, 32)[0]
    if data_offset != spec['data_offset']:
        raise PatchError('Неверное начало данных assets.')
    output = bytearray(source[:data_offset])
    changes = {change['id']: change for change in spec['changes']}
    previous_end = data_offset
    for object_id, start, length, metadata in spec['objects']:
        if start < previous_end or start + length > len(source) or metadata < 48 or metadata + 12 > data_offset:
            raise PatchError('Повреждена таблица объектов assets.')
        block = source[start:start + length]
        change = changes.pop(object_id, None)
        if change:
            if 'font' in change:
                name = change['font']
                if Path(name).name != name:
                    raise PatchError('Недопустимый путь шрифта.')
                font = fonts / name
                if not font.is_file():
                    raise PatchError('Не найден системный шрифт: ' + name)
                blob = font.read_bytes()
                if blob[:4] not in [b'\x00\x01\x00\x00', b'OTTO']:
                    raise PatchError('Неверный формат шрифта: ' + name)
                block = base64.b64decode(change['prefix'], validate=True) + struct.pack('<I', len(blob)) + blob + b'\0' * (-len(blob) % 4) + base64.b64decode(change['suffix'], validate=True)
            else:
                block = byte_patch(block, change['edits'])
                if digest(block) != change['output_sha256']:
                    raise PatchError('Не прошла проверка изменённого объекта: ' + str(object_id))
        output.extend(b'\0' * (-len(output) % 8))
        new_start = len(output)
        output.extend(block)
        struct.pack_into('<qI', output, metadata, new_start - data_offset, len(block))
        previous_end = start + length
    if changes:
        raise PatchError('В assets отсутствуют требуемые объекты.')
    output.extend(source[previous_end:])
    struct.pack_into('>Q', output, 24, len(output))
    return bytes(output)


def build_csv(source: Path, spec: dict) -> bytes:
    rows = list(csv.reader(io.StringIO(source.read_text(encoding='utf-8-sig'), newline='')))
    header = rows[0]
    by_id = {row[0]: row for row in rows[1:] if row}
    if len(by_id) != sum(bool(row) for row in rows[1:]):
        raise PatchError('Повторяющиеся идентификаторы в ' + spec['path'])
    for change in spec['changes']:
        try:
            row = by_id[change['id']]
            column = header.index(change['column'])
            before = row[column]
        except (KeyError, ValueError, IndexError) as error:
            raise PatchError('Не найдена строка перевода в ' + spec['path']) from error
        if sorted(re.findall(r'\{[^{}]+\}', before)) != sorted(re.findall(r'\{[^{}]+\}', change['value'])):
            raise PatchError('Не совпадают подстановки текста в ' + spec['path'])
        row[column] = change['value']
    for row in spec['append']:
        if row[0] in by_id:
            raise PatchError('Повторяющийся дополнительный ключ: ' + row[0])
        rows.append(row)
        by_id[row[0]] = row
    target = io.StringIO(newline='')
    csv.writer(target, lineterminator='\r\n').writerows(rows)
    return target.getvalue().encode('utf-8-sig')


def state(game: Path, manifest: dict, receipt: dict | None):
    hashes = {}
    for entry in manifest['files']:
        path = inside(game, entry['path'])
        if not path.is_file():
            raise PatchError('Не найден файл игры: ' + entry['path'])
        hashes[entry['path']] = file_hash(path)
    if all(hashes[e['path']] == e['sha256'] for e in manifest['files']):
        return 'original', hashes
    if receipt and receipt.get('patch_version') == manifest['patch_version'] and all(hashes[e['path']] == receipt.get('installed_hashes', {}).get(e['path']) for e in manifest['files']):
        return 'installed', hashes
    if all(hashes[e['path']] == e['legacy_translated_sha256'] for e in manifest['files']):
        return 'legacy', hashes
    raise PatchError('Версия игры не поддерживается, файлы изменены или патч установлен частично. Ничего не заменено. Проверьте целостность файлов в Steam либо восстановите прежнюю резервную копию.')


def verify_backups(game: Path, manifest: dict):
    backup = game / BACKUP_NAME / 'original'
    for entry in manifest['files']:
        original = inside(backup, entry['path'])
        if not original.is_file() or file_hash(original) != entry['sha256']:
            raise PatchError('Резервная копия отсутствует или повреждена: ' + entry['path'])


def prepare(game: Path, stage: Path, manifest: dict):
    entries = {entry['path']: entry for entry in manifest['files']}
    for spec in read_json(PACKAGE / 'payload/csv.json'):
        target = inside(stage, spec['path'])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(build_csv(inside(game, spec['path']), spec))
    fonts = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts'
    for spec in read_json(PACKAGE / 'payload/assets.json'):
        print('  Подготовка: ' + Path(spec['path']).name)
        target = inside(stage, spec['path'])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(build_asset(inside(game, spec['path']), spec, fonts, entries[spec['path']]['sha256']))
    assembly = 'DEADLINE_Data/Managed/Assembly-CSharp.dll'
    target = inside(stage, assembly)
    target.parent.mkdir(parents=True, exist_ok=True)
    process = subprocess.run([str(PACKAGE / 'bin/AssemblyPatcher.exe'), str(inside(game, assembly)), str(target), str(PACKAGE / 'payload/assembly.json'), str(game / 'DEADLINE_Data/Managed')], capture_output=True, text=True, errors='replace', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if process.returncode or not target.is_file():
        raise PatchError('Не удалось подготовить встроенные строки: ' + (process.stderr or process.stdout).strip())
    return {e['path']: file_hash(inside(stage, e['path'])) for e in manifest['files']}


def install(game: Path, manifest: dict):
    receipt_file = game / BACKUP_NAME / 'receipt.json'
    receipt = read_json(receipt_file) if receipt_file.is_file() else None
    current, _ = state(game, manifest, receipt)
    if current == 'installed':
        verify_backups(game, manifest)
        print('Русский перевод уже установлен. Повторная установка не требуется.')
        return
    if current == 'legacy':
        raise PatchError('Ранняя локальная версия перевода уже установлена. Сначала запустите _RussianLocalization/RestoreOriginal.cmd, затем этот установщик.')
    if receipt:
        verify_backups(game, manifest)
    elif (game / BACKUP_NAME).exists():
        raise PatchError('Папка резервной копии уже существует без квитанции установки. Сохраните её отдельно и проверьте файлы; она не будет перезаписана.')
    print('Версия игры подходит. Готовлю перевод...')
    with tempfile.TemporaryDirectory(prefix='.deadline-ru-build-', dir=game) as temporary:
        stage = Path(temporary).resolve()
        installed = prepare(game, stage, manifest)
        if state(game, manifest, None)[0] != 'original':
            raise PatchError('Игра изменилась во время подготовки.')
        ensure_closed(game)
        if not receipt:
            for entry in manifest['files']:
                target = inside(game / BACKUP_NAME / 'original', entry['path'])
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(inside(game, entry['path']), target)
            verify_backups(game, manifest)
        receipt = {'patch_version': manifest['patch_version'], 'status': 'prepared', 'installed_hashes': installed}
        write_json(receipt_file, receipt)
        changed = []
        try:
            for entry in manifest['files']:
                os.replace(inside(stage, entry['path']), inside(game, entry['path']))
                changed.append(entry['path'])
            receipt['status'] = 'installed'
            write_json(receipt_file, receipt)
        except BaseException:
            failures = []
            for relative in reversed(changed):
                try:
                    shutil.copy2(inside(game / BACKUP_NAME / 'original', relative), inside(game, relative))
                except OSError:
                    failures.append(relative)
            receipt['status'] = 'rollback_failed' if failures else 'restored'
            receipt['rollback_failures'] = failures
            write_json(receipt_file, receipt)
            if failures:
                raise PatchError('Сбой установки и автоматического отката. Сохранены оригиналы в ' + str(game / BACKUP_NAME))
            raise
    print('Готово! Русский перевод установлен. Запускайте игру через Steam.')
    print('Оригиналы сохранены: ' + str(game / BACKUP_NAME))


def restore(game: Path, manifest: dict):
    receipt_file = game / BACKUP_NAME / 'receipt.json'
    if not receipt_file.is_file():
        raise PatchError('Не найдена резервная копия этого установщика.')
    receipt = read_json(receipt_file)
    if receipt.get('patch_version') != manifest['patch_version']:
        raise PatchError('Эта резервная копия создана другой версией патча.')
    verify_backups(game, manifest)
    desired = []
    for entry in manifest['files']:
        actual = file_hash(inside(game, entry['path']))
        if actual == entry['sha256']:
            continue
        if actual != receipt.get('installed_hashes', {}).get(entry['path']):
            raise PatchError('Файл изменился после установки; откат остановлен без замены файлов: ' + entry['path'])
        desired.append(entry['path'])
    with tempfile.TemporaryDirectory(prefix='.deadline-ru-restore-', dir=game) as temporary:
        stage = Path(temporary).resolve()
        for relative in desired:
            for area, source in [('original', inside(game / BACKUP_NAME / 'original', relative)), ('undo', inside(game, relative))]:
                target = inside(stage / area, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        changed = []
        try:
            for relative in desired:
                os.replace(inside(stage / 'original', relative), inside(game, relative))
                changed.append(relative)
        except BaseException:
            for relative in reversed(changed):
                shutil.copy2(inside(stage / 'undo', relative), inside(game, relative))
            raise
    receipt['status'] = 'restored'
    write_json(receipt_file, receipt)
    print('Оригинальные файлы восстановлены. Сохранения игрока не изменены.')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Русский перевод DEADLINE: Afterlife Processing Demo')
    parser.add_argument('action', choices=['install', 'restore', 'check'], nargs='?', default='install')
    parser.add_argument('--game', type=Path, help='Папка с DEADLINE.exe')
    args = parser.parse_args(argv)
    try:
        if os.name != 'nt':
            raise PatchError('Этот установщик предназначен для Windows.')
        game = args.game.resolve() if args.game else detect_game()
        if game is None and sys.stdin.isatty():
            game = Path(input('Не удалось найти игру. Вставьте путь к папке с DEADLINE.exe: ').strip().strip('"')).resolve()
        if game is None or not is_game(game):
            raise PatchError('Игра не найдена. Укажите папку с DEADLINE.exe через --game или перетащите её на INSTALL.cmd.')
        print('Игра: ' + str(game))
        manifest = read_json(PACKAGE / 'payload/manifest.json')
        if args.action == 'check':
            receipt_file = game / BACKUP_NAME / 'receipt.json'
            receipt = read_json(receipt_file) if receipt_file.is_file() else None
            current, _ = state(game, manifest, receipt)
            print({'original': 'Совместимая оригинальная версия.', 'installed': 'Перевод установлен, контрольные суммы совпадают.', 'legacy': 'Установлена ранняя локальная версия перевода.'}[current])
        else:
            ensure_closed(game)
            (install if args.action == 'install' else restore)(game, manifest)
        return 0
    except (PatchError, OSError, ValueError, KeyError) as error:
        print('Ошибка: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf8')
        sys.stderr.reconfigure(encoding='utf8')
    raise SystemExit(main())
