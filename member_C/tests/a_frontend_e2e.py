"""End-to-end contract validation for A's deployed Presentation Service.

Exercises the full chain  A → B → C → SubmissionEventFn → ProcessingFn →
ResultUpdateFn → C  by submitting through A's public HTTP endpoint
(``POST /submit``, multipart/form-data — exactly what the browser does).

For every case we:

1. Submit via A.
2. Extract the new submission id from A's 302 ``Location`` header.
3. Read the final record **directly from C Data Service** (bypassing A / B)
   to compare what actually landed in the database versus what we sent.
4. Poll until Processing Function flips the status out of ``PENDING``.
5. Assert against the contract (`API_CONTRACT.md` §3.3/§3.4, and
   `API_CONTRACT_APPEND.md` §Append-3/§Append-5/§Append-7/§Append-10/§Append-13).

Endpoints come from ``config.md``.  The script produces a PASS/FAIL summary
table at the end and exits with a non-zero code if any case fails.

Usage:
    python tests/a_frontend_e2e.py

Requires: ``requests`` (``pip install requests``).
"""

from __future__ import annotations

import io
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

import requests

# ---------------------------------------------------------------------------
# Endpoints (from config.md)
# ---------------------------------------------------------------------------

A_URL = "REPLACED_FOR_SAFETY_AND_ANONYMITY"
B_URL = "REPLACED_FOR_SAFETY_AND_ANONYMITY"
C_URL = "REPLACED_FOR_SAFETY_AND_ANONYMITY"

HTTP_TIMEOUT = 30
POLL_INTERVAL_S = 1.0
POLL_MAX_S = 15.0  # Processing Fn should finish well within this window.

# ---------------------------------------------------------------------------
# Minimal valid 1x1 images — embedded to keep the script self-contained.
# ---------------------------------------------------------------------------

# 67-byte valid PNG (1x1, black pixel).
MINIMAL_PNG: bytes = bytes(
    [
        0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A,
        0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52,
        0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,
        0x08, 0x02, 0x00, 0x00, 0x00, 0x90, 0x77, 0x53,
        0xDE, 0x00, 0x00, 0x00, 0x0C, 0x49, 0x44, 0x41,
        0x54, 0x78, 0x9C, 0x63, 0xF8, 0x0F, 0x00, 0x00,
        0x01, 0x01, 0x00, 0x05, 0x18, 0xD8, 0x4E, 0x00,
        0x00, 0x00, 0x00, 0x49, 0x45, 0x4E, 0x44, 0xAE,
        0x42, 0x60, 0x82,
    ]
)

# 150-byte valid JPEG (1x1).
MINIMAL_JPEG: bytes = bytes(
    [
        0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46, 0x49, 0x46, 0x00, 0x01,
        0x01, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00, 0xFF, 0xDB, 0x00, 0x43,
        0x00, 0x08, 0x06, 0x06, 0x07, 0x06, 0x05, 0x08, 0x07, 0x07, 0x07, 0x09,
        0x09, 0x08, 0x0A, 0x0C, 0x14, 0x0D, 0x0C, 0x0B, 0x0B, 0x0C, 0x19, 0x12,
        0x13, 0x0F, 0x14, 0x1D, 0x1A, 0x1F, 0x1E, 0x1D, 0x1A, 0x1C, 0x1C, 0x20,
        0x24, 0x2E, 0x27, 0x20, 0x22, 0x2C, 0x23, 0x1C, 0x1C, 0x28, 0x37, 0x29,
        0x2C, 0x2F, 0x1C, 0x00, 0xFF, 0xC0, 0x00, 0x0B, 0x08, 0x00, 0x01, 0x00,
        0x01, 0x01, 0x01, 0x11, 0x00, 0xFF, 0xC4, 0x00, 0x14, 0x00, 0x01, 0x00,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00, 0x0A, 0xFF, 0xC4, 0x00, 0x14, 0x10, 0x01, 0x00,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00, 0xFF, 0xDA, 0x00, 0x08, 0x01, 0x01, 0x00, 0x00,
        0x3F, 0x00, 0x54, 0xBF, 0xFF, 0xD9,
    ]
)

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

SUBMIT_URL = f"{A_URL}/submit"


