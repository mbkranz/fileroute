"""Public command handoffs between discovery, native steps, and transfer plans."""

from typer.testing import CliRunner
import pytest

from fileroute.cli import app
from fileroute.descriptor import load, save
from fileroute.discovery import (
    DiscoveryScan,
    ReconcileDiscoveryRequest,
    reconcile_discovery,
)
from fileroute.models import Catalog, Location, Resource
from fileroute.transfer import plan_pull


@pytest.mark.parametrize("existing", [False, True])
def test_markdown_discovery_output_cannot_alias_generated_artifact(
    tmp_path, monkeypatch, existing
):
    from fileroute.discovery import DiscoveryResult

    monkeypatch.chdir(tmp_path)
    descriptor = tmp_path / "catalog.yaml"
    document = Catalog(
        path="data",
        targets=[],
        sources=[
            Location(
                path="https://drive.google.com/drive/folders/folder",
                resourcePathTemplate="{filename}",
            )
        ],
    )
    save(document, descriptor)
    generated = document.model_copy(deep=True)
    generated.resources["term"] = Resource(
        path="data/term.md",
        sources=[Location(path="https://drive.google.com/file/d/file/view")],
    )
    output = tmp_path / "data/term.md"
    if existing:
        output.parent.mkdir()
        output.write_text("definition: existing authored text\n")
    before = output.read_bytes() if existing else None
    monkeypatch.setattr(
        "fileroute.discovery.discover_report",
        lambda *args, **kwargs: DiscoveryResult(catalog=generated),
    )

    result = CliRunner().invoke(
        app, ["discover", str(descriptor), "--to", "markdown", "--output", str(output)]
    )

    assert result.exit_code == 1 and "overlaps a pull destination" in result.stderr
    assert output.read_bytes() == before if existing else not output.exists()


def test_written_discovery_inventory_is_pullable_with_processing(tmp_path):
    descriptor = tmp_path / "catalog.yaml"
    source = Location(
        path="https://drive.google.com/drive/folders/existing-folder",
        resourcePathTemplate="{filename}",
    )
    catalog = Catalog(path="acquired", targets=[], sources=[source])
    discovered = Resource(
        path="acquired/term.docx",
        sources=[
            Location(
                path="https://drive.google.com/file/d/existing-file/view",
                serviceType="GoogleDrive",
                serviceId="existing-file",
                discovery={
                    "scope": "",
                    "relativePath": "term.docx",
                    "name": "term.docx",
                    "version": "1",
                },
            )
        ],
    )
    report = reconcile_discovery(
        ReconcileDiscoveryRequest(
            catalog=catalog,
            scans=[DiscoveryScan(scope="", resources={"term": discovered})],
            descriptor=descriptor,
            root=tmp_path,
            to_markdown=True,
        )
    )
    save(Catalog.model_validate(report.catalog.model_dump(by_alias=True)), descriptor)
    entries = plan_pull(descriptor, root=tmp_path)
    assert len(entries) == 1
    assert entries[0].local == tmp_path / "acquired/term.md"
    assert entries[0].hook is not None
    assert load(descriptor).sources[0].resource_path_template == "{filename}"


def test_incomplete_discovery_cli_reports_observations_without_writing(
    tmp_path, monkeypatch
):
    from fileroute.discovery import DiscoveryObservation, DiscoveryResult

    descriptor = tmp_path / "catalog.yaml"
    save(
        Catalog(
            path="data",
            targets=[],
            sources=[
                Location(
                    path="https://drive.google.com/drive/folders/existing-folder",
                    resourcePathTemplate="{filename}",
                )
            ],
        ),
        descriptor,
    )
    before = descriptor.read_bytes()
    monkeypatch.setattr(
        "fileroute.discovery.discover_report",
        lambda *args, **kwargs: DiscoveryResult(
            catalog=load(descriptor),
            complete=False,
            errors=["listing interrupted"],
            observations=[
                DiscoveryObservation(resource="term", scope="", statuses=["unknown"])
            ],
        ),
    )
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        app, ["discover", str(descriptor), "--write", "--format", "json"]
    )
    assert result.exit_code == 1
    assert '"unknown"' in result.stdout
    assert "listing interrupted" in result.stderr
    assert descriptor.read_bytes() == before
