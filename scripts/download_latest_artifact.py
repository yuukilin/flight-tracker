"""
Download the latest Flight Price Scrape artifact into data/.

Usage:
  python scripts/download_latest_artifact.py
  GITHUB_TOKEN=... python scripts/download_latest_artifact.py
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path


ROOT = Path(__file__).parent.parent
DEFAULT_REPO = "yuukilin/flight-tracker"
DEFAULT_WORKFLOW = "Flight Price Scrape"
DEFAULT_ARTIFACT_PREFIX = "prices-db-"


def api_get(url, token=None, accept="application/vnd.github+json"):
    headers = {
        "Accept": accept,
        "User-Agent": "flight-tracker-artifact-downloader",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
        content_type = resp.headers.get("Content-Type", "")
    if "application/json" in content_type or data[:1] in (b"{", b"["):
        return json.loads(data.decode("utf-8"))
    return data


def latest_successful_run(repo, workflow_name, token=None):
    url = f"https://api.github.com/repos/{repo}/actions/runs?per_page=30"
    runs = api_get(url, token).get("workflow_runs", [])
    for run in runs:
        if run.get("name") != workflow_name:
            continue
        if run.get("status") == "completed" and run.get("conclusion") == "success":
            return run
    raise RuntimeError(f"找不到成功完成的 {workflow_name} run")


def latest_artifact(repo, run_id, token=None, name_prefix=DEFAULT_ARTIFACT_PREFIX):
    url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/artifacts"
    artifacts = api_get(url, token).get("artifacts", [])
    artifacts = [
        a for a in artifacts
        if not a.get("expired") and a.get("name", "").startswith(name_prefix)
    ]
    if not artifacts:
        raise RuntimeError(f"run {run_id} 沒有可下載的 {name_prefix} artifact")
    artifacts.sort(key=lambda a: a.get("created_at", ""), reverse=True)
    return artifacts[0]


def download_zip(download_url, out_path, token=None):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "flight-tracker-artifact-downloader",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(download_url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        out_path.write_bytes(resp.read())


def extract_data(zip_path, target_dir):
    target_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = Path(info.filename).name
            if name.startswith("."):
                continue
            dest = target_dir / name
            with zf.open(info) as src, open(dest, "wb") as dst:
                shutil.copyfileobj(src, dst)


def write_outputs(outputs, output_path=None):
    output_path = output_path or os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as f:
        for key, value in outputs.items():
            f.write(f"{key}={value}\n")


def main():
    parser = argparse.ArgumentParser(description="下載最新 flight-tracker Actions artifact 到 data/")
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--workflow", default=DEFAULT_WORKFLOW)
    parser.add_argument("--out", default=str(ROOT / "data"))
    parser.add_argument("--run-id", type=int, default=None)
    parser.add_argument("--artifact-prefix", default=DEFAULT_ARTIFACT_PREFIX)
    parser.add_argument("--resolve-only", action="store_true")
    parser.add_argument("--github-output", nargs="?", const="", default=None)
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    run = None
    artifact = None
    try:
        if args.run_id:
            run = {"id": args.run_id, "html_url": f"https://github.com/{args.repo}/actions/runs/{args.run_id}"}
        else:
            run = latest_successful_run(args.repo, args.workflow, token)
        artifact = latest_artifact(args.repo, run["id"], token, args.artifact_prefix)

        outputs = {
            "run_id": run["id"],
            "run_url": run["html_url"],
            "artifact_id": artifact["id"],
            "artifact_name": artifact["name"],
        }
        if args.github_output is not None:
            write_outputs(outputs, args.github_output or None)

        if args.resolve_only:
            print(f"latest_run_id={run['id']}")
            print(f"latest_run_url={run['html_url']}")
            print(f"artifact_id={artifact['id']}")
            print(f"artifact_name={artifact['name']}")
            return

        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "artifact.zip"
            download_zip(artifact["archive_download_url"], zip_path, token)
            out_dir = Path(args.out)
            extract_data(zip_path, out_dir)

        if not (out_dir / "prices.db").exists():
            raise RuntimeError(f"artifact {artifact['name']} 未包含 prices.db")

        print(f"已下載：{artifact['name']}")
        print(f"來源：{run['html_url']}")
        print(f"輸出：{Path(args.out).resolve()}")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            print("GitHub 拒絕下載 artifact。請設定 GITHUB_TOKEN 或 GH_TOKEN 後重試。", file=sys.stderr)
            if run:
                print(f"也可手動下載：{run['html_url']}", file=sys.stderr)
            if artifact:
                print(f"artifact：{artifact.get('name', 'unknown')}", file=sys.stderr)
        else:
            print(f"下載失敗：HTTP {e.code}", file=sys.stderr)
        raise SystemExit(1)
    except Exception as e:
        print(f"下載失敗：{e}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
