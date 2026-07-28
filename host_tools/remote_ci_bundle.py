#!/usr/bin/env python3
"""Bridge GitLab API job identities to the remote execution verifier."""
from __future__ import annotations

from pathlib import Path
from typing import Mapping
from urllib import error as urlerror, parse as urlparse
from urllib import request as urlrequest

from remote_ci_archive import RemoteJobExpectation, verify_downloaded_job_evidence
from remote_ci_evidence import RemoteCIEvidenceError
from strict_json import strict_json_loads


REMOTE_RESPONSE_LIMIT = 256 * 1024 * 1024


class RemoteCIBindingError(ValueError):
    pass


class GitLabRedirectHandler(urlrequest.HTTPRedirectHandler):
    def __init__(self, allow_cross_origin: bool):
        super().__init__()
        self.allow_cross_origin = allow_cross_origin

    @staticmethod
    def origin(url: str) -> tuple[str, str | None, int | None]:
        parsed = urlparse.urlsplit(url)
        port = parsed.port or ({"http": 80, "https": 443}.get(parsed.scheme))
        return parsed.scheme, parsed.hostname, port

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is None or self.origin(req.full_url) == self.origin(newurl):
            return redirected
        old_host = urlparse.urlsplit(req.full_url).hostname
        new_target = urlparse.urlsplit(newurl)
        loopback = old_host in {"127.0.0.1", "::1", "localhost"} and (
            new_target.hostname in {"127.0.0.1", "::1", "localhost"}
        )
        if not self.allow_cross_origin or (new_target.scheme != "https" and not loopback):
            raise urlerror.HTTPError(
                req.full_url, code, "cross-origin redirect rejected", headers, fp
            )
        for name, _ in list(redirected.header_items()):
            if name.lower() in {"private-token", "authorization"}:
                redirected.remove_header(name)
        return redirected


def gitlab_fetch(
    base_url: str,
    endpoint: str,
    token: str,
    timeout: float,
    allow_cross_origin: bool = False,
) -> tuple[bytes, object]:
    request = urlrequest.Request(
        f"{base_url.rstrip('/')}/api/v4/{endpoint}",
        headers={
            "PRIVATE-TOKEN": token,
            "User-Agent": "agentos-evidence-collector/2",
        },
    )
    try:
        opener = urlrequest.build_opener(GitLabRedirectHandler(allow_cross_origin))
        with opener.open(request, timeout=timeout) as response:
            data = response.read(REMOTE_RESPONSE_LIMIT + 1)
            headers = response.headers
    except (urlerror.HTTPError, urlerror.URLError, TimeoutError) as error:
        raise RemoteCIBindingError(f"GitLab API request failed: {endpoint}") from error
    if not data or len(data) > REMOTE_RESPONSE_LIMIT:
        raise RemoteCIBindingError(
            f"GitLab API response is empty or too large: {endpoint}"
        )
    return data, headers


def decode_gitlab_json(data: bytes, label: str) -> object:
    try:
        return strict_json_loads(data)
    except (UnicodeDecodeError, ValueError) as error:
        raise RemoteCIBindingError(f"GitLab API JSON is invalid: {label}") from error


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise RemoteCIBindingError(f"{label} is not an object")
    return value


def verify_job_execution(
    trace: Path,
    archive: Path,
    project: object,
    pipeline: object,
    job: object,
    runner_tag: str,
    repo_root: Path,
) -> dict[str, object]:
    """Verify one downloaded job against its live GitLab API identity."""
    project = _mapping(project, "GitLab project")
    pipeline = _mapping(pipeline, "GitLab pipeline")
    job = _mapping(job, "GitLab job")
    runner = _mapping(job.get("runner"), "GitLab runner")
    try:
        expectation = RemoteJobExpectation(
            project_id=project["id"],
            project_path=project["path_with_namespace"],
            pipeline_id=pipeline["id"],
            pipeline_source=pipeline["source"],
            job_id=job["id"],
            job_name=job["name"],
            commit=pipeline["sha"],
            ref=pipeline["ref"],
            runner_id=runner["id"],
            runner_tag=runner_tag,
        )
    except KeyError as error:
        raise RemoteCIBindingError(
            f"GitLab API identity is incomplete: {error.args[0]}"
        ) from error
    try:
        result = verify_downloaded_job_evidence(
            Path(trace), Path(archive), expectation, Path(repo_root)
        )
    except (RemoteCIEvidenceError, OSError) as error:
        raise RemoteCIBindingError(
            f"remote CI execution attestation is invalid for {job.get('name')}: {error}"
        ) from error
    expected = {
        "status": "execution-attested",
        "job": expectation.job_name,
        "commit": expectation.commit,
        "job_id": expectation.job_id,
    }
    if (
        set(result)
        != {*expected, "attestation_sha256", "artifact_count"}
        or any(result.get(key) != value for key, value in expected.items())
        or not isinstance(result.get("attestation_sha256"), str)
        or len(result["attestation_sha256"]) != 64
        or not isinstance(result.get("artifact_count"), int)
        or isinstance(result.get("artifact_count"), bool)
        or result["artifact_count"] <= 0
    ):
        raise RemoteCIBindingError("remote CI verifier returned an invalid result")
    return result


__all__ = [
    "RemoteCIBindingError",
    "decode_gitlab_json",
    "gitlab_fetch",
    "verify_job_execution",
]
