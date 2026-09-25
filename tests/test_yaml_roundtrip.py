"""Keyed authoring preserves comments, quotes and links."""

from fileroute.descriptor import load, save


def test_yaml_roundtrip(tmp_path):
    path = tmp_path / "root.yaml"
    path.write_text("""# catalog comment
catalogs:
  external:
    descriptor: 'child.yaml' # link comment
resources:
  guide: # entry comment
    path: "guide.csv"
    custom: {owner: 'team'}
    targets: []
""")
    model = load(path)
    model.resources["guide"].title = "Guide"
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
    assert load(path).resources["guide"].targets == []
