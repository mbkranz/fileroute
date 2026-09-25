"""Graph migration never overwrites inputs or publishes partial output."""

import json
import pytest
from fileroute.migration import migrate
from fileroute.descriptor import load, find


def test_shared_graph_migration_preserves_files_and_metadata(tmp_path):
    root = tmp_path / "root.yaml"
    root.write_text(
        "# retained\ncatalogs:\n  - name: left\n    $ref: child.json\n  - name: right\n    $ref: child.json\n"
    )
    child = tmp_path / "child.json"
    child.write_text(
        json.dumps({
            "resources": [
                {
                    "name": "file",
                    "path": "out.csv",
                    "targets": [],
                    "extra": {"owner": "team"},
                }
            ]
        })
    )
    before = {p: p.read_bytes() for p in (root, child)}
    out = tmp_path / "converted"
    report = migrate(root, out, dry_run=True)
    assert len(report) == 2 and not out.exists()
    assert migrate(root, out) == report
    result = load(out / "root.yaml", resolve_references=True)
    for name in ("left.file", "right.file"):
        resource = find(result, name)
        assert resource.path == "out.csv" and resource.targets == []
        assert resource.model_extra["extra"] == {"owner": "team"}
    assert "# retained" in (out / "root.yaml").read_text()
    assert all(p.read_bytes() == data for p, data in before.items())
    with pytest.raises(ValueError, match="exists"):
        migrate(root, out)


@pytest.mark.parametrize(
    "children",
    [
        [{"path": "x"}],
        [{"name": "bad.name", "path": "x"}],
        [{"name": "file", "path": "x"}, {"name": "FILE", "path": "y"}],
    ],
)
def test_invalid_names_publish_nothing(tmp_path, children):
    root = tmp_path / "root.json"
    root.write_text(json.dumps({"resources": children}))
    with pytest.raises(ValueError):
        migrate(root, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_cycles_and_failed_publish_leave_no_stage(tmp_path, monkeypatch):
    root = tmp_path / "root.json"
    root.write_text(json.dumps({"catalogs": [{"name": "cycle", "$ref": "root.json"}]}))
    with pytest.raises(ValueError, match="Cyclic"):
        migrate(root, tmp_path / "out")
    root.write_text(json.dumps({"resources": [{"name": "file", "path": "x"}]}))
    monkeypatch.setattr(
        "fileroute.descriptor.load",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("verification failed")),
    )
    with pytest.raises(ValueError, match="verification failed"):
        migrate(root, tmp_path / "out")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["root.json"]


def test_migration_preserves_name_and_link_comments(tmp_path):
    root = tmp_path / "root.yaml"
    root.write_text(
        "catalogs:\n  - name: child # name comment\n    $ref: child.yaml # link comment\n"
    )
    (tmp_path / "child.yaml").write_text(
        "resources:\n  - name: file # resource comment\n    path: x\n"
    )
    migrate(root, tmp_path / "out")
    text = (tmp_path / "out/root.yaml").read_text()
    assert "# name comment" in text and "# link comment" in text
    assert "# resource comment" in (tmp_path / "out/child.yaml").read_text()
