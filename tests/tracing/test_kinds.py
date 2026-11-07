from tracing.kinds import classify, WRITE_KINDS


def cf(result="SUCCESS", **details):
    return classify("CreateFile", result, details)


def test_create_file_and_folder():
    assert cf(OpenResult="Created", Options="Synchronous IO Non-Alert, Non-Directory File") == "file_create"
    assert cf(OpenResult="Created", Options="Directory File, Synchronous IO Non-Alert") == "folder_create"


def test_overwrite_is_a_write_and_plain_open_is_nothing():
    assert cf(OpenResult="Overwritten", Options="") == "file_write"
    assert cf(OpenResult="Superseded", Options="") == "file_write"
    assert cf(OpenResult="Opened", Options="", Disposition="OpenIf") is None


def test_delete_on_close():
    assert cf(OpenResult="Opened", Options="Delete On Close, Non-Directory File") == "delete"


def test_failed_creates_are_kept_failed_reads_are_not():
    assert cf("ACCESS DENIED", Disposition="Create", Options="") == "file_create"
    assert cf("ACCESS DENIED", Disposition="OpenIf", Options="", **{"Desired Access": "Generic Write"}) == "file_create"
    assert cf("NAME NOT FOUND", Disposition="Open", Options="", **{"Desired Access": "Generic Read"}) is None
    assert cf("NAME NOT FOUND", Disposition="OpenIf", Options="", **{"Desired Access": "Generic Read"}) is None


def test_retried_results_are_dropped():
    assert classify("WriteFile", "FAST IO DISALLOWED", {}) is None
    assert cf("REPARSE", Disposition="Create", Options="") is None


def test_rename_delete_and_registry():
    assert classify("WriteFile", "SUCCESS", {"Length": 10}) == "file_write"
    assert classify("SetRenameInformationEx", "SUCCESS", {"FileName": "C:\\a"}) == "rename"
    assert classify("SetDispositionInformationEx", "SUCCESS", {"Delete": "True"}) == "delete"
    assert classify("SetDispositionInformationFile", "SUCCESS", {"Delete": "False"}) is None
    assert classify("RegCreateKey", "SUCCESS", {"Disposition": "REG_CREATED_NEW_KEY"}) == "reg_create_key"
    assert classify("RegCreateKey", "SUCCESS", {"Disposition": "REG_OPENED_EXISTING_KEY"}) is None
    assert classify("RegCreateKey", "ACCESS DENIED", {}) == "reg_create_key"
    assert classify("RegSetValue", "SUCCESS", {"Type": "REG_SZ"}) == "reg_set_value"
    assert classify("RegDeleteValue", "SUCCESS", {}) == "reg_delete_value"
    assert classify("RegDeleteKey", "SUCCESS", {}) == "reg_delete_key"
    assert classify("ReadFile", "SUCCESS", {}) is None
    assert classify("Process Start", "SUCCESS", {}) is None


def test_kinds_are_the_spec_list():
    assert set(WRITE_KINDS) == {"file_create", "folder_create", "file_write", "rename", "delete",
                                "reg_create_key", "reg_set_value", "reg_delete_value", "reg_delete_key"}
