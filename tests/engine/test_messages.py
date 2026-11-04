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
