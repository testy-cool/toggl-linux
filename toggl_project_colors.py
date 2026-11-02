"""Shared local display colors for project dots and the running tray icon."""
import hashlib


# Gruvbox red is lightened within its hue to remain visible on hover surfaces.
PALETTES = {
    "grok-night": ("#7aa2f7", "#9ece6a", "#e0af68", "#bb9af7", "#f7768e", "#7dcfff"),
    "gruvbox": ("#83a598", "#b8bb26", "#fabd2f", "#d3869b", "#fc7d5d", "#8ec07c"),
}


def palette(theme):
    return PALETTES.get(theme, PALETTES["gruvbox"])


def _key(value):
    return str(value) if isinstance(value, (int, str)) and not isinstance(value, bool) else None


def _sort_key(value):
    digits = value.lstrip("0") or "0"
    return (0, len(digits), digits, value) if value.isascii() and value.isdigit() else (1, 0, value, value)


def project_colors(projects, workspace, theme, extra_ids=()):
    """Assign cached identities before filtering; API order and names do not matter."""
    ids = {_key(p.get("id")) for p in projects if isinstance(p, dict)
           and str(p.get("workspace_id")) == str(workspace)} - {None}
    colors = palette(theme)
    result = {key: colors[index % len(colors)] for index, key in enumerate(sorted(ids, key=_sort_key))}
    for value in extra_ids:
        key = _key(value)
        if key is not None and key not in result:
            digest = hashlib.sha256(f"{workspace}:{key}".encode("utf-8", errors="replace")).digest()
            result[key] = colors[digest[0] % len(colors)]
    return result


def appearance(projects, tasks, current, theme):
    workspace = current.get("workspace_id")
    extra = [current.get("project_id"), *(t.get("project_id") for t in tasks if isinstance(t, dict)
             and str(t.get("workspace_id")) == str(workspace))]
    return {"ui_theme": theme, "project_colors": project_colors(projects, workspace, theme, extra)}


def tracking_color(projects, current, theme):
    project_id = current.get("project_id")
    if project_id is not None:
        return project_colors(projects, current.get("workspace_id"), theme, [project_id]).get(_key(project_id))
    description = " ".join((current.get("description") or "").split())
    if not description:
        return None
    digest = hashlib.sha256(description.encode("utf-8", errors="replace")).digest()
    colors = palette(theme)
    return colors[digest[0] % len(colors)]