def submit_via_a(
    title: str | None,
    description: str | None,
    poster_filename: str | None,
    image_bytes: bytes | None = None,
    image_mime: str | None = None,
    image_fname: str = "poster.bin",
) -> requests.Response:
    """Send a multipart/form-data POST to A /submit just like the browser does.

    ``None`` values are skipped (i.e. the form field is omitted entirely),
    which is distinct from sending the empty string.
    """
    data: dict[str, str] = {}
    if title is not None:
        data["title"] = title
    if description is not None:
        data["description"] = description
    if poster_filename is not None:
        data["posterFilename"] = poster_filename

    files: dict[str, Any] = {}
    if image_bytes is not None:
        files["posterImage"] = (
            image_fname,
            io.BytesIO(image_bytes),
            image_mime or "application/octet-stream",
        )

    return requests.post(
        SUBMIT_URL,
        data=data,
        files=files or None,
        allow_redirects=False,
        timeout=HTTP_TIMEOUT,
    )


def extract_submission_id(resp: requests.Response) -> str | None:
    """Parse the UUID out of A's 302 ``Location: /submission/<uuid>`` header."""
    if resp.status_code != 302:
        return None
    loc = resp.headers.get("Location", "")
    marker = "/submission/"
    i = loc.rfind(marker)
    if i < 0:
        return None
    return loc[i + len(marker):].split("?", 1)[0].strip()


def get_record_from_c(submission_id: str) -> tuple[int, dict[str, Any]]:
    """Read the record directly from C, bypassing A and B for ground truth."""
    r = requests.get(f"{C_URL}/submissions/{submission_id}", timeout=HTTP_TIMEOUT)
    if r.status_code != 200:
        return r.status_code, {}
    return 200, r.json()


def wait_for_final_status(
    submission_id: str,
    max_s: float = POLL_MAX_S,
) -> tuple[int, dict[str, Any]]:
    """Poll C until ``status != 'PENDING'`` or timeout."""
    deadline = time.monotonic() + max_s
    last_code, last_record = 0, {}
    while time.monotonic() < deadline:
        last_code, last_record = get_record_from_c(submission_id)
        if last_code == 200 and last_record.get("status") != "PENDING":
            return last_code, last_record
        time.sleep(POLL_INTERVAL_S)
    return last_code, last_record


def get_poster_via_a(submission_id: str) -> requests.Response:
    return requests.get(
        f"{A_URL}/submission/{submission_id}/poster", timeout=HTTP_TIMEOUT
    )


def get_status_page(submission_id: str) -> requests.Response:
    return requests.get(f"{A_URL}/submission/{submission_id}", timeout=HTTP_TIMEOUT)


# ---------------------------------------------------------------------------
# Test case framework
# ---------------------------------------------------------------------------


@dataclass
class CaseResult:
    case_id: str
    title: str
    contract_ref: str
    passed: bool
    expected: str
    actual: str
    submission_id: str = ""
    notes: list[str] = field(default_factory=list)


RESULTS: list[CaseResult] = []


def record(
    case_id: str,
    title: str,
    contract_ref: str,
    passed: bool,
    expected: str,
    actual: str,
    submission_id: str = "",
    notes: list[str] | None = None,
) -> None:
    RESULTS.append(
        CaseResult(
            case_id=case_id,
            title=title,
            contract_ref=contract_ref,
            passed=passed,
            expected=expected,
            actual=actual,
            submission_id=submission_id,
            notes=notes or [],
        )
    )
    tag = "PASS" if passed else "FAIL"
    print(f"[{tag}] {case_id:<6} {title}")
    if not passed:
        print(f"         expected: {expected}")
        print(f"         actual:   {actual}")
        for n in notes or []:
            print(f"         note:     {n}")


