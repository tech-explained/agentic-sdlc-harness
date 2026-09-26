from impl import fizzbuzz


def test_prefix():
    assert fizzbuzz(5) == ["1", "2", "fizz", "4", "buzz"]


def test_fizzbuzz():
    assert fizzbuzz(15)[-1] == "fizzbuzz"


def test_empty():
    assert fizzbuzz(0) == []
