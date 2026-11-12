#!/usr/bin/env python3
"""Mirror one published GitHub release to Gitee; --mode check is read-only.

Only the official public repository pair is supported. GITEE_TOKEN is read from
the environment and used only for Gitee API calls and temporary Git askpass.
Existing divergent refs or different same-name files stop the sync for review.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid

ROOT = Path(__file__).resolve().parents[1]
GITHUB_REPO = "chenyipier-ui/cy-edit-skills"
GITEE_REPO = "chenyipier/cy-edit-skills"
GITHUB_GIT = f"https://github.com/{GITHUB_REPO}.git"
GITEE_GIT = f"https://gitee.com/{GITEE_REPO}.git"
SKILLS = ("cy-edit-shortvideo", "cy-edit-longvideo")
MAX_FILE = 128 * 1024 * 1024


class SyncError(RuntimeError):
    pass


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if req.has_header("Authorization") or urlsplit(newurl).scheme != "https":
            raise SyncError("Refused an authenticated or non-HTTPS redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def http(url, *, method="GET", token="", data=None, content_type=None, missing_ok=False):
    headers = {"User-Agent": "cy-edit-skills-release-mirror", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if content_type:
        headers["Content-Type"] = content_type
    request = Request(url, data=data, headers=headers, method=method)
    # GETs can be retried. A failed POST might already have succeeded server-side;
    # rerunning the workflow discovers that release/file before attempting a write.
    for attempt in range(3):
        try:
            with build_opener(SafeRedirect).open(request, timeout=60) as response:
                result = response.read(MAX_FILE + 1)
                if len(result) > MAX_FILE:
                    raise SyncError("Response exceeds the 128 MiB mirror limit")
                return result
        except HTTPError as error:
            if error.code == 404 and missing_ok:
                return None
            if method == "GET" and error.code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(2 ** (attempt + 1))
                continue
            # Do not expose response bodies, URLs with parameters, or credentials.
            raise SyncError(f"{urlsplit(url).hostname}: {method} returned HTTP {error.code}") from None
        except (URLError, TimeoutError, OSError):
            if method == "GET" and attempt < 2:
                time.sleep(2 ** (attempt + 1))
                continue
            raise SyncError(f"{urlsplit(url).hostname}: {method} network failure; rerun the workflow") from None


def api(service, path, *, method="GET", payload=None, missing_ok=False):
    if service == "github":
        base, token = "https://api.github.com", os.environ.get("GH_TOKEN", "")
    elif service == "gitee":
        base, token = "https://gitee.com/api/v5", os.environ.get("GITEE_TOKEN", "")
    else:
        raise SyncError("Unknown API service")
    result = http(base + path, method=method, token=token,
                  data=json.dumps(payload).encode() if payload is not None else None,
                  content_type="application/json" if payload is not None else None,
                  missing_ok=missing_ok)
    return json.loads(result) if result is not None else None


def git(*args, env=None, check=True):
    result = subprocess.run(["git", *args], cwd=ROOT, env=env,
                            text=True, capture_output=True)
    if check and result.returncode:
        # Git's remote messages can contain credentials; do not echo raw output.
        raise SyncError(f"git {args[0]} failed (exit {result.returncode}); check access and ref divergence")
    return result


def validate_tag(tag):
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9][A-Za-z0-9.-]*)?", tag):
        raise SyncError("Use a version tag such as v2.3.0 or v2.4.0-beta.1")
    return tag


def expected_names(tag):
    return [f"{name}-{tag}.zip" for name in (*SKILLS, "cy-edit-skills")]


def validate_release(release, tag):
    if release.get("draft") or release.get("tag_name") != tag:
        raise SyncError("Source must be a published GitHub release with the requested tag")
    names = [item["name"] for item in release.get("assets", [])]
    if len(names) != len(set(names)) or set(names) != {*expected_names(tag), "SHA256SUMS"}:
        raise SyncError("Publish all three versioned ZIPs and SHA256SUMS before publishing the release")
    if any(item.get("state", "uploaded") != "uploaded" for item in release["assets"]):
        raise SyncError("A GitHub asset has not finished uploading")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def validate_manifest(files, tag):
    checksums = {}
    for line in files["SHA256SUMS"].decode("utf-8").splitlines():
        match = re.fullmatch(r"([a-f0-9]{64})  ([A-Za-z0-9.-]+)", line)
        if not match or match[2] in checksums:
            raise SyncError("Malformed or duplicate entry in SHA256SUMS")
        checksums[match[2]] = match[1]
    if set(checksums) != set(expected_names(tag)):
        raise SyncError("SHA256SUMS does not describe exactly the three versioned ZIPs")
    for name, expected in checksums.items():
        if sha256(files[name]) != expected:
            raise SyncError(f"GitHub checksum mismatch: {name}")
    return {name: sha256(data) for name, data in files.items()}


def asset_url(service, tag, name):
    host, repo = ("github.com", GITHUB_REPO) if service == "github" else ("gitee.com", GITEE_REPO)
    return f"https://{host}/{repo}/releases/download/{quote(tag, safe='')}/{quote(name, safe='')}"


def read_source(tag):
    release = api("github", f"/repos/{GITHUB_REPO}/releases/tags/{quote(tag, safe='')}")
    validate_release(release, tag)
    files = {}
    for asset in release["assets"]:
        name = asset["name"]
        # Public downloads deliberately carry no API token, including redirects.
        data = http(asset_url("github", tag, name))
        if len(data) != asset["size"]:
            raise SyncError(f"Incomplete GitHub asset: {name}")
        if asset.get("digest") and asset["digest"] != f"sha256:{sha256(data)}":
            raise SyncError(f"GitHub asset digest mismatch: {name}")
        files[name] = data
    hashes = validate_manifest(files, tag)
    return release, files, hashes


def source_refs(tag):
    git("fetch", "--no-tags", GITHUB_GIT,
        "refs/heads/main:refs/remotes/origin/main", f"refs/tags/{tag}:refs/tags/{tag}")
    main = git("rev-parse", "refs/remotes/origin/main").stdout.strip()
    tag_object = git("rev-parse", f"refs/tags/{tag}").stdout.strip()
    commit = git("rev-parse", f"refs/tags/{tag}^{{commit}}").stdout.strip()
    if git("merge-base", "--is-ancestor", commit, main, check=False).returncode:
        raise SyncError("The release commit is not on GitHub main")
    for path in ("VERSION", *(f"skills/{name}/VERSION" for name in SKILLS)):
        if git("show", f"{commit}:{path}").stdout.strip() != tag[1:]:
            raise SyncError(f"Release tag and {path} disagree")
    return {"main": main, "tag_object": tag_object, "release_commit": commit}


def remote_refs(tag):
    lines = git("ls-remote", GITEE_GIT, "refs/heads/main", f"refs/tags/{tag}").stdout.splitlines()
    return dict((ref, sha) for sha, ref in (line.split() for line in lines))


def inspect_refs(tag, source):
    current = remote_refs(tag)
    tag_ref = f"refs/tags/{tag}"
    if current.get(tag_ref) not in (None, source["tag_object"]):
        raise SyncError("Gitee has a different tag object; refusing to overwrite a published tag")
    old_main = current.get("refs/heads/main")
    if old_main:
        git("fetch", "--no-tags", GITEE_GIT, "refs/heads/main")
        if git("merge-base", "--is-ancestor", old_main, source["main"], check=False).returncode:
            raise SyncError("Gitee main has independent changes; review them before syncing")
    return current


def missing_assets(release, files, tag):
    names = [item["name"] for item in release.get("assets", [])] if release else []
    missing = []
    for name, data in files.items():
        if names.count(name) > 1:
            raise SyncError(f"Duplicate Gitee asset: {name}")
        if name not in names:
            missing.append(name)
        elif sha256(http(asset_url("gitee", tag, name))) != sha256(data):
            raise SyncError(f"Gitee asset differs: {name}; refusing to replace it automatically")
    return missing


def push_refs(tag, source):
    with tempfile.TemporaryDirectory(prefix="cy-gitee-auth-") as folder:
        askpass = Path(folder) / "askpass.py"
        askpass.write_text(
            "#!/usr/bin/env python3\nimport os, sys\n"
            "print('chenyipier' if 'Username' in sys.argv[1] else os.environ['GITEE_TOKEN'])\n",
            encoding="utf-8")
        askpass.chmod(0o700)
        env = dict(os.environ, GIT_ASKPASS=str(askpass), GIT_TERMINAL_PROMPT="0", LC_ALL="C")
        # No --force, --mirror, --prune or deletion refspec. The token is never
        # embedded in a URL, process argument, repository config or output file.
        git("-c", "credential.helper=", "push", GITEE_GIT,
            f"{source['main']}:refs/heads/main", f"refs/tags/{tag}:refs/tags/{tag}", env=env)


def mirror_body(release, tag):
    return ("此版本同步自 GitHub，下面的安装包直接存放在 Gitee。\n\n"
            f"上游发布页：https://github.com/{GITHUB_REPO}/releases/tag/{tag}\n\n"
            + (release.get("body") or ""))


def upload_asset(release_id, name, data):
    boundary = "cy-edit-" + uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
            "Content-Type: application/octet-stream\r\n\r\n").encode()
    body += data + f"\r\n--{boundary}--\r\n".encode()
    http(f"https://gitee.com/api/v5/repos/{GITEE_REPO}/releases/{int(release_id)}/attach_files",
         method="POST", token=os.environ["GITEE_TOKEN"], data=body,
         content_type=f"multipart/form-data; boundary={boundary}")


def sync(tag, mode):
    validate_tag(tag)
    release, files, hashes = read_source(tag)
    source = source_refs(tag)
    before = inspect_refs(tag, source)
    release_path = f"/repos/{GITEE_REPO}/releases"
    tagged_path = f"{release_path}/tags/{quote(tag, safe='')}"
    existing = api("gitee", tagged_path, missing_ok=True)
    missing = missing_assets(existing, files, tag)
    report = {"mode": mode, "tag": tag, "source": source, "gitee_refs_before": before,
              "assets_sha256": hashes, "assets_to_upload": missing,
              "github_release": f"https://github.com/{GITHUB_REPO}/releases/tag/{tag}",
              "gitee_release": f"https://gitee.com/{GITEE_REPO}/releases/tag/{tag}"}
    if mode == "check":
        return dict(report, status="checked_without_writes")
    if not os.environ.get("GITEE_TOKEN"):
        raise SyncError("Missing repository Actions secret GITEE_TOKEN; see docs/PUBLISHING.md")
    push_refs(tag, source)
    payload = {"tag_name": tag, "name": release.get("name") or tag,
               "body": mirror_body(release, tag), "prerelease": bool(release.get("prerelease"))}
    if existing:
        api("gitee", f"{release_path}/{int(existing['id'])}", method="PATCH", payload=payload)
        release_id = existing["id"]
    else:
        created = api("gitee", release_path, method="POST",
                      payload=dict(payload, target_commitish=source["release_commit"]))
        release_id = created["id"]
    for name in missing:
        upload_asset(release_id, name, files[name])
        print(f"Uploaded {name}", flush=True)
    final = api("gitee", tagged_path)
    if any(final.get(key) != value for key, value in payload.items()):
        raise SyncError("Gitee release metadata does not match the source")
    if missing_assets(final, files, tag):
        raise SyncError("Gitee is still missing release assets; rerun to resume")
    after = remote_refs(tag)
    if after.get("refs/heads/main") != source["main"] or after.get(f"refs/tags/{tag}") != source["tag_object"]:
        raise SyncError("Gitee refs changed during sync; inspect and rerun")
    return dict(report, status="synced_and_verified", gitee_refs_after=after)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--mode", choices=("sync", "check"), default="check")
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    try:
        report = sync(args.tag, args.mode)
    except (SyncError, ValueError, KeyError, TypeError) as error:
        message = str(error)
        for key in ("GITEE_TOKEN", "GH_TOKEN"):
            if os.environ.get(key):
                message = message.replace(os.environ[key], "[redacted]")
        report = {"status": "failed", "error": message}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = f"Gitee mirror: {report['status']}\n"
    if report["status"] == "failed":
        summary += report["error"] + "\n"
    else:
        summary += f"{report['tag']}: {report['gitee_release']}\n\n"
        summary += "\n".join(f"- `{name}`: `{digest}`" for name, digest in report["assets_sha256"].items()) + "\n"
    print(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as output:
            output.write(summary)
    return int(report["status"] == "failed")


if __name__ == "__main__":
    sys.exit(main())
