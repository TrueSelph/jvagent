# Changelog

All notable changes to **jvagent** (this package) are documented here. Indexing and database-adapter behavior that lives in **jvspatial** is recorded in the [jvspatial changelog](../jvspatial/CHANGELOG.md).

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Added

- Startup index migration: `jvagent.core.index_bootstrap.run_index_migration()` runs before application graph bootstrap — drops deprecated MongoDB index names, then eagerly calls `ensure_indexes` for core entity types. Wired from `pre_startup_bootstrap` and `bootstrap_only`. See [docs/database-indexing.md](docs/database-indexing.md).