def check_submit_then_status(
    case_id: str,
    title: str,
    *,
    contract_ref: str,
    form_title: str | None,
    form_description: str | None,
    form_poster_filename: str | None,
    image_bytes: bytes | None = None,
    image_mime: str | None = None,
    image_fname: str = "poster.bin",
    expect_status: str,
    expect_note_contains: list[str] | None = None,
    expect_poster_mime: str | None = None,
    expect_poster_size: int | None = None,
    expect_title: str | None = None,
    expect_description: str | None = None,
    expect_poster_filename: str | None = None,
) -> str | None:
    """Submit through A and compare C's final record against expectations.

    Returns the submission id on successful submit (even if the final
    assertions fail), so downstream cases can reuse it (e.g. GET poster).
    """
    r = submit_via_a(
        title=form_title,
        description=form_description,
        poster_filename=form_poster_filename,
        image_bytes=image_bytes,
        image_mime=image_mime,
        image_fname=image_fname,
    )
    sid = extract_submission_id(r)
    if sid is None:
        record(
            case_id,
            title,
            contract_ref,
            False,
            "302 Found with Location: /submission/<uuid>",
            f"HTTP {r.status_code}, Location={r.headers.get('Location', '<none>')}",
        )
        return None

    code, rec = wait_for_final_status(sid)
    if code != 200:
        record(
            case_id,
            title,
            contract_ref,
            False,
            f"status={expect_status}",
            f"C GET returned HTTP {code}",
            submission_id=sid,
        )
        return sid

    actual_status = rec.get("status", "<missing>")
    actual_note = rec.get("note") or ""

    problems: list[str] = []
    if actual_status != expect_status:
        problems.append(f"status={actual_status!r} (want {expect_status!r})")
    for needle in expect_note_contains or []:
        if needle not in actual_note:
            problems.append(f"note missing {needle!r}; got {actual_note!r}")

    for field_name, expected_value in (
        ("title", expect_title),
        ("description", expect_description),
        ("posterFilename", expect_poster_filename),
    ):
        if expected_value is None:
            continue
        got = rec.get(field_name)
        if got != expected_value:
            problems.append(f"{field_name}={got!r} (want {expected_value!r})")

    if expect_poster_mime is not None:
        got_mime = rec.get("posterMimeType")
        if got_mime != expect_poster_mime:
            problems.append(
                f"posterMimeType={got_mime!r} (want {expect_poster_mime!r})"
            )
    if expect_poster_size is not None:
        got_size = rec.get("posterSize")
        if got_size != expect_poster_size:
            problems.append(
                f"posterSize={got_size!r} (want {expect_poster_size!r})"
            )

    # §Append-3.3: posterImage must never appear in GET response.
    if "posterImage" in rec:
        problems.append("posterImage leaked in GET response")

    record(
        case_id,
        title,
        contract_ref,
        passed=not problems,
        expected=(
            f"status={expect_status}"
            + (f", note contains {expect_note_contains}" if expect_note_contains else "")
        ),
        actual=(
            f"status={actual_status}"
            + (f", note={actual_note!r}" if actual_note else "")
            + (f", issues={problems}" if problems else "")
        ),
        submission_id=sid,
    )
    return sid


# ---------------------------------------------------------------------------
# Individual scenarios
# ---------------------------------------------------------------------------


