# AnchorLock — On-chain web content attestation

A GenLayer Intelligent Contract primitive for proving **what a URL said at a point in time**. Anyone attests a URL; validators independently fetch the page, extract its visible text, and derive a compact content fingerprint. The attestation commits only when a majority reproduce the identical fingerprint. Later, anyone can re-fetch and compare against the stored fingerprint to detect content drift.

## Why it exists

Dispute evidence, proof-of-publication, content oracles, and audit trails all need the same primitive: an on-chain, consensus-verified record of what a URL contained. AnchorLock provides that record without trusting any single node.

## Deployed

- **Network:** GenLayer Bradbury Testnet (chain 4221)
- **Contract:** `0xfa37e421d51E116D8DCd8AA68057600204fB4244`
- **Explorer:** https://explorer-bradbury.genlayer.com/address/0xfa37e421d51E116D8DCd8AA68057600204fB4244

## How consensus works

`attest_url` runs `gl.eq_principle.strict_eq` — the canonical GenLayer equivalence primitive:

1. The leader fetches the URL, strips HTML to visible text, and derives a fingerprint: a SHA-256 digest over the normalized (lowercased, single-spaced) text, plus a word count and opening excerpt.
2. Every validator independently fetches the same URL and derives its own fingerprint.
3. `strict_eq` commits only when a majority produce a byte-identical fingerprint. Divergent extraction fails the round — no single node can forge an attestation.

Normalization makes the digest robust to trivial case/whitespace variance between independent fetches, so genuine agreement is not defeated by encoding noise while any real content change still alters the digest.

## Reference URLs GenVM can reach

GenVM's web egress is blocked by bot protection on many major sites. Use GenLayer's own test server or plain static pages:

- `https://test-server.genlayer.com/static/genvm/hello.html` (GenLayer's own)
- `https://example.com` (used throughout GenLayer's docs)

Wikipedia, GitHub raw, and most CDN sites are blocked and will fail independently of the contract.

## API

| Method | Type | Description |
|---|---|---|
| `attest_url(url)` | write | Fetch, fingerprint, and commit an attestation. Returns attest ID. |
| `verify_attestation(attest_id)` | write | Re-fetch and compare against the stored fingerprint. Returns MATCH / DRIFT / INCONCLUSIVE. |
| `get_attestation(attest_id)` | view | Read a stored attestation: fingerprint, excerpt, word count, attester, timestamps, verify count. |
| `total_attestations()` | view | Counter. |
| `now()` | view | Timestamp. |

## Verified on-chain

- `attest_url("https://example.com")` → MAJORITY_AGREE; attestation committed with fingerprint `0x2f71cf3a...`, word count 25, real extracted excerpt.
- `verify_attestation("0")` → MAJORITY_AGREE; verify_count incremented, content matched (no drift).

## How to run

```bash
genlayer network set testnet-bradbury

# Attest a URL
genlayer write <CONTRACT> attest_url --args "https://example.com"

# Read the attestation
genlayer call <CONTRACT> get_attestation --args "0"

# Verify it hasn't drifted
genlayer write <CONTRACT> verify_attestation --args "0"
```

## Tech Stack

- **Contract:** Python GenLayer Intelligent Contract (GenVM runner `1jb45aa8...`)
- **Storage:** `TreeMap[str, Attestation]`, `u256` counter
- **Consensus:** `gl.vm.run_nondet` leader/validator, digest comparison
- **Web:** `gl.nondet.web.request` for independent per-validator fetches
- **Network:** GenLayer Bradbury Testnet (chain 4221)

## Repository

https://github.com/Adebisi1111/anchorlock

## License

MIT
