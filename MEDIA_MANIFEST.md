# External media manifest

The repository's generated MP3 and MP4 files live in OneDrive at
`D:\OneDrive\Library`. Git tracks `media-manifest.json` instead of storing the
large media files themselves.

The manifest records each media file's:

- path relative to the OneDrive `Library` folder;
- MP3/MP4 type;
- logical file size;
- modification time in UTC;
- Files On-Demand availability.

## Update the manifest

Run this command from the repository root:

```powershell
python misc\generate_media_manifest.py "D:\OneDrive\Library"
```

Review and commit `media-manifest.json` whenever media is added, removed,
renamed, or changed.

Verify that the committed manifest is current:

```powershell
python misc\generate_media_manifest.py "D:\OneDrive\Library" --check
```

## Optional content hashes

The normal manifest uses metadata only, so OneDrive files can remain
online-only. To include SHA-256 content hashes:

```powershell
python misc\generate_media_manifest.py "D:\OneDrive\Library" --sha256
```

Warning: hashing reads every file and may download all Files On-Demand media.
For the current library this could hydrate roughly 26 GB.

The existing `*.mp3` and `*.mp4` ignore rules must remain enabled; otherwise
large media files could accidentally be added to Git.