def run_judgment_rule_cases() -> dict[str, str]:
    """Cover §Append-4 (1) INCOMPLETE, (2) NEEDS REVISION, (3) READY."""
    ids: dict[str, str] = {}

    # ---- INCOMPLETE: any required field missing ----
    # Rule (1): title/description/posterFilename any missing -> INCOMPLETE.
    check_submit_then_status(
        "J1",
        "Empty form (all three required fields missing)",
        contract_ref="§Append-4(1), base §3.4(1)",
        form_title="",
        form_description="",
        form_poster_filename="",
        expect_status="INCOMPLETE",
        expect_note_contains=["title", "description", "posterFilename"],
    )

    check_submit_then_status(
        "J2",
        "Missing only title",
        contract_ref="§Append-4(1)",
        form_title="",
        form_description="This description is definitely longer than thirty chars.",
        form_poster_filename="ok.jpg",
        expect_status="INCOMPLETE",
        expect_note_contains=["title"],
    )

    check_submit_then_status(
        "J3",
        "Missing only description",
        contract_ref="§Append-4(1)",
        form_title="Hello",
        form_description="",
        form_poster_filename="ok.jpg",
        expect_status="INCOMPLETE",
        expect_note_contains=["description"],
    )

    check_submit_then_status(
        "J4",
        "Missing only posterFilename",
        contract_ref="§Append-4(1)",
        form_title="Hello",
        form_description="This description is definitely longer than thirty chars.",
        form_poster_filename="",
        expect_status="INCOMPLETE",
        expect_note_contains=["posterFilename"],
    )

    # ---- NEEDS REVISION: description too short OR bad extension ----
    check_submit_then_status(
        "J5",
        "Description < 30 chars",
        contract_ref="§Append-4(2), base §3.4(2)",
        form_title="Hello",
        form_description="Too short.",
        form_poster_filename="ok.jpg",
        expect_status="NEEDS REVISION",
    )

    # Base §3.4 table (line 173) defines "missing" as:
    #   not present, OR null, OR strip() == "".
    # So a 50-space description, despite raw length > 30, is treated as
    # missing → rule (1) INCOMPLETE, not rule (2) NEEDS REVISION.
    check_submit_then_status(
        "J6",
        "Whitespace-only description (strip() == '') → missing per base §3.4",
        contract_ref="base §3.4 (missing definition)",
        form_title="Hello",
        form_description="            " + " " * 30,
        form_poster_filename="ok.jpg",
        expect_status="INCOMPLETE",
        expect_note_contains=["description"],
    )

    check_submit_then_status(
        "J7",
        "Bad poster extension (.txt)",
        contract_ref="§Append-4(2)",
        form_title="Hello",
        form_description="This description is definitely longer than thirty chars.",
        form_poster_filename="ok.txt",
        expect_status="NEEDS REVISION",
    )

    # ---- READY: all three good, no image ----
    long_desc = (
        "This description is definitely longer than thirty characters total."
    )
    for ext in (".jpg", ".jpeg", ".png"):
        cid = f"J8{ext}"
        sid = check_submit_then_status(
            cid,
            f"READY, no image, filename*{ext}",
            contract_ref="§Append-4(3)",
            form_title="Spring Launch",
            form_description=long_desc,
            form_poster_filename=f"launch-poster{ext}",
            expect_status="READY",
            expect_title="Spring Launch",
            expect_description=long_desc,
            expect_poster_filename=f"launch-poster{ext}",
            expect_poster_mime=None,  # no image uploaded
            expect_poster_size=None,
        )
        if sid and ext == ".jpg":
            ids["ready_no_image"] = sid

    return ids


def run_image_cases() -> dict[str, str]:
    """Cover §Append-3 (posterImage/posterMimeType/posterSize) happy paths."""
    ids: dict[str, str] = {}
    long_desc = (
        "This description is definitely longer than thirty characters total."
    )

    sid = check_submit_then_status(
        "I1",
        "JPEG upload, filename .jpg",
        contract_ref="§Append-3, §Append-13.1 row 3",
        form_title="Probe JPEG",
        form_description=long_desc,
        form_poster_filename="probe.jpg",
        image_bytes=MINIMAL_JPEG,
        image_mime="image/jpeg",
        image_fname="probe.jpg",
        expect_status="READY",
        expect_poster_mime="image/jpeg",
        expect_poster_size=len(MINIMAL_JPEG),
        expect_title="Probe JPEG",
    )
    if sid:
        ids["ready_jpeg"] = sid

    sid = check_submit_then_status(
        "I2",
        "PNG upload, filename .png",
        contract_ref="§Append-3, §Append-13.1 row 3",
        form_title="Probe PNG",
        form_description=long_desc,
        form_poster_filename="probe.png",
        image_bytes=MINIMAL_PNG,
        image_mime="image/png",
        image_fname="probe.png",
        expect_status="READY",
        expect_poster_mime="image/png",
        expect_poster_size=len(MINIMAL_PNG),
    )
    if sid:
        ids["ready_png"] = sid

    return ids


