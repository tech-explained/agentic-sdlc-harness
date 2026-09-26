from impl import discounted


def test_no_discount():
    assert discounted(50) == 50


def test_ten_percent():
    assert discounted(100) == 90
    assert discounted(200) == 180


def test_twenty_percent():
    assert discounted(500) == 400


def test_boundary():
    assert discounted(99.99) == 99.99
