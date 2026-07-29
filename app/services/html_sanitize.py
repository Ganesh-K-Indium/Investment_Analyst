"""
Sanitization for analyst-report rich-text HTML (content_html / draft item html).

Content originates from the report editor (TipTap) and from clipped research
content, both client-controlled, so it's sanitized here again server-side —
never trust client-side sanitization alone.
"""
import re
import nh3

_ALLOWED_TAGS = {
    "p", "br", "strong", "em", "u", "s", "span",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li",
    "table", "thead", "tbody", "tr", "th", "td",
    "a", "img", "blockquote", "code", "pre", "div",
}

_ALLOWED_ATTRIBUTES = {
    "a": {"href", "title"},
    "img": {"src", "alt", "class", "style"},
    "*": {"style", "class"},
}

# Only these CSS properties survive in a sanitized style="..." attribute.
_ALLOWED_STYLE_PROPS = {
    "color", "background-color", "font-weight",
    "text-decoration", "text-align", "font-style", "font-size",
}
_STYLE_DECL_RE = re.compile(r"([a-zA-Z-]+)\s*:\s*([^;]+)")


def _filter_style(style_value: str) -> str:
    kept = []
    for prop, value in _STYLE_DECL_RE.findall(style_value):
        prop = prop.strip().lower()
        if prop in _ALLOWED_STYLE_PROPS:
            kept.append(f"{prop}: {value.strip()}")
    return "; ".join(kept)


_STYLE_ATTR_RE = re.compile(r'style="([^"]*)"')


def sanitize_report_html(html: str) -> str:
    """Strip to a safe tag/attribute allowlist, then filter inline style properties."""
    if not html:
        return html

    cleaned = nh3.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        link_rel="noopener noreferrer",
    )

    def _replace(match: "re.Match[str]") -> str:
        filtered = _filter_style(match.group(1))
        return f'style="{filtered}"' if filtered else ""

    return _STYLE_ATTR_RE.sub(_replace, cleaned)
