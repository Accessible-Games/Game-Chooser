from language_names import language_name


def test_language_names_from_codes():
    assert language_name("fr") == "French" and language_name("PT") == "Portuguese" and language_name(" ja ") == "Japanese"
    assert language_name("tlh") == "tlh" and language_name("") == "" and language_name(None) == ""
