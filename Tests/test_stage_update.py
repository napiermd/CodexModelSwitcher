import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import select
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.request


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/stage-update.py'
spec = importlib.util.spec_from_file_location('stage_update', SCRIPT)
staging = importlib.util.module_from_spec(spec)
spec.loader.exec_module(staging)
SIGNATURE = {'verified': True, 'Identifier': 'dev.napier.ModelHarbor', 'CDHash': 'synthetic'}
CONTEXT = {'checkout_commit_at_staging': 'a' * 40, 'checkout_dirty_at_staging': True,
           'artifact_source_binding': 'unverified'}


@unittest.skipUnless(sys.platform == 'darwin', 'macOS app staging uses renamex_np and codesign')
class StageUpdateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repo = Path(self.directory.name).resolve()
        self.app = self.repo / 'candidate/Model Harbor.app'
        (self.app / 'Contents/MacOS').mkdir(parents=True)
        self.binary = self.app / 'Contents/MacOS/Model Harbor'
        self.binary.write_bytes(b'fixture: must never execute\n')
        self.binary.chmod(0o755)
        self.info = {'CFBundleIdentifier': 'dev.napier.ModelHarbor',
                     'CFBundleExecutable': 'Model Harbor',
                     'CFBundleShortVersionString': '0.1.0', 'CFBundleVersion': '4'}
        self.write_info()
        self.signer = patch.object(staging, 'verify_signature', return_value=SIGNATURE).start()
        self.addCleanup(patch.stopall)
        patch.object(staging, 'source_context', return_value=CONTEXT).start()

    def write_info(self):
        (self.app / 'Contents/Info.plist').write_bytes(plistlib.dumps(self.info))

    def run_stage(self, name='candidate'):
        return staging.stage(self.app, self.repo, name)

    def assert_no_stage(self):
        root = self.repo / 'build/staged-updates'
        self.assertEqual(list(root.iterdir()) if root.exists() else [], [])

    def test_stages_verified_copy_with_honest_provenance_and_no_install_claim(self):
        destination = self.run_stage()
        self.assertEqual(destination, self.repo / 'build/staged-updates/candidate')
        manifest = json.loads((destination / 'manifest.json').read_text())
        self.assertEqual(manifest['state'], 'staged')
        for key in ('installed', 'provider_verified', 'live_handoff_verified'):
            self.assertIs(manifest[key], False)
        self.assertEqual(manifest['source_context'], CONTEXT)
        self.assertEqual(manifest['bundle']['metadata'], self.info)
        self.assertEqual(manifest['bundle']['signature'], SIGNATURE)
        entry = manifest['bundle']['entries']['Contents/MacOS/Model Harbor']
        self.assertEqual(entry, {'type': 'file', 'mode': 0o755, 'size': 28,
                                'sha256': hashlib.sha256(b'fixture: must never execute\n').hexdigest()})
        self.assertEqual((destination / 'Model Harbor.app/Contents/MacOS/Model Harbor').read_bytes(),
                         self.binary.read_bytes())
        self.assertEqual(self.signer.call_count, 2)

    def test_existing_stage_is_never_overwritten(self):
        destination = self.run_stage()
        original = (destination / 'manifest.json').read_bytes()
        with self.assertRaisesRegex(staging.StageError, 'already exists'):
            self.run_stage()
        self.assertEqual((destination / 'manifest.json').read_bytes(), original)

    def test_atomic_publish_refuses_even_an_empty_destination_created_during_copy(self):
        original_copy = shutil.copytree
        def racing_copy(*args, **kwargs):
            result = original_copy(*args, **kwargs)
            if Path(args[0]) == self.app:
                (self.repo / 'build/staged-updates/candidate').mkdir()
            return result
        with patch.object(staging.shutil, 'copytree', side_effect=racing_copy):
            with self.assertRaises(FileExistsError):
                self.run_stage()
        root = self.repo / 'build/staged-updates'
        self.assertEqual([path.name for path in root.iterdir()], ['candidate'])
        self.assertEqual(list((root / 'candidate').iterdir()), [])

    def test_source_or_copy_changes_abort_publication(self):
        original_copy = shutil.copytree
        for change_source in (True, False):
            with self.subTest(change_source=change_source):
                def changing_copy(source, destination, *args, **kwargs):
                    result = original_copy(source, destination, *args, **kwargs)
                    if Path(source) == self.app:
                        target = self.binary if change_source else Path(destination) / 'Contents/MacOS/Model Harbor'
                        target.write_bytes(b'changed while staging')
                    return result
                with patch.object(staging.shutil, 'copytree', side_effect=changing_copy):
                    with self.assertRaisesRegex(staging.StageError, 'does not match|source changed'):
                        self.run_stage()
                self.assert_no_stage()
                self.binary.write_bytes(b'fixture: must never execute\n')

    def test_failed_copy_cleans_only_its_partial_output(self):
        sibling = self.run_stage('retained')
        with patch.object(staging.shutil, 'copytree', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                self.run_stage()
        self.assertTrue((sibling / 'manifest.json').exists())
        self.assertEqual([path.name for path in sibling.parent.iterdir()], ['retained'])

    def test_source_and_copied_signature_failures_do_not_publish(self):
        for answers in ([staging.StageError('invalid signature')],
                        [SIGNATURE, staging.StageError('invalid signature')]):
            with self.subTest(answers=len(answers)):
                self.signer.side_effect = answers
                with self.assertRaisesRegex(staging.StageError, 'invalid signature'):
                    self.run_stage()
                self.assert_no_stage()

    def test_malformed_identity_and_executable_metadata_fail(self):
        for changes in ({'CFBundleIdentifier': 'another.app'},
                        {'CFBundleExecutable': '../outside'},
                        {'CFBundleVersion': 4}, {'CFBundleExecutable': 'missing'}):
            with self.subTest(changes=changes):
                original = dict(self.info)
                self.info.update(changes)
                self.write_info()
                with self.assertRaises(staging.StageError):
                    self.run_stage()
                self.assert_no_stage()
                self.info = original
        for content in (b'not a plist', b'<?xml version="1.0"?><plist><dict>'):
            with self.subTest(content=content):
                (self.app / 'Contents/Info.plist').write_bytes(content)
                with self.assertRaises(staging.StageError):
                    self.run_stage()
                self.assert_no_stage()

    def test_external_and_dangling_symlinks_fail(self):
        outside = self.repo / 'private.txt'
        outside.write_text('not part of the bundle')
        link = self.app / 'Contents/link'
        for target in (outside, Path('../../private.txt'), Path('missing'), Path('link')):
            with self.subTest(target=target):
                link.symlink_to(target)
                with self.assertRaises(staging.StageError):
                    self.run_stage()
                self.assert_no_stage()
                link.unlink()
        self.assertEqual(outside.read_text(), 'not part of the bundle')

    def test_internal_framework_style_links_are_preserved(self):
        (self.app / 'Contents/current').symlink_to('MacOS')
        destination = self.run_stage()
        link = destination / 'Model Harbor.app/Contents/current'
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), 'MacOS')

    def test_special_files_are_rejected_before_copy(self):
        os.mkfifo(self.app / 'Contents/pipe')
        with self.assertRaisesRegex(staging.StageError, 'file type'):
            self.run_stage()
        self.assert_no_stage()

    def test_stage_names_cannot_escape_or_address_hidden_partials(self):
        for name in ('../escape', '/Applications/Model Harbor.app', '.partial-existing', 'x/y', ''):
            with self.subTest(name=name):
                with self.assertRaises(staging.StageError):
                    self.run_stage(name)
        self.assert_no_stage()

    def test_applications_destination_is_rejected_before_writing(self):
        with self.assertRaisesRegex(staging.StageError, 'Applications'):
            staging.stage(self.app, Path('/Applications'), 'candidate')

    def test_input_bundle_cannot_contain_staging_output(self):
        before = staging.inventory(self.app)
        with self.assertRaisesRegex(staging.StageError, 'contain the staging directory'):
            staging.stage(self.app, self.app, 'candidate')
        self.assertEqual(staging.inventory(self.app), before)

    def test_symlinked_build_directory_cannot_redirect_writes(self):
        protected = self.repo / 'untouched'
        protected.mkdir()
        (self.repo / 'build').symlink_to(protected, target_is_directory=True)
        with self.assertRaisesRegex(staging.StageError, 'symlinks'):
            self.run_stage()
        self.assertEqual(list(protected.iterdir()), [])

    def test_staging_leaves_mock_gateway_and_existing_state_untouched(self):
        protected = self.repo / 'existing-state'
        protected.mkdir()
        for name in ('installed-app', 'config.toml', 'task-history.jsonl'):
            (protected / name).write_bytes(b'preserve this existing content\n')
        before = {path.name: path.read_bytes() for path in protected.iterdir()}
        code = '''from http.server import HTTPServer, BaseHTTPRequestHandler
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(200); self.end_headers(); self.wfile.write(b"still serving")
 def log_message(self, *args): pass
server = HTTPServer(("127.0.0.1", 0), Handler)
print(server.server_port, flush=True)
server.serve_forever()
'''
        process = subprocess.Popen([sys.executable, '-u', '-c', code], stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, text=True)
        try:
            ready, _, _ = select.select([process.stdout], [], [], 5)
            self.assertTrue(ready, 'Mock gateway did not start within five seconds')
            port = int(process.stdout.readline())
            with urllib.request.urlopen(f'http://127.0.0.1:{port}', timeout=5) as response:
                self.assertEqual(response.read(), b'still serving')
            self.run_stage()
            self.assertIsNone(process.poll())
            with urllib.request.urlopen(f'http://127.0.0.1:{port}', timeout=5) as response:
                self.assertEqual(response.read(), b'still serving')
            self.assertEqual({path.name: path.read_bytes() for path in protected.iterdir()}, before)
        finally:
            process.terminate()
            process.wait(timeout=5)
            process.stdout.close()


if __name__ == '__main__':
    unittest.main()
