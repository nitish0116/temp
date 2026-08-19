# Security Policy

## Supported versions

Security fixes are applied to the latest revision of the default branch. Older
commits, local experimental branches, generated artifacts, and downloaded model
files are not separately supported.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability.

Use GitHub's private vulnerability reporting feature on the repository's
**Security** tab when it is available. If private reporting is unavailable,
contact the repository owner privately through their GitHub profile and include
only enough information to establish a secure follow-up channel.

Please include:

- the affected component and revision;
- steps to reproduce or a minimal proof of concept;
- expected and observed impact;
- relevant operating-system and dependency versions; and
- any suggested mitigation.

Do not include real credentials, private documents, copyrighted media, personal
data, or destructive payloads. Use synthetic fixtures wherever possible.

The maintainer will acknowledge a complete report when reasonably possible,
investigate it, coordinate a fix, and credit the reporter unless anonymity is
requested. Public disclosure should wait until a fix or mitigation is available.

## Security-sensitive areas

Take particular care with subprocess arguments, filesystem deletion or moves,
archive extraction, untrusted document parsing, generated HTML, network-backed
text-to-speech, model downloads, tokens, and media paths.

