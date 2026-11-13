
import pytest

from engine.errors import Cancelled, NoVerifiedSource
from engine.messages import DEVELOPER_CHANGED_UNVERIFIED
from engine.sources import fetch_exact, fetch_verified, hash_file
from tests.engine.fakes import FakeFetcher, sha

DEV = "https://dev.example.com/files/game.zip"
DEV2 = "https://other.example.com/game.zip"
BLOCKED = "https://www.agarchive.net/games/game.zip"
GOOD = b"good bytes"
BAD = b"changed bytes!"


def mirror(data: bytes = GOOD) -> str:
    return f"https://files.accessiblegames.net/files/{sha(data)}/game.zip"


def src(url, data=GOOD, **extra):
    s = {"platform": "windows", "url": url, "install": {"method": "zip"}}
    if data is not None:
        s["sha256"] = sha(data)
        s["size"] = len(data)
    s.update(extra)
    return s


def entry(*sources):
    return {"id": "g", "kind": "game", "sources": list(sources)}


def run(e, ff, tmp_path, cancel=lambda: False, seen=None):
    seen = [] if seen is None else seen
    return fetch_verified(e, "windows", tmp_path, ff, seen.append, cancel)


def files(tmp_path):
    return sorted(x.name for x in tmp_path.rglob("*") if x.is_file())


def test_first_verifying_source_wins(tmp_path):
    ff = FakeFetcher({DEV: GOOD, mirror(): GOOD})
    f = run(entry(src(DEV), src(mirror())), ff, tmp_path)
    assert f.source["url"] == DEV and f.position == 0 and f.note is None
    assert f.sha256 == sha(GOOD) and f.size == len(GOOD)
    assert f.path.read_bytes() == GOOD and f.path.name == "game.zip"
    assert ff.requests == [DEV]


def test_later_dev_mismatches_are_deleted(tmp_path):
    ff = FakeFetcher({DEV: BAD, DEV2: b"yet another"})
    f = run(entry(src(DEV), src(DEV2)), ff, tmp_path)
    assert f.position == 0 and f.note == DEVELOPER_CHANGED_UNVERIFIED  # the first, installed with the note
    assert f.sha256 == sha(BAD) and f.size == len(BAD) and f.path.read_bytes() == BAD
    assert files(tmp_path) == [f.path.name]


def test_mirror_mismatch_is_never_used(tmp_path):
    ff = FakeFetcher({mirror(): BAD})
    with pytest.raises(NoVerifiedSource) as ei:
        run(entry(src(mirror())), ff, tmp_path)
    assert len(ei.value.reasons) == 1
    assert files(tmp_path) == []


@pytest.mark.parametrize("served, fields, note", [
    pytest.param(BAD, {}, None, id="no size"),
    pytest.param(BAD, {"size": 3}, DEVELOPER_CHANGED_UNVERIFIED, id="size differs"),
    pytest.param(GOOD, {"size": len(GOOD)}, None, id="size matches"),
])
def test_source_without_a_hash_counts_as_changed_only_when_its_size_differs(tmp_path, served, fields, note):
    f = run(entry(src(DEV, data=None, **fields)), FakeFetcher({DEV: served}), tmp_path)
    assert f.note == note and f.sha256 == sha(served) and f.size == len(served)


def test_unreachable_dev_then_mirror_has_no_note(tmp_path):
    ff = FakeFetcher({mirror(): GOOD})
    f = run(entry(src(DEV), src(mirror())), ff, tmp_path)
    assert f.note is None and f.position == 1


def test_all_unreachable_gives_one_reason_each(tmp_path):
    with pytest.raises(NoVerifiedSource) as ei:
        run(entry(src(DEV), src(mirror())), FakeFetcher(), tmp_path)
    assert len(ei.value.reasons) == 2


