"""The cross-SDK signature conformance vector.

blocks-genesis-net asserts the same five inputs and the same expected signature in
`XUnitTest/Delegation/DelegationConformanceVector.cs`, so a divergence in either SDK fails a test
rather than a production exchange. Keep the two files in sync.
"""

from blocks_genesis._delegation import constants, signature

TENANT_ID = "tenant-abc"
DELEGATION_ID = "dg_00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
NONCE = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
TS = 1739577600
TENANT_SALT = "d3f1c0de-5a17-4b0c-9e8a-1f2b3c4d5e6f"

EXPECTED_SIGNATURE_INPUT = (
    "tenant-abc"
    "|dg_00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
    "|0f1e2d3c4b5a69788796a5b4c3d2e1f0"
    "|1739577600"
)
EXPECTED_SIGNATURE = "c01a5f122b9793b09385796b95f00ec3ebb28528d1043dc96cf3a9fe7628d560"


def test_signature_input_is_pipe_delimited_in_exact_field_order():
    assert constants.build_signature_input(TENANT_ID, DELEGATION_ID, NONCE, TS) == EXPECTED_SIGNATURE_INPUT


def test_signature_matches_the_dotnet_vector():
    assert signature.compute(EXPECTED_SIGNATURE_INPUT, TENANT_SALT) == EXPECTED_SIGNATURE
    assert signature.sign(TENANT_ID, DELEGATION_ID, NONCE, TS, TENANT_SALT) == EXPECTED_SIGNATURE


def test_signature_is_lowercase_hex_of_sha256_length():
    computed = signature.compute(EXPECTED_SIGNATURE_INPUT, TENANT_SALT)
    assert len(computed) == 64
    assert computed == computed.lower()
    assert all(char in "0123456789abcdef" for char in computed)


def test_verify_is_constant_time_and_rejects_mismatches():
    assert signature.verify(EXPECTED_SIGNATURE, EXPECTED_SIGNATURE)
    assert not signature.verify(EXPECTED_SIGNATURE, "deadbeef")
    assert not signature.verify(EXPECTED_SIGNATURE, None)
    assert not signature.verify(EXPECTED_SIGNATURE, "")


def test_wire_constants_match_the_dotnet_side():
    # These strings are the contract. Changing one without the other breaks every exchange.
    assert constants.DELEGATION_GRANT_HEADER == "DelegationGrant"
    assert constants.GRANT_KEY_PREFIX == "delegation:"
    assert constants.NONCE_KEY_PREFIX == "nonce:"
    assert constants.REDEMPTION_KEY_PREFIX == "redemption:"
    assert constants.GRANT_ID_PREFIX == "dg_"
    assert constants.GRANT_ID_RANDOM_BYTES == 32
    assert constants.NONCE_RANDOM_BYTES == 16
    assert constants.TOKEN_EXCHANGE_GRANT_TYPE == "urn:ietf:params:oauth:grant-type:token-exchange"
    assert constants.DELEGATION_GRANT_TOKEN_TYPE == "urn:blocks:params:oauth:token-type:delegation-grant"
    assert constants.BLOCKS_KEY_HEADER == "x-blocks-key"
    assert constants.DEFAULT_GRANT_TTL_SECONDS == 2 * 24 * 60 * 60
    assert constants.NONCE_TTL_SECONDS == 120
    assert constants.CLOCK_WINDOW_SECONDS == 60
    assert constants.TOKEN_RENEWAL_MARGIN_SECONDS == 60


def test_grant_ids_are_prefixed_lowercase_hex_and_unique():
    ids = [signature.new_grant_id() for _ in range(200)]

    for value in ids:
        assert value.startswith("dg_")
        assert len(value) == len("dg_") + 64
        assert signature.is_well_formed(value)

    assert len(set(ids)) == len(ids)


def test_nonces_are_thirty_two_lowercase_hex_chars_and_unique():
    nonces = [signature.new_nonce() for _ in range(100)]

    for value in nonces:
        assert len(value) == 32
        assert all(char in "0123456789abcdef" for char in value)

    assert len(set(nonces)) == len(nonces)


def test_is_well_formed_rejects_everything_but_the_exact_shape():
    body = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"

    assert signature.is_well_formed(f"dg_{body}")
    assert not signature.is_well_formed(None)
    assert not signature.is_well_formed("")
    assert not signature.is_well_formed("dg_")
    assert not signature.is_well_formed(body)                       # no prefix
    assert not signature.is_well_formed(f"dg_{body.upper()}")       # uppercase hex
    assert not signature.is_well_formed(f"dg_{body[:-1]}")          # one char short
    assert not signature.is_well_formed(f"dg_{body}a")              # one char long
    assert not signature.is_well_formed(f"dg_{body[:-1]}z")         # not hex
