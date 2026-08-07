from __future__ import annotations

from pokebot.reseller.capture import CaptureFile, CapturedRequest, dot_get, substitute


def test_substitute_replaces_known_and_keeps_unknown():
    out = substitute("tcin={{tcin}} qty={{quantity}} miss={{nope}}", {"tcin": "123", "quantity": 2})
    assert out == "tcin=123 qty=2 miss={{nope}}"


def test_dot_get_nested_and_list_index():
    data = {"cart": {"items": [{"id": "a"}, {"id": "b"}]}}
    assert dot_get(data, "cart.items.1.id") == "b"
    assert dot_get(data, "cart.items.5.id") is None
    assert dot_get(data, "cart.missing") is None


def test_capture_ordered_follows_sequence():
    capture = CaptureFile(
        retailer="target",
        sequence=["b", "a"],
        requests=[
            CapturedRequest(name="a", url="https://x/a"),
            CapturedRequest(name="b", url="https://x/b"),
        ],
    )
    assert [r.name for r in capture.ordered()] == ["b", "a"]


def test_capture_roundtrip(tmp_path):
    capture = CaptureFile(
        retailer="target",
        cookies={"_abck": "v"},
        sequence=["a"],
        requests=[CapturedRequest(name="a", method="POST", url="https://x/a", body="{{tcin}}")],
    )
    path = tmp_path / "cap.json"
    capture.save(path)
    loaded = CaptureFile.load(path)
    assert loaded.cookies == {"_abck": "v"}
    assert loaded.requests[0].body == "{{tcin}}"
