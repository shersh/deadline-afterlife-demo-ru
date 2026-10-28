import base64
import csv
import io
import json
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import patch


class BinaryTests(unittest.TestCase):
    def test_byte_edits_and_bounds(self):
        self.assertEqual(patch.byte_patch(b'one two', [[4, 3, base64.b64encode('два'.encode()).decode()]]), 'one два'.encode())
        with self.assertRaises(patch.PatchError):
            patch.byte_patch(b'test', [[5, 0, '']])
        with self.assertRaises(patch.PatchError):
            patch.byte_patch(b'test', [[1, 2, ''], [2, 1, '']])

    def asset_fixture(self, folder, blocks):
        source = bytearray(512)
        struct.pack_into('>I', source, 8, 22)
        struct.pack_into('>Q', source, 32, 512)
        table = []
        for index, block in enumerate(blocks):
            source.extend(b'\0' * (-len(source) % 8))
            start = len(source)
            source.extend(block)
            metadata = 128 + index * 24
            struct.pack_into('<qqIi', source, metadata, index + 1, start - 512, len(block), 1)
            table.append([index + 1, start, len(block), metadata + 8])
        struct.pack_into('>Q', source, 24, len(source))
        path = folder / 'fixture.assets'
        path.write_bytes(source)
        return path, bytes(source), {'path': 'fixture.assets', 'data_offset': 512, 'objects': table, 'changes': []}

    def test_asset_relocation_keeps_unmodified_object(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path, source, spec = self.asset_fixture(root, [b'hello', b'untouched'])
            translated = 'здравствуйте'.encode()
            spec['changes'] = [{'id': 1, 'edits': [[0, 5, base64.b64encode(translated).decode()]], 'output_sha256': patch.digest(translated)}]
            output = patch.build_asset(path, spec, root, patch.digest(source))
            first, size = struct.unpack_from('<qI', output, 136)
            second, second_size = struct.unpack_from('<qI', output, 160)
            self.assertEqual(output[512 + first:512 + first + size], translated)
            self.assertEqual(output[512 + second:512 + second + second_size], b'untouched')
            self.assertEqual((512 + second) % 8, 0)
            self.assertEqual(struct.unpack_from('>Q', output, 24)[0], len(output))

    def test_font_data_is_read_locally_and_aligned(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path, source, spec = self.asset_fixture(root, [b'old font'])
            font = b'\x00\x01\x00\x00LOCAL_FONT'
            (root / 'local.ttf').write_bytes(font)
            spec['changes'] = [{'id': 1, 'font': 'local.ttf', 'prefix': base64.b64encode(b'head').decode(), 'suffix': base64.b64encode(b'tail').decode()}]
            output = patch.build_asset(path, spec, root, patch.digest(source))
            start, size = struct.unpack_from('<qI', output, 136)
            block = output[512 + start:512 + start + size]
            self.assertEqual(block, b'head' + struct.pack('<I', len(font)) + font + b'\0' * (-len(font) % 4) + b'tail')

    def test_bad_asset_hash_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path, source, spec = self.asset_fixture(root, [b'a'])
            with self.assertRaises(patch.PatchError):
                patch.build_asset(path, spec, root, 'wrong')

    def test_csv_preserves_keys_columns_multiline_and_placeholders(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'text.csv'
            path.write_text('id,text,trigger\r\na,"Line {n}\nsecond",keep_key\r\n', encoding='utf-8-sig', newline='')
            spec = {'path': 'text.csv', 'changes': [{'id': 'a', 'column': 'text', 'value': 'Строка {n}\nвторая'}], 'append': [['new', 'Ещё', 'unchanged']]}
            output = patch.build_csv(path, spec)
            self.assertTrue(output.startswith(b'\xef\xbb\xbf'))
            rows = list(csv.reader(io.StringIO(output.decode('utf-8-sig'), newline='')))
            self.assertEqual(rows[1], ['a', 'Строка {n}\nвторая', 'keep_key'])
            self.assertEqual(rows[2], ['new', 'Ещё', 'unchanged'])
            spec['changes'][0]['value'] = 'Без переменной'
            with self.assertRaises(patch.PatchError):
                patch.build_csv(path, spec)

    def test_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(patch.PatchError):
                patch.inside(root, '../outside')

    @unittest.skipUnless(os.name == 'nt', 'Windows process inspection')
    def test_running_copy_is_checked_by_full_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            game = Path(temporary) / 'game'
            listed = mock.Mock(returncode=0, stdout='"DEADLINE.exe","1"')
            other = mock.Mock(returncode=0, stdout=json.dumps([{'ExecutablePath': str(Path(temporary) / 'other/DEADLINE.exe')}]))
            with mock.patch.object(patch.subprocess, 'run', side_effect=[listed, other]):
                patch.ensure_closed(game)
            same = mock.Mock(returncode=0, stdout=json.dumps([{'ExecutablePath': str(game / 'DEADLINE.exe')}]))
            with mock.patch.object(patch.subprocess, 'run', side_effect=[listed, same]):
                with self.assertRaises(patch.PatchError):
                    patch.ensure_closed(game)


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.game = Path(self.temporary.name)
        self.originals = {'data/a': b'original A', 'data/b': b'original B'}
        self.translated = {'data/a': 'перевод А'.encode(), 'data/b': 'перевод Б'.encode()}
        self.manifest = {'patch_version': '1.0.0', 'files': []}
        for name, data in self.originals.items():
            target = self.game / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            self.manifest['files'].append({'path': name, 'sha256': patch.digest(data), 'legacy_translated_sha256': 'legacy'})
        (self.game / 'player.save').write_bytes(b'KEEP USER SAVE')
        self.closed = mock.patch.object(patch, 'ensure_closed')
        self.closed.start()
        self.prepare = mock.patch.object(patch, 'prepare', side_effect=self.build)
        self.builder = self.prepare.start()
        self.quiet = mock.patch('builtins.print')
        self.quiet.start()

    def tearDown(self):
        self.quiet.stop()
        self.prepare.stop()
        self.closed.stop()
        self.temporary.cleanup()

    def build(self, game, stage, manifest):
        for name, data in self.translated.items():
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return {name: patch.digest(data) for name, data in self.translated.items()}

    def assert_files(self, contents):
        for name, data in contents.items():
            self.assertEqual((self.game / name).read_bytes(), data)
        self.assertEqual((self.game / 'player.save').read_bytes(), b'KEEP USER SAVE')

    def test_install_noop_restore_and_reinstall(self):
        patch.install(self.game, self.manifest)
        self.assert_files(self.translated)
        patch.install(self.game, self.manifest)
        self.assertEqual(self.builder.call_count, 1)
        patch.restore(self.game, self.manifest)
        self.assert_files(self.originals)
        patch.install(self.game, self.manifest)
        self.assert_files(self.translated)
        patch.verify_backups(self.game, self.manifest)

    def test_wrong_version_fails_before_build_or_backup(self):
        (self.game / 'data/b').write_bytes(b'unknown update')
        with self.assertRaises(patch.PatchError):
            patch.install(self.game, self.manifest)
        self.builder.assert_not_called()
        self.assertFalse((self.game / patch.BACKUP_NAME).exists())
        self.assertEqual((self.game / 'data/a').read_bytes(), self.originals['data/a'])

    def test_changed_file_blocks_restore_without_partial_writes(self):
        patch.install(self.game, self.manifest)
        (self.game / 'data/b').write_bytes(b'USER MOD')
        with self.assertRaises(patch.PatchError):
            patch.restore(self.game, self.manifest)
        self.assertEqual((self.game / 'data/a').read_bytes(), self.translated['data/a'])
        self.assertEqual((self.game / 'data/b').read_bytes(), b'USER MOD')

    def test_second_replace_failure_rolls_back_first_file(self):
        real_replace = os.replace
        def fail_second(source, destination):
            if str(destination).replace('\\', '/').endswith('/data/b') and '.deadline-ru-build-' in str(source):
                raise PermissionError('simulated locked game file')
            return real_replace(source, destination)
        with mock.patch.object(patch.os, 'replace', side_effect=fail_second):
            with self.assertRaises(PermissionError):
                patch.install(self.game, self.manifest)
        self.assert_files(self.originals)
        receipt = patch.read_json(self.game / patch.BACKUP_NAME / 'receipt.json')
        self.assertEqual(receipt['status'], 'restored')
        patch.verify_backups(self.game, self.manifest)

    def test_interrupted_commit_can_be_restored(self):
        patch.install(self.game, self.manifest)
        (self.game / 'data/b').write_bytes(self.originals['data/b'])
        receipt_file = self.game / patch.BACKUP_NAME / 'receipt.json'
        receipt = patch.read_json(receipt_file)
        receipt['status'] = 'prepared'
        patch.write_json(receipt_file, receipt)
        patch.restore(self.game, self.manifest)
        self.assert_files(self.originals)

    def test_corrupt_backup_stops_restore(self):
        patch.install(self.game, self.manifest)
        (self.game / patch.BACKUP_NAME / 'original/data/a').write_bytes(b'bad backup')
        with self.assertRaises(patch.PatchError):
            patch.restore(self.game, self.manifest)
        self.assert_files(self.translated)

    def test_unknown_backup_is_never_overwritten(self):
        (self.game / patch.BACKUP_NAME).mkdir()
        with self.assertRaises(patch.PatchError):
            patch.install(self.game, self.manifest)
        self.assert_files(self.originals)
        self.builder.assert_not_called()


if __name__ == '__main__':
    unittest.main()