def run_poster_endpoint_cases(ids: dict[str, str]) -> None:
    """Cover §Append-5.4 / §Append-7.5: GET /submission/<id>/poster through A."""

    def _poster_case(
        case_id: str,
        case_title: str,
        sid: str,
        expect_code: int,
        expect_ct_startswith: str | None = None,
        expect_bytes_len: int | None = None,
    ) -> None:
        r = get_poster_via_a(sid)
        problems: list[str] = []
        if r.status_code != expect_code:
            problems.append(f"HTTP {r.status_code} (want {expect_code})")
        if expect_ct_startswith is not None:
            ct = r.headers.get("Content-Type", "")
            if not ct.lower().startswith(expect_ct_startswith):
                problems.append(
                    f"Content-Type={ct!r} (want startswith {expect_ct_startswith!r})"
                )
        if expect_bytes_len is not None and len(r.content) != expect_bytes_len:
            problems.append(
                f"body length={len(r.content)} (want {expect_bytes_len})"
            )
        record(
            case_id,
            case_title,
            "§Append-5.4 / §Append-7.5",
            passed=not problems,
            expected=(
                f"HTTP {expect_code}"
                + (f", Content-Type~{expect_ct_startswith}" if expect_ct_startswith else "")
                + (f", body len={expect_bytes_len}" if expect_bytes_len is not None else "")
            ),
            actual=(
                f"HTTP {r.status_code}, CT={r.headers.get('Content-Type','<none>')}, "
                f"body len={len(r.content)}"
                + (f", issues={problems}" if problems else "")
            ),
            submission_id=sid,
        )

    if "ready_jpeg" in ids:
        _poster_case(
            "P1",
            "GET /poster on JPEG record → 200 image/jpeg",
            ids["ready_jpeg"],
            expect_code=200,
            expect_ct_startswith="image/jpeg",
            expect_bytes_len=len(MINIMAL_JPEG),
        )
    if "ready_png" in ids:
        _poster_case(
            "P2",
            "GET /poster on PNG record → 200 image/png",
            ids["ready_png"],
            expect_code=200,
            expect_ct_startswith="image/png",
            expect_bytes_len=len(MINIMAL_PNG),
        )
    if "ready_no_image" in ids:
        _poster_case(
            "P3",
            "GET /poster on image-less record → 404",
            ids["ready_no_image"],
            expect_code=404,
        )

    random_id = str(uuid.uuid4())
    _poster_case(
        "P4",
        "GET /poster on random UUID → 404",
        random_id,
        expect_code=404,
    )


def run_404_status_page() -> None:
    random_id = str(uuid.uuid4())
    r = get_status_page(random_id)
    passed = r.status_code == 404
    record(
        "S1",
        "GET /submission/<random-uuid> → 404",
        contract_ref="base §4.2.3",
        passed=passed,
        expected="HTTP 404",
        actual=f"HTTP {r.status_code}",
        submission_id=random_id,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    print(f"A presentation = {A_URL}")
    print(f"B workflow     = {B_URL}")
    print(f"C data         = {C_URL}")
    print(f"MINIMAL_PNG  len = {len(MINIMAL_PNG)} bytes")
    print(f"MINIMAL_JPEG len = {len(MINIMAL_JPEG)} bytes")
    print()

    print("=== Section J — judgment rules (§Append-4) ===")
    ids = run_judgment_rule_cases()

    print()
    print("=== Section I — image upload round-trip (§Append-3) ===")
    ids.update(run_image_cases())

    print()
    print("=== Section P — poster endpoint (§Append-5.4 / §Append-7.5) ===")
    run_poster_endpoint_cases(ids)

    print()
    print("=== Section S — status page 404 ===")
    run_404_status_page()

    # ---- Summary ----
    print()
    print("================ SUMMARY ================")
    width_case = max(len(r.case_id) for r in RESULTS) if RESULTS else 4
    width_title = max(len(r.title) for r in RESULTS) if RESULTS else 5
    header = (
        f"{'CASE':<{width_case}}  "
        f"{'RESULT':<6}  "
        f"{'REF':<32}  "
        f"TITLE"
    )
    print(header)
    print("-" * len(header))
    passed = failed = 0
    for r in RESULTS:
        tag = "PASS" if r.passed else "FAIL"
        if r.passed:
            passed += 1
        else:
            failed += 1
        print(
            f"{r.case_id:<{width_case}}  "
            f"{tag:<6}  "
            f"{r.contract_ref:<32}  "
            f"{r.title}"
        )
    print("-" * len(header))
    print(f"total={passed + failed}  pass={passed}  fail={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
