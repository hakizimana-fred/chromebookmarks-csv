# chromebookmarks-csv

Export and explore your Chromium-browser bookmarks. No dependencies, just Python 3.9+.

Works on Linux, macOS and Windows with Chrome, Chromium, Brave, Edge and Vivaldi, across all profiles.

```
python app.py                  # writes CSV, JSON, importable HTML and the interactive viewer, then opens it
python app.py --list           # show detected browsers/profiles
python app.py --check          # also probe every URL and flag dead links
python app.py -b brave -f csv  # one browser, one format
python app.py -p Work -o out/  # one profile, custom output folder
```

## Outputs

| File | What it is |
| --- | --- |
| `bookmarks.csv` | Title, URL, Folder, Domain, Added, Browser, Profile, Duplicate, Status (opens cleanly in Excel/Sheets) |
| `bookmarks.json` | Same data as JSON |
| `bookmarks_viewer.html` | Self-contained, offline explorer: instant search, folder tree, top-domain chips, sorting, list/grid view, dark mode, duplicate and dead-link filters, copy URL, export the current filtered view. Press `/` to search, `Esc` to reset. |
| `bookmarks_import.html` | Standard bookmarks file you can import into any browser |

Your bookmarks never leave your machine; the viewer makes no network requests. (`--check` is the only feature that contacts the bookmarked sites.)
