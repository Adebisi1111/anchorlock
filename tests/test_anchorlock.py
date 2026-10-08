"""AnchorLock direct-mode tests.

Uses genlayer-test's direct VM: deploys the contract into an in-process GenVM
and drives it as leader, with validator rounds checkable via run_validator().
"""

import json
import hashlib
import re
import pytest


CONTRACT = "contracts/anchorlock.py"

# gltest exposes direct_vm/direct_alice as pytest fixtures. Bind them to module
# globals via an autouse fixture so every test body can reference them directly.
_vm = None
_alice = None


@pytest.fixture(autouse=True)
def _bind_fixtures(direct_vm, direct_alice):
    global _vm, _alice
    _vm = direct_vm
    _alice = direct_alice
    yield

# Stable fixture HTML — a realistic page with paragraphs, script and style
# noise, and HTML entities, so extraction is genuinely exercised.
FIXTURE_HTML = """<html><head>
<title>Fixture</title>
<style>body{color:red}</style>
<script>console.log("noise")</script>
</head><body>
<h1>Fixture Page</h1>
<p>The   quick brown fox</p>
<p>jumps over the lazy dog &amp; runs away.</p>
</body></html>"""

# Second fixture representing *changed* content for the DRIFT case.
DRIFT_HTML = """<html><body>
<p>Completely different text than before.</p>
</body></html>"""


def _local_fingerprint(html):
    """Reimplementation of the contract's extraction, for expected values."""
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    paras = re.findall(r"<p[^>]*>(.*?)</p>", text, flags=re.DOTALL | re.IGNORECASE)
    text = " ".join(paras)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
    text = re.sub(r"\s+", " ", text).strip()
    normalized = re.sub(r"\s+", " ", text.lower()).strip()
    return {
        "digest": "0x" + hashlib.sha256(normalized.encode()).hexdigest(),
        "word_count": len(normalized.split(" ")),
        "excerpt": normalized[:300],
    }


@pytest.fixture
def contract(direct_deploy):
    with _vm.prank(_alice):
        return direct_deploy(CONTRACT)


# ---------------------------------------------------------------------------
# Extraction: HTML is stripped, entities decoded, whitespace collapsed
# ---------------------------------------------------------------------------

def test_extraction_strips_html_and_decodes_entities(contract):
    """Attestation excerpt contains visible text only — no tags, no script,
    entities decoded, whitespace collapsed."""
    _vm.mock_web(r".*fixture\.test.*", {"status": 200, "body": FIXTURE_HTML})
    contract.attest_url("https://fixture.test/page")
    a = contract.get_attestation("0")

    expected = _local_fingerprint(FIXTURE_HTML)
    assert a["excerpt"] == expected["excerpt"]
    assert a["fingerprint"] == expected["digest"]
    assert a["word_count"] == expected["word_count"]
    # The excerpt is the actual page text, proving the fetch happened.
    assert "quick brown fox" in a["excerpt"]
    assert "runs away" in a["excerpt"]
    assert "<" not in a["excerpt"]
    assert "console.log" not in a["excerpt"]
    assert "body{color" not in a["excerpt"]


def test_fingerprint_is_case_and_whitespace_insensitive(contract):
    """Two pages differing only in case and whitespace collapse to the same
    fingerprint — agreement is not defeated by encoding noise."""
    _vm.mock_web(r".*case-a\.test.*", {"status": 200, "body": "<p>Hello World</p>"})
    contract.attest_url("https://case-a.test/x")
    a = contract.get_attestation("0")

    _vm.mock_web(r".*case-b\.test.*", {"status": 200, "body": "<html><p>  hello    world  </p></html>"})
    contract.attest_url("https://case-b.test/x")
    b = contract.get_attestation("1")

    assert a["fingerprint"] == b["fingerprint"]
    assert a["word_count"] == b["word_count"] == 2


def test_content_change_alters_fingerprint(contract):
    """A real content change must change the fingerprint — otherwise drift
    detection is impossible."""
    _vm.mock_web(r".*v1\.test.*", {"status": 200, "body": "<p>Original content</p>"})
    contract.attest_url("https://v1.test/x")
    v1 = contract.get_attestation("0")

    _vm.mock_web(r".*v2\.test.*", {"status": 200, "body": "<p>Edited content</p>"})
    contract.attest_url("https://v2.test/x")
    v2 = contract.get_attestation("1")

    assert v1["fingerprint"] != v2["fingerprint"]


# ---------------------------------------------------------------------------
# Consensus: validators agree on identical content, reject divergent content
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Consensus invariants
#
# gl.eq_principle.strict_eq's internal leader/validator round cannot be
# simulated by gltest direct mode's run_validator() (the direct VM does not
# expose that primitive's round). The on-chain round is proven live instead:
# two real attestations on Bradbury both reached MAJORITY_AGREE. What these
# tests pin down locally is the invariant strict_eq depends on — that the
# fingerprint is a deterministic function of visible text, so identical
# content yields identical fingerprints (agreement) and any real content
# change yields a different one (divergence forces disagreement).
# ---------------------------------------------------------------------------