def test_blocked_source_is_skipped(tmp_path):
    ff = FakeFetcher({mirror(): GOOD})
    f = run(entry(src(BLOCKED), src(mirror())), ff, tmp_path)
    assert f.position == 1 and f.note is None
    with pytest.raises(NoVerifiedSource) as ei:
        run(entry(src(BLOCKED)), ff, tmp_path)
    assert "blocked" in ei.value.reasons[0].lower()
    assert "agarchive" not in ei.value.reasons[0].lower()


def test_cancel_stops_without_trying_next(tmp_path):
    ff = FakeFetcher({DEV: GOOD, mirror(): GOOD})
    with pytest.raises(Cancelled):
        run(entry(src(DEV), src(mirror())), ff, tmp_path, cancel=lambda: True)
    assert ff.requests == [DEV]
    assert files(tmp_path) == []


def test_cancel_after_a_candidate_leaves_nothing(tmp_path):
    ff = FakeFetcher({DEV: BAD, mirror(): GOOD})

    def cancel():
        return ff.requests == [DEV, mirror()]

    with pytest.raises(Cancelled):
        run(entry(src(DEV), src(mirror())), ff, tmp_path, cancel=cancel)
    assert files(tmp_path) == []


def test_progress_phases(tmp_path):
    seen = []
    run(entry(src(DEV)), FakeFetcher({DEV: GOOD}), tmp_path, seen=seen)
    phases = [p.phase for p in seen]
    assert phases[0] == "downloading" and phases[-1] == "verifying"
    assert set(phases) == {"downloading", "verifying"}
    assert phases.index("verifying") > max(i for i, x in enumerate(phases) if x == "downloading")
    assert seen[0].total == len(GOOD)


def test_url_name_is_unquoted_and_safe(tmp_path):
    url = "https://dev.example.com/a/My%20Game%3A1.zip"
    f = run(entry(src(url, data=None)), FakeFetcher({url: GOOD}), tmp_path)
    assert f.path.name == "My Game1.zip"
    url2 = "https://dev.example.com/"
    f2 = run(entry(src(url2, data=None)), FakeFetcher({url2: GOOD}), tmp_path / "b")
    assert f2.path.name == "download.bin"


def test_fetch_exact_rejects_other_hashes(tmp_path):
    ff = FakeFetcher({DEV: BAD, mirror(): BAD})
    with pytest.raises(NoVerifiedSource) as ei:
        fetch_exact([DEV, mirror()], "f" * 64, tmp_path, ff, lambda p: None, lambda: False)
    assert len(ei.value.reasons) == 2
    assert ei.value.downloaded                  # files arrived; they just didn't match
    assert files(tmp_path) == []


def test_fetch_exact_says_when_nothing_downloaded(tmp_path):
    with pytest.raises(NoVerifiedSource) as ei:
        fetch_exact([DEV, mirror()], "f" * 64, tmp_path, FakeFetcher({}), lambda p: None, lambda: False)
    assert not ei.value.downloaded
    with pytest.raises(NoVerifiedSource) as ei:   # one unreachable, one changed
        fetch_exact([DEV, mirror()], "f" * 64, tmp_path / "b", FakeFetcher({mirror(): BAD}), lambda p: None,
                    lambda: False)
    assert ei.value.downloaded
    assert not NoVerifiedSource(["x"]).downloaded  # existing callers' shape still works


def test_fetch_exact_accepts_matching_hash(tmp_path):
    ff = FakeFetcher({DEV: BAD, mirror(): GOOD})
    f = fetch_exact([DEV, mirror()], sha(GOOD), tmp_path, ff, lambda p: None, lambda: False)
    assert f.position == 1 and f.note is None and f.sha256 == sha(GOOD)
    assert files(tmp_path) == [f.path.name]


def test_hash_file_streams_in_chunks(tmp_path):
    p = tmp_path / "big.bin"
    data = b"x" * (3 * 2**20 + 5)
    p.write_bytes(data)
    assert hash_file(p) == sha(data)
