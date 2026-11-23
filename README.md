# Generated data

The "Update news" workflow on `main` writes this branch every hour: `news.json`,
`search.json`, `meta.json`, `seen.json`, `feed.xml` and `archive/YYYY-MM.json`.
The code and its README are on `main`.

To take an item off the page, delete it from its `archive/YYYY-MM.json` file on this
branch. The next run rebuilds the other files from the archive, and `seen.json` keeps
the item from coming back.
