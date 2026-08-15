"""The bundled contract dataset.

This package holds data only. `dataset.json` is the manifest — a version, a
date, and the profiles to load — and `profiles/` holds one file per provider
surface. Nothing here is executable, and nothing here is fetched at runtime.

The rules a record must satisfy before it belongs in this directory are in
docs/contract-data.md.
"""
