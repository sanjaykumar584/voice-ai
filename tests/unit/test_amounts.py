from app.voice.collections import amount_spoken


def test_lakhs_whole():
    assert amount_spoken(100647) == "1 lakh rupees"
    assert amount_spoken(100000) == "1 lakh rupees"
    assert amount_spoken(200000) == "2 lakh rupees"


def test_lakhs_with_decimal():
    assert amount_spoken(1780700) == "17.8 lakh rupees"
    assert amount_spoken(371987) == "3.7 lakh rupees"


def test_below_lakh_rounds_to_thousand():
    assert amount_spoken(89464) == "around 89 thousand rupees"
    assert amount_spoken(11183) == "around 11 thousand rupees"
    assert amount_spoken(900) == "900 rupees"


def test_zero_and_negative():
    assert amount_spoken(0) == "0 rupees"
    assert amount_spoken(None) == "0 rupees"
