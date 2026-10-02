"""Where Stashy's static files (CSS, JavaScript, images) are stored and how they're named."""

from whitenoise.storage import CompressedManifestStaticFilesStorage


def _without_source_maps(patterns):
    """Django's list of references to follow inside CSS and JS files, minus `sourceMappingURL`."""

    def keep(pattern):
        regex = pattern if isinstance(pattern, str) else pattern[0]
        return "sourceMappingURL" not in regex

    return tuple((glob, tuple(p for p in group if keep(p))) for glob, group in patterns)


class StashyStaticFilesStorage(CompressedManifestStaticFilesStorage):
    """WhiteNoise's compressed storage with fingerprinted file names (stashy.3f9a1c.css).

    While fingerprinting, Django rewrites every file a CSS or JS file points to, and stops with
    an error when one is missing. The vendored libraries (Bootstrap, Chart.js) end with a
    `sourceMappingURL` comment naming a .map file - a debugging aid for the library's own
    developers - that Stashy doesn't ship. Following those comments would make
    `collectstatic`, and so every Stashy.bat launch, fail. Browsers only fetch .map files
    while developer tools are open."""

    patterns = _without_source_maps(CompressedManifestStaticFilesStorage.patterns)
