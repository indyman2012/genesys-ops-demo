from __future__ import annotations

# Common PascalCase template headers -> snake_case field names
_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "site_name": ("site_name", "SiteName", "Site"),
    "location_name": ("location_name", "LocationName", "Location"),
    "phone_number": ("phone_number", "PhoneNumber", "CallerID", "CallerIDNumber"),
    "route_name": ("route_name", "RouteName"),
    "trunk_name": ("trunk_name", "TrunkName", "TrunkBaseName"),
}


def csv_field(row: dict[str, str], canonical: str, *, default: str = "") -> str:
    """Return a CSV cell value accepting multiple header spellings."""
    names = _COLUMN_ALIASES.get(canonical, (canonical,))
    for name in names:
        if name in row and row[name]:
            return str(row[name]).strip()
    lower_map = {k.casefold(): k for k in row}
    for name in names:
        real = lower_map.get(name.casefold())
        if real and row.get(real):
            return str(row[real]).strip()
    return default


def normalize_csv_row(row: dict[str, str]) -> dict[str, str]:
    """Add snake_case keys for known PascalCase template headers."""
    out = dict(row)
    for canonical in _COLUMN_ALIASES:
        val = csv_field(row, canonical)
        if val and not out.get(canonical):
            out[canonical] = val
    return out
