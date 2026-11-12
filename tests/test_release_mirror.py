"""Publishing regressions: fail before writes, and resume partial uploads safely."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.request import Request

SPEC = importlib.util.spec_from_file_location(
    "mirror", Path(__file__).resolve().parents[1] / "tools/sync_gitee_release.py")
mirror = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mirror)
TAG = "v2.3.0"


def fixture():
    files = {name: (name + " content").encode() for name in mirror.expected_names(TAG)}
    files["SHA256SUMS"] = "".join(
        f"{mirror.sha256(data)}  {name}\n" for name, data in files.items()).encode()
    release = {"id": 42, "tag_name": TAG, "name": "Test release", "body": "Release notes",
               "prerelease": True, "draft": False,
               "assets": [{"name": name, "size": len(data), "state": "uploaded"}
                          for name, data in files.items()]}
    return release, files


class SourceChecks(unittest.TestCase):
    def test_complete_assets_and_manifest(self):
        release, files = fixture()
        mirror.validate_release(release, TAG)
        self.assertEqual(len(mirror.validate_manifest(files, TAG)), 4)

    def test_draft_or_incomplete_release_is_rejected(self):
        release, _ = fixture()
        release["draft"] = True
        with self.assertRaises(mirror.SyncError):
            mirror.validate_release(release, TAG)
        release["draft"] = False
        release["assets"].pop()
        with self.assertRaises(mirror.SyncError):
            mirror.validate_release(release, TAG)

    def test_tampered_package_is_rejected(self):
        _, files = fixture()
        files[mirror.expected_names(TAG)[0]] += b"corrupt"
        with self.assertRaisesRegex(mirror.SyncError, "checksum mismatch"):
            mirror.validate_manifest(files, TAG)

    def test_duplicate_or_path_manifest_is_rejected(self):
        _, files = fixture()
        valid = files["SHA256SUMS"]
        for invalid in (valid + valid.splitlines(keepends=True)[0],
                        valid.replace(b"  cy-edit-", b"  ../cy-edit-")):
            files["SHA256SUMS"] = invalid
            with self.assertRaises(mirror.SyncError):
                mirror.validate_manifest(files, TAG)

    def test_unsafe_tags_are_rejected(self):
        for tag in ("--all", "v2.3.0;echo", "../../main", "v2.3.0\n", "main"):
            with self.subTest(tag=tag), self.assertRaises(mirror.SyncError):
                mirror.validate_tag(tag)
        self.assertEqual(mirror.validate_tag("v2.4.0-beta.1"), "v2.4.0-beta.1")

    def test_token_is_not_forwarded_to_redirects(self):
        request = Request("https://gitee.com/api/v5/example",
                          headers={"Authorization": "Bearer test-placeholder"})
        with self.assertRaises(mirror.SyncError):
            mirror.SafeRedirect().redirect_request(
                request, None, 302, "", {}, "https://another.invalid/download")

    def test_published_tag_conflict_stops_before_fetch_or_push(self):
        with patch.object(mirror, "remote_refs", return_value={f"refs/tags/{TAG}": "other"}), \
                patch.object(mirror, "git") as git:
            with self.assertRaisesRegex(mirror.SyncError, "different tag"):
                mirror.inspect_refs(TAG, {"tag_object": "expected"})
            git.assert_not_called()

    def test_divergent_branch_is_rejected(self):
        with patch.object(mirror, "remote_refs", return_value={"refs/heads/main": "gitee-only"}), \
                patch.object(mirror, "git", side_effect=[SimpleNamespace(returncode=0),
                                                         SimpleNamespace(returncode=1)]):
            with self.assertRaisesRegex(mirror.SyncError, "independent changes"):
                mirror.inspect_refs(TAG, {"tag_object": "tag", "main": "github-main"})


class MirrorFlow(unittest.TestCase):
    def setUp(self):
        self.release, self.files = fixture()
        self.source = {"main": "main-sha", "tag_object": "tag-sha", "release_commit": "commit-sha"}
        self.uploaded = {name: data for name, data in self.files.items()}
        self.target = dict(self.release)
        self.mutations = []
        self.patches = [
            patch.object(mirror, "read_source", return_value=(
                self.release, self.files, mirror.validate_manifest(self.files, TAG))),
            patch.object(mirror, "source_refs", return_value=self.source),
            patch.object(mirror, "inspect_refs", return_value={}),
            patch.object(mirror, "remote_refs", return_value={
                "refs/heads/main": "main-sha", f"refs/tags/{TAG}": "tag-sha"}),
            patch.object(mirror, "api", side_effect=self.api),
            patch.object(mirror, "http", side_effect=self.download),
            patch.object(mirror, "upload_asset", side_effect=self.upload),
            patch.object(mirror, "push_refs", side_effect=lambda *a: self.mutations.append("push")),
            patch.dict(mirror.os.environ, {"GITEE_TOKEN": "test-placeholder"}),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def api(self, service, path, *, method="GET", payload=None, **kwargs):
        if method != "GET":
            self.mutations.append(method)
            self.target = dict(payload, id=42)
        if self.target is None:
            return None
        return dict(self.target, assets=[{"name": name} for name in self.uploaded])

    def download(self, url, **kwargs):
        return self.uploaded[url.rsplit("/", 1)[1]]

    def upload(self, release_id, name, data):
        self.mutations.append("upload:" + name)
        self.uploaded[name] = data

    def test_check_never_writes_even_with_a_token(self):
        result = mirror.sync(TAG, "check")
        self.assertEqual(result["status"], "checked_without_writes")
        self.assertEqual(self.mutations, [])

    def test_missing_token_prevents_all_writes(self):
        with patch.dict(mirror.os.environ, {"GITEE_TOKEN": ""}):
            with self.assertRaisesRegex(mirror.SyncError, "Missing.*GITEE_TOKEN"):
                mirror.sync(TAG, "sync")
        self.assertEqual(self.mutations, [])

    def test_new_release_copies_all_four_files(self):
        self.target = None
        self.uploaded.clear()
        result = mirror.sync(TAG, "sync")
        self.assertEqual(result["status"], "synced_and_verified")
        self.assertIn("POST", self.mutations)
        self.assertEqual(self.uploaded, self.files)
        self.assertEqual(len([x for x in self.mutations if x.startswith("upload:")]), 4)

    def test_repeat_run_does_not_reupload_files(self):
        mirror.sync(TAG, "sync")
        mirror.sync(TAG, "sync")
        self.assertFalse(any(x.startswith("upload:") for x in self.mutations))

    def test_partial_upload_resumes_only_missing_file(self):
        missing = mirror.expected_names(TAG)[1]
        del self.uploaded[missing]
        mirror.sync(TAG, "sync")
        self.assertEqual([x for x in self.mutations if x.startswith("upload:")], ["upload:" + missing])

    def test_changed_existing_asset_stops_before_any_write(self):
        self.uploaded["SHA256SUMS"] = b"different content"
        with self.assertRaisesRegex(mirror.SyncError, "refusing to replace"):
            mirror.sync(TAG, "sync")
        self.assertEqual(self.mutations, [])


if __name__ == "__main__":
    unittest.main()
