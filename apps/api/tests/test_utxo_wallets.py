"""Bitcoin and Cardano wallet address validation."""

from app.btc_fetch import is_valid_btc_address
from app.cardano_fetch import is_valid_cardano_address


def test_btc_address_validation():
    assert is_valid_btc_address("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa")
    assert is_valid_btc_address(
        "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"
    )
    assert not is_valid_btc_address("not-a-btc-address")


def test_cardano_address_validation():
    assert is_valid_cardano_address(
        "addr1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh"
        "0sgjx0s5ps8ps7l3w4ah50kxwsc2xcgjslq0s5ps8ps7l3w4ah50"
    )
    assert is_valid_cardano_address(
        "stake1u9f2v0a8qq2r3c3p3d3p3d3p3d3p3d3p3d3p3d3p3d3p3d3p3d3p3d3p3d"
    )
    assert not is_valid_cardano_address("addr1tooshort")
