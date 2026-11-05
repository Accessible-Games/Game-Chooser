from engine.messages import Progress, format_size, phase_label, update_summary


def test_format_size():
    assert format_size(53_051_392) == "50.6 megabytes"
    assert format_size(839_680) == "820 kilobytes"
    assert format_size(1_288_490_189) == "1.2 gigabytes"
    assert format_size(None) == "unknown size"


def test_phase_label():
    assert phase_label(Progress("downloading", 23_068_672, 52_428_800), "Entombed") == "Downloading Entombed, 22 of 50 megabytes"
    assert phase_label(Progress("downloading", 1000, None), "Entombed") == "Downloading Entombed"
    assert phase_label(Progress("installing_dependency", detail="DirectX 8 for Visual Basic"), "X") == "Installing DirectX 8 for Visual Basic"
    assert [phase_label(Progress(p), "X") for p in ("verifying", "extracting", "finishing", "waiting_for_installer")] == \
        ["Verifying", "Extracting", "Finishing", "Waiting for the installer"]


def test_update_summary():
    assert update_summary("Pawprints", 0) == "Pawprints is updated"
    assert update_summary("Pawprints", 1) == "Updated Pawprints. Kept a backup of 1 file you'd changed."
    assert update_summary("Pawprints", 2) == "Updated Pawprints. Kept a backup of 2 files you'd changed."


def test_with_waiting():
    from engine.messages import with_waiting
    assert with_waiting("Verifying", 0) == "Verifying"
    assert with_waiting("Verifying", 1) == "Verifying, 1 more waiting"
    assert with_waiting("Verifying", 3) == "Verifying, 3 more waiting"


def test_finished_text_per_action():
    from engine.messages import finished_text
    assert finished_text("install", "Entombed") == "Entombed is installed"
    assert finished_text("uninstall", "Entombed") == "Entombed is uninstalled"
    assert finished_text("repair", "Entombed") == "Entombed is repaired"
    assert finished_text("undo", "Entombed") == "Entombed is restored to the previous version"
    assert finished_text("extras", "Entombed") == "Entombed's extras are installed"
    assert finished_text("update", "Entombed") == "Entombed is updated"
    assert finished_text("update", "Entombed", backups=2) == \
        "Updated Entombed. Kept a backup of 2 files you'd changed."


def test_finished_text_appends_each_note_as_a_sentence():
    from engine.messages import finished_text
    assert finished_text("install", "Entombed", ["Restart Windows to finish installing Entombed."]) == \
        "Entombed is installed. Restart Windows to finish installing Entombed."
    assert finished_text("install", "Entombed", ["One note", "Two notes."]) == \
        "Entombed is installed. One note. Two notes."
    assert finished_text("update", "Entombed", ["A note."], backups=1) == \
        "Updated Entombed. Kept a backup of 1 file you'd changed. A note."
    assert finished_text("install", "Entombed", ["", "  "]) == "Entombed is installed"


def test_failed_text_per_action():
    from engine.messages import failed_text
    assert failed_text("install", "Entombed") == "Entombed couldn't be installed"
    assert failed_text("update", "Entombed") == "Entombed couldn't be updated"
    assert failed_text("uninstall", "Entombed") == "Entombed couldn't be uninstalled"
    assert failed_text("repair", "Entombed") == "Entombed couldn't be repaired"
    assert failed_text("undo", "Entombed") == "Entombed couldn't be restored to the previous version"
    assert failed_text("extras", "Entombed") == "Entombed's extras couldn't be installed"
