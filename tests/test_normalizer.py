import pytest
from src.domain.normalizer import (
    normalize_unicode,
    strip_edition_noise,
    extract_title_and_featured,
    normalize_artist,
    clean_query_string,
    extract_track_flags
)

def test_normalize_unicode():
    assert normalize_unicode("Beyoncé’s Song “Halo”") == "Beyonce's Song \"Halo\""
    assert normalize_unicode("Track – Remaster") == "Track - Remaster"
    assert normalize_unicode("Björk … Homogenic") == "Bjork ... Homogenic"

def test_strip_edition_noise():
    assert strip_edition_noise("Comfortably Numb - Remastered 2011") == "Comfortably Numb"
    assert strip_edition_noise("Stayin' Alive - From \"Saturday Night Fever\" Soundtrack") == "Stayin' Alive"
    assert strip_edition_noise("Let It Be - 2021 Mix / Deluxe Edition") == "Let It Be - 2021 Mix"
    assert strip_edition_noise("Rocket Man (I Think It's Going to Be a Long, Long Time) - 2017 Remaster") == "Rocket Man (I Think It's Going to Be a Long, Long Time)"
    assert strip_edition_noise("Good Vibrations - Stereo / Remastered 2012") == "Good Vibrations"

def test_extract_title_and_featured():
    title, feat = extract_title_and_featured("Empire State of Mind (feat. Alicia Keys)")
    assert title == "Empire State of Mind"
    assert feat == ["Alicia Keys"]

    title2, feat2 = extract_title_and_featured("HUMBLE. - feat. Jay-Z, Kendrick")
    assert title2 == "HUMBLE."
    assert "Jay-Z" in feat2

    title3, feat3 = extract_title_and_featured("Cold Heart - PNAU Remix with Dua Lipa")
    assert "Cold Heart" in title3
    assert "Dua Lipa" in feat3

def test_normalize_artist():
    assert normalize_artist("Simon & Garfunkel") == ["simon", "garfunkel"]
    assert normalize_artist("David Bowie, Queen") == ["david bowie", "queen"]
    assert normalize_artist("Skrillex vs. Diplo") == ["skrillex", "diplo"]
    assert normalize_artist("Calvin Harris with Dua Lipa") == ["calvin harris", "dua lipa"]

def test_extract_track_flags():
    live_flags = extract_track_flags("Hotel California (Live at The Forum)", "Eagles")
    assert live_flags["is_live"] is True
    assert live_flags["is_remix"] is False

    remix_flags = extract_track_flags("Levitating - Club Mix", "Dua Lipa")
    assert remix_flags["is_remix"] is True

    karaoke_flags = extract_track_flags("Bohemian Rhapsody (Instrumental)", "Queen")
    assert karaoke_flags["is_instrumental"] is True

def test_clean_query_string():
    query = clean_query_string("The Beatles", "Here Comes The Sun - 2019 Mix (Remastered)")
    assert "The Beatles" in query
    assert "Here Comes The Sun" in query
    assert "Remastered" not in query
