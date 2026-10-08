# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

"""AnchorLock — on-chain web content attestation.

Primitive for proving what a URL said at a point in time. Anyone attests a
URL: validators independently fetch the page and extract its visible text.
The attestation commits only when a majority derive an identical content
fingerprint — a compact, stable signature of the text, not the raw body.

Trust model: no single node is trusted. An attestation exists only because
multiple independent validators reproduced the same fingerprint from the
same URL. Divergent extraction fails the round.

Performance: the fingerprint is a short, normalized signature (word count,
a rolling content digest, and the opening excerpt) rather than a hash of the
full text. This keeps the consensus comparison cheap and robust to trivial
whitespace/encoding variance between fetches, so the round fits a block
window even for large pages.
"""

import json
import hashlib
import re
from datetime import datetime, timezone
from dataclasses import dataclass

from genlayer import *


# ---------------------------------------------------------------------------
# Storage model
# ---------------------------------------------------------------------------


@allow_storage
@dataclass
class Attestation:
    """A committed attestation of a URL's content at a point in time."""

    attest_id: str
    url: str
    fingerprint: str
    excerpt: str
    word_count: u256
    attester: str
    created_at: u256
    verify_count: u256
    last_verify_result: str


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _extract_text(raw_html: str) -> str:
    """Strip HTML to visible text. Deterministic: same input -> same output."""
    text = re.sub(r"<script[^>]*>.*?</script>", "", raw_html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", text, flags=re.DOTALL | re.IGNORECASE)
    if paragraphs:
        text = " ".join(paragraphs)
    else:
        text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _fingerprint(text: str) -> dict:
    """Derive a compact, stable signature of extracted text.

    Components:
      - word_count: number of whitespace-separated words
      - digest: SHA-256 over the normalized lowercase text
      - excerpt: the first 300 normalized characters

    Normalization (lowercase, single-spaced) makes the digest robust to
    trivial case/whitespace variance between independent validator fetches,
    so genuine agreement is not defeated by encoding noise while a real
    content change still alters the digest.
    """
    normalized = re.sub(r"\s+", " ", text.lower()).strip()
    words = normalized.split(" ")
    digest = "0x" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return {
        "digest": digest,
        "word_count": len(words),
        "excerpt": normalized[:300],
    }


def _fetch_and_fingerprint(url: str) -> dict:
    """Fetch a URL and derive its content fingerprint.

    Runs independently on every validator. Deterministic given the page's
    visible text.
    """
    response = gl.nondet.web.request(url, method="GET")
    body = response.body if hasattr(response, "body") else (
        response.get("body", "") if isinstance(response, dict) else str(response)
    )
    raw_html = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body)
    text = _extract_text(raw_html)
    fp = _fingerprint(text)
    return {
        "text": text,
        "digest": fp["digest"],
        "word_count": fp["word_count"],
        "excerpt": fp["excerpt"],
    }


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------


class AnchorLock(gl.Contract):
    """On-chain web content attestation with validator-verified extraction."""

    attestations: TreeMap[str, Attestation]
    attest_count: u256 = u256(0)

    def __init__(self):
        self.attest_count = u256(0)

    def _now(self) -> int:
        return int(datetime.now(timezone.utc).timestamp())

    @gl.public.write
    def attest_url(self, url: str) -> str:
        """Attest a URL's content. Returns the attestation ID.

        Validators independently fetch the page and derive its content
        fingerprint. The attestation commits only if a majority produce the
        identical fingerprint digest. Divergent extraction fails the round.
        """
        if not url or not url.strip():
            raise gl.vm.UserError("URL cannot be empty")
        if not url.startswith(("http://", "https://")):
            raise gl.vm.UserError("URL must start with http:// or https://")

        def leader_fn() -> dict:
            return _fetch_and_fingerprint(url)

        def validator_fn(leader_res) -> bool:
            if not isinstance(leader_res, gl.vm.Return):
                return False
            mine = _fetch_and_fingerprint(url)
            return mine.get("digest") == leader_res.calldata.get("digest")

        result = gl.vm.run_nondet(leader_fn, validator_fn)

        result_data = result.calldata if hasattr(result, "calldata") else result
        digest = result_data.get("digest", "")
        excerpt = result_data.get("excerpt", "")
        word_count = result_data.get("word_count", 0)

        if not digest:
            raise gl.vm.UserError("Consensus produced no content fingerprint")
        if not excerpt:
            raise gl.vm.UserError("Extracted no text content from URL")

        attest_id = str(int(self.attest_count))
        self.attest_count += u256(1)

        self.attestations[attest_id] = Attestation(
            attest_id=attest_id,
            url=url,
            fingerprint=digest,
            excerpt=excerpt[:500],
            word_count=int(word_count),
            attester=str(gl.message.sender_address),
            created_at=u256(self._now()),
            verify_count=u256(0),
            last_verify_result="",
        )
        return attest_id

    @gl.public.write
    def verify_attestation(self, attest_id: str) -> str:
        """Re-fetch a URL and compare against the stored fingerprint.

        Returns MATCH (content unchanged since attestation), DRIFT (content
        changed), or INCONCLUSIVE (extraction could not reach consensus).
        """
        attest_id = str(attest_id)
        attest = self.attestations.get(attest_id, None)
        if attest is None:
            raise gl.vm.UserError("Attestation not found")

        stored_digest = attest.fingerprint
        target_url = attest.url

        def leader_fn() -> dict:
            return _fetch_and_fingerprint(target_url)

        def validator_fn(leader_res) -> bool:
            if not isinstance(leader_res, gl.vm.Return):
                return False
            mine = _fetch_and_fingerprint(target_url)
            return mine.get("digest") == leader_res.calldata.get("digest")

        result = gl.vm.run_nondet(leader_fn, validator_fn)
        result_data = result.calldata if hasattr(result, "calldata") else result
        current_digest = result_data.get("digest", "")

        attest.verify_count += u256(1)

        if not current_digest:
            verdict = "INCONCLUSIVE"
        elif current_digest == stored_digest:
            verdict = "MATCH"
        else:
            verdict = "DRIFT"

        attest.last_verify_result = verdict
        self.attestations[attest_id] = attest
        return verdict

    @gl.public.view
    def get_attestation(self, attest_id: str) -> dict:
        """Read a stored attestation record."""
        attest_id = str(attest_id)
        a = self.attestations.get(attest_id, None)
        if a is None:
            return {"exists": False}
        return {
            "exists": True,
            "attest_id": a.attest_id,
            "url": a.url,
            "fingerprint": a.fingerprint,
            "excerpt": a.excerpt,
            "word_count": int(a.word_count),
            "attester": a.attester,
            "created_at": a.created_at,
            "verify_count": a.verify_count,
            "last_verify_result": a.last_verify_result,
        }

    @gl.public.view
    def total_attestations(self) -> u256:
        return self.attest_count

    @gl.public.view
    def now(self) -> str:
        return self._now_iso()

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()
