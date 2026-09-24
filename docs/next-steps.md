# Next steps

The descriptor simplification is implemented: Fileroute owns four Pydantic
models, nested groups use ordinary catalogs, and sources/targets express transfer
direction. `dplib-py`, unused GitPython, and the unrelated `google` package are
removed. The lockfile no longer contains their exclusive transitive dependencies.

Before release, exercise canonical descriptors against development SharePoint,
Google Drive, and S3 credentials. Offline regression tests cover model round
trips, reference loading, URL resolution/write-back, target inheritance/overrides, provider
contracts, and mocked transfers. They do not validate live tenant access.

Future work should follow concrete demand:

- Add provider upload capabilities for Google Drive/S3 when required.
- Add SharePoint upload sessions if publication needs files larger than 250 MB.
- Add automatic descriptor discovery only when its operator workflow is defined.

Keep transformation/authoring tools such as Quarto outside Fileroute. Sources
can document those inputs, but Fileroute does not execute transformations.
