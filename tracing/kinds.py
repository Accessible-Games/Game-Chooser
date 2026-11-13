"""Maps one Process Monitor event to a write kind, or None when the event is not a write."""

WRITE_KINDS = (
    "file_create", "folder_create", "file_write", "rename", "delete",
    "reg_create_key", "reg_set_value", "reg_delete_value", "reg_delete_key",
)

_RETRIED_RESULTS = ("FAST IO DISALLOWED", "REPARSE")
_CREATING_DISPOSITIONS = ("Create", "Overwrite", "OverwriteIf", "Supersede")
# Process Monitor's operation names (procmon.py captures them, pml.py decodes the renames)
RENAME_OPERATIONS = ("SetRenameInformationFile", "SetRenameInformationEx", "SetRenameInformationExBypassAccessCheck")
DISPOSITION_OPERATIONS = ("SetDispositionInformationFile", "SetDispositionInformationEx")
_REGISTRY = {"RegSetValue": "reg_set_value", "RegDeleteValue": "reg_delete_value", "RegDeleteKey": "reg_delete_key"}


def classify(operation: str, result: str, details: dict) -> str | None:
    if result in _RETRIED_RESULTS:
        return None
    if operation == "CreateFile":
        return _classify_create(result, details)
    if operation == "WriteFile":
        return "file_write"
    if operation in RENAME_OPERATIONS:
        return "rename"
    if operation in DISPOSITION_OPERATIONS:
        return "delete" if details.get("Delete") == "True" else None
    if operation == "RegCreateKey":
        if details.get("Disposition") == "REG_CREATED_NEW_KEY" or result != "SUCCESS":
            return "reg_create_key"
        return None
    return _REGISTRY.get(operation)


def _classify_create(result: str, details: dict) -> str | None:
    tokens = (details.get("Options") or "").split(", ")
    open_result = details.get("OpenResult")
    if open_result == "Created":
        return "folder_create" if "Directory File" in tokens else "file_create"
    if open_result in ("Overwritten", "Superseded"):
        return "file_write"
    if "Delete On Close" in tokens and result == "SUCCESS":
        return "delete"
    if result != "SUCCESS":
        disposition = details.get("Disposition")
        if disposition in _CREATING_DISPOSITIONS:
            return "file_create"
        access = details.get("Desired Access") or ""
        if disposition == "OpenIf" and ("Write" in access or "Append" in access):
            return "file_create"
    return None
