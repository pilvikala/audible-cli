# Wrapper scripts for audible-cli

Use `get_library.py` to get the data file describing the library content.
Use `download_all.py` to download files from the library file.

`library.json` records the date range it was fetched with:

```json
{
    "start_date": "2024-01-01",
    "end_date": null,
    "books": [
        {"id": "B002V8DKY2", "author": "Michael Connelly", "title": "A Darkness More Than Night"}
    ]
}
```

`start_date`/`end_date` are the `--start-date`/`--end-date` values passed to
`get_library.py`, or `null` if not given. `download_all.py` also still accepts
the older format, a bare list of books.
