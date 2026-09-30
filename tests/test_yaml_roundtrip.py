"""Named YAML entries keep their comments when edited and reordered."""

from fileroute.descriptor import load, save
from fileroute.models import Resource


def test_yaml_roundtrip(tmp_path):
    path = tmp_path / "root.yaml"
    path.write_text("""# catalog comment
catalogs:
  # link comment
  - name: external
    descriptor: 'child.yaml'
resources:
  # entry comment
  - name: guide
    path: "guide.csv"
    custom:
      owner: 'team'
    targets: []
""")
    model = load(path)
    model.resources[0].title = "Guide"
    save(model, path)
    text = path.read_text()
    for value in (
        "# catalog comment",
        "# link comment",
        "# entry comment",
        "'child.yaml'",
        '"guide.csv"',
        "owner: 'team'",
    ):
        assert value in text
    assert load(path).resources[0].targets == []


def test_reorder_and_insert_keep_comments_with_names(tmp_path):
    path = tmp_path / "catalog.yaml"
    path.write_text("""resources:
  # alpha entry
  - name: alpha
    path: a.csv
  # beta entry
  - name: beta
    path: b.csv
""")
    catalog = load(path)
    catalog.resources[:] = [
        catalog.resources[1],
        Resource(name="new", path="new.csv"),
        catalog.resources[0],
    ]
    save(catalog, path)
    text = path.read_text()
    assert text.index("# beta entry") < text.index("name: beta")
    assert text.index("# alpha entry") < text.index("name: alpha")
    assert (
        text.index("name: beta") < text.index("name: new") < text.index("name: alpha")
    )