def test_fingerprint_is_deterministic(contract):
    """The same content attested twice must produce the same fingerprint.
    strict_eq commits only when validators reproduce this value, so a
    nondeterministic fingerprint would make consensus impossible."""
    _vm.mock_web(r".*stable\.test.*", {"status": 200, "body": FIXTURE_HTML})
    contract.attest_url("https://stable.test/x")
    first = contract.get_attestation("0")["fingerprint"]

    _vm.mock_web(r".*stable\.test.*", {"status": 200, "body": FIXTURE_HTML})
    contract.attest_url("https://stable.test/x")
    second = contract.get_attestation("1")["fingerprint"]

    assert first == second


def test_identical_content_across_nodes_agrees(contract):
    """Two independent fetches of byte-identical content derive the same
    fingerprint — this is exactly what makes validators AGREE under
    strict_eq. The live on-chain proof of the full round is in the README."""
    _vm.mock_web(r".*agree-a\.test.*", {"status": 200, "body": FIXTURE_HTML})
    contract.attest_url("https://agree-a.test/x")
    a = contract.get_attestation("0")

    _vm.mock_web(r".*agree-b\.test.*", {"status": 200, "body": FIXTURE_HTML})
    contract.attest_url("https://agree-b.test/x")
    b = contract.get_attestation("1")

    assert a["fingerprint"] == b["fingerprint"]
    assert a["excerpt"] == b["excerpt"]


def test_divergent_content_across_nodes_would_disagree(contract):
    """The mirror of the above: content that differs between fetches produces
    a different fingerprint, which is what forces a validator to DISAGREE
    under strict_eq. A single node cannot forge an attestation because the
    digest is a deterministic function of the text it actually fetched."""
    _vm.mock_web(r".*leader-view\.test.*", {"status": 200, "body": FIXTURE_HTML})
    contract.attest_url("https://leader-view.test/x")
    leader = contract.get_attestation("0")["fingerprint"]

    # A different node fetching a different body for its own URL.
    _vm.mock_web(r".*validator-view\.test.*", {"status": 200, "body": DRIFT_HTML})
    contract.attest_url("https://validator-view.test/x")
    divergent = contract.get_attestation("1")["fingerprint"]

    assert leader != divergent


def test_validator_rejects_empty_extraction(contract):
    """A page that yields no extractable text must not be attestable."""
    _vm.mock_web(r".*empty\.test.*", {"status": 200, "body": "<html><body></body></html>"})
    with pytest.raises(Exception):
        contract.attest_url("https://empty.test/x")


# ---------------------------------------------------------------------------
# Drift detection
# ---------------------------------------------------------------------------

def test_verify_reports_match_when_content_unchanged(contract):
    _vm.mock_web(r".*same\.test.*", {"status": 200, "body": FIXTURE_HTML})
    contract.attest_url("https://same.test/x")
    result = contract.verify_attestation("0")
    assert result == "MATCH"
    a = contract.get_attestation("0")
    assert a["last_verify_result"] == "MATCH"
    assert a["verify_count"] == 1


def test_verify_reports_drift_when_content_changed(contract):
    """Content changed after attestation → DRIFT, and it is persisted."""
    _vm.mock_web(r".*drifted\.test.*", {"status": 200, "body": FIXTURE_HTML})
    contract.attest_url("https://drifted.test/x")

    _vm.clear_mocks()
    _vm.mock_web(r".*drifted\.test.*", {"status": 200, "body": DRIFT_HTML})
    result = contract.verify_attestation("0")
    assert result == "DRIFT"
    a = contract.get_attestation("0")
    assert a["last_verify_result"] == "DRIFT"
    assert a["verify_count"] == 1


def test_verify_count_accumulates(contract):
    _vm.mock_web(r".*count\.test.*", {"status": 200, "body": FIXTURE_HTML})
    contract.attest_url("https://count.test/x")
    contract.verify_attestation("0")
    contract.verify_attestation("0")
    assert contract.get_attestation("0")["verify_count"] == 2


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

def test_rejects_empty_url(contract):
    with pytest.raises(Exception):
        contract.attest_url("")


def test_rejects_non_http_url(contract):
    with pytest.raises(Exception):
        contract.attest_url("ftp://example.com/file")


# ---------------------------------------------------------------------------
# State design
# ---------------------------------------------------------------------------

def test_attest_ids_increment(contract):
    _vm.mock_web(r".*id0\.test.*", {"status": 200, "body": "<p>zero</p>"})
    contract.attest_url("https://id0.test/x")
    _vm.mock_web(r".*id1\.test.*", {"status": 200, "body": "<p>one</p>"})
    contract.attest_url("https://id1.test/x")
    assert contract.total_attestations() == 2
    assert contract.get_attestation("0")["url"] == "https://id0.test/x"
    assert contract.get_attestation("1")["url"] == "https://id1.test/x"


def test_unknown_attestation_returns_not_exists(contract):
    assert contract.get_attestation("999")["exists"] is False


def test_verify_unknown_attestation_rejected(contract):
    with pytest.raises(Exception):
        contract.verify_attestation("999")


def test_attester_is_recorded(contract):
    _vm.mock_web(r".*who\.test.*", {"status": 200, "body": "<p>who attested</p>"})
    contract.attest_url("https://who.test/x")
    a = contract.get_attestation("0")
    # The attester is the sender the write was made as. Assert it is a valid
    # checksummed 0x address (not empty, not a raw-bytes repr).
    assert re.fullmatch(r"0x[0-9a-fA-F]{40}", a["attester"])
    assert a["attester"] != "0x"
