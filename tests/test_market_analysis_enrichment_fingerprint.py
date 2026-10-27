from market_analysis.enrichment_contract import enrichment_fingerprint


def test_enrichment_fingerprint_is_stable_and_not_version_labeled():
    value = enrichment_fingerprint()
    assert len(value) == 64
    assert "version" not in value
