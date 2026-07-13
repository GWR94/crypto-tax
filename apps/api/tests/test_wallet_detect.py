"""Wallet chain auto-detection."""

from app.wallet_detect import detect_wallet_chain, resolve_wallet_import


def test_detect_evm():
    assert detect_wallet_chain("0xEA0767C2D006914A1B6181E2BFDa60f1290cCf20") == "ethereum"


def test_detect_btc():
    assert (
        detect_wallet_chain("bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq")
        == "bitcoin"
    )


def test_detect_cardano():
    addr = (
        "addr1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh"
        "0sgjx0s5ps8ps7l3w4ah50kxwsc2xcgjslq0s5ps8ps7l3w4ah50"
    )
    assert detect_wallet_chain(addr) == "cardano"


def test_detect_solana():
    assert (
        detect_wallet_chain("7jEvut3Ck87PAxK5mF1bbG1NJ73tcnhz1VZSKqfBT8Eh")
        == "solana"
    )


def test_detect_celestia():
    addr = "celestia1" + "a" * 38
    assert detect_wallet_chain(addr) == "celestia"


def test_resolve_without_explicit_chain():
    addr, chain = resolve_wallet_import(
        "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"
    )
    assert chain == "bitcoin"
    assert addr.startswith("bc1")
